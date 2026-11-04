"""Review one archived 30-minute phase; never establish whole-clock qualification.

Only integer timestamps and exact rational arithmetic are used. This module does
not sample clocks, load settings, or connect to operating-system services.
"""
from __future__ import annotations

import copy
from fractions import Fraction
import hashlib
import json

POLICY = {
    "policy_id": "clock-rate-phase-component-v1",
    "phase_seconds": 1800,
    "interval_seconds": 60,
    "maximum_sample_gap_seconds": 2,
    "minimum_ratio": [999, 1000],
    "maximum_ratio": [1001, 1000],
    "whole_clock_qualification": "not_evaluated",
}
POLICY_SHA256 = hashlib.sha256(
    json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
_CLOCKS = ("monotonic", "raw")
_SAMPLE_FIELDS = {"index", "qpc_tick"} | {
    f"{clock}_{edge}_ns" for clock in _CLOCKS for edge in ("before", "after")
}


def _integer(value, name: str, *, minimum=0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _ratio_json(value: Fraction) -> dict:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _interval_status(lower: Fraction, upper: Fraction) -> str:
    minimum = Fraction(*POLICY["minimum_ratio"])
    maximum = Fraction(*POLICY["maximum_ratio"])
    if upper < minimum or lower > maximum:
        return "failed"
    if minimum <= lower <= upper <= maximum:
        return "passed"
    return "incomplete"


def review_clock_rate_phase(value: object) -> dict:
    """Check the elapsed-rate component of one phase from its full sample list.

    The caller must retain the input bytes and their hash. Source authenticity,
    acquisition guarantees, QPC calibration, UTC, resume coverage, and operating
    system identity are outside this component and remain unevaluated.
    """
    fields = {"schema_version", "kind", "phase", "qpc_frequency_hz", "samples"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Input must contain exactly the clock-rate phase schema fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported clock-rate phase schema version")
    if value["kind"] != "clock-rate-phase-v1" or not isinstance(value["phase"], str) or value["phase"] not in {"idle", "fixture-load"}:
        raise ValueError("Unsupported clock-rate phase kind or phase")
    frequency = _integer(value["qpc_frequency_hz"], "qpc_frequency_hz", minimum=1)
    samples = value["samples"]
    if not isinstance(samples, list) or not 2 <= len(samples) <= 10000:
        raise ValueError("samples must contain 2..10000 records")
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict) or set(sample) != _SAMPLE_FIELDS:
            raise ValueError("Each sample must contain exactly the required timestamp fields")
        for name in _SAMPLE_FIELDS:
            _integer(sample[name], name)
        if sample["index"] != index:
            raise ValueError("Sample indexes must be contiguous and start at zero")

    issues = []
    intervals = []

    def issue(code, status, **detail):
        issues.append({"code": code, "status": status, **detail})

    qpc_ordered = True
    for previous, sample in zip(samples, samples[1:]):
        delta = sample["qpc_tick"] - previous["qpc_tick"]
        if delta <= 0:
            qpc_ordered = False
            issue("qpc_not_strictly_increasing", "failed", index=sample["index"])
        elif delta > POLICY["maximum_sample_gap_seconds"] * frequency:
            issue("sample_gap", "incomplete", index=sample["index"])
    for clock in _CLOCKS:
        for index, sample in enumerate(samples):
            before, after = sample[f"{clock}_before_ns"], sample[f"{clock}_after_ns"]
            if before > after:
                issue("reversed_bracket", "failed", clock=clock, index=index)
            if index and before <= samples[index - 1][f"{clock}_after_ns"]:
                issue("clock_order_or_bracket_overlap", "failed", clock=clock, index=index)

    if qpc_ordered:
        origin = samples[0]["qpc_tick"]
        boundary_indexes = [0]
        cursor = 1
        for second in range(POLICY["interval_seconds"], POLICY["phase_seconds"] + 1, POLICY["interval_seconds"]):
            while cursor < len(samples) and samples[cursor]["qpc_tick"] - origin < second * frequency:
                cursor += 1
            if cursor == len(samples):
                issue("missing_phase_boundary", "incomplete", boundary_seconds=second)
                break
            boundary_indexes.append(cursor)
        if len(boundary_indexes) == POLICY["phase_seconds"] // POLICY["interval_seconds"] + 1:
            if boundary_indexes[-1] != len(samples) - 1:
                issue("samples_after_first_phase_endpoint", "incomplete")
        pairs = [("window", first, last) for first, last in zip(boundary_indexes, boundary_indexes[1:])]
        if len(boundary_indexes) > 1:
            # Full phase only exists when the fixed 1800-second boundary exists.
            if len(boundary_indexes) == POLICY["phase_seconds"] // POLICY["interval_seconds"] + 1:
                pairs.append(("phase", 0, boundary_indexes[-1]))
        for kind, first, last in pairs:
            if first == last:
                issue("unobserved_window", "incomplete", index=first)
                continue
            left, right = samples[first], samples[last]
            elapsed = Fraction(right["qpc_tick"] - left["qpc_tick"], frequency)
            for clock in _CLOCKS:
                b0, a0 = left[f"{clock}_before_ns"], left[f"{clock}_after_ns"]
                b1, a1 = right[f"{clock}_before_ns"], right[f"{clock}_after_ns"]
                if not b0 <= a0 < b1 <= a1:
                    continue  # Already failed above; never manufacture a valid interval.
                lower = Fraction(b1 - a0, 10**9) / elapsed
                upper = Fraction(a1 - b0, 10**9) / elapsed
                status = _interval_status(lower, upper)
                intervals.append({"kind": kind, "clock": clock, "first_index": first,
                                  "last_index": last, "lower": _ratio_json(lower),
                                  "upper": _ratio_json(upper), "status": status})
                if status != "passed":
                    issue("rate_outside_bounds" if status == "failed" else "rate_uncertainty_crosses_bounds",
                          status, clock=clock, first_index=first, last_index=last)

    status = "failed" if any(item["status"] == "failed" for item in issues) else (
        "incomplete" if issues else "passed"
    )
    return {"schema_version": 1, "scope": "archived_elapsed_rate_phase_component_only",
            "policy": copy.deepcopy(POLICY), "policy_sha256": POLICY_SHA256, "phase": value["phase"],
            "samples": len(samples), "rate_component_status": status,
            "clock_qualification_status": "not_evaluated", "issues": issues, "intervals": intervals,
            "unevaluated": ["source_authenticity_and_bracket_acquisition", "qpc_calibration",
                            "external_utc", "future_rejection", "boot_process_settings_identity",
                            "both_phases_and_resume_campaign", "maintenance_and_human_review"]}
