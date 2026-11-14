"""Product components: deployment."""
from __future__ import annotations


# ========================================================================
# capital_deployment_evidence
# ========================================================================

"""Portable, hash-bound Capital deployment evidence.

These are local test attestations and saved reports, not authenticated signatures.
No estimator is fitted, evaluation is repeated, or traffic switched here.
"""


import hashlib
import io
import json
import math
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic_ns

from bike_mlops.evaluation import BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
from bike_mlops.config import _profiles_load_data_profile as load_data_profile

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
    observed = datetime.now(timezone.utc)
    if parsed > observed:
        delta = parsed - observed
        ahead_us = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        error = ValueError("Evidence timestamp must not be in the future")
        error.add_note(f"timestamp_utc={parsed.isoformat()} observed_at_utc={observed.isoformat()} "
                       f"ahead_microseconds={ahead_us} monotonic_ns={monotonic_ns()}")
        raise error
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
    from bike_mlops.evaluation import baseline_accuracy_gate
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
    from bike_mlops.evaluation import candidate_identity
    from bike_mlops.operational_validation import recheck_measurement_records
    from bike_mlops.operational_validation import _validate_completed_evidence
    from bike_mlops.evaluation import validate_protocol
    from bike_mlops.evaluation import baseline_accuracy_gate

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


# ========================================================================
# capital_deployment_decisions
# ========================================================================

"""Record a reviewed operational gate and a separate reasoned approval.

Registry tags provide hash-linked evidence, not authenticated identities. The
reviewer and approver names are declarations in this local learning project.
"""


import hashlib as _deployment_decisions_hashlib
import json as _deployment_decisions_json
import math as _deployment_decisions_math
import re as _deployment_decisions_re
from datetime import datetime as _deployment_decisions_datetime, timezone as _deployment_decisions_timezone

_deployment_decisions_REF_KEYS = REF_KEYS
_deployment_decisions_binding = binding
_deployment_decisions_utc_time = utc_time
_deployment_decisions_verified_registered_evidence = verified_registered_evidence

from bike_mlops.evaluation import BOOTSTRAP_QUALITY_GATE_POLICY as _deployment_decisions_BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256 as _deployment_decisions_BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
from bike_mlops.config import _profiles_load_data_profile as _deployment_decisions_load_data_profile

CAPITAL_MODEL_NAME = "bike-demand-capital"
_SHA256 = _deployment_decisions_re.compile(r"^[0-9a-f]{64}$")
_CAPITAL_PROFILE = _deployment_decisions_load_data_profile("capital-bikeshare")


def _canonical_sha256(payload: dict) -> str:
    encoded = _deployment_decisions_json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _deployment_decisions_hashlib.sha256(encoded).hexdigest()


def _required(tags: dict, key: str, expected: str | None = None) -> str:
    value = tags.get(key)
    if not isinstance(value, str) or not value.strip() or (expected is not None and value != expected):
        raise ValueError(f"Capital deployment evidence tag is missing or invalid: {key}")
    return value


def _sha_tag(tags: dict, key: str) -> str:
    value = _required(tags, key)
    if not _SHA256.fullmatch(value):
        raise ValueError(f"Capital deployment evidence tag is not a SHA-256: {key}")
    return value


def _validate_registered_version(client, version: str):
    if not isinstance(version, str) or not version.isdecimal() or int(version) < 1 or str(int(version)) != version:
        raise ValueError("A positive exact Capital model version is required")
    registered = client.get_model_version(CAPITAL_MODEL_NAME, int(version))
    if (registered.name != CAPITAL_MODEL_NAME or str(registered.version) != version
            or registered.status != "READY"):
        raise ValueError("Exact Capital model version must be READY")
    tags = registered.tags
    if not isinstance(tags, dict):
        raise ValueError("Registered Capital model version has no tag mapping")
    _deployment_decisions_binding(tags)
    if tags.get("capital_operational_review_sha256") and tags.get("capital_deployment_decision_schema_version") != "2":
        raise ValueError("Legacy decisions cannot be upgraded in place")
    profile = _CAPITAL_PROFILE
    for key, expected in {
        "data_profile_id": profile["profile_id"],
        "target_contract_id": profile["target_contract_id"],
        "quality_gate": "passed",
        "validation_gate_status": "passed",
        "final_accuracy_gate_status": "passed",
        "quality_gate_policy_id": _deployment_decisions_BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
        "quality_gate_policy_sha256": _deployment_decisions_BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
    }.items():
        _required(tags, key, expected)
    _required(tags, "candidate_id")
    for key in ("candidate_id", "candidate_model_sha256", "candidate_report_sha256",
                "final_report_sha256", "evaluation_protocol_sha256", "data_sha256", "artifact_sha256"):
        value = _required(tags, key)
        if not _SHA256.fullmatch(value):
            raise ValueError(f"Registered candidate tag is not a SHA-256: {key}")
    for key in ("capital_operational_report_sha256", "capital_operational_plan_sha256",
                "capital_operational_plan_file_sha256", "capital_operational_request_id_sha256"):
        _sha_tag(tags, key)
    _required(tags, "capital_operational_evidence_status", "recorded")
    _required(tags, "capital_operational_measurement_status")
    _required(tags, "capital_operational_numeric_recheck_status", "matched")
    _required(tags, "capital_operational_input_provenance_status", "passed")
    _required(tags, "capital_operational_audit_status", "passed")
    _required(tags, "capital_operational_resource_context_status", "captured")
    _required(tags, "capital_operational_attempt_id")
    try:
        p95 = float(_required(tags, "capital_operational_p95_ms"))
    except ValueError as error:
        raise ValueError("Recorded operational p95 is not numeric") from error
    if not _deployment_decisions_math.isfinite(p95) or p95 < 0:
        raise ValueError("Recorded operational p95 must be finite and non-negative")
    return registered, tags


def _snapshot(tags: dict, *, require_reference: bool = True) -> dict:
    result = _deployment_decisions_binding(tags)
    for key in _deployment_decisions_REF_KEYS:
        result[key] = _required(tags, key) if require_reference else tags.get(key)
    return result


def _decision_time(tags: dict, key: str) -> str:
    at = tags.get(key, _deployment_decisions_datetime.now(_deployment_decisions_timezone.utc).isoformat())
    _deployment_decisions_utc_time(at)
    return at


def _review_payload(*, model_version: str, tags: dict, reviewer: str, decision: str, reason: str) -> dict:
    return {
        "schema_version": 2, "decision_type": "capital_operational_review",
        "model_name": CAPITAL_MODEL_NAME, "model_version": model_version,
        "evidence": _snapshot(tags, require_reference=decision == "passed"),
        "reviewer": reviewer, "decision": decision, "reason": reason,
        "recorded_at_utc": _required(tags, "capital_operational_review_at_utc"),
    }


def _approval_payload(*, model_version: str, tags: dict, approver: str, reason: str) -> dict:
    return {
        "schema_version": 2, "decision_type": "capital_deployment_approval",
        "model_name": CAPITAL_MODEL_NAME, "model_version": model_version,
        "evidence": _snapshot(tags), "review_sha256": tags["capital_operational_review_sha256"],
        "approver": approver, "reason": reason,
        "recorded_at_utc": _required(tags, "capital_deployment_approval_at_utc"),
    }


def _write_tags_immutably(client, version: str, tags_to_write: dict[str, str], expected: dict) -> None:
    current = client.get_model_version(CAPITAL_MODEL_NAME, int(version))
    if (_deployment_decisions_binding(current.tags) != _deployment_decisions_binding(expected)
            or any(current.tags.get(key) != expected.get(key) for key in _deployment_decisions_REF_KEYS)):
        raise ValueError("Deployment evidence changed before decision write")
    for key, value in tags_to_write.items():
        existing = current.tags.get(key)
        if existing is not None and existing != value:
            raise ValueError(f"Refusing to overwrite a different decision tag: {key}")
    for key, value in tags_to_write.items():
        if current.tags.get(key) != value:
            client.set_model_version_tag(CAPITAL_MODEL_NAME, version, key, value)


def record_operational_review(client, version: str, reviewer: str, decision: str, reason: str) -> dict:
    """Serialize a human review with other Capital registry evidence writers."""
    from bike_mlops.operational_validation import _version_record_lock

    with _version_record_lock(CAPITAL_MODEL_NAME, version):
        return _record_operational_review_locked(client, version, reviewer, decision, reason)


def _record_operational_review_locked(client, version: str, reviewer: str, decision: str, reason: str) -> dict:
    """Record a single human review; only a passing review advances the operational gate."""
    reviewer = reviewer.strip() if isinstance(reviewer, str) else ""
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reviewer or len(reviewer) > 200 or not reason or len(reason) > 1000:
        raise ValueError("Operational review requires a reviewer name and a non-empty reason")
    if decision not in {"passed", "rejected"}:
        raise ValueError("Operational review decision must be passed or rejected")
    _, tags = _validate_registered_version(client, version)
    gate_status = tags.get("operational_gate_status")
    permission = tags.get("deployment_permission")
    if (gate_status, permission) not in {
        ("not_evaluated", "blocked"), ("passed", "blocked"), ("failed", "blocked")
    }:
        raise ValueError("Operational review requires an unevaluated or previously recorded blocked decision")
    if (gate_status != "not_evaluated"
            and not tags.get("capital_operational_review_sha256")):
        raise ValueError("Operational gate status was changed without a linked review record")
    p95 = float(tags["capital_operational_p95_ms"])
    if decision == "passed" and (
        tags["capital_operational_measurement_status"] != "passed" or p95 > 250.0
    ):
        raise ValueError("Operational review cannot pass: measurement status or p95 violates the fixed gate")

    if decision == "passed":
        _deployment_decisions_verified_registered_evidence(client, version, tags)
    tags = dict(tags)
    tags["capital_operational_review_at_utc"] = _decision_time(tags, "capital_operational_review_at_utc")
    payload = _review_payload(model_version=version, tags=tags, reviewer=reviewer,
                              decision=decision, reason=reason)
    digest = _canonical_sha256(payload)
    outcome = "passed" if decision == "passed" else "rejected"
    tags_to_write = {
        "capital_operational_review_at_utc": tags["capital_operational_review_at_utc"],
        "capital_deployment_decision_schema_version": "2",
        "capital_operational_review_status": outcome,
        "capital_operational_review_reviewer": reviewer,
        "capital_operational_review_decision": decision,
        "capital_operational_review_reason": reason,
        "capital_operational_review_report_sha256": tags["capital_operational_report_sha256"],
        "capital_operational_review_candidate_id": tags["candidate_id"],
        "capital_operational_review_model_version": version,
        "capital_operational_review_artifact_sha256": tags["artifact_sha256"],
        "capital_operational_review_sha256": digest,
    }
    # Gate status is written last: interrupted writes remain fail-closed.
    _write_tags_immutably(client, version, tags_to_write, tags)
    gate_status = "passed" if decision == "passed" else "failed"
    client.set_model_version_tag(CAPITAL_MODEL_NAME, version, "operational_gate_status", gate_status)
    verified = client.get_model_version(CAPITAL_MODEL_NAME, int(version))
    if (verified.tags.get("operational_gate_status") != gate_status
            or verified.tags.get("deployment_permission") != "blocked"
            or verified.tags.get("capital_operational_review_sha256") != digest
            or any(verified.tags.get(k) != v for k, v in tags_to_write.items())
            or _snapshot(verified.tags, require_reference=decision == "passed") != _snapshot(tags, require_reference=decision == "passed")):
        raise RuntimeError("Operational review did not persist in the expected blocked state")
    return {"status": "review_recorded", "model_version": version, "decision": decision,
            "operational_gate_status": gate_status, "deployment_permission": "blocked",
            "review_sha256": digest}


def record_deployment_approval(client, version: str, approver: str, reason: str) -> dict:
    """Serialize a separate approval with other Capital registry evidence writers."""
    from bike_mlops.operational_validation import _version_record_lock

    with _version_record_lock(CAPITAL_MODEL_NAME, version):
        return _record_deployment_approval_locked(client, version, approver, reason)


def _record_deployment_approval_locked(client, version: str, approver: str, reason: str) -> dict:
    """Record reasoned approval separately from review; write eligible permission last."""
    approver = approver.strip() if isinstance(approver, str) else ""
    reason = reason.strip() if isinstance(reason, str) else ""
    if not approver or len(approver) > 200 or not reason or len(reason) > 1000:
        raise ValueError("Deployment approval requires an approver name and a non-empty reason")
    _, tags = _validate_registered_version(client, version)
    if tags.get("operational_gate_status") != "passed":
        raise ValueError("A passed operational review is required before deployment approval")
    if tags.get("deployment_permission") not in {"blocked", "eligible"}:
        raise ValueError("Deployment approval requires blocked or already approved permission state")
    if (tags.get("capital_operational_measurement_status") != "passed"
            or float(tags["capital_operational_p95_ms"]) > 250.0):
        raise ValueError("Deployment approval requires the fixed operational measurement gate to pass")
    if tags.get("capital_operational_review_status") != "passed":
        raise ValueError("A recorded passing operational review is required")
    review_hash = _sha_tag(tags, "capital_operational_review_sha256")
    review_payload = _review_payload(
        model_version=version, tags=tags,
        reviewer=_required(tags, "capital_operational_review_reviewer"),
        decision=_required(tags, "capital_operational_review_decision"),
        reason=_required(tags, "capital_operational_review_reason"),
    )
    if (_canonical_sha256(review_payload) != review_hash
            or tags.get("capital_operational_review_decision") != "passed"
            or tags.get("capital_operational_review_report_sha256") != tags["capital_operational_report_sha256"]
            or tags.get("capital_operational_review_candidate_id") != tags["candidate_id"]
            or tags.get("capital_operational_review_model_version") != version
            or tags.get("capital_operational_review_artifact_sha256") != tags["artifact_sha256"]):
        raise ValueError("Operational review hash or exact candidate/version/artifact binding is invalid")
    if approver.casefold() == tags["capital_operational_review_reviewer"].casefold():
        raise ValueError("The deployment approver must differ from the operational reviewer")
    _deployment_decisions_verified_registered_evidence(client, version, tags)
    tags = dict(tags)
    tags["capital_deployment_approval_at_utc"] = _decision_time(tags, "capital_deployment_approval_at_utc")
    if _deployment_decisions_utc_time(tags["capital_deployment_approval_at_utc"]) < _deployment_decisions_utc_time(tags["capital_operational_review_at_utc"]):
        raise ValueError("Approval cannot predate its review")
    payload = _approval_payload(model_version=version, tags=tags, approver=approver, reason=reason)
    digest = _canonical_sha256(payload)
    tags_to_write = {
        "capital_deployment_approval_at_utc": tags["capital_deployment_approval_at_utc"],
        "capital_deployment_approval_status": "approved",
        "capital_deployment_approver": approver,
        "capital_deployment_approval_reason": reason,
        "capital_deployment_approval_review_sha256": review_hash,
        "capital_deployment_approval_report_sha256": tags["capital_operational_report_sha256"],
        "capital_deployment_approval_candidate_id": tags["candidate_id"],
        "capital_deployment_approval_model_version": version,
        "capital_deployment_approval_artifact_sha256": tags["artifact_sha256"],
        "capital_deployment_approval_sha256": digest,
    }
    _write_tags_immutably(client, version, tags_to_write, tags)
    # This is the only state transition to eligible and is intentionally last.
    client.set_model_version_tag(CAPITAL_MODEL_NAME, version, "deployment_permission", "eligible")
    verified = client.get_model_version(CAPITAL_MODEL_NAME, int(version))
    if (verified.tags.get("deployment_permission") != "eligible"
            or verified.tags.get("operational_gate_status") != "passed"
            or verified.tags.get("capital_deployment_approval_sha256") != digest):
        raise RuntimeError("Deployment approval did not persist with all evidence bindings")
    validate_capital_decision_tags(verified.tags, model_name=CAPITAL_MODEL_NAME, model_version=version)
    return {"status": "deployment_approved", "model_version": version,
            "operational_gate_status": "passed", "deployment_permission": "eligible",
            "approval_sha256": digest}


def validate_capital_decision_tags(tags: dict, *, model_name: str | None = None,
                                   model_version: str | None = None) -> None:
    """Validate review/approval records before a Capital model is loaded or promoted."""
    _deployment_decisions_binding(tags)
    _required(tags, "capital_deployment_decision_schema_version", "2")
    if model_name != CAPITAL_MODEL_NAME:
        raise ValueError("Capital decision is bound to a different registered-model name")
    version = _required(tags, "capital_operational_review_model_version")
    if version != str(model_version):
        raise ValueError("Capital decision is bound to a different model version")
    if not version.isdecimal() or int(version) < 1:
        raise ValueError("Capital decision model version is invalid")
    for key in ("candidate_id", "artifact_sha256", "candidate_model_sha256", "candidate_report_sha256",
                "final_report_sha256", "evaluation_protocol_sha256", "data_sha256",
                "capital_operational_report_sha256", "capital_operational_plan_sha256",
                "capital_operational_plan_file_sha256", "capital_operational_request_id_sha256"):
        _sha_tag(tags, key)
    _required(tags, "capital_operational_evidence_status", "recorded")
    _required(tags, "capital_operational_measurement_status", "passed")
    _required(tags, "capital_operational_numeric_recheck_status", "matched")
    _required(tags, "capital_operational_input_provenance_status", "passed")
    _required(tags, "capital_operational_audit_status", "passed")
    _required(tags, "capital_operational_resource_context_status", "captured")
    _required(tags, "capital_operational_attempt_id")
    reviewer = _required(tags, "capital_operational_review_reviewer")
    review_reason = _required(tags, "capital_operational_review_reason")
    review_decision = _required(tags, "capital_operational_review_decision")
    review_status = _required(tags, "capital_operational_review_status")
    if (review_status != "passed" or review_decision != "passed"
            or tags.get("operational_gate_status") != "passed"):
        raise ValueError("Capital operational review has not passed")
    _deployment_decisions_utc_time(_required(tags, "capital_operational_review_at_utc"))
    review_payload = _review_payload(model_version=version, tags=tags, reviewer=reviewer,
                                     decision=review_decision, reason=review_reason)
    review_hash = _sha_tag(tags, "capital_operational_review_sha256")
    if _canonical_sha256(review_payload) != review_hash:
        raise ValueError("Capital operational review record hash does not match its fields")
    for key, expected in {
        "capital_operational_review_report_sha256": tags["capital_operational_report_sha256"],
        "capital_operational_review_candidate_id": tags["candidate_id"],
        "capital_operational_review_model_version": version,
        "capital_operational_review_artifact_sha256": tags["artifact_sha256"],
    }.items():
        _required(tags, key, expected)
    if tags.get("capital_operational_measurement_status") != "passed":
        raise ValueError("Capital operational measurement did not pass")
    try:
        p95 = float(_required(tags, "capital_operational_p95_ms"))
    except ValueError as error:
        raise ValueError("Capital operational p95 is invalid") from error
    if not _deployment_decisions_math.isfinite(p95) or p95 < 0 or p95 > 250.0:
        raise ValueError("Capital operational p95 exceeds the fixed 250 ms limit")
    approver = _required(tags, "capital_deployment_approver")
    approval_reason = _required(tags, "capital_deployment_approval_reason")
    if approver.casefold() == reviewer.casefold():
        raise ValueError("Capital deployment approver must differ from operational reviewer")
    if (_required(tags, "capital_deployment_approval_status") != "approved"
            or _required(tags, "deployment_permission") != "eligible"
            or _required(tags, "operational_gate_status") != "passed"):
        raise ValueError("Capital deployment approval is incomplete")
    if _deployment_decisions_utc_time(_required(tags, "capital_deployment_approval_at_utc")) < _deployment_decisions_utc_time(tags["capital_operational_review_at_utc"]):
        raise ValueError("Approval cannot predate its review")
    approval_payload = _approval_payload(model_version=version, tags=tags,
                                         approver=approver, reason=approval_reason)
    approval_hash = _sha_tag(tags, "capital_deployment_approval_sha256")
    if _canonical_sha256(approval_payload) != approval_hash:
        raise ValueError("Capital deployment approval record hash does not match its fields")
    for key, expected in {
        "capital_deployment_approval_review_sha256": review_hash,
        "capital_deployment_approval_report_sha256": tags["capital_operational_report_sha256"],
        "capital_deployment_approval_candidate_id": tags["candidate_id"],
        "capital_deployment_approval_model_version": version,
        "capital_deployment_approval_artifact_sha256": tags["artifact_sha256"],
    }.items():
        _required(tags, key, expected)


# ========================================================================
# capital_deployment_evidence_publisher
# ========================================================================

"""Publish complete content-addressed evidence for a blocked Capital version."""


import os
import re as _deployment_evidence_publisher_re
import tempfile as _deployment_evidence_publisher_tempfile
from pathlib import Path as _deployment_evidence_publisher_Path

_deployment_evidence_publisher_CASES = CASES
_deployment_evidence_publisher_MODEL = MODEL
_deployment_evidence_publisher_READINESS_POLICY = READINESS_POLICY
_deployment_evidence_publisher_binding = binding
_deployment_evidence_publisher_canonical_bytes = canonical_bytes
_deployment_evidence_publisher_document = document
_deployment_evidence_publisher_evidence_identity = evidence_identity
_deployment_evidence_publisher_reference_tags = reference_tags
_deployment_evidence_publisher_validate_bundle = validate_bundle
_deployment_evidence_publisher_verified_registered_evidence = verified_registered_evidence


def publish_deployment_evidence(candidate_id: str, attempt_id: str, final_path: _deployment_evidence_publisher_Path,
                                prerequisite_paths: dict[str, _deployment_evidence_publisher_Path]) -> dict:
    from bike_mlops import operational as ledger
    from bike_mlops import operational_validation as recovery
    from bike_mlops.operational import local_registry_url
    from bike_mlops.operational_validation import _version_record_lock
    from bike_mlops.staging import verify_candidate_for_staging
    from bike_mlops.evaluation import sha256_file
    from bike_mlops.config import DATA_PROFILE_ID, PROJECT_ROOT

    if DATA_PROFILE_ID != "capital-bikeshare" or not _deployment_evidence_publisher_re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("Deployment evidence publishing requires a Capital candidate ID")
    if not recovery._canonical_attempt(attempt_id) or set(prerequisite_paths) != set(_deployment_evidence_publisher_CASES):
        raise ValueError("A completed attempt and all prerequisite report paths are required")
    with recovery._existing_lock(ledger.DEFAULT_LOCK_DIR / f"{candidate_id}.lock"):
        with recovery._existing_lock(ledger.DEFAULT_LEDGER.with_suffix(ledger.DEFAULT_LEDGER.suffix + ".lock")):
            state = ledger.read_operational_ledger(ledger.DEFAULT_LEDGER)
            record = next((r for r in state["records"] if r["candidate_id"] == candidate_id), None)
            attempt = next((a for a in record["attempts"] if a["attempt_id"] == attempt_id), None) if record else None
            if attempt is None or attempt.get("status") != "completed":
                raise ValueError("Deployment evidence needs a completed ledger-linked operational attempt")
            plan, status = recovery._plan_check(attempt, candidate_id)
            if plan is None or status != "matched":
                raise ValueError("The ledger-linked operational plan differs")
            expected_report = f"data/reports/capital-bikeshare/operational-{attempt_id}.json"
            descriptor = attempt.get("report")
            if not isinstance(descriptor, dict) or descriptor.get("path") != expected_report:
                raise ValueError("Operational report is not the canonical completed ledger report")
            report_path = PROJECT_ROOT / expected_report
            report, report_hash, issue = recovery._read_report(report_path)
            if issue or report_hash != descriptor.get("sha256"):
                raise ValueError("Ledger-linked operational report differs")
            if recovery._report_error(report, attempt, candidate_id, plan, linked=True):
                raise ValueError("Operational report does not match its attempt")
            recovery.verify_completed_journal(attempt, candidate_id, plan, report)
            selection_path = PROJECT_ROOT / "data/candidates" / candidate_id / "candidate-report.json"
            verified = verify_candidate_for_staging(selection_path, final_path)
            final_path = verified["final_report_path"]
            uri = local_registry_url(os.getenv("MLFLOW_TRACKING_URI", ""))
            if uri != plan["registry_uri"] or os.getenv("MLFLOW_REGISTRY_URI", uri).rstrip("/") != uri:
                raise ValueError("Tracking/registry URI must match the frozen Capital plan")
            from mlflow.tracking import MlflowClient

            version = str(plan["identity"]["model_version"])
            with _version_record_lock(_deployment_evidence_publisher_MODEL, version):
                client = MlflowClient(tracking_uri=uri, registry_uri=uri)
                registered = client.get_model_version(_deployment_evidence_publisher_MODEL, version)
                if (registered.name != _deployment_evidence_publisher_MODEL or str(registered.version) != version or registered.status != "READY"
                        or registered.tags.get("deployment_permission") != "blocked"):
                    raise ValueError("Evidence may only be attached to the exact READY blocked version")
                tags = registered.tags
                identity = _deployment_evidence_publisher_evidence_identity(tags, version, registered.run_id)
                paths = {
                    "candidate": selection_path, "final": final_path,
                    "protocol": selection_path.parent / "evaluation-protocol.json",
                    "validation_rows": selection_path.parent / "validation-predictions.csv",
                    "final_rows": PROJECT_ROOT / verified["final"]["evaluation_window"]["row_level_predictions"],
                    "plan": PROJECT_ROOT / attempt["plan_path"], "operational": report_path,
                    **prerequisite_paths,
                }
                bodies, resolved_paths = {}, {}
                for name, path in paths.items():
                    path = _deployment_evidence_publisher_Path(path)
                    resolved = path.resolve(strict=True)
                    if (path.is_symlink() or not resolved.is_file()
                            or not resolved.is_relative_to((PROJECT_ROOT / "data").resolve())):
                        raise ValueError("Deployment evidence inputs must be regular project data files")
                    bodies[name] = _deployment_evidence_publisher_document(resolved.read_bytes())
                    resolved_paths[name] = resolved
                bundle = {"schema_version": 1, "kind": "capital-deployment-evidence",
                          "policy": _deployment_evidence_publisher_READINESS_POLICY, "identity": identity, "reports": bodies}
                _deployment_evidence_publisher_validate_bundle(bundle, tags, version, registered.run_id)
                for name, path in resolved_paths.items():
                    if sha256_file(path) != bodies[name]["sha256"]:
                        raise ValueError("Deployment evidence file changed during preparation")
                body = _deployment_evidence_publisher_canonical_bytes(bundle)
                refs = _deployment_evidence_publisher_reference_tags(body, registered.run_id)
                current = client.get_model_version(_deployment_evidence_publisher_MODEL, version)
                if (_deployment_evidence_publisher_binding(current.tags) != _deployment_evidence_publisher_binding(tags) or current.status != "READY"
                        or current.run_id != registered.run_id or current.tags.get("deployment_permission") != "blocked"):
                    raise ValueError("Registry evidence changed before publication")
                if any(k in current.tags and current.tags[k] != v for k, v in refs.items()):
                    raise ValueError("Refusing to overwrite a different deployment evidence bundle")
                with _deployment_evidence_publisher_tempfile.TemporaryDirectory(prefix="capital-evidence-publish-") as directory:
                    path = _deployment_evidence_publisher_Path(directory) / "bundle.json"
                    path.write_bytes(body)
                    client.log_artifact(registered.run_id, str(path),
                                        artifact_path=refs["capital_deployment_evidence_path"].rsplit("/", 1)[0])
                for key, value in refs.items():
                    if current.tags.get(key) != value:
                        client.set_model_version_tag(_deployment_evidence_publisher_MODEL, version, key, value)
                after = client.get_model_version(_deployment_evidence_publisher_MODEL, version)
                _deployment_evidence_publisher_verified_registered_evidence(client, version, after.tags)
                if (after.tags.get("deployment_permission") != "blocked"
                        or after.tags.get("operational_gate_status") != tags.get("operational_gate_status")):
                    raise RuntimeError("Evidence publication unexpectedly changed a deployment gate")
                return {"status": "deployment_evidence_recorded", "model_version": version,
                        "bundle_sha256": refs["capital_deployment_evidence_sha256"],
                        "deployment_permission": "blocked"}


# ========================================================================
# deployment_gates
# ========================================================================

"""Profile-specific checks that must pass before a registered model can serve traffic."""



from bike_mlops.evaluation import BOOTSTRAP_QUALITY_GATE_POLICY as _deployment_gates_BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256 as _deployment_gates_BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
_deployment_gates_validate_capital_decision_tags = validate_capital_decision_tags
_deployment_gates_verified_registered_evidence = verified_registered_evidence


def require_deployment_eligible(profile_id: str, tags: object, *, model_name: str | None = None,
                                model_version: str | None = None, client=None) -> None:
    """Fail closed for Capital unless every accuracy/operations gate is explicitly passed."""
    if profile_id != "capital-bikeshare":
        return
    if not model_name or not model_version:
        raise ValueError("Capital deployment requires the exact registered model name and version")
    if not isinstance(tags, dict):
        raise ValueError("Capital deployment requires registered-model gate tags")
    required = {
        "quality_gate": "passed",
        "validation_gate_status": "passed",
        "final_accuracy_gate_status": "passed",
        "operational_gate_status": "passed",
        "deployment_permission": "eligible",
        "quality_gate_policy_id": _deployment_gates_BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
        "quality_gate_policy_sha256": _deployment_gates_BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
    }
    missing_or_wrong = [key for key, expected in required.items() if tags.get(key) != expected]
    if missing_or_wrong:
        raise ValueError(
            "Capital model is not eligible for deployment; missing or non-passed gate tags: "
            + ", ".join(missing_or_wrong)
        )
    try:
        _deployment_gates_validate_capital_decision_tags(tags, model_name=model_name, model_version=model_version)
        _deployment_gates_verified_registered_evidence(client, str(model_version), tags)
    except ValueError as error:
        raise ValueError(f"Capital model is not eligible for deployment; decision evidence is invalid: {error}") from error


# ========================================================================
# deployment_metadata
# ========================================================================

"""Read only binding between a pinned API process and one deployment operation.

Names/reasons are operator declarations, never authenticated approval evidence.
No lookup by version, timestamp, or latest row is permitted.
"""


from contextlib import contextmanager
import json as _deployment_metadata_json
from pathlib import Path as _deployment_metadata_Path
import re as _deployment_metadata_re
import sqlite3

_deployment_metadata_strict_json = strict_json


@contextmanager
def read_connection(url):
    connection = None
    try:
        if url.startswith('sqlite:///'):
            raw = url[len('sqlite:///'):]
            if not raw or raw == ':memory:' or '?' in raw or '#' in raw:
                raise ValueError('Unsupported database URL')
            connection = sqlite3.connect(_deployment_metadata_Path(raw).resolve().as_uri() + '?mode=ro', uri=True,
                                         timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            placeholder = '?'
        elif url.startswith(('postgresql://', 'postgres://')):
            import psycopg
            from psycopg.rows import dict_row
            connection = psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5,
                options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000')
            connection.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            placeholder = '%s'
        else:
            raise ValueError('Unsupported database URL')
        yield connection, placeholder
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()


def operation_identity(row, artifact_hash):
    return dict(deployment_id=row['deployment_id'], slot=row['to_slot'],
                model_version=str(row['to_version']), artifact_sha256=artifact_hash,
                data_profile_id=row['data_profile_id'], target_contract_id=row['target_contract_id'],
                declared_approver=row['approver'], declared_reason=row['reason'])


def inspect_api_operation(url, operation_id, slot, profile, target, model_name, version, artifact_hash):
    base = dict(status='unavailable', approval_evidence=False)
    if not operation_id:
        return dict(base, status='legacy_unverifiable')
    if not _deployment_metadata_re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', operation_id) or slot not in {'blue', 'green'}:
        return dict(base, status='invalid_operation_binding')
    try:
        with read_connection(url) as (connection, placeholder):
            metadata = connection.execute('SELECT data_profile_id,target_contract_id FROM data_profile_metadata WHERE singleton=1').fetchone()
            if metadata is None or metadata['data_profile_id'] != profile or metadata['target_contract_id'] != target:
                return dict(base, status='database_profile_mismatch')
            row = connection.execute('SELECT * FROM deployments WHERE deployment_id=' + placeholder,
                                     (operation_id,)).fetchone()
            if row is None:
                return dict(base, status='operation_missing')
            row = dict(row)
            if row['data_profile_id'] != profile or row['target_contract_id'] != target:
                return dict(base, status='ledger_profile_mismatch')
            if row['to_slot'] != slot or str(row['to_version']) != str(version):
                return dict(base, status='runtime_identity_mismatch')
            context = _deployment_metadata_strict_json(row['probe_json'] or '{}').get('recovery_context', {})
            identity = context.get('candidate_identity', {})
            expected = dict(model_name=model_name, model_version=str(version), artifact_sha256=artifact_hash,
                            data_profile_id=profile, target_contract_id=target)
            if not artifact_hash or any(identity.get(k) != v for k, v in expected.items()):
                return dict(base, status='artifact_identity_mismatch')
            # Declarations may be displayed, but never echo URI/secret-shaped input.
            declaration = row['approver'] + ' ' + row['reason']
            if _deployment_metadata_re.search(r'(?i)(?:[a-z][a-z0-9+.-]*://|(?:password|token|secret|api[_-]?key|authorization)\s*[:=])', declaration):
                return dict(base, status='unsafe_declaration')
            binding = operation_identity(row, artifact_hash)
            if context.get('schema_version') != 3 or identity.get('deployment_operation') != binding:
                return dict(base, status='operation_metadata_mismatch')
            if row['status'] not in {'in_progress', 'completed'}:
                return dict(base, status='operation_not_active')
            return dict(base, status='matched_completed' if row['status'] == 'completed' else 'matched_pending',
                        operation=binding, ledger_phase=row['phase'], ledger_status=row['status'])
    except Exception as error:
        return dict(base, error_type=type(error).__name__)


# ========================================================================
# deployment_inspection
# ========================================================================

"""Read-only state/ledger diagnosis. It cannot approve, migrate or repair state."""


import os as _deployment_inspection_os
from pathlib import Path as _deployment_inspection_Path
import sqlite3 as _deployment_inspection_sqlite3
import stat

_deployment_inspection_strict_json = strict_json


def compare_state(state, row, pending, profile, target):
    out = dict(status="unconfirmed", exit_code=2, automatic_repair=False,
               deployment_id=state.get("last_deployment_id"), pending_count=len(pending),
               differences=[], approval_evidence=False)
    if (state.get("data_profile_id", "uci-bike-sharing") != profile
            or state.get("target_contract_id", "uci-bike-sharing-cnt-dataset-local-hour") != target):
        return dict(out, status="state_profile_mismatch", exit_code=1)
    if pending:
        return dict(out, status="incomplete_deployment_requires_reconciliation")
    if row is None:
        return dict(out, status="legacy_unverifiable")
    expected = dict(active_version=str(row["to_version"]), active_slot=row["to_slot"],
                    approver=row["approver"], reason=row["reason"], last_deployment_id=row["deployment_id"])
    differences = [key for key, value in expected.items() if state.get(key) != value]
    if state.get("deployment_operation") is not None:
        try:
            import json
            prepared = json.loads(row["probe_json"] or "{}")["recovery_context"]["candidate_identity"]["deployment_operation"]
            from bike_mlops.deployment import operation_identity
            if state["deployment_operation"] != prepared or prepared != operation_identity(row, prepared["artifact_sha256"]):
                differences.append("deployment_operation")
        except (KeyError, TypeError, ValueError):
            differences.append("deployment_operation")
    if row["data_profile_id"] != profile or row["target_contract_id"] != target:
        differences.append("ledger_profile")
    if row["status"] != "completed":
        differences.append("ledger_status")
    return dict(out, status="metadata_mismatch" if differences else "matched",
                exit_code=1 if differences else 0, differences=differences,
                approval_evidence=False, limitation="Metadata consistency alone is not a deployment approval.")


def _read_state_file(path):
    fd = _deployment_inspection_os.open(path, _deployment_inspection_os.O_RDONLY | _deployment_inspection_os.O_NOFOLLOW | _deployment_inspection_os.O_NONBLOCK)
    with _deployment_inspection_os.fdopen(fd, "rb") as stream:
        before = _deployment_inspection_os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 65536:
            raise ValueError("Unsupported state file")
        body = stream.read(65537)
        after = _deployment_inspection_os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(before) != identity(after) or identity(path.lstat()) != identity(before) or len(body) != before.st_size:
        raise ValueError("State changed during read")
    return body, identity(before)


def inspect_state(path: _deployment_inspection_Path, url: str, profile: str, target: str):
    connection = None
    base = dict(status="unavailable", exit_code=2, automatic_repair=False, approval_evidence=False)
    try:
        body, identity = _read_state_file(path)
        state = _deployment_inspection_strict_json(body.decode())
        if url.startswith("sqlite:///"):
            raw = url[len("sqlite:///"):]
            if not raw or raw == ":memory:" or "?" in raw or "#" in raw:
                raise ValueError("Unsupported database URL")
            connection = _deployment_inspection_sqlite3.connect(_deployment_inspection_Path(raw).resolve().as_uri() + "?mode=ro", uri=True,
                                         timeout=5, isolation_level=None)
            connection.row_factory = _deployment_inspection_sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            placeholder = "?"
        elif url.startswith(("postgresql://", "postgres://")):
            import psycopg
            from psycopg.rows import dict_row
            connection = psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5,
                options="-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000")
            connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            placeholder = "%s"
        else:
            raise ValueError("Unsupported database URL")
        # Profile is checked before any SELECT of deployment rows.
        metadata = connection.execute("SELECT data_profile_id,target_contract_id FROM data_profile_metadata WHERE singleton=1").fetchone()
        if metadata is None or metadata["data_profile_id"] != profile or metadata["target_contract_id"] != target:
            return dict(base, status="database_profile_mismatch", exit_code=1)
        pending = connection.execute("SELECT deployment_id FROM deployments WHERE status='in_progress' LIMIT 2").fetchall()
        row = connection.execute("SELECT * FROM deployments WHERE deployment_id=" + placeholder,
                                 (state.get("last_deployment_id"),)).fetchone()
        result = compare_state(state, dict(row) if row else None, pending, profile, target)
        # Detect atomic replacement, same-file rewrite, and same-size rewrite.
        if _read_state_file(path) != (body, identity):
            return dict(base, status="state_changed_during_inspection")
        return result
    except Exception as error:
        # Never include URL, SQL, state fields or native error strings.
        return dict(base, error_type=type(error).__name__)
    finally:
        if connection is not None:
            try:
                connection.rollback()
                connection.close()
            except Exception as error:
                try:
                    connection.close()
                except Exception:
                    pass
                return dict(base, error_type=type(error).__name__)
