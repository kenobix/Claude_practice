"""Explicit, reportable quality-gate calculations for Capital bootstrap evaluations."""

from __future__ import annotations

import math
from numbers import Real

from bike_mlops.evaluation_protocol import (
    BOOTSTRAP_QUALITY_GATE_POLICY,
    BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
)


def _read_metrics(name: str, metrics: object) -> tuple[dict[str, float] | None, list[str]]:
    if not isinstance(metrics, dict):
        return None, [f"{name} metrics are missing or are not an object"]
    values = {}
    errors = []
    for key in ("mae", "rmse"):
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, Real):
            errors.append(f"{name}.{key} is missing or is not numeric")
            continue
        number = float(value)
        if not math.isfinite(number) or number < 0:
            errors.append(f"{name}.{key} must be finite and non-negative")
            continue
        values[key] = number
    return (values if not errors else None), errors


def baseline_accuracy_gate(candidate: object, baseline: object, *, period: str) -> dict:
    """Apply the frozen strict-MAE / non-worsening-RMSE policy without coercing bad data."""
    candidate_values, candidate_errors = _read_metrics("candidate", candidate)
    baseline_values, baseline_errors = _read_metrics("historical_baseline", baseline)
    errors = [*candidate_errors, *baseline_errors]
    if errors:
        return {
            "period": period,
            "status": "not_evaluable",
            "passed": False,
            "policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
            "policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
            "candidate": candidate_values,
            "historical_baseline": baseline_values,
            "conditions": {},
            "reasons": errors,
        }

    assert candidate_values is not None and baseline_values is not None
    mae_better = candidate_values["mae"] < baseline_values["mae"]
    rmse_not_worse = candidate_values["rmse"] <= baseline_values["rmse"]
    conditions = {
        "candidate_mae_strictly_below_baseline": {
            "passed": mae_better,
            "candidate": candidate_values["mae"],
            "historical_baseline": baseline_values["mae"],
            "operator": "<",
        },
        "candidate_rmse_not_above_baseline": {
            "passed": rmse_not_worse,
            "candidate": candidate_values["rmse"],
            "historical_baseline": baseline_values["rmse"],
            "operator": "<=",
        },
    }
    passed = mae_better and rmse_not_worse
    reasons = []
    if not mae_better:
        reasons.append("candidate MAE must be strictly below historical baseline MAE")
    if not rmse_not_worse:
        reasons.append("candidate RMSE must not exceed historical baseline RMSE")
    return {
        "period": period,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
        "policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
        "candidate": candidate_values,
        "historical_baseline": baseline_values,
        "conditions": conditions,
        "reasons": reasons,
    }
