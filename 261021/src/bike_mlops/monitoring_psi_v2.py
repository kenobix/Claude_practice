"""Versioned PSI reference bins with explicit constant-reference handling.

This module is not connected to the existing monitor or historical UCI reports.
A reference SHA detects accidental changes; it is not proof of authenticity.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np

PSI_ALGORITHM_V2 = "psi-v2-constant-bins"
_SMOOTHING = 1e-6


def _sha(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "reference_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _numbers(values) -> tuple[np.ndarray, dict[str, int]]:
    try:
        arr = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError("PSI inputs must be a one-dimensional sequence of numeric or missing values") from error
    if arr.ndim != 1:
        raise ValueError("PSI inputs must be one-dimensional")
    finite = arr[np.isfinite(arr)]
    return finite, {"total": int(arr.size), "finite": int(finite.size),
                    "missing": int(np.isnan(arr).sum()), "nonfinite": int(np.isinf(arr).sum())}


def _validate_options(version: str, feature_name: str, reference_id: str, bins: int, minimum_count: int) -> None:
    if version != PSI_ALGORITHM_V2:
        raise ValueError("Unsupported PSI algorithm version")
    if not isinstance(feature_name, str) or not feature_name.strip() or not isinstance(reference_id, str) or not reference_id.strip():
        raise ValueError("A feature name and reference ID are required")
    if type(bins) is not int or not 2 <= bins <= 100:
        raise ValueError("PSI bin count must be an integer between 2 and 100")
    if type(minimum_count) is not int or minimum_count < 2:
        raise ValueError("PSI minimum count must be an integer of at least two")


def make_psi_reference(values, *, version: str, feature_name: str, reference_id: str,
                       bins: int = 10, minimum_count: int = 2) -> dict[str, Any]:
    """Freeze the reference bins and counts; the caller owns source/protocol binding."""
    _validate_options(version, feature_name, reference_id, bins, minimum_count)
    finite, counts = _numbers(values)
    result = {"schema_version": 1, "algorithm": version, "feature_name": feature_name,
              "reference_id": reference_id, "requested_bins": bins, "minimum_count": minimum_count,
              "smoothing": _SMOOTHING, "input_counts": counts, "status": None,
              "mode": None, "constant_value": None, "edges": [], "reference_counts": []}
    if counts["nonfinite"]:
        result["status"] = "invalid_reference_values"
    elif not counts["finite"]:
        result["status"] = "no_reference"
    elif counts["finite"] < minimum_count:
        result["status"] = "insufficient_reference"
    else:
        boundaries = np.unique(np.quantile(finite, np.linspace(0, 1, bins + 1)))
        if not np.all(np.isfinite(boundaries)):
            raise ValueError("Reference quantiles produced non-finite boundaries")
        if len(boundaries) == 1:
            value = float(boundaries[0])
            result.update(status="ready", mode="constant_three_way", constant_value=value,
                          reference_counts=[0, int(finite.size), 0])
        else:
            if len(boundaries) == 2:
                lower, upper = float(boundaries[0]), float(boundaries[1])
                midpoint = lower * 0.5 + upper * 0.5
                # Adjacent representable floats can round the midpoint down to
                # the lower value; the upper value still separates them.
                edges = np.array([upper if midpoint <= lower else midpoint])
            else:
                edges = boundaries[1:-1]
            if not np.all(np.isfinite(edges)):
                raise ValueError("Reference boundaries must be finite")
            indices = np.searchsorted(edges, finite, side="right")
            result.update(status="ready", mode="quantile", edges=edges.tolist(),
                          reference_counts=np.bincount(indices, minlength=len(edges) + 1).tolist())
    result["reference_sha256"] = _sha(result)
    return result


def _validate_reference(reference: object, version: str) -> dict:
    if not isinstance(reference, dict) or type(reference.get("schema_version")) is not int or reference["schema_version"] != 1:
        raise ValueError("Unsupported PSI reference schema")
    _validate_options(version, reference.get("feature_name"), reference.get("reference_id"),
                      reference.get("requested_bins"), reference.get("minimum_count"))
    if reference.get("algorithm") != version or reference.get("smoothing") != _SMOOTHING:
        raise ValueError("PSI reference algorithm or smoothing differs from the requested version")
    try:
        digest = _sha(reference)
    except (TypeError, ValueError) as error:
        raise ValueError("PSI reference is not JSON-compatible") from error
    if reference.get("reference_sha256") != digest:
        raise ValueError("PSI reference SHA-256 mismatch")
    counts = reference.get("input_counts")
    if (not isinstance(counts, dict) or set(counts) != {"total", "finite", "missing", "nonfinite"}
            or any(type(value) is not int or value < 0 for value in counts.values())
            or counts["total"] != counts["finite"] + counts["missing"] + counts["nonfinite"]):
        raise ValueError("PSI reference input counts are invalid")
    status = reference.get("status")
    expected_status = ("invalid_reference_values" if counts["nonfinite"] else
                       "no_reference" if counts["finite"] == 0 else
                       "insufficient_reference" if counts["finite"] < reference["minimum_count"] else "ready")
    if status != expected_status:
        raise ValueError("PSI reference state disagrees with its counts")
    mode, edges, bin_counts = reference.get("mode"), reference.get("edges"), reference.get("reference_counts")
    if not isinstance(edges, list) or not isinstance(bin_counts, list):
        raise ValueError("PSI reference bins must be lists")
    if status != "ready":
        if mode is not None or edges or bin_counts or reference.get("constant_value") is not None:
            raise ValueError("Unavailable PSI reference must not contain bins")
        return reference
    if any(type(value) is not int or value < 0 for value in bin_counts) or sum(bin_counts) != counts["finite"]:
        raise ValueError("PSI reference bin counts are invalid")
    if mode == "constant_three_way":
        constant = reference.get("constant_value")
        if (type(constant) not in {int, float} or not math.isfinite(constant) or edges
                or bin_counts != [0, counts["finite"], 0]):
            raise ValueError("Constant PSI reference bins are invalid")
    elif mode == "quantile":
        if (reference.get("constant_value") is not None or not 1 <= len(edges) <= reference["requested_bins"] - 1
                or len(bin_counts) != len(edges) + 1
                or any(type(value) not in {int, float} or not math.isfinite(value) for value in edges)
                or any(left >= right for left, right in zip(edges, edges[1:]))):
            raise ValueError("Quantile PSI reference bins are invalid")
    else:
        raise ValueError("Unsupported PSI reference bin mode")
    return reference


def evaluate_psi_reference(reference: dict, values, *, version: str, minimum_count: int | None = None) -> dict[str, Any]:
    """Evaluate only with the supplied fixed bins; missing and invalid values stay visible."""
    ref = _validate_reference(reference, version)
    if minimum_count is None:
        minimum_count = ref["minimum_count"]
    if type(minimum_count) is not int or minimum_count < 2:
        raise ValueError("PSI minimum current count must be an integer of at least two")
    finite, current_counts = _numbers(values)
    result = {"algorithm": version, "reference_sha256": ref["reference_sha256"],
              "reference_status": ref["status"], "reference_input_counts": dict(ref["input_counts"]),
              "current_input_counts": current_counts, "minimum_current_count": minimum_count,
              "status": None, "psi": None, "current_bin_counts": []}
    if ref["status"] != "ready":
        result["status"] = ref["status"]
    elif current_counts["nonfinite"]:
        result["status"] = "invalid_current_values"
    elif not current_counts["finite"]:
        result["status"] = "no_current_data"
    elif current_counts["finite"] < minimum_count:
        result["status"] = "insufficient_current_data"
    else:
        if ref["mode"] == "constant_three_way":
            constant = ref["constant_value"]
            observed = np.array([(finite < constant).sum(), (finite == constant).sum(), (finite > constant).sum()], dtype=float)
        else:
            indices = np.searchsorted(ref["edges"], finite, side="right")
            observed = np.bincount(indices, minlength=len(ref["reference_counts"])).astype(float)
        expected = np.asarray(ref["reference_counts"], dtype=float)
        # Smooth proportions, rather than counts, so identical distributions
        # have PSI zero even when their sample sizes differ.
        expected_share = (expected / expected.sum() + _SMOOTHING) / (1.0 + _SMOOTHING * len(expected))
        observed_share = (observed / observed.sum() + _SMOOTHING) / (1.0 + _SMOOTHING * len(observed))
        psi = float(np.sum((observed_share - expected_share) * np.log(observed_share / expected_share)))
        result.update(status="ready", psi=psi, current_bin_counts=observed.astype(int).tolist())
    return result
