"""Read-only inspection of a failed measurement's ambiguous pending request."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from bike_mlops import capital_operational_ledger as ledger
from bike_mlops import capital_operational_recovery as recovery
from bike_mlops.capital_measurement_runner import _validate_staging_database


def inspect_pending_audit(candidate_id: str, attempt_id: str, database, *, ledger_path=None, lock_dir=None) -> dict:
    """Look for one pending ID; an absent row may still be committed later.

    Never send a request, modify evidence, declare a gate passed, or retry.
    """
    if not isinstance(candidate_id, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("A lowercase candidate ID is required")
    if not recovery._canonical_attempt(attempt_id):
        raise ValueError("A canonical measurement attempt ID is required")
    _validate_staging_database(database)
    ledger_path = ledger_path if ledger_path is not None else ledger.DEFAULT_LEDGER
    lock_dir = lock_dir if lock_dir is not None else ledger.DEFAULT_LOCK_DIR
    with recovery._existing_lock(lock_dir / f"{candidate_id}.lock"):
        with recovery._existing_lock(ledger_path.with_suffix(ledger_path.suffix + ".lock")):
            state = ledger.read_operational_ledger(ledger_path)
            record = next((item for item in state["records"] if item["candidate_id"] == candidate_id), None)
            if record is None:
                raise ValueError("Candidate has no operational attempt record")
            attempt = next((item for item in record["attempts"] if item["attempt_id"] == attempt_id), None)
            if attempt is None or attempt.get("status") != "failed":
                raise ValueError("A failed ledger-linked measurement is required")
            plan, plan_status = recovery._plan_check(attempt, candidate_id)
            if plan is None or plan_status != "matched":
                raise ValueError("Frozen operational plan does not match the ledger")
            report_path = f"data/reports/capital-bikeshare/operational-{attempt_id}-failure.json"
            descriptor = attempt.get("report")
            if not isinstance(descriptor, dict) or descriptor.get("path") != report_path:
                raise ValueError("Ledger does not link the canonical failure report")
            report, digest, error = recovery._read_report(recovery.PROJECT_ROOT / report_path)
            if error or digest != descriptor.get("sha256"):
                raise ValueError("Ledger-linked failure report is missing, invalid or has a different SHA-256")
            issue = recovery._report_error(report, attempt, candidate_id, plan, linked=True)
            if issue:
                raise ValueError(f"Ledger-linked failure report identity is invalid: {issue}")
            partial = report.get("partial_evidence")
            if not isinstance(partial, dict) or partial.get("pending_request_status") != "outcome_unknown_may_have_been_sent":
                raise ValueError("Failure report has no ambiguous pending request")
            request_id = partial.get("pending_request_id")
            if not isinstance(request_id, str):
                raise ValueError("Pending request ID is missing")
            match = re.fullmatch(rf"capital-op:{re.escape(attempt_id)}:(warmup|measured):([0-9]{{4}})", request_id)
            if not match or int(match.group(2)) >= plan["policy"]["warmup_requests" if match.group(1) == "warmup" else "measured_requests"]:
                raise ValueError("Pending request ID is outside the frozen workload")
            try:
                with database.session() as connection:
                    database.execute(connection, "SET TRANSACTION READ ONLY")
                    rows = database.execute(connection,
                        "SELECT request_id, model_name, model_version, candidate_id, artifact_sha256, "
                        "data_profile_id, target_contract_id FROM predictions WHERE request_id = ?", (request_id,)).fetchall()
            except Exception:
                raise RuntimeError("Could not query the isolated staging database in read-only mode") from None
            if len(rows) > 1:
                raise ValueError("Staging database returned duplicate request IDs")
            observed = "not_seen_at_query_time"
            if rows:
                row = dict(rows[0])
                identity = plan["identity"]
                expected = {"request_id": request_id, "candidate_id": candidate_id, "model_name": identity["model_name"],
                            "model_version": identity["model_version"], "artifact_sha256": identity["registered_artifact_sha256"],
                            "data_profile_id": identity["data_profile_id"], "target_contract_id": identity["target_contract_id"]}
                observed = "identity_matches_unverified" if all(row.get(k) == v for k, v in expected.items()) else "identity_mismatch"
            return {"candidate_id": candidate_id, "attempt_id": attempt_id, "request_id": request_id,
                    "report_sha256": digest, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "audit_observation": observed, "later_commit_possible": not bool(rows),
                    "decision": "inspection_only_no_retry_or_approval",
                    "limitations": ["A missing row may appear later; a matching row is not full input or prediction verification."]}
