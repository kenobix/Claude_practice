"""Independently recalculate the numeric portion of a frozen measurement report.

This is not a provenance, DB audit, resource, approval, or deployment decision.
The caller is responsible for loading the report from a trusted ledger link and
checking its exact file SHA, candidate evidence and current registry separately.
"""
from __future__ import annotations

import hashlib
import math
import re
import uuid

import pandas as pd

from bike_mlops.capital_operational_plan import validate_operational_plan


def recheck_measurement_records(plan: dict, report: dict, *, attempt_id: str, plan_file_sha256: str) -> dict:
    """Check exact workload/order and recompute the p95 from saved request rows."""
    validate_operational_plan(plan, allow_legacy=True)
    try:
        if str(uuid.UUID(attempt_id)) != attempt_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("A canonical measurement attempt ID is required") from error
    if not isinstance(plan_file_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_file_sha256):
        raise ValueError("The frozen plan file SHA-256 is required")
    if not isinstance(report, dict):
        raise ValueError("Measurement report must be an object")
    if (report.get("operational_attempt_id") != attempt_id or report.get("benchmark_id") != attempt_id
            or report.get("operational_plan_sha256") != plan["plan_sha256"]
            or report.get("operational_plan_file_sha256") != plan_file_sha256 or report.get("completed") is not True):
        raise ValueError("Report does not match the completed attempt and frozen plan")
    identity = plan["identity"]
    model = report.get("model_identity")
    expected_model = {"candidate_id": identity["candidate_id"], "model_name": identity["model_name"],
                      "model_version": identity["model_version"], "artifact_sha256": identity["registered_artifact_sha256"],
                      "feature_contract_version": identity["feature_contract_version"]}
    if (not isinstance(model, dict) or any(model.get(k) != v for k, v in expected_model.items())
            or report.get("data_profile_id") != identity["data_profile_id"]
            or report.get("target_contract_id") != identity["target_contract_id"]
            or report.get("api_url") != plan["api_url"]
            or not isinstance(report.get("deployment_permission"), dict)
            or report["deployment_permission"].get("status") != "blocked"):
        raise ValueError("Report model, profile, endpoint or deployment status differs from the frozen plan")
    window = report.get("input_window")
    if (not isinstance(window, dict) or window.get("start_inclusive") != plan["input_window"]["start_inclusive"]
            or window.get("end_exclusive") != plan["input_window"]["end_exclusive"] or window.get("hours") != 168):
        raise ValueError("Report window differs from the frozen plan")
    gate = report.get("operational_gate")
    if not isinstance(gate, dict) or gate.get("status") != "not_evaluated" or not isinstance(gate.get("observed"), dict):
        raise ValueError("Report must retain an unevaluated operational gate")
    records = report.get("measurements")
    warmup, measured = plan["policy"]["warmup_requests"], plan["policy"]["measured_requests"]
    if not isinstance(records, list) or len(records) != warmup + measured:
        raise ValueError("Report does not contain all fixed measurement records")
    start = pd.Timestamp(plan["input_window"]["start_inclusive"])
    ids, durations = [], []
    for index, row in enumerate(records):
        phase = "warmup" if index < warmup else "measured"
        phase_index = index if index < warmup else index - warmup
        request_id = f"capital-op:{attempt_id}:{phase}:{phase_index:04d}"
        target = (start + pd.Timedelta(hours=phase_index % 168)).isoformat(sep=" ")
        if (not isinstance(row, dict) or row.get("phase") != phase or type(row.get("index")) is not int
                or row["index"] != phase_index or row.get("request_id") != request_id or row.get("target_hour") != target):
            raise ValueError("Measurement request order or target differs from the frozen plan")
        latency, prediction, data_hash = row.get("latency_ms"), row.get("prediction"), row.get("data_version_sha256")
        if (type(row.get("http_status")) is not int or row["http_status"] != 200 or row.get("error") is not None
                or type(latency) not in {int, float} or not math.isfinite(latency) or latency < 0
                or type(prediction) not in {int, float} or not math.isfinite(prediction) or prediction < 0
                or not isinstance(data_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", data_hash)):
            raise ValueError("Measurement contains a failed or malformed response record")
        ids.append(request_id)
        if phase == "measured":
            durations.append(float(latency))
    digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()
    observed = gate["observed"]
    expected_counts = {"warmup_requests": warmup, "warmup_errors": 0, "measured_requests": measured,
                       "measured_errors": 0, "unique_request_ids": warmup + measured,
                       "retry_count": 0, "latency_count": measured}
    if (any(type(observed.get(k)) is not int or observed[k] != v for k, v in expected_counts.items())
            or observed.get("request_id_sha256") != digest):
        raise ValueError("Reported counts or request ID digest differ from the saved records")
    rank = math.ceil(0.95 * len(durations))
    p95 = sorted(durations)[rank - 1]
    recorded_p95 = observed.get("p95_ms")
    if type(recorded_p95) not in {int, float} or not math.isfinite(recorded_p95) or not math.isclose(p95, recorded_p95, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError("Reported p95 differs from the saved latency records")
    numeric_status = "passed" if p95 <= plan["policy"]["p95_limit_ms"] else "failed"
    if gate.get("measurement_status") != numeric_status:
        raise ValueError("Reported numerical status differs from the recalculation")
    return {"numeric_status": numeric_status, "measured_requests": measured, "p95_ms": p95,
            "request_id_sha256": digest, "operational_gate": "not_evaluated", "deployment_permission": "blocked",
            "limitations": ["Recalculates saved rows only; does not independently verify DB audit, resource context, retry behavior or file authenticity."]}


def recheck_linked_measurement(candidate_id: str, attempt_id: str, *, ledger_path=None, lock_dir=None) -> dict:
    """Recheck only a ledger-linked report while candidate and ledger locks are held.

    This remains a numeric check, not independent audit, resource or approval proof.
    Missing locks, changed files and incomplete attempts are rejected; nothing is written.
    """
    from bike_mlops import capital_operational_ledger as ledger
    from bike_mlops import capital_operational_recovery as recovery

    if not isinstance(candidate_id, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("A lowercase candidate ID is required")
    if not recovery._canonical_attempt(attempt_id):
        raise ValueError("A canonical measurement attempt ID is required")
    ledger_path = ledger_path if ledger_path is not None else ledger.DEFAULT_LEDGER
    lock_dir = lock_dir if lock_dir is not None else ledger.DEFAULT_LOCK_DIR
    with recovery._existing_lock(lock_dir / f"{candidate_id}.lock"):
        with recovery._existing_lock(ledger_path.with_suffix(ledger_path.suffix + ".lock")):
            state = ledger.read_operational_ledger(ledger_path)
            record = next((item for item in state["records"] if item["candidate_id"] == candidate_id), None)
            if record is None:
                raise ValueError("Candidate has no operational attempt record")
            attempt = next((item for item in record["attempts"] if item["attempt_id"] == attempt_id), None)
            if attempt is None or attempt.get("status") != "completed":
                raise ValueError("A completed ledger-linked measurement is required")
            plan, plan_status = recovery._plan_check(attempt, candidate_id)
            if plan is None or plan_status != "matched":
                raise ValueError("Frozen operational plan does not match the ledger")
            report_path = f"data/reports/capital-bikeshare/operational-{attempt_id}.json"
            descriptor = attempt.get("report")
            if not isinstance(descriptor, dict) or descriptor.get("path") != report_path:
                raise ValueError("Ledger does not link the canonical measurement report")
            report, digest, error = recovery._read_report(recovery.PROJECT_ROOT / report_path)
            if error or digest != descriptor.get("sha256"):
                raise ValueError("Ledger-linked measurement report is missing, invalid or has a different SHA-256")
            issue = recovery._report_error(report, attempt, candidate_id, plan, linked=True)
            if issue:
                raise ValueError(f"Ledger-linked measurement report identity or outcome is invalid: {issue}")
            recovery.verify_completed_journal(attempt, candidate_id, plan, report)
            result = recheck_measurement_records(plan, report, attempt_id=attempt_id,
                                                  plan_file_sha256=attempt["plan_file_sha256"])
            return {**result, "candidate_id": candidate_id, "attempt_id": attempt_id,
                    "report_path": report_path, "report_sha256": digest, "ledger_binding": "matched"}
