"""Product components: operational_runner."""
from __future__ import annotations


# ========================================================================
# capital_measurement_preflight
# ========================================================================

"""Revalidate a locked operational attempt before opening the staging snapshot.

This function sends no prediction requests and grants no operational eligibility.
It must run inside the candidate's measurement_attempt context; its returned frame
must be used while the caller holds and validates the staging observation lock.
"""


import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from bike_mlops.evaluation import sha256_file
from bike_mlops.operational_validation import check_candidate_training_window
from bike_mlops.operational import _read_plan
from bike_mlops.operational import validate_plan_registration, verify_operational_plan_binding
from bike_mlops.staging import verify_candidate_for_staging
from bike_mlops.cli import _load_profile_hourly
from bike_mlops.config import DATA_PROFILE_ID, DATASET_MANIFEST, DEFAULT_DATASET, MODEL_NAME


@dataclass(frozen=True)
class VerifiedMeasurementInputs:
    plan: dict
    frame: pd.DataFrame
    evidence: dict
    window: dict
    plan_file_sha256: str


def verify_measurement_inputs(attempt, plan_path: Path, selection_path: Path, final_path: Path) -> VerifiedMeasurementInputs:
    """Recheck plan, candidate, dataset and registry against the started attempt."""
    if DATA_PROFILE_ID != "capital-bikeshare" or MODEL_NAME != "bike-demand-capital":
        raise ValueError("Measurement preflight requires the Capital data profile and model")
    if not getattr(attempt, "_active", False) or getattr(attempt, "_finished", True):
        raise ValueError("Measurement preflight requires an active unfinished attempt")
    plan, _, file_hash = _read_plan(plan_path)
    identity = plan["identity"]
    if (attempt.candidate_id != identity["candidate_id"] or attempt.plan_sha256 != plan["plan_sha256"]
            or attempt.plan_file_sha256 != file_hash):
        raise ValueError("Operational plan differs from the started attempt")
    window = check_candidate_training_window(selection_path, final_path, identity["candidate_id"],
                                             start=plan["input_window"]["start_inclusive"],
                                             end=plan["input_window"]["end_exclusive"])
    evidence = verify_candidate_for_staging(selection_path, final_path)
    expected_data = identity["data_sha256"]
    expected_manifest = identity["manifest_sha256"]

    def check_data_files():
        if sha256_file(DEFAULT_DATASET) != expected_data or sha256_file(DATASET_MANIFEST) != expected_manifest:
            raise ValueError("Operational CSV or manifest differs from the frozen candidate")

    check_data_files()
    frame = _load_profile_hourly(DEFAULT_DATASET)
    check_data_files()
    from mlflow.tracking import MlflowClient
    import mlflow.artifacts

    registry_uri = plan["registry_uri"]
    client = MlflowClient(tracking_uri=registry_uri, registry_uri=registry_uri)
    registered = client.get_model_version(identity["model_name"], identity["model_version"])
    validate_plan_registration(evidence, registered)
    with tempfile.TemporaryDirectory(prefix="capital-preflight-artifact-") as directory:
        artifact_dir = Path(mlflow.artifacts.download_artifacts(
            artifact_uri=f"models:/{identity['model_name']}/{identity['model_version']}",
            dst_path=directory, tracking_uri=registry_uri, registry_uri=registry_uri,
        ))
        verify_operational_plan_binding(plan, evidence=evidence, window=window, registered=registered,
                                        artifact_dir=artifact_dir, registry_uri=registry_uri)
        # Detect registry changes that happened during the download as well.
        registered_after = client.get_model_version(identity["model_name"], identity["model_version"])
        verify_operational_plan_binding(plan, evidence=evidence, window=window, registered=registered_after,
                                        artifact_dir=artifact_dir, registry_uri=registry_uri)
    latest_evidence = verify_candidate_for_staging(selection_path, final_path)
    if any(latest_evidence.get(key) != evidence.get(key) for key in (
        "candidate_id", "selection_report_sha256", "final_report_sha256", "evaluation_protocol_sha256",
        "candidate_artifact_sha256",
    )):
        raise ValueError("Candidate evidence changed during preflight")
    check_data_files()
    current_plan, _, current_file_hash = _read_plan(plan_path)
    if current_file_hash != file_hash or current_plan["plan_sha256"] != plan["plan_sha256"]:
        raise ValueError("Operational plan changed during preflight")
    if not getattr(attempt, "_active", False) or getattr(attempt, "_finished", True):
        raise ValueError("Measurement attempt ended during preflight")
    return VerifiedMeasurementInputs(plan=plan, frame=frame, evidence=evidence, window=window,
                                     plan_file_sha256=file_hash)


# ========================================================================
# capital_measurement_runner
# ========================================================================

"""Run one frozen staging measurement; deployment approval is deliberately absent."""


from pathlib import Path as _measurement_runner_Path
import signal
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit

from bike_mlops.operational_validation import CapitalBenchmarkObserver
from bike_mlops.operational_validation import FeatureSourceExpectations, hold_observation_snapshot
_measurement_runner_verify_measurement_inputs = verify_measurement_inputs
from bike_mlops.operational import measurement_attempt
from bike_mlops.config import PROJECT_ROOT
from bike_mlops.operational import RequestJournal
from bike_mlops.operational import run_capital_operational_benchmark, write_benchmark_report


def _validate_staging_database(database) -> None:
    """Restrict the internal runner to the explicitly isolated staging DB."""
    try:
        parsed = urlsplit(database.url)
        valid = (parsed.scheme in {"postgresql", "postgres"} and parsed.hostname in {"localhost", "127.0.0.1"}
                 and parsed.port == 55434 and parsed.username == "bike_app" and parsed.path == "/bikeops"
                 and not parsed.query and not parsed.fragment and not database.sqlite
                 and database.data_profile_id == "capital-bikeshare"
                 and database.target_contract_id == "capital-trip-starts-hourly-v1")
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Measurement requires the isolated local Capital staging PostgreSQL database")


def _identity(plan: dict) -> dict:
    identity = plan["identity"]
    return {"candidate_id": identity["candidate_id"], "model_name": identity["model_name"],
            "model_version": identity["model_version"], "artifact_sha256": identity["registered_artifact_sha256"],
            "data_profile_id": identity["data_profile_id"], "target_contract_id": identity["target_contract_id"],
            "feature_contract_version": identity["feature_contract_version"]}


def _attach_attempt(report: dict, attempt) -> None:
    report.update({"operational_attempt_id": attempt.attempt_id, "operational_plan_sha256": attempt.plan_sha256,
                   "operational_plan_file_sha256": attempt.plan_file_sha256})


def _failure_report(attempt, stage: str, error_kind: str, report: dict | None, observer) -> dict:
    """Preserve partial request evidence without writing exception text or credentials."""
    exchanges = observer.exchanges if observer is not None else []
    pending = getattr(observer, "_pending", None) if observer is not None else None
    verified_ids = [payload["request_id"] for payload, _ in exchanges]
    if report is None:
        report = {"schema_version": 1, "completed": False, "measurements": [], "verified_response_ids": verified_ids,
            "pending_request_id": pending.get("request_id") if isinstance(pending, dict) else None}
    _attach_attempt(report, attempt)
    report["partial_evidence"] = {
        "verified_response_ids": verified_ids,
        "verified_response_count": len(verified_ids),
        "pending_request_id": pending.get("request_id") if isinstance(pending, dict) else None,
        "pending_request_status": "outcome_unknown_may_have_been_sent" if isinstance(pending, dict) else None,
        "limitation": "Memory may be lost; inspect the durable journal. Timeout or DB absence does not rule out a later commit.",
    }
    report["request_sequence_completed"] = report.get("completed") is True
    report["completed"] = False
    gate = report.setdefault("operational_gate", {})
    gate["status"] = "failed"
    gate["input_provenance_status"] = "not_evaluated"
    report["failure"] = {"stage": stage, "error_type": error_kind}
    report["audit"] = {"status": "not_evaluated"}
    report["deployment_permission"] = {"status": "blocked"}
    return report


@contextmanager
def _termination_as_interrupt():
    """Best-effort SIGTERM report on CLI's main thread; SIGKILL requires journal inspection."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)
    def stop(_signum, _frame):
        raise KeyboardInterrupt("SIGTERM")
    signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def run_staging_measurement(plan_path: _measurement_runner_Path, selection_path: _measurement_runner_Path, final_path: _measurement_runner_Path, database) -> dict:
    """Run one auditable candidate attempt; never make a deployment decision.

    Numeric success plus audit verification still leaves the operational gate
    not_evaluated pending independent evidence/resource review. The caller must
    not treat the ledger's completed state as a passed gate.
    """
    _validate_staging_database(database)
    outcome = None
    interrupted = False
    with _termination_as_interrupt(), measurement_attempt(plan_path) as attempt:
        stage = "preflight"
        observer = None
        report = None
        journal = None
        success_report_path = PROJECT_ROOT / "data/reports/capital-bikeshare" / f"operational-{attempt.attempt_id}.json"
        failure_report_path = PROJECT_ROOT / "data/reports/capital-bikeshare" / f"operational-{attempt.attempt_id}-failure.json"
        try:
            verified = _measurement_runner_verify_measurement_inputs(attempt, plan_path, selection_path, final_path)
            plan, frame = verified.plan, verified.frame
            identity = _identity(plan)
            stage = "journal"
            journal = RequestJournal(success_report_path.with_suffix(".requests.jsonl"), attempt, plan)
            stage = "snapshot"
            with hold_observation_snapshot(database, frame, plan["identity"]["data_sha256"]) as snapshot:
                sources = FeatureSourceExpectations(frame, plan["identity"]["data_sha256"],
                                                    label_delay_hours=plan["input_window"]["label_delay_hours"])
                observer = CapitalBenchmarkObserver(attempt, snapshot, sources, pinned_identity=identity, journal=journal)
                stage = "measurement"
                report = run_capital_operational_benchmark(
                    api_url=plan["api_url"], model_version=identity["model_version"],
                    candidate_id=identity["candidate_id"], start=plan["input_window"]["start_inclusive"],
                    end=plan["input_window"]["end_exclusive"], attempt_id=attempt.attempt_id,
                    expected_artifact_sha256=identity["artifact_sha256"], request_observer=observer,
                )
                _attach_attempt(report, attempt)
                if not report.get("completed"):
                    if report.get("interruption_requested"):
                        raise KeyboardInterrupt("Measurement termination requested")
                    raise RuntimeError("Incomplete measurement sequence")
                if report.get("operational_gate", {}).get("status") != "not_evaluated":
                    raise ValueError("Operational gate must remain unevaluated")
                if report.get("operational_gate", {}).get("measurement_status") not in {"passed", "failed"}:
                    raise ValueError("Completed measurement must retain its numerical result")
                if (report.get("benchmark_id") != attempt.attempt_id or not isinstance(report.get("model_identity"), dict)
                        or any(report["model_identity"].get(key) != value for key, value in identity.items()
                               if key in {"candidate_id", "model_name", "model_version", "artifact_sha256", "feature_contract_version"})):
                    raise ValueError("Measurement report identity differs from the fixed attempt")
                stage = "audit"
                journal.assert_complete()
                audit = observer.verify_audits()
                measured_ids = report.get("operational_gate", {}).get("observed", {}).get("request_id_sha256")
                if audit.get("verified_rows") != 1050 or audit.get("request_id_sha256") != measured_ids:
                    raise ValueError("Audit count or request ID digest differs from the measurement")
                report["audit"] = {"status": "passed", **audit}
                report["operational_gate"]["input_provenance_status"] = "passed"
                report["snapshot"] = {"status": "verified", "observation_count": snapshot.row_count}
                report["limitations"] = [item for item in report.get("limitations", [])
                                         if item != "DB snapshot and API data provenance are not verified; numerical success is not an operational gate pass."]
                report["limitations"].append("Independent operational evidence and resource review remain incomplete.")
                snapshot.assert_held()
                stage = "report"
                report["request_journal"] = journal.descriptor()
                write_benchmark_report(success_report_path, report)
                snapshot.assert_held()
                stage = "snapshot_release"
            # Do not mark the attempt complete unless the snapshot context
            # successfully checks and releases its lock.
            stage = "ledger"
            attempt.finish(status="completed", report_path=success_report_path)
            outcome = {"status": "completed", "measurement_status": report["operational_gate"]["measurement_status"],
                       "operational_gate": "not_evaluated", "deployment_permission": "blocked",
                       "attempt_id": attempt.attempt_id, "report_path": str(success_report_path)}
        except (Exception, KeyboardInterrupt) as error:
            interrupted = isinstance(error, KeyboardInterrupt)
            failure = _failure_report(attempt, stage, type(error).__name__, report, observer)
            if journal is not None:
                failure["request_journal"] = journal.descriptor()
            write_benchmark_report(failure_report_path, failure)
            failure_code = {"preflight": "preflight_failed", "snapshot": "snapshot_lost", "snapshot_release": "snapshot_lost", "measurement": "request_failed",
                            "audit": "audit_mismatch"}.get(stage, "other")
            attempt.finish(status="failed", report_path=failure_report_path, failure_code=failure_code)
            outcome = {"status": "failed", "operational_gate": "failed", "deployment_permission": "blocked",
                       "attempt_id": attempt.attempt_id, "report_path": str(failure_report_path),
                       "failure_stage": stage, "error_type": type(error).__name__}
        finally:
            if journal is not None:
                journal.close()
    if interrupted:
        raise KeyboardInterrupt("Measurement interrupted; failure report was saved")
    return outcome


# ========================================================================
# capital_pending_audit
# ========================================================================

"""Read-only inspection of a failed measurement's ambiguous pending request."""


import re
from datetime import datetime, timezone

from bike_mlops import operational as ledger
from bike_mlops import operational_validation as recovery
_pending_audit_validate_staging_database = _validate_staging_database


def inspect_pending_audit(candidate_id: str, attempt_id: str, database, *, ledger_path=None, lock_dir=None) -> dict:
    """Look for one pending ID; an absent row may still be committed later.

    Never send a request, modify evidence, declare a gate passed, or retry.
    """
    if not isinstance(candidate_id, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("A lowercase candidate ID is required")
    if not recovery._canonical_attempt(attempt_id):
        raise ValueError("A canonical measurement attempt ID is required")
    _pending_audit_validate_staging_database(database)
    ledger_path = ledger_path if ledger_path is not None else ledger.DEFAULT_LEDGER
    lock_dir = lock_dir if lock_dir is not None else ledger.DEFAULT_LOCK_DIR
    with recovery._existing_lock(lock_dir / f"{candidate_id}.lock"):
        with recovery._existing_lock(ledger_path.with_suffix(ledger_path.suffix + ".lock")):
            state = ledger.read_operational_ledger(ledger_path)
            record = next((item for item in state["records"] if item["candidate_id"] == candidate_id), None)
            if record is None:
                raise ValueError("Candidate has no operational attempt record")
            attempt = next((item for item in record["attempts"] if item["attempt_id"] == attempt_id), None)
            if attempt is None or attempt.get("status") not in {"failed", "interrupted", "started"}:
                raise ValueError("A stopped measurement attempt is required")
            plan, plan_status = recovery._plan_check(attempt, candidate_id)
            if plan is None or plan_status != "matched":
                raise ValueError("Frozen operational plan does not match the ledger")
            digest = None
            journal_sha = None
            if "request_journal" in plan["policy"]:
                journal = recovery.inspect_attempt_journal(attempt, candidate_id, plan)
                if journal["status"] not in {"readable", "torn_tail"}:
                    raise ValueError("Request journal is missing or invalid")
                unknown = [key for key, value in journal["requests"].items()
                           if value == "outcome_unknown_may_have_been_sent"]
                if len(unknown) != 1:
                    raise ValueError("Journal has no single ambiguous pending request")
                request_id = unknown[0]
                journal_sha = journal["sha256"]
            else:
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
                    "report_sha256": digest, "request_journal_sha256": journal_sha, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "audit_observation": observed, "later_commit_possible": not bool(rows),
                    "decision": "inspection_only_no_retry_or_approval",
                    "limitations": ["A missing row may appear later; a matching row is not full input or prediction verification."]}
