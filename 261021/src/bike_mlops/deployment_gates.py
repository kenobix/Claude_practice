"""Profile-specific checks that must pass before a registered model can serve traffic."""

from __future__ import annotations

from bike_mlops.evaluation_protocol import (
    BOOTSTRAP_QUALITY_GATE_POLICY,
    BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
)


def require_deployment_eligible(profile_id: str, tags: object) -> None:
    """Fail closed for Capital unless every accuracy/operations gate is explicitly passed."""
    if profile_id != "capital-bikeshare":
        return
    if not isinstance(tags, dict):
        raise ValueError("Capital deployment requires registered-model gate tags")
    required = {
        "quality_gate": "passed",
        "validation_gate_status": "passed",
        "final_accuracy_gate_status": "passed",
        "operational_gate_status": "passed",
        "deployment_permission": "eligible",
        "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
        "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
    }
    missing_or_wrong = [key for key, expected in required.items() if tags.get(key) != expected]
    if missing_or_wrong:
        raise ValueError(
            "Capital model is not eligible for deployment; missing or non-passed gate tags: "
            + ", ".join(missing_or_wrong)
        )
