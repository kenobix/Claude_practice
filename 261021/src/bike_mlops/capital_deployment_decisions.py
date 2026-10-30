"""Record a reviewed operational gate and a separate reasoned approval.

Registry tags provide hash-linked evidence, not authenticated identities. The
reviewer and approver names are declarations in this local learning project.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone

from bike_mlops.capital_deployment_evidence import (
    REF_KEYS, binding, utc_time, verified_registered_evidence,
)

from bike_mlops.evaluation_protocol import (
    BOOTSTRAP_QUALITY_GATE_POLICY,
    BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
)
from bike_mlops.profiles import load_data_profile

CAPITAL_MODEL_NAME = "bike-demand-capital"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CAPITAL_PROFILE = load_data_profile("capital-bikeshare")


def _canonical_sha256(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    binding(tags)
    if tags.get("capital_operational_review_sha256") and tags.get("capital_deployment_decision_schema_version") != "2":
        raise ValueError("Legacy decisions cannot be upgraded in place")
    profile = _CAPITAL_PROFILE
    for key, expected in {
        "data_profile_id": profile["profile_id"],
        "target_contract_id": profile["target_contract_id"],
        "quality_gate": "passed",
        "validation_gate_status": "passed",
        "final_accuracy_gate_status": "passed",
        "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
        "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
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
    if not math.isfinite(p95) or p95 < 0:
        raise ValueError("Recorded operational p95 must be finite and non-negative")
    return registered, tags


def _snapshot(tags: dict, *, require_reference: bool = True) -> dict:
    result = binding(tags)
    for key in REF_KEYS:
        result[key] = _required(tags, key) if require_reference else tags.get(key)
    return result


def _decision_time(tags: dict, key: str) -> str:
    at = tags.get(key, datetime.now(timezone.utc).isoformat())
    utc_time(at)
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
    if (binding(current.tags) != binding(expected)
            or any(current.tags.get(key) != expected.get(key) for key in REF_KEYS)):
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
    from bike_mlops.capital_operational_registry import _version_record_lock

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
        verified_registered_evidence(client, version, tags)
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
    from bike_mlops.capital_operational_registry import _version_record_lock

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
    verified_registered_evidence(client, version, tags)
    tags = dict(tags)
    tags["capital_deployment_approval_at_utc"] = _decision_time(tags, "capital_deployment_approval_at_utc")
    if utc_time(tags["capital_deployment_approval_at_utc"]) < utc_time(tags["capital_operational_review_at_utc"]):
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
    binding(tags)
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
    utc_time(_required(tags, "capital_operational_review_at_utc"))
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
    if not math.isfinite(p95) or p95 < 0 or p95 > 250.0:
        raise ValueError("Capital operational p95 exceeds the fixed 250 ms limit")
    approver = _required(tags, "capital_deployment_approver")
    approval_reason = _required(tags, "capital_deployment_approval_reason")
    if approver.casefold() == reviewer.casefold():
        raise ValueError("Capital deployment approver must differ from operational reviewer")
    if (_required(tags, "capital_deployment_approval_status") != "approved"
            or _required(tags, "deployment_permission") != "eligible"
            or _required(tags, "operational_gate_status") != "passed"):
        raise ValueError("Capital deployment approval is incomplete")
    if utc_time(_required(tags, "capital_deployment_approval_at_utc")) < utc_time(tags["capital_operational_review_at_utc"]):
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
