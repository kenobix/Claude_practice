"""Portable, hash-bound Capital deployment evidence.

These are local test attestations and saved reports, not authenticated signatures.
No estimator is fitted, evaluation is repeated, or traffic switched here.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from bike_mlops.evaluation_protocol import (
    BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
)
from bike_mlops.profiles import load_data_profile

MODEL = "bike-demand-capital"
PROFILE = load_data_profile("capital-bikeshare")
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{64}$")
CASES = {
    "monitoring": {
        "training_reference_bound": "matched",
        "psi_v2_constant_shift": "detected",
        "distribution_error_separation": "distinguished",
        "delayed_missing_duplicate_labels": "handled",
        "insufficient_labels_and_unset_reference": "explicit",
        "monitor_persistence_reconciliation": "matched",
    },
    "recovery": {
        "concurrent_request_retry": "committed_once",
        "changed_input_conflict": "409",
        "version_switch_preserves_saved_prediction": "preserved",
        "audit_write_failure": "503",
        "bad_artifact_and_ready": "rejected",
        "api_stop": "detected",
        "caddy_switch_and_rollback": "recovered",
        "interrupted_deployment": "recovered",
    },
    "restore": {
        "database_artifact_snapshot_binding": "matched",
        "separate_volume_restore": "restored",
        "restored_version_prediction_audit": "matched",
        "legacy_real_double_migration": "matched",
    },
    "metadata": {
        "entry_runtime_database_identity": "matched",
        "approval_operation_metadata": "matched",
        "unloadable_version": "excluded",
        "timeout_stage_outcome": "explicit",
    },
}
READINESS_POLICY = {"policy_id": "capital-deployment-evidence-v1", "schema_version": 1,
                    "required_cases": CASES, "uncertain_evidence_allowed": False}
BINDING_KEYS = (
    "data_profile_id", "target_contract_id", "feature_contract_version",
    "quality_gate", "validation_gate_status", "final_accuracy_gate_status",
    "quality_gate_policy_id", "quality_gate_policy_sha256",
    "candidate_id", "candidate_model_sha256", "candidate_report_sha256",
    "final_report_sha256", "evaluation_protocol_sha256", "data_sha256", "artifact_sha256",
    "capital_operational_evidence_status", "capital_operational_attempt_id",
    "capital_operational_report_sha256", "capital_operational_plan_sha256",
    "capital_operational_plan_file_sha256", "capital_operational_measurement_status",
    "capital_operational_numeric_recheck_status", "capital_operational_input_provenance_status",
    "capital_operational_audit_status", "capital_operational_resource_context_status",
    "capital_operational_request_id_sha256", "capital_operational_p95_ms",
)
REF_KEYS = (
    "capital_deployment_evidence_sha256", "capital_deployment_evidence_path",
    "capital_deployment_evidence_run_id", "capital_deployment_evidence_policy_id",
    "capital_deployment_evidence_policy_sha256",
)


def canonical_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


READINESS_POLICY_SHA256 = digest(READINESS_POLICY)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate evidence JSON key")
        result[key] = value
    return result


def strict_json(body: str) -> dict:
    try:
        value = json.loads(body, object_pairs_hook=_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Evidence must be valid finite JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Evidence must contain a JSON object")
    # Also reject numeric overflow such as 1e999.
    canonical_bytes(value)
    return value


def utc_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Evidence needs an explicit UTC timestamp") from error
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Evidence needs an explicit UTC timestamp")
    if parsed > datetime.now(timezone.utc):
        raise ValueError("Evidence timestamp must not be in the future")
    return parsed


def binding(tags: dict) -> dict:
    if not isinstance(tags, dict):
        raise ValueError("Capital evidence tags are missing")
    values = {}
    for key in BINDING_KEYS:
        value = tags.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Capital deployment evidence tag is missing or invalid: {key}")
        if (key.endswith("sha256") or key == "candidate_id") and not _SHA.fullmatch(value):
            raise ValueError(f"Invalid SHA-256 evidence tag: {key}")
        values[key] = value
    for key, expected in {
        "data_profile_id": PROFILE["profile_id"], "target_contract_id": PROFILE["target_contract_id"],
        "feature_contract_version": PROFILE["feature_contract_version"],
        "quality_gate": "passed", "validation_gate_status": "passed", "final_accuracy_gate_status": "passed",
        "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
        "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
        "capital_operational_evidence_status": "recorded",
        "capital_operational_numeric_recheck_status": "matched",
        "capital_operational_input_provenance_status": "passed",
        "capital_operational_audit_status": "passed", "capital_operational_resource_context_status": "captured",
    }.items():
        if values[key] != expected:
            raise ValueError(f"Capital evidence differs from its fixed policy: {key}")
    if values["capital_operational_measurement_status"] not in {"passed", "failed"}:
        raise ValueError("Invalid operational measurement status")
    try:
        number = float(values["capital_operational_p95_ms"])
        attempt = str(uuid.UUID(values["capital_operational_attempt_id"]))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid operational p95 or attempt ID") from error
    if not math.isfinite(number) or number < 0 or attempt != values["capital_operational_attempt_id"]:
        raise ValueError("Operational p95 must be finite and non-negative; attempt ID must be canonical")
    return values


def evidence_identity(tags: dict, version: str, run_id: str) -> dict:
    if not isinstance(version, str) or not version.isdecimal() or str(int(version)) != version or int(version) < 1:
        raise ValueError("A positive exact Capital model version is required")
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", run_id):
        raise ValueError("Registered Capital model must identify its source run")
    return {"model_name": MODEL, "model_version": version, "run_id": run_id, **binding(tags)}


def prerequisite_identity(identity: dict) -> dict:
    return {key: identity[key] for key in (
        "model_name", "model_version", "candidate_id", "artifact_sha256",
        "data_profile_id", "target_contract_id", "data_sha256", "feature_contract_version",
    )}


def document(body: bytes) -> dict:
    text = body.decode("utf-8")
    if len(body) > MAX_BUNDLE_BYTES:
        raise ValueError("Evidence document is too large")
    return {"sha256": hashlib.sha256(body).hexdigest(), "content": text}


def unpack(value: object, *, csv: bool = False):
    if (not isinstance(value, dict) or set(value) != {"sha256", "content"}
            or not isinstance(value["content"], str)):
        raise ValueError("Evidence document content is missing")
    body = value["content"].encode("utf-8")
    if len(body) > MAX_BUNDLE_BYTES or hashlib.sha256(body).hexdigest() != value["sha256"]:
        raise ValueError("Evidence document SHA-256 mismatch")
    return value["content"] if csv else strict_json(value["content"])


def validate_prerequisite(report: dict, category: str, identity: dict) -> None:
    expected_identity = prerequisite_identity(identity)
    if (type(report.get("schema_version")) is not int or report.get("schema_version") != 1
            or report.get("kind") != "capital-deployment-prerequisite"
            or report.get("category") != category or report.get("identity") != expected_identity
            or report.get("policy_sha256") != READINESS_POLICY_SHA256
            or report.get("completed") is not True or report.get("status") != "passed"
            or report.get("evidence_state") != "confirmed"):
        raise ValueError(f"Missing, failed, uncertain, or foreign {category} prerequisite evidence")
    if utc_time(report.get("started_at_utc")) > utc_time(report.get("completed_at_utc")):
        raise ValueError("Prerequisite evidence timestamps are out of order")
    checks = report.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(CASES[category]):
        raise ValueError(f"Required {category} prerequisite cases are missing or unsupported")
    for case_id, expected in CASES[category].items():
        result = unpack(checks[case_id])
        if (result.get("case_id") != case_id or result.get("identity") != expected_identity
                or result.get("completed") is not True or result.get("observed_outcome") != expected
                or not isinstance(result.get("observations"), dict) or not result["observations"]):
            raise ValueError(f"Prerequisite case is incomplete or did not meet its fixed outcome: {case_id}")


def _accuracy_rows(text: str, window: dict, delay: int, metrics: dict, baseline: dict, period: str) -> None:
    import pandas as pd
    from bike_mlops.quality_gates import baseline_accuracy_gate
    from bike_mlops.training import regression_metrics

    rows = pd.read_csv(io.StringIO(text))
    required = {"timestamp", "label_available_at", "actual", "candidate_prediction", "baseline_prediction"}
    if (set(rows.columns) != required or not len(rows) or type(window.get("rows")) is not int
            or len(rows) != window.get("rows")):
        raise ValueError("Accuracy row schema/count differs from the frozen protocol")
    times = pd.to_datetime(rows["timestamp"], errors="raise")
    labels = pd.to_datetime(rows["label_available_at"], errors="raise")
    if (times.dt.tz is not None or labels.dt.tz is not None or times.isna().any() or labels.isna().any()
            or not times.is_unique or not times.is_monotonic_increasing
            or (times != times.dt.floor("h")).any()
            or (times < pd.Timestamp(window["start_inclusive"])).any()
            or (times >= pd.Timestamp(window["end_exclusive"])).any()
            or not labels.equals(times + pd.Timedelta(hours=delay))):
        raise ValueError("Accuracy row timestamps/label availability differ from the protocol")
    for column in ("actual", "candidate_prediction", "baseline_prediction"):
        rows[column] = pd.to_numeric(rows[column], errors="raise")
        if not rows[column].map(math.isfinite).all() or (rows[column] < 0).any():
            raise ValueError("Accuracy row values must be finite and non-negative")
    if (rows["actual"] % 1 != 0).any():
        raise ValueError("Actual labels must be integer counts")
    for column, claimed in (("candidate_prediction", metrics), ("baseline_prediction", baseline)):
        actual = regression_metrics(rows["actual"], rows[column])
        if not isinstance(claimed, dict) or any(
            type(claimed.get(key)) not in {int, float}
            or not math.isfinite(claimed[key])
            or not math.isclose(claimed[key], value, rel_tol=1e-12, abs_tol=1e-12)
            for key, value in actual.items()
        ):
            raise ValueError("Reported accuracy differs from saved prediction rows")
    if baseline_accuracy_gate(metrics, baseline, period=period).get("passed") is not True:
        raise ValueError("Saved accuracy evidence does not pass the fixed baseline gate")


def validate_bundle(bundle: dict, tags: dict, version: str, run_id: str) -> dict:
    from bike_mlops.candidate_artifacts import candidate_identity
    from bike_mlops.capital_measurement_recheck import recheck_measurement_records
    from bike_mlops.capital_operational_registry import _validate_completed_evidence
    from bike_mlops.evaluation_protocol import validate_protocol
    from bike_mlops.quality_gates import baseline_accuracy_gate

    if len(canonical_bytes(bundle)) > MAX_BUNDLE_BYTES:
        raise ValueError("Deployment evidence bundle is too large")
    identity = evidence_identity(tags, version, run_id)
    if (type(bundle.get("schema_version")) is not int or bundle.get("schema_version") != 1
            or bundle.get("kind") != "capital-deployment-evidence"
            or canonical_bytes(bundle.get("policy")) != canonical_bytes(READINESS_POLICY)
            or bundle.get("identity") != identity):
        raise ValueError("Deployment evidence policy or exact registered identity differs")
    reports = bundle.get("reports")
    names = {"candidate", "final", "protocol", "plan", "operational", "validation_rows", "final_rows", *CASES}
    if not isinstance(reports, dict) or set(reports) != names:
        raise ValueError("Deployment evidence is missing required report bodies")
    parsed = {name: unpack(reports[name], csv=name.endswith("_rows")) for name in names}
    for name, key in (("candidate", "candidate_report_sha256"), ("final", "final_report_sha256"),
                      ("plan", "capital_operational_plan_file_sha256"),
                      ("operational", "capital_operational_report_sha256")):
        if reports[name]["sha256"] != identity[key]:
            raise ValueError(f"Deployment evidence report differs from registry: {name}")
    candidate, final, protocol, plan, report = (parsed[k] for k in ("candidate", "final", "protocol", "plan", "operational"))
    ci = candidate_identity(candidate)
    candidate_hash = hashlib.sha256(json.dumps(ci, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if (candidate.get("candidate_identity") != ci or candidate_hash != identity["candidate_id"]
            or candidate.get("candidate_id") != identity["candidate_id"]
            or candidate.get("candidate_artifact", {}).get("sha256") != identity["candidate_model_sha256"]
            or candidate.get("evaluation_protocol_sha256") != identity["evaluation_protocol_sha256"]
            or candidate.get("data_sha256") != identity["data_sha256"]):
        raise ValueError("Candidate report does not prove the bound identity")
    validate_protocol(protocol)
    if (protocol.get("schema_version") != 2 or protocol.get("protocol_sha256") != identity["evaluation_protocol_sha256"]
            or protocol["dataset"].get("data_sha256") != identity["data_sha256"]
            or protocol["dataset"].get("source_manifest_sha256") != candidate["data_source"].get("source_manifest_sha256")
            or reports["protocol"]["sha256"] != candidate["evaluation_protocol_artifact"]["sha256"]
            or reports["validation_rows"]["sha256"] != candidate["validation_predictions"]["sha256"]
            or reports["final_rows"]["sha256"] != final.get("evaluation_window", {}).get("row_level_predictions_sha256")):
        raise ValueError("Protocol or prediction-row artifacts differ from the frozen reports")
    for name, body in (("candidate", candidate), ("final", final)):
        if (body.get("data_profile_id") != identity["data_profile_id"]
                or body.get("target_contract_id") != identity["target_contract_id"]
                or body.get("validation_gate_status") != "passed"
                or body.get("quality_gate_policy_id") != identity["quality_gate_policy_id"]
                or body.get("quality_gate_policy_sha256") != identity["quality_gate_policy_sha256"]):
            raise ValueError(f"Wrong profile or accuracy policy in {name} report")
    if (candidate.get("quality_gate_passed") is not True
            or candidate.get("feature_contract_version") != identity["feature_contract_version"]
            or candidate.get("comparison", {}).get("reference") != "historical_baseline_only"
            or candidate.get("comparison", {}).get("incumbent_model_version") is not None
            or candidate.get("comparison", {}).get("incumbent_artifact_sha256") is not None
            or final.get("candidate_id") != identity["candidate_id"]
            or final.get("candidate_artifact_sha256") != identity["candidate_model_sha256"]
            or final.get("evaluation_protocol_sha256") != identity["evaluation_protocol_sha256"]
            or final.get("selection_report") != f"data/candidates/{identity['candidate_id']}/candidate-report.json"
            or final.get("data_source", {}).get("csv_sha256") != identity["data_sha256"]
            or final.get("data_source", {}).get("source_manifest_sha256") != candidate["data_source"]["source_manifest_sha256"]
            or final.get("model_fit_performed_during_evaluation") is not False
            or final.get("evaluation_window", {}).get("used_for_candidate_selection") is not False
            or final.get("operational_gate_status") != "not_evaluated"
            or final.get("deployment_permission", {}).get("status") != "blocked"):
        raise ValueError("Candidate/final reports do not prove the same blocked baseline-only candidate")
    validation, reserved = protocol["validation"], protocol["evaluation"]
    final_window = final["evaluation_window"]
    if (any(final_window.get(k) != reserved.get(k) for k in ("start_inclusive", "end_exclusive", "rows"))
            or candidate["training_cutoff"] != protocol["training"]["cutoff"]
            or candidate["split_counts"]["train"] != protocol["training"]["rows"]
            or candidate["split_counts"]["validation"] != validation["rows"]
            or candidate["validation_window"] != {"start": validation["start_inclusive"], "end_exclusive": validation["end_exclusive"]}):
        raise ValueError("Candidate/final windows differ from the frozen protocol")
    for text, window, model, baseline, period in (
        (parsed["validation_rows"], validation, candidate["validation"]["model"], candidate["validation"]["baseline"], "validation_selection"),
        (parsed["final_rows"], reserved, final["candidate"]["model"], final["historical_baseline"]["model"], "final_evaluation"),
    ):
        _accuracy_rows(text, window, protocol["label_availability"]["delay_hours"], model, baseline, period)
        claimed = candidate["comparison"]["validation_gate"] if period == "validation_selection" else final["final_accuracy_gate"]
        if claimed != baseline_accuracy_gate(model, baseline, period=period):
            raise ValueError("Recorded accuracy gate differs from recomputation")
    expected_plan = {
        "candidate_id": identity["candidate_id"], "candidate_report_sha256": identity["candidate_report_sha256"],
        "final_report_sha256": identity["final_report_sha256"], "evaluation_protocol_sha256": identity["evaluation_protocol_sha256"],
        "data_sha256": identity["data_sha256"], "manifest_sha256": candidate["data_source"]["source_manifest_sha256"],
        "candidate_artifact_sha256": identity["candidate_model_sha256"], "registered_artifact_sha256": identity["artifact_sha256"],
        "model_name": MODEL, "model_version": version, "data_profile_id": identity["data_profile_id"],
        "target_contract_id": identity["target_contract_id"], "feature_contract_version": identity["feature_contract_version"],
    }
    if (plan.get("identity") != expected_plan or plan.get("plan_sha256") != identity["capital_operational_plan_sha256"]
            or plan.get("input_window", {}).get("training_cutoff") != candidate["training_cutoff"]
            or plan.get("input_window", {}).get("training_rows") != candidate["split_counts"]["train"]
            or plan.get("input_window", {}).get("training_row_keys_sha256") != candidate["training_row_keys_sha256"]):
        raise ValueError("Operational plan differs from candidate training identity")
    numeric = recheck_measurement_records(plan, report, attempt_id=identity["capital_operational_attempt_id"],
                                          plan_file_sha256=identity["capital_operational_plan_file_sha256"])
    recorded = _validate_completed_evidence(plan, report, reports["operational"]["sha256"],
                                            identity["capital_operational_attempt_id"],
                                            identity["capital_operational_plan_file_sha256"])
    if (any(tags.get(k) != v for k, v in recorded.items()) or numeric["numeric_status"] != "passed"
            or numeric["p95_ms"] > 250):
        raise ValueError("Operational evidence or fixed p95 gate differs from saved rows")
    for category in CASES:
        validate_prerequisite(parsed[category], category, identity)
    return {"identity": identity, "p95_ms": numeric["p95_ms"], "policy_sha256": READINESS_POLICY_SHA256}


def reference_tags(body: bytes, run_id: str) -> dict:
    value = hashlib.sha256(body).hexdigest()
    return {"capital_deployment_evidence_sha256": value,
            "capital_deployment_evidence_path": f"capital-deployment-evidence/{value}/bundle.json",
            "capital_deployment_evidence_run_id": run_id,
            "capital_deployment_evidence_policy_id": READINESS_POLICY["policy_id"],
            "capital_deployment_evidence_policy_sha256": READINESS_POLICY_SHA256}


def verified_registered_evidence(client, version: str, tags: dict) -> dict:
    if client is None:
        raise ValueError("Capital deployment requires the registry client and report bodies")
    tags = dict(tags)
    before = client.get_model_version(MODEL, version)
    run_id = getattr(before, "run_id", None)
    identity = evidence_identity(tags, version, run_id)
    if (before.name != MODEL or str(before.version) != version or before.status != "READY"
            or binding(before.tags) != binding(tags)):
        raise ValueError("Registered model identity changed before evidence retrieval")
    expected_sha = tags.get(REF_KEYS[0])
    expected_path = f"capital-deployment-evidence/{expected_sha}/bundle.json"
    if (not isinstance(expected_sha, str) or not _SHA.fullmatch(expected_sha)
            or tags.get(REF_KEYS[1]) != expected_path or tags.get(REF_KEYS[2]) != run_id
            or tags.get(REF_KEYS[3]) != READINESS_POLICY["policy_id"]
            or tags.get(REF_KEYS[4]) != READINESS_POLICY_SHA256):
        raise ValueError("Required deployment report artifact reference is missing or invalid")
    try:
        with tempfile.TemporaryDirectory(prefix="capital-deployment-evidence-") as directory:
            path = Path(client.download_artifacts(run_id, expected_path, directory))
            if path.is_symlink() or not path.resolve().is_relative_to(Path(directory).resolve()) or not path.is_file():
                raise ValueError("Downloaded evidence must be a regular file in the temporary directory")
            if path.stat().st_size > MAX_BUNDLE_BYTES:
                raise ValueError("Deployment evidence bundle is too large")
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != expected_sha:
                raise ValueError("Deployment evidence bundle SHA-256 mismatch")
            result = validate_bundle(strict_json(body.decode("utf-8")), tags, version, run_id)
    except (OSError, UnicodeError, KeyError, TypeError) as error:
        raise ValueError("Deployment evidence report bodies are missing or invalid") from error
    after = client.get_model_version(MODEL, version)
    protected = set(BINDING_KEYS) | set(REF_KEYS) | {"operational_gate_status", "deployment_permission"}
    protected.update(key for current in (tags, before.tags, after.tags) for key in current
                     if key.startswith(("capital_operational_review_", "capital_deployment_approval_"))
                     or key in {"capital_deployment_approver", "capital_deployment_decision_schema_version"})
    if (after.name != MODEL or str(after.version) != version or after.status != "READY"
            or getattr(after, "run_id", None) != run_id
            or any(before.tags.get(k) != tags.get(k) or after.tags.get(k) != tags.get(k) for k in protected)):
        raise ValueError("Registered deployment evidence changed during retrieval")
    return {**result, "bundle_sha256": expected_sha}
