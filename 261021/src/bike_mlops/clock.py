"""Product components: clock."""
from __future__ import annotations


# ========================================================================
# clock_rate_review
# ========================================================================

"""Review one archived 30-minute phase; never establish whole-clock qualification.

Only integer timestamps and exact rational arithmetic are used. This module does
not sample clocks, load settings, or connect to operating-system services.
"""


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


# ========================================================================
# clock_campaign_review
# ========================================================================

"""Offline, untrusted campaign evidence inspection; never grant clock qualification."""


import copy as _clock_campaign_review_copy
from fractions import Fraction as _clock_campaign_review_Fraction
import hashlib as _clock_campaign_review_hashlib
import json as _clock_campaign_review_json
import re

RATE_POLICY_SHA256 = POLICY_SHA256
_clock_campaign_review_review_clock_rate_phase = review_clock_rate_phase


def canonical_sha256(value):
    return _clock_campaign_review_hashlib.sha256(_clock_campaign_review_json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


_clock_campaign_review_POLICY = {
    "id": "clock-campaign-offline-v1",
    "design_sha256": "cc077a3d963df7fd9e14e999d30c32e4f8a9e8093af7b54978ab68741be73a9e",
    "rate_policy_sha256": RATE_POLICY_SHA256,
    "campaigns": ["normal", "post-resume"],
    "phases": ["idle", "fixture-load"],
    "phase_seconds": 1800, "boundary_seconds": 60, "maximum_gap_seconds": 2,
    "maximum_utc_offset_ns": 10**9,
    "qpc_minimum_ratio": [9999, 10000], "qpc_maximum_ratio": [10001, 10000],
    "automatic_qualification": False,
}
_clock_campaign_review_POLICY_SHA256 = canonical_sha256(_clock_campaign_review_POLICY)
IDENTITY_FIELDS = {"host_boot_sha256", "guest_boot_sha256", "process_set_sha256",
                   "sync_configuration_sha256", "versions_sha256", "clocksource_sha256"}
BASE_FIELDS = {"index", "qpc_tick", "monotonic_before_ns", "monotonic_after_ns",
               "raw_before_ns", "raw_after_ns"}
EXTRA_FIELDS = {"windows_utc_before_ns", "windows_utc_after_ns", "realtime_before_ns",
                "realtime_after_ns", "boottime_before_ns", "boottime_after_ns", "identity_sha256"}
PREREQUISITES = ["acquisition_authenticity_and_bracket_protocol_review",
                 "independent_utc_and_calibration_source_review",
                 "synchronization_ownership_and_repair_review",
                 "maintenance_and_resume_authorization_review",
                 "collector_workload_and_raw_evidence_review",
                 "human_whole_campaign_acceptance"]


def _object(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Invalid campaign object schema")


def _clock_campaign_review_integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Invalid integer")
    return value


def _hash(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Invalid digest")
    return value


def _list(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("Invalid list")
    return value


def _identity(value):
    if value is not None:
        _object(value, IDENTITY_FIELDS)
        for item in value.values():
            _hash(item)


def _bounds(lower, upper, minimum, maximum):
    if lower > upper:
        return "failed"
    if upper < minimum or lower > maximum:
        return "failed"
    return "passed" if minimum <= lower <= upper <= maximum else "incomplete"


def review_clock_campaign(value, *, expected_plan_sha256):
    """Recompute all four phases. Caller supplies a separately retained plan hash.

    Digests establish content binding, not authenticity. The strongest whole-clock
    result here is incomplete; an independent acquisition/review gate is absent.
    """
    _hash(expected_plan_sha256)
    _object(value, {"schema_version", "kind", "policy", "policy_sha256", "plan", "plan_sha256",
                    "campaigns", "resume"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "clock-campaign-v1":
        raise ValueError("Unsupported campaign schema")
    plan = value["plan"]
    _object(plan, {"policy_sha256", "collector_sha256", "workload_sha256", "utc_reader_sha256",
                   "utc_source_sha256", "calibration_source_sha256", "qpc_frequency_hz"})
    for key in set(plan) - {"qpc_frequency_hz"}:
        _hash(plan[key])
    frequency = _clock_campaign_review_integer(plan["qpc_frequency_hz"], 1)
    for digest in (value["policy_sha256"], value["plan_sha256"]):
        _hash(digest)
    # Compare serialized representations as well as hashes: bool cannot stand in for integer.
    if (canonical_sha256(value["policy"]) != _clock_campaign_review_POLICY_SHA256 or value["policy_sha256"] != _clock_campaign_review_POLICY_SHA256
            or plan["policy_sha256"] != _clock_campaign_review_POLICY_SHA256):
        raise ValueError("Policy binding mismatch")
    if value["plan_sha256"] != canonical_sha256(plan) or value["plan_sha256"] != expected_plan_sha256:
        raise ValueError("Plan binding mismatch")

    issues, phase_reports, calculations = [], [], []

    def issue(code, status="incomplete", **where):
        issues.append({"code": code, "status": status, **where})

    def check_bounds(code, lower, upper, minimum, maximum, **where):
        status = _bounds(lower, upper, minimum, maximum)
        calculations.append({"check": code, "lower": [lower.numerator, lower.denominator],
                             "upper": [upper.numerator, upper.denominator], "status": status, **where})
        if status != "passed":
            issue(code, status, **where)

    campaigns = _list(value["campaigns"], 2)
    seen_trials = set()
    all_phases = {}
    for ci, campaign in enumerate(campaigns):
        _object(campaign, {"role", "trial_sha256", "phases"})
        if campaign["role"] != _clock_campaign_review_POLICY["campaigns"][ci]:
            raise ValueError("Campaign order mismatch")
        _hash(campaign["trial_sha256"])
        if campaign["trial_sha256"] in seen_trials:
            issue("reused_trial", "failed")
        seen_trials.add(campaign["trial_sha256"])
        phases = _list(campaign["phases"], 2)
        previous = None
        for pi, envelope in enumerate(phases):
            _object(envelope, {"sha256", "data"})
            _hash(envelope["sha256"])
            data = envelope["data"]
            if canonical_sha256(data) != envelope["sha256"]:
                raise ValueError("Phase binding mismatch")
            _object(data, {"phase", "identity_start", "identity_end", "samples", "external_utc",
                           "qpc_calibration", "future_probes", "lost_samples", "interrupted"})
            if data["phase"] != _clock_campaign_review_POLICY["phases"][pi]:
                raise ValueError("Phase order mismatch")
            where = {"campaign": ci, "phase": pi}
            _identity(data["identity_start"])
            _identity(data["identity_end"])
            start, end = data["identity_start"], data["identity_end"]
            if start is None or end is None:
                issue("identity_missing", **where)
            elif start != end:
                issue("identity_changed", "failed", **where)
            if _clock_campaign_review_integer(data["lost_samples"]):
                issue("instrumentation_loss", **where)
            if type(data["interrupted"]) is not bool:
                raise ValueError("Invalid interruption value")
            if data["interrupted"]:
                issue("collector_interrupted", **where)
            samples = _list(data["samples"], 10000)
            last_values = {}
            for index, sample in enumerate(samples):
                _object(sample, BASE_FIELDS | EXTRA_FIELDS)
                if type(sample["index"]) is not int or sample["index"] != index:
                    raise ValueError("Invalid sample index")
                for key in (BASE_FIELDS | EXTRA_FIELDS) - {"identity_sha256"}:
                    if sample[key] is None:
                        issue("sample_value_missing", index=index, field=key, **where)
                    else:
                        _clock_campaign_review_integer(sample[key])
                digest = sample["identity_sha256"]
                if digest is not None:
                    _hash(digest)
                if digest is None or start is None:
                    issue("sample_identity_missing", index=index, **where)
                elif digest != canonical_sha256(start):
                    issue("sample_identity_changed", "failed", index=index, **where)
                for clock in ("windows_utc", "realtime", "boottime", "monotonic", "raw"):
                    before, after = sample[f"{clock}_before_ns"], sample[f"{clock}_after_ns"]
                    if before is not None and after is not None and before > after:
                        issue("clock_reversed_bracket", "failed", clock=clock, index=index, **where)
                    if before is not None and clock in last_values and before < last_values[clock]:
                        issue("clock_backwards", "failed", clock=clock, index=index, **where)
                    if after is not None:
                        last_values[clock] = after
            known_qpc = [s["qpc_tick"] for s in samples if s["qpc_tick"] is not None]
            if any(b <= a for a, b in zip(known_qpc, known_qpc[1:])):
                issue("qpc_not_increasing", "failed", **where)
            if any(b - a > 2 * frequency for a, b in zip(known_qpc, known_qpc[1:])):
                issue("qpc_gap", **where)
            complete_base = len(samples) >= 2 and all(s[k] is not None for s in samples for k in BASE_FIELDS)
            rate = None
            if complete_base:
                rate = _clock_campaign_review_review_clock_rate_phase({"schema_version": 1, "kind": "clock-rate-phase-v1",
                    "phase": data["phase"], "qpc_frequency_hz": frequency,
                    "samples": [{k: s[k] for k in BASE_FIELDS} for s in samples]})
                for item in rate["issues"]:
                    issue("rate_" + item["code"], item["status"], **where)
            else:
                issue("rate_samples_missing", **where)
            phase_reports.append({**where, "sha256": envelope["sha256"], "rate": rate})
            # Boundaries are selected from QPC, never from caller-selected indexes.
            boundaries = {0: 0} if samples and samples[0]["qpc_tick"] is not None else {}
            if len(samples) >= 2 and all(s["qpc_tick"] is not None for s in samples) and all(b["qpc_tick"] > a["qpc_tick"] for a, b in zip(samples, samples[1:])):
                cursor = 1
                for seconds in range(60, 1801, 60):
                    while cursor < len(samples) and samples[cursor]["qpc_tick"] - samples[0]["qpc_tick"] < seconds * frequency:
                        cursor += 1
                    if cursor < len(samples):
                        boundaries[seconds] = cursor
            rate_boundaries = boundaries
            rate_check = "partial_elapsed_rate"
            if samples and samples[0]["qpc_tick"] is not None and any(s["qpc_tick"] is None for s in samples):
                # Exact observed endpoints still prove a rate violation. They cannot
                # establish first-reach coverage across the missing QPC samples.
                origin = samples[0]["qpc_tick"]
                if all(b > a for a, b in zip(known_qpc, known_qpc[1:])):
                    rate_boundaries = {0: 0}
                    for index, sample in enumerate(samples[1:], 1):
                        tick = sample["qpc_tick"]
                        if tick is not None and (tick - origin) % (60 * frequency) == 0:
                            second = (tick - origin) // frequency
                            if second in range(60, 1801, 60):
                                rate_boundaries[second] = index
                    rate_check = "observed_exact_boundary_rate"
            if not complete_base and len(rate_boundaries) > 1:
                # These diagnostics never fill missing coverage or grant qualification.
                pairs = list(zip(list(rate_boundaries.values()), list(rate_boundaries.values())[1:]))
                if 1800 in rate_boundaries:
                    pairs.append((0, rate_boundaries[1800]))
                for first, last in pairs:
                    if first == last:
                        continue
                    elapsed = _clock_campaign_review_Fraction(samples[last]["qpc_tick"] - samples[first]["qpc_tick"], frequency)
                    for clock in ("monotonic", "raw"):
                        b0, a0 = (samples[first][f"{clock}_{edge}_ns"] for edge in ("before", "after"))
                        b1, a1 = (samples[last][f"{clock}_{edge}_ns"] for edge in ("before", "after"))
                        if all(v is not None for v in (b0, a0, b1, a1)) and b0 <= a0 < b1 <= a1:
                            check_bounds(rate_check, _clock_campaign_review_Fraction(b1 - a0, 10**9) / elapsed,
                                         _clock_campaign_review_Fraction(a1 - b0, 10**9) / elapsed, _clock_campaign_review_Fraction(999, 1000),
                                         _clock_campaign_review_Fraction(1001, 1000), clock=clock, first_index=first, last_index=last, **where)
            if len(boundaries) != 31:
                issue("coverage_missing", **where)
            utc_seen = set()
            for item in _list(data["external_utc"], 31):
                _object(item, {"boundary_seconds", "sample_index", "lower_ns", "upper_ns", "source_sha256"})
                seconds, index = _clock_campaign_review_integer(item["boundary_seconds"]), _clock_campaign_review_integer(item["sample_index"])
                lower, upper = _clock_campaign_review_integer(item["lower_ns"]), _clock_campaign_review_integer(item["upper_ns"])
                _hash(item["source_sha256"])
                if seconds in utc_seen or seconds not in range(0, 1801, 60):
                    issue("utc_boundary_duplicate_or_extra", "failed", **where)
                utc_seen.add(seconds)
                if item["source_sha256"] != plan["utc_source_sha256"]:
                    issue("utc_source_changed", "failed", **where)
                if lower > upper:
                    issue("utc_reference_reversed", "failed", **where)
                    continue
                if seconds not in boundaries:
                    issue("utc_sample_unobserved", **where)
                    continue
                if boundaries[seconds] != index:
                    issue("utc_wrong_sample", "failed", **where)
                    continue
                for clock in ("windows_utc", "realtime"):
                    b, a = samples[index][f"{clock}_before_ns"], samples[index][f"{clock}_after_ns"]
                    if b is not None and a is not None:
                        check_bounds("external_utc_offset", _clock_campaign_review_Fraction(b - upper), _clock_campaign_review_Fraction(a - lower),
                                     _clock_campaign_review_Fraction(-10**9), _clock_campaign_review_Fraction(10**9), boundary_seconds=seconds, clock=clock, **where)
            if utc_seen != set(range(0, 1801, 60)):
                issue("utc_boundaries_missing", **where)
            expected_pairs = {(boundaries[a], boundaries[b]) for a, b in zip(range(0, 1800, 60), range(60, 1801, 60))
                              if a in boundaries and b in boundaries and boundaries[a] != boundaries[b]}
            if 1800 in boundaries:
                expected_pairs.add((0, boundaries[1800]))
            cal_seen = set()
            for item in _list(data["qpc_calibration"], 31):
                _object(item, {"first_index", "last_index", "reference_lower_ns", "reference_upper_ns", "source_sha256"})
                pair = (_clock_campaign_review_integer(item["first_index"]), _clock_campaign_review_integer(item["last_index"]))
                lower, upper = _clock_campaign_review_integer(item["reference_lower_ns"], 1), _clock_campaign_review_integer(item["reference_upper_ns"], 1)
                _hash(item["source_sha256"])
                if pair in cal_seen:
                    issue("calibration_pair_duplicate_or_extra", "failed", **where)
                elif pair not in expected_pairs:
                    issue("calibration_pair_duplicate_or_extra", "failed" if len(boundaries) == 31 else "incomplete", **where)
                cal_seen.add(pair)
                if item["source_sha256"] != plan["calibration_source_sha256"]:
                    issue("calibration_source_changed", "failed", **where)
                if lower > upper:
                    issue("calibration_reference_reversed", "failed", **where)
                elif pair in expected_pairs:
                    q = _clock_campaign_review_Fraction(samples[pair[1]]["qpc_tick"] - samples[pair[0]]["qpc_tick"], frequency)
                    check_bounds("qpc_calibration", q * 10**9 / upper, q * 10**9 / lower,
                                 _clock_campaign_review_Fraction(9999, 10000), _clock_campaign_review_Fraction(10001, 10000), first_index=pair[0], last_index=pair[1], **where)
            if cal_seen != expected_pairs or len(expected_pairs) != 31:
                issue("calibration_windows_missing", **where)
            probe_seen = set()
            for probe in _list(data["future_probes"], 3):
                _object(probe, {"case", "generated_utc_ns", "validated_utc_ns", "decision",
                                "blocked_before_sha256", "blocked_after_sha256", "tags_before_sha256", "tags_after_sha256",
                                "generated_monotonic_ns", "validated_monotonic_ns", "sample_index"})
                case = probe["case"]
                if not isinstance(case, str) or case not in {"equal", "future-1us", "live"}:
                    raise ValueError("Invalid future probe case")
                if case in probe_seen:
                    issue("future_probe_duplicate", "failed", **where)
                probe_seen.add(case)
                delta = _clock_campaign_review_integer(probe["generated_utc_ns"]) - _clock_campaign_review_integer(probe["validated_utc_ns"])
                if not isinstance(probe["decision"], str) or probe["decision"] not in {"allow", "reject"}:
                    raise ValueError("Invalid future decision")
                for key in {"blocked_before_sha256", "blocked_after_sha256", "tags_before_sha256", "tags_after_sha256"}:
                    _hash(probe[key])
                if _clock_campaign_review_integer(probe["generated_monotonic_ns"]) > _clock_campaign_review_integer(probe["validated_monotonic_ns"]):
                    issue("future_probe_monotonic_backwards", "failed", **where)
                index = probe["sample_index"]
                if case == "live":
                    if index is None or _clock_campaign_review_integer(index) >= len(samples):
                        issue("live_probe_sample_missing", **where)
                    else:
                        for clock, prefix in (("realtime", "utc"), ("monotonic", "monotonic")):
                            before, after = samples[index][f"{clock}_before_ns"], samples[index][f"{clock}_after_ns"]
                            if before is None or after is None:
                                issue("live_probe_sample_missing", **where)
                            elif not (before <= probe[f"generated_{prefix}_ns"] <= after and before <= probe[f"validated_{prefix}_ns"] <= after):
                                issue("live_probe_outside_sample", "failed", **where)
                elif index is not None:
                    raise ValueError("Fixture probe must not claim a live sample")
                expected_delta = (case == "equal" and delta == 0) or (case == "future-1us" and delta == 1000) or (case == "live" and delta <= 0)
                if not expected_delta or probe["decision"] != ("reject" if delta > 0 else "allow"):
                    issue("strict_future_probe_failed", "failed", **where)
                if delta > 0 and any(probe[f"{k}_before_sha256"] != probe[f"{k}_after_sha256"] for k in ("blocked", "tags")):
                    issue("future_rejection_mutated_state", "failed", **where)
            if probe_seen != {"equal", "future-1us", "live"}:
                issue("future_probes_missing", **where)
            if previous is not None:
                # Phase transition is the same sample, with a fresh phase-local index.
                if previous["samples"] and samples:
                    left = {k: v for k, v in previous["samples"][-1].items() if k != "index"}
                    right = {k: v for k, v in samples[0].items() if k != "index"}
                    if any(left[k] is not None and right[k] is not None and left[k] != right[k] for k in left):
                        issue("phase_boundary_not_shared", "failed", **where)
                    if any(left[k] is None or right[k] is None for k in left):
                        issue("phase_boundary_missing", **where)
                else:
                    issue("phase_transition_missing", **where)
                if previous["identity_end"] is None or start is None:
                    issue("phase_identity_missing", **where)
                elif previous["identity_end"] != start:
                    issue("phase_identity_changed", "failed", **where)
            previous = data
            all_phases[(ci, pi)] = data
        if len(phases) != 2:
            issue("phase_missing", campaign=ci)
    if len(campaigns) != 2:
        issue("campaign_missing")

    resume = value["resume"]
    if resume is None:
        issue("resume_evidence_missing")
    else:
        _object(resume, {"before_identity_sha256", "after_identity_sha256", "windows_before_ns", "windows_after_ns",
                         "guest_before_ns", "guest_after_ns"})
        for key in ("before_identity_sha256", "after_identity_sha256"):
            _hash(resume[key])
        for key in set(resume) - {"before_identity_sha256", "after_identity_sha256"}:
            if resume[key] is None:
                issue("resume_utc_missing")
            else:
                _clock_campaign_review_integer(resume[key])
        for clock, field in (("windows", "windows_utc"), ("guest", "realtime")):
            before, after = resume[f"{clock}_before_ns"], resume[f"{clock}_after_ns"]
            if before is not None and after is not None and after < before:
                issue("resume_utc_backwards", "failed", clock=clock)
            for ci, pi, edge, observed in ((0, 1, "end", before), (1, 0, "start", after)):
                data = all_phases.get((ci, pi))
                if data and data["samples"]:
                    sample = data["samples"][-1 if edge == "end" else 0]
                    bound = sample[f"{field}_{'after' if edge == 'end' else 'before'}_ns"]
                    if observed is not None and bound is not None and ((edge == "end" and observed < bound) or (edge == "start" and observed > bound)):
                        issue("resume_outside_campaign_order", "failed", clock=clock)
        for pair, key in (((0, 1), "before"), ((1, 0), "after")):
            data = all_phases.get(pair)
            identity = data[f"identity_{'end' if key == 'before' else 'start'}"] if data else None
            if identity is None:
                issue("resume_identity_missing")
            elif resume[f"{key}_identity_sha256"] != canonical_sha256(identity):
                issue("resume_identity_mismatch", "failed")

    status = "failed" if any(i["status"] == "failed" for i in issues) else ("incomplete" if issues else "passed")
    return {"schema_version": 1, "scope": "untrusted_archived_whole_campaign_checks_only",
            "policy": _clock_campaign_review_copy.deepcopy(_clock_campaign_review_POLICY), "policy_sha256": _clock_campaign_review_POLICY_SHA256,
            "plan_sha256": value["plan_sha256"], "offline_checks_status": status,
            "clock_qualification_status": "failed" if status == "failed" else "incomplete",
            "qualification_prerequisites_not_established": list(PREREQUISITES),
            "issues": issues, "phases": phase_reports, "calculations": calculations}


# ========================================================================
# clock_campaign_capture
# ========================================================================

"""Synthetic acquisition protocol only. Never collect or qualify a real clock.

The child reader is a fixed fixture, not an operator-provided command or endpoint.
Raw records are replayable without the child, OS clocks, or a service connection.
"""


import copy as _clock_campaign_capture_copy
import hashlib as _clock_campaign_capture_hashlib
import json as _clock_campaign_capture_json
import os
from pathlib import Path
import re as _clock_campaign_capture_re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

_clock_campaign_capture_POLICY = _clock_campaign_review_POLICY
_clock_campaign_capture_POLICY_SHA256 = _clock_campaign_review_POLICY_SHA256
_clock_campaign_capture_canonical_sha256 = canonical_sha256
_clock_campaign_capture_review_clock_campaign = review_clock_campaign

NS = 10**9
FREQUENCY = 1000
ROLES = ("normal", "post-resume")
PHASES = ("idle", "fixture-load")
CLOCKS = ("monotonic", "raw", "realtime", "boottime")
MAX_BYTES = 64 * 1024 * 1024
MEMBERS = {"plan.json", "sources.json", "journal.jsonl", "complete.json"}
SOURCE = {"kind": "synthetic-independent-reference-v1", "uncertainty_ns": 1000}
RECIPE = {"kind": "synthetic-arithmetic-workload-v1", "seconds": 1800, "period_seconds": 1}
FAULTS = {"none", "nonce", "sequence", "trial", "duplicate", "frequency", "reverse",
          "missing", "identity", "namespace", "configuration", "delay", "exit", "trailing"}


def encode(value):
    return _clock_campaign_capture_json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode() + b"\n"


def object_exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("Invalid synthetic protocol schema")


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid synthetic integer")
    return value


def digest(value):
    if type(value) is not str or _clock_campaign_capture_re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Invalid digest")
    return value


def token(value):
    if type(value) is not str or _clock_campaign_capture_re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("Invalid synthetic token")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def decode(payload):
    return _clock_campaign_capture_json.loads(payload, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid constant")))


def read_stable(path, maximum=MAX_BYTES):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError("Invalid evidence file")
        payload = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if (identity(before) != identity(after) or identity(after) != identity(path.stat(follow_symlinks=False))
            or len(payload) != before.st_size):
        raise ValueError("Evidence changed during reading")
    return payload


def publish_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encode(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Exclusive publication; never overwrite a prior attempt.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def identity_evidence():
    """Whitelisted synthetic originals; no live process/config/host discovery."""
    return {
        "host_boot": {"method": "fixture-boot-v1", "boot": 1},
        "guest_boot": {"boot": 2, "namespace": 3},
        "process_set": [{"role": "fixture-sync", "start_tick": 4, "kernel_pid": 5,
                         "local_pid": 6, "parent_start_tick": 7, "exe_sha256": "a" * 64,
                         "exe_dev": 8, "exe_inode": 9, "pid_namespace": 10,
                         "mnt_namespace": 11, "time_namespace": 12}],
        "sync_configuration": {"active_bytes_hex": "66697874757265", "includes": []},
        "versions": {"method": "fixture-versions-v1", "version": 1},
        "clocksource": {"method": "fixture-clock-api-v1", "frequency_hz": FREQUENCY},
    }


def identity_hashes(value):
    # This fixture-only schema deliberately cannot claim to describe a real collector.
    template = identity_evidence()
    object_exact(value, template)
    for name in ("host_boot", "guest_boot", "versions", "clocksource", "sync_configuration"):
        object_exact(value[name], template[name])
    for name in ("host_boot", "guest_boot", "versions", "clocksource"):
        for key, item in value[name].items():
            if type(template[name][key]) is str:
                if item != template[name][key]:
                    raise ValueError("Unsupported synthetic identity method")
            else:
                integer(item)
    config = value["sync_configuration"]
    if (type(config["active_bytes_hex"]) is not str or
            _clock_campaign_capture_re.fullmatch(r"[0-9a-f]{0,128}", config["active_bytes_hex"]) is None or config["includes"] != []):
        raise ValueError("Invalid synthetic configuration")
    rows = value["process_set"]
    if type(rows) is not list or len(rows) != 1:
        raise ValueError("Invalid process set")
    object_exact(rows[0], template["process_set"][0])
    for key, item in rows[0].items():
        if key == "role":
            if item != "fixture-sync":
                raise ValueError("Invalid process role")
        elif key == "exe_sha256":
            digest(item)
        else:
            integer(item)
    return {name + "_sha256": _clock_campaign_capture_canonical_sha256(original) for name, original in value.items()}


def guest_values(t, edge):
    return {clock: (1800000000 + t if clock == "realtime" else t) * NS + edge
            for clock in CLOCKS}


def fake_response(request):
    """Independent fixture truth counter; no real UTC/QPC or OS reads."""
    object_exact(request, {"trial", "role", "phase", "sequence", "nonce", "truth_second"})
    token(request["trial"])
    token(request["nonce"])
    integer(request["sequence"])
    t = integer(request["truth_second"])
    if request["role"] not in ROLES or request["phase"] not in PHASES:
        raise ValueError("Invalid synthetic request")
    return {"echo": _clock_campaign_capture_copy.deepcopy(request), "read_order": ["utc-before", "qpc", "utc-after"],
            "qpc_tick": t * FREQUENCY, "qpc_frequency_hz": FREQUENCY,
            "utc_before_ns": (1800000000 + t) * NS + 200,
            "utc_after_ns": (1800000000 + t) * NS + 800,
            "reference": {"source": _clock_campaign_capture_copy.deepcopy(SOURCE), "utc_center_ns": (1800000000 + t) * NS,
                          "elapsed_counter_ns": t * NS, "uncertainty_ns": 1000}}


def validate_response(request, response, frequency):
    object_exact(response, {"echo", "read_order", "qpc_tick", "qpc_frequency_hz",
                            "utc_before_ns", "utc_after_ns", "reference"})
    if encode(response["echo"]) != encode(request):
        raise ValueError("Response challenge mismatch")
    if response["read_order"] != ["utc-before", "qpc", "utc-after"]:
        raise ValueError("Reader order mismatch")
    for key in ("qpc_tick", "qpc_frequency_hz", "utc_before_ns", "utc_after_ns"):
        integer(response[key])
    if response["qpc_frequency_hz"] != frequency:
        raise ValueError("QPC frequency changed")
    reference = response["reference"]
    object_exact(reference, {"source", "utc_center_ns", "elapsed_counter_ns", "uncertainty_ns"})
    if encode(reference["source"]) != encode(SOURCE):
        raise ValueError("Fixture source changed")
    for key in ("utc_center_ns", "elapsed_counter_ns", "uncertainty_ns"):
        integer(reference[key])
    # Synthetic source has a fixed, nonzero declared uncertainty. Never infer zero.
    if reference["uncertainty_ns"] != SOURCE["uncertainty_ns"]:
        raise ValueError("Fixture uncertainty changed")


class Reader:
    """One fixed child, one outstanding request; bounded pipe reads and cleanup."""
    def __init__(self, fault="none", timeout=3.0):
        if fault not in FAULTS:
            raise ValueError("Unknown fixture fault")
        self.timeout = timeout
        self.buffer = b""
        self.process = None
        self.selector = selectors.DefaultSelector()
        try:
            self.process = subprocess.Popen([sys.executable, "-m", 'bike_mlops.clock', fault],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
        except BaseException:
            if self.process is not None:
                try:
                    self.close()
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
            else:
                self.selector.close()
            raise

    def request(self, value):
        self.process.stdin.write(encode(value))
        self.process.stdin.flush()
        deadline = time.monotonic() + self.timeout  # IO safety only; never clock evidence.
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise TimeoutError("Synthetic reader timeout")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise ValueError("Synthetic reader closed early")
            self.buffer += chunk
            if len(self.buffer) > 16384:
                raise ValueError("Synthetic reader response too large")
        line, self.buffer = self.buffer.split(b"\n", 1)
        if self.buffer:
            raise ValueError("Unsolicited duplicate response")
        return decode(line)

    def close(self):
        self.selector.close()
        try:
            if not self.process.stdin.closed:
                self.process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=1)
        try:
            os.set_blocking(self.process.stdout.fileno(), False)
            extra = os.read(self.process.stdout.fileno(), 16385)
        finally:
            self.process.stdout.close()
        if self.process.returncode != 0 or extra or self.buffer:
            raise ValueError("Synthetic reader did not exit normally")


def make_plan(*, scheduled_recipe=None):
    # Consolidated collector, reader, scheduler and verifiers share this module.
    # Bind both actual files; schedule semantics remain in the raw header/recipe.
    sources = {"clock.py": Path(__file__).read_bytes().hex(),
               "clock_tools.py": (Path(__file__).resolve().parents[2] /
                                  "scripts/clock_tools.py").read_bytes().hex()}
    manifest = {"kind": "synthetic-module-manifest-v1", "files": {
        name: _clock_campaign_capture_hashlib.sha256(bytes.fromhex(payload)).hexdigest() for name, payload in sources.items()}}
    manifest["python_version"] = list(sys.version_info[:3])
    plan = {"policy_sha256": _clock_campaign_capture_POLICY_SHA256, "collector_sha256": _clock_campaign_capture_canonical_sha256(manifest),
            "workload_sha256": _clock_campaign_capture_canonical_sha256(RECIPE if scheduled_recipe is None else scheduled_recipe), "utc_reader_sha256": _clock_campaign_capture_canonical_sha256(manifest),
            "utc_source_sha256": _clock_campaign_capture_canonical_sha256(SOURCE), "calibration_source_sha256": _clock_campaign_capture_canonical_sha256(SOURCE),
            "qpc_frequency_hz": FREQUENCY}
    return plan, manifest, sources


def capture_fake(root, *, fault="none", reader_timeout=3.0, on_record=None, schedule=None):
    """Exclusive synthetic attempt. Partial journal survives failures; no complete marker.

    on_record is an in-process test injection, not a CLI command/plugin surface.
    No workload, real source, or real collector exists behind this fixture entry.
    """
    if schedule is not None:
        from bike_mlops.clock import capture_scheduled
        return capture_scheduled(root, scenario=schedule, fault=fault,
                                 reader_timeout=reader_timeout, on_record=on_record)
    root.mkdir()  # Caller selects a new path; never delete/reuse an existing one.
    plan, manifest, sources = make_plan()
    publish_json(root / "sources.json", sources)
    publish_json(root / "plan.json", plan)
    plan_hash = _clock_campaign_capture_canonical_sha256(plan)
    reader, chain, count = None, "0" * 64, 0
    with (root / "journal.jsonl").open("xb") as journal:
        def append(data):
            nonlocal chain, count
            record = {"sequence": count, "previous_sha256": chain, "data": data}
            chain = _clock_campaign_capture_canonical_sha256(record)
            record["sha256"] = chain
            journal.write(encode(record))
            journal.flush()
            count += 1
            if on_record is not None:
                on_record(data)
        try:
            append({"type": "header", "kind": "synthetic-clock-raw-v1", "plan_sha256": plan_hash,
                    "manifest": manifest, "source": SOURCE, "recipe": RECIPE})
            # Deliver interruption only after the caller owns the child. Otherwise
            # a handler during Popen's return can strand a child before assignment.
            previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
            try:
                reader = Reader(fault=fault, timeout=reader_timeout)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            for ci, role in enumerate(ROLES):
                trial = secrets.token_hex(16)
                for pi, phase in enumerate(PHASES):
                    append({"type": "phase-start", "role": role, "phase": phase, "trial": trial,
                            "identity": identity_evidence()})
                    probes = None
                    for sequence in range(1 if pi else 0, 1801):
                        t = ci * 4000 + pi * 1800 + sequence
                        request = {"trial": trial, "role": role, "phase": phase, "sequence": sequence,
                                   "nonce": secrets.token_hex(16), "truth_second": t}
                        before = guest_values(t, 0)
                        # Retain the intent even if sending/reading never completes.
                        append({"type": "request", "request": request, "before": before})
                        response = reader.request(request)
                        after = guest_values(t, 1000)
                        identity = identity_evidence()
                        if fault in ("identity", "namespace", "configuration") and sequence == 20:
                            if fault == "identity":
                                identity["process_set"][0]["start_tick"] += 1
                            elif fault == "namespace":
                                identity["process_set"][0]["time_namespace"] += 1
                            else:
                                identity["sync_configuration"]["active_bytes_hex"] = "00"
                        # Preserve bad raw response before validation. Do not retry or renumber.
                        append({"type": "sample", "request": request, "before": before,
                                "response": response, "after": after, "identity": identity})
                        validate_response(request, response, FREQUENCY)
                        if sequence == 10:
                            probes = _probes({"realtime_after_ns": after["realtime"],
                                              "monotonic_before_ns": before["monotonic"],
                                              "monotonic_after_ns": after["monotonic"]})
                    append({"type": "phase-end", "role": role, "phase": phase,
                            "identity": identity_evidence(), "future_probes": probes})
                if ci == 0:
                    append({"type": "resume", "before_identity_sha256": _clock_campaign_capture_canonical_sha256(identity_hashes(identity_evidence())),
                            "after_identity_sha256": _clock_campaign_capture_canonical_sha256(identity_hashes(identity_evidence())),
                            "windows_before_ns": (1800000000 + 3601) * NS,
                            "windows_after_ns": (1800000000 + 3999) * NS,
                            "guest_before_ns": (1800000000 + 3601) * NS,
                            "guest_after_ns": (1800000000 + 3999) * NS})
            reader.close()
            reader = None
            append({"type": "end", "campaigns": 2})
            os.fsync(journal.fileno())
        except BaseException as error:
            # Also handles signal-to-exception from the CLI. SIGKILL cannot run cleanup.
            if reader is not None:
                try:
                    reader.close()
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
            try:
                append({"type": "failure", "error_class": type(error).__name__,
                        "reader_reaped": reader is None or reader.process.poll() is not None})
                os.fsync(journal.fileno())
            except BaseException:
                pass  # Disk/signal failure cannot make a reliable error record mandatory.
            raise
    payload = read_stable(root / "journal.jsonl")
    marker = {"kind": "synthetic-clock-complete-v1", "plan_sha256": plan_hash,
              "journal_sha256": _clock_campaign_capture_hashlib.sha256(payload).hexdigest(), "journal_bytes": len(payload),
              "records": count, "last_sha256": chain, "reader_reaped": True}
    publish_json(root / "complete.json", marker)
    return plan_hash


def _probes(sample):
    state = _clock_campaign_capture_canonical_sha256({"blocked": True, "synthetic": True})
    result = []
    for case, delta in (("equal", 0), ("future-1us", 1000), ("live", -1000)):
        result.append({"case": case, "sample_index": 10 if case == "live" else None,
                       "generated_utc_ns": sample["realtime_after_ns"] + delta,
                       "validated_utc_ns": sample["realtime_after_ns"],
                       "generated_monotonic_ns": sample["monotonic_before_ns"],
                       "validated_monotonic_ns": sample["monotonic_after_ns"],
                       "decision": "reject" if delta > 0 else "allow",
                       "blocked_before_sha256": state, "blocked_after_sha256": state,
                       "tags_before_sha256": state, "tags_after_sha256": state})
    return result


def derive_raw(root, *, expected_plan_sha256):
    """Replay exact synthetic records and recompute derived numbers, hashes and status."""
    digest(expected_plan_sha256)
    if root.is_symlink() or not root.is_dir() or {p.name for p in root.iterdir()} != MEMBERS:
        raise ValueError("Incomplete or unexpected evidence members")
    paths = {name: root / name for name in MEMBERS}
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    initial_identity = {name: identity(path.stat(follow_symlinks=False)) for name, path in paths.items()}
    payloads = {name: read_stable(path) for name, path in paths.items()}
    plan, marker = decode(payloads["plan.json"]), decode(payloads["complete.json"])
    object_exact(plan, {"policy_sha256", "collector_sha256", "workload_sha256", "utc_reader_sha256",
                        "utc_source_sha256", "calibration_source_sha256", "qpc_frequency_hz"})
    for key in set(plan) - {"qpc_frequency_hz"}:
        digest(plan[key])
    if integer(plan["qpc_frequency_hz"]) != FREQUENCY or plan["policy_sha256"] != _clock_campaign_capture_POLICY_SHA256:
        raise ValueError("Unsupported synthetic plan")
    if _clock_campaign_capture_canonical_sha256(plan) != expected_plan_sha256:
        raise ValueError("Plan binding mismatch")
    object_exact(marker, {"kind", "plan_sha256", "journal_sha256", "journal_bytes", "records",
                          "last_sha256", "reader_reaped"})
    raw = payloads["journal.jsonl"]
    if (marker["kind"] != "synthetic-clock-complete-v1" or marker["reader_reaped"] is not True
            or marker["plan_sha256"] != expected_plan_sha256
            or marker["journal_sha256"] != _clock_campaign_capture_hashlib.sha256(raw).hexdigest()
            or integer(marker["journal_bytes"]) != len(raw)):
        raise ValueError("Completion marker mismatch")
    lines = raw.splitlines()
    if not raw.endswith(b"\n") or len(lines) > 16000 or integer(marker["records"]) != len(lines):
        raise ValueError("Invalid journal framing")
    records, previous = [], "0" * 64
    for index, line in enumerate(lines):
        if len(line) > 16384:
            raise ValueError("Oversized record")
        row = decode(line)
        object_exact(row, {"sequence", "previous_sha256", "data", "sha256"})
        if integer(row["sequence"]) != index or row["previous_sha256"] != previous:
            raise ValueError("Journal sequence mismatch")
        previous = digest(row.pop("sha256"))
        if _clock_campaign_capture_canonical_sha256(row) != previous:
            raise ValueError("Journal chain mismatch")
        records.append(row["data"])
    if marker["last_sha256"] != previous:
        raise ValueError("Journal final hash mismatch")
    cursor = iter(records)
    header = next(cursor)
    object_exact(header, {"type", "kind", "plan_sha256", "manifest", "source", "recipe"})
    scheduled = header["kind"] == "synthetic-clock-scheduled-raw-v1"
    if scheduled:
        from bike_mlops.clock import validate_recipe, replay_scheduled
        validate_recipe(header["recipe"])
    recipe = header["recipe"] if scheduled else RECIPE
    if (header["type"] != "header" or header["kind"] not in ("synthetic-clock-raw-v1", "synthetic-clock-scheduled-raw-v1")
            or header["plan_sha256"] != expected_plan_sha256
            or encode(header["source"]) != encode(SOURCE) or encode(header["recipe"]) != encode(recipe)):
        raise ValueError("Synthetic header mismatch")
    manifest = header["manifest"]
    object_exact(manifest, {"kind", "files", "python_version"})
    # Archived pre-consolidation evidence remains readable. Exact allowlists
    # forbid extra members in either layout, and saved source bytes never execute.
    current_sources = {"clock.py", "clock_tools.py"}
    legacy_sources = {"clock_campaign_capture.py", "clock_campaign_fake_reader.py",
                      "clock_campaign_review.py", "clock_rate_review.py",
                      "capture_fake_clock_campaign.py", "derive_clock_campaign.py"}
    if scheduled:
        legacy_sources.add("clock_campaign_schedule.py")
    expected_sources = current_sources if set(manifest["files"]) == current_sources else legacy_sources
    object_exact(manifest["files"], expected_sources)
    if manifest["kind"] != "synthetic-module-manifest-v1":
        raise ValueError("Unsupported manifest")
    sources = decode(payloads["sources.json"])
    object_exact(sources, manifest["files"])
    for name, value in manifest["files"].items():
        digest(value)
        if type(sources[name]) is not str or len(sources[name]) > 256 * 1024:
            raise ValueError("Invalid source bytes")
        if _clock_campaign_capture_hashlib.sha256(bytes.fromhex(sources[name])).hexdigest() != value:
            raise ValueError("Source bytes mismatch")
    if type(manifest["python_version"]) is not list or len(manifest["python_version"]) != 3:
        raise ValueError("Invalid runtime version")
    for value in manifest["python_version"]:
        integer(value)
    for key, original in (("collector_sha256", manifest), ("utc_reader_sha256", manifest),
                          ("workload_sha256", recipe), ("utc_source_sha256", SOURCE),
                          ("calibration_source_sha256", SOURCE)):
        if plan[key] != _clock_campaign_capture_canonical_sha256(original):
            raise ValueError("Recipe/source binding mismatch")
    result = {"schema_version": 1, "kind": "clock-campaign-v1", "policy": _clock_campaign_capture_copy.deepcopy(_clock_campaign_capture_POLICY),
              "policy_sha256": _clock_campaign_capture_POLICY_SHA256, "plan": plan, "plan_sha256": expected_plan_sha256,
              "campaigns": [], "resume": None}
    if scheduled:
        replay_scheduled(cursor, result, header["recipe"])
    else:
        nonces, trials = set(), set()
        for ci, role in enumerate(ROLES):
            campaign = {"role": role, "trial_sha256": None, "phases": []}
            shared = shared_reference = None
            for pi, phase in enumerate(PHASES):
                start = next(cursor)
                object_exact(start, {"type", "role", "phase", "trial", "identity"})
                trial = token(start["trial"])
                if start["type"] != "phase-start" or start["role"] != role or start["phase"] != phase:
                    raise ValueError("Phase order mismatch")
                if not pi:
                    if trial in trials:
                        raise ValueError("Trial reused")
                    trials.add(trial)
                    campaign["trial_sha256"] = _clock_campaign_capture_canonical_sha256(trial)
                elif campaign["trial_sha256"] != _clock_campaign_capture_canonical_sha256(trial):
                    raise ValueError("Trial changed within campaign")
                data = {"phase": phase, "identity_start": identity_hashes(start["identity"]), "identity_end": None,
                        "samples": [], "external_utc": [], "qpc_calibration": [], "future_probes": [],
                        "lost_samples": 0, "interrupted": False}
                references = []
                if pi:
                    data["samples"].append({**shared, "index": 0})
                    references.append(shared_reference)
                for sequence in range(1 if pi else 0, 1801):
                    intent = next(cursor)
                    object_exact(intent, {"type", "request", "before"})
                    if intent["type"] != "request":
                        raise ValueError("Request intent missing")
                    record = next(cursor)
                    object_exact(record, {"type", "request", "before", "response", "after", "identity"})
                    if record["type"] != "sample":
                        raise ValueError("Sample missing")
                    if encode(intent["request"]) != encode(record["request"]) or encode(intent["before"]) != encode(record["before"]):
                        raise ValueError("Request intent changed before response")
                    request = record["request"]
                    object_exact(request, {"trial", "role", "phase", "sequence", "nonce", "truth_second"})
                    nonce = token(request["nonce"])
                    if (nonce in nonces or request["trial"] != trial or request["role"] != role
                            or request["phase"] != phase or integer(request["sequence"]) != sequence
                            or integer(request["truth_second"]) != ci * 4000 + pi * 1800 + sequence):
                        raise ValueError("Sample challenge/order mismatch")
                    nonces.add(nonce)
                    validate_response(request, record["response"], plan["qpc_frequency_hz"])
                    sample = {"index": sequence, "qpc_tick": record["response"]["qpc_tick"],
                              "identity_sha256": _clock_campaign_capture_canonical_sha256(identity_hashes(record["identity"]))}
                    for edge in ("before", "after"):
                        object_exact(record[edge], CLOCKS)
                        for clock, value in record[edge].items():
                            sample[f"{clock}_{edge}_ns"] = integer(value)
                        sample[f"windows_utc_{edge}_ns"] = record["response"][f"utc_{edge}_ns"]
                    data["samples"].append(sample)
                    references.append(record["response"]["reference"])
                end = next(cursor)
                object_exact(end, {"type", "role", "phase", "identity", "future_probes"})
                if end["type"] != "phase-end" or end["role"] != role or end["phase"] != phase:
                    raise ValueError("Phase end mismatch")
                data["identity_end"] = identity_hashes(end["identity"])
                for second in range(0, 1801, 60):
                    ref = references[second]
                    data["external_utc"].append({"boundary_seconds": second, "sample_index": second,
                        "lower_ns": ref["utc_center_ns"] - ref["uncertainty_ns"],
                        "upper_ns": ref["utc_center_ns"] + ref["uncertainty_ns"],
                        "source_sha256": plan["utc_source_sha256"]})
                for a, b in [(s, s + 60) for s in range(0, 1800, 60)] + [(0, 1800)]:
                    elapsed = references[b]["elapsed_counter_ns"] - references[a]["elapsed_counter_ns"]
                    uncertainty = references[a]["uncertainty_ns"] + references[b]["uncertainty_ns"]
                    data["qpc_calibration"].append({"first_index": a, "last_index": b,
                        "reference_lower_ns": elapsed - uncertainty, "reference_upper_ns": elapsed + uncertainty,
                        "source_sha256": plan["calibration_source_sha256"]})
                data["future_probes"] = end["future_probes"]
                shared, shared_reference = data["samples"][-1], references[-1]
                campaign["phases"].append({"sha256": _clock_campaign_capture_canonical_sha256(data), "data": data})
            result["campaigns"].append(campaign)
            if ci == 0:
                resume = next(cursor)
                object_exact(resume, {"type", "before_identity_sha256", "after_identity_sha256",
                                      "windows_before_ns", "windows_after_ns", "guest_before_ns", "guest_after_ns"})
                if resume.pop("type") != "resume":
                    raise ValueError("Resume record missing")
                result["resume"] = resume
        end = next(cursor)
        if encode(end) != encode({"type": "end", "campaigns": 2}) or next(cursor, None) is not None:
            raise ValueError("Unexpected journal tail")
    # Validate numerical/schema output too. It may fail, but never qualifies a real clock.
    report = _clock_campaign_capture_review_clock_campaign(result, expected_plan_sha256=expected_plan_sha256)
    # Re-read each saved file after all computation, catching visible replacement/changes.
    if ({p.name for p in root.iterdir()} != MEMBERS
            or any(read_stable(paths[n]) != p for n, p in payloads.items())
            or any(identity(paths[n].stat(follow_symlinks=False)) != initial_identity[n] for n in MEMBERS)):
        raise ValueError("Evidence changed during replay")
    return result, report


# ========================================================================
# clock_campaign_schedule
# ========================================================================

"""Virtual QPC scheduler and raw replay. No real timer, source, or workload.

The fake counter advances by script, never by local UTC or elapsed test time.
OS MONOTONIC in the fixed pipe reader remains a separate transport safety limit.
"""


import copy as _clock_campaign_schedule_copy
import hashlib as _clock_campaign_schedule_hashlib
import os as _clock_campaign_schedule_os
import secrets as _clock_campaign_schedule_secrets
import signal as _clock_campaign_schedule_signal
import subprocess as _clock_campaign_schedule_subprocess

from bike_mlops import clock as raw
_clock_campaign_schedule_BASE_FIELDS = BASE_FIELDS
_clock_campaign_schedule_EXTRA_FIELDS = EXTRA_FIELDS
_clock_campaign_schedule_canonical_sha256 = canonical_sha256

SCENARIOS = {"punctual", "wake-late", "response-late", "deadline", "endpoint-late",
             "qpc-backwards", "interrupt"}
SCHEDULER = {"kind": "fake-qpc-fixed-period-v1", "period_ticks": 1000,
             "phase_ticks": 1800000, "frequency_hz": 1000,
             "dispatch_lateness_ticks": 0, "catch_up": False, "extend_phase": False}


def recipe_for(scenario):
    if type(scenario) is not str or scenario not in SCENARIOS:
        raise ValueError("Unknown synthetic schedule")
    return {**raw.RECIPE, "scheduler": _clock_campaign_schedule_copy.deepcopy(SCHEDULER), "scenario": scenario}


def validate_recipe(recipe):
    raw.object_exact(recipe, set(raw.RECIPE) | {"scheduler", "scenario"})
    if raw.encode(recipe) != raw.encode(recipe_for(recipe["scenario"])):
        raise ValueError("Schedule policy changed")


def slot_decision(origin, index, observed):
    """Exact lattice: late slots are lost; even one tick is never caught up."""
    for value in (origin, index, observed):
        raw.integer(value)
    if index > 1800:
        raise ValueError("Slot outside phase")
    due, deadline = origin + index * 1000, origin + 1800000
    if observed < due:
        raise ValueError("Wait returned early")
    return {"due_tick": due, "observed_tick": observed, "deadline_tick": deadline,
            "action": "dispatch" if observed == due else "missed",
            "reason": "on-time" if observed == due else
                      ("phase-deadline" if observed > deadline else "late-wake")}


class FakeQPC:
    """Scripted single virtual counter. No sleep, OS clock, or global settings."""
    def __init__(self, scenario):
        recipe_for(scenario)
        self.scenario, self.tick, self.previous = scenario, 0, 0

    def observe(self):
        value = raw.integer(self.tick)
        if value < self.previous:
            raise ValueError("Synthetic QPC went backwards")
        self.previous = value
        return value

    def wake(self, ci, pi, sequence):
        due = (ci * 4000 + pi * 1800 + sequence) * 1000
        self.tick = max(self.tick, due)
        if ci == pi == 0 and sequence == 20:
            if self.scenario == "wake-late":
                self.tick += 2500
            elif self.scenario == "deadline":
                self.tick = 1801000
            elif self.scenario == "qpc-backwards":
                self.tick -= 2000
            elif self.scenario == "interrupt":
                raise InterruptedError("Synthetic scheduler interrupted")
        return self.observe()

    def complete(self, ci, pi, sequence):
        if ci == pi == 0:
            if self.scenario == "response-late" and sequence == 20:
                self.tick += 2500
            elif self.scenario == "endpoint-late" and sequence == 1800:
                self.tick += 1
        return self.observe()


def _request(trial, role, phase, sequence, nonce, second):
    return {"trial": trial, "role": role, "phase": phase, "sequence": sequence,
            "nonce": nonce, "truth_second": second}


def _phase_start(ci, pi, trial, clock):
    return {"type": "phase-start", "role": raw.ROLES[ci], "phase": raw.PHASES[pi],
            "trial": trial, "identity": raw.identity_evidence(),
            "origin_tick": (ci * 4000 + pi * 1800) * 1000,
            "deadline_tick": (ci * 4000 + pi * 1800 + 1800) * 1000,
            "observed_tick": clock.observe()}


def _resume():
    return {"type": "resume",
            "before_identity_sha256": _clock_campaign_schedule_canonical_sha256(raw.identity_hashes(raw.identity_evidence())),
            "after_identity_sha256": _clock_campaign_schedule_canonical_sha256(raw.identity_hashes(raw.identity_evidence())),
            "windows_before_ns": (1800000000 + 3601) * raw.NS,
            "windows_after_ns": (1800000000 + 3999) * raw.NS,
            "guest_before_ns": (1800000000 + 3601) * raw.NS,
            "guest_after_ns": (1800000000 + 3999) * raw.NS}


def capture_scheduled(root, *, scenario, fault="none", reader_timeout=3.0, on_record=None):
    recipe = recipe_for(scenario)
    if fault not in raw.FAULTS:
        raise ValueError("Unknown fixture fault")
    root.mkdir()
    plan, manifest, sources = raw.make_plan(scheduled_recipe=recipe)
    raw.publish_json(root / "sources.json", sources)
    raw.publish_json(root / "plan.json", plan)
    plan_hash = _clock_campaign_schedule_canonical_sha256(plan)
    reader, chain, count = None, "0" * 64, 0
    dispatched = 0
    clock = FakeQPC(scenario)
    with (root / "journal.jsonl").open("xb") as journal:
        def append(data):
            nonlocal chain, count
            record = {"sequence": count, "previous_sha256": chain, "data": data}
            chain = _clock_campaign_schedule_canonical_sha256(record)
            journal.write(raw.encode({**record, "sha256": chain}))
            journal.flush()
            count += 1
            if on_record is not None:
                on_record(data)
        try:
            append({"type": "header", "kind": "synthetic-clock-scheduled-raw-v1",
                    "plan_sha256": plan_hash, "manifest": manifest,
                    "source": raw.SOURCE, "recipe": recipe})
            mask = _clock_campaign_schedule_signal.pthread_sigmask(_clock_campaign_schedule_signal.SIG_BLOCK, {_clock_campaign_schedule_signal.SIGINT, _clock_campaign_schedule_signal.SIGTERM})
            try:
                reader = raw.Reader(fault=fault, timeout=reader_timeout)
            finally:
                _clock_campaign_schedule_signal.pthread_sigmask(_clock_campaign_schedule_signal.SIG_SETMASK, mask)
            for ci, role in enumerate(raw.ROLES):
                trial = _clock_campaign_schedule_secrets.token_hex(16)
                # Synthetic resume advances the same counter; no new clock epoch.
                if ci:
                    clock.tick = max(clock.tick, ci * 4000000)
                for pi, phase in enumerate(raw.PHASES):
                    start = _phase_start(ci, pi, trial, clock)
                    append(start)
                    probes = []
                    lost = 0
                    for sequence in range(1 if pi else 0, 1801):
                        try:
                            observed = clock.wake(ci, pi, sequence)
                        except (ValueError, InterruptedError):
                            append({"type": "scheduler-error", "role": role, "phase": phase,
                                    "sequence": sequence, "previous_tick": clock.previous,
                                    "observed_tick": clock.tick})
                            raise
                        decision = slot_decision(start["origin_tick"], sequence, observed)
                        if decision["action"] == "missed":
                            append({"type": "missed", "sequence": sequence, "schedule": decision})
                            lost += 1
                            continue
                        second = decision["due_tick"] // 1000
                        request = _request(trial, role, phase, sequence, _clock_campaign_schedule_secrets.token_hex(16), second)
                        before = raw.guest_values(second, 0)
                        append({"type": "request", "request": request, "before": before, "schedule": decision})
                        response = reader.request(request)
                        completed = clock.complete(ci, pi, sequence)
                        limit = min(decision["due_tick"] + 1000, start["deadline_tick"])
                        late = completed > limit
                        after_ns = (completed - decision["due_tick"]) * raw.NS // 1000
                        after = raw.guest_values(second, max(1000, after_ns))
                        identity = raw.identity_evidence()
                        # Match the fixed reader's 21st actual request, even when
                        # earlier slots were skipped. Never inject only elsewhere.
                        if dispatched == 20:
                            if fault == "identity":
                                identity["process_set"][0]["start_tick"] += 1
                            elif fault == "namespace":
                                identity["process_set"][0]["time_namespace"] += 1
                            elif fault == "configuration":
                                identity["sync_configuration"]["active_bytes_hex"] = "00"
                        append({"type": "sample", "request": request, "before": before,
                                "response": response, "after": after, "identity": identity,
                                "completed_tick": completed, "late": late})
                        raw.validate_response(request, response, raw.FREQUENCY)
                        dispatched += 1
                        lost += int(late)
                        if sequence == 10:
                            probes = raw._probes({"realtime_after_ns": after["realtime"],
                                                  "monotonic_before_ns": before["monotonic"],
                                                  "monotonic_after_ns": after["monotonic"]})
                    append({"type": "phase-end", "role": role, "phase": phase,
                            "identity": raw.identity_evidence(), "observed_tick": clock.observe(),
                            "lost_samples": lost, "future_probes": probes})
                if ci == 0:
                    append(_resume())
            reader.close()
            reader = None
            append({"type": "end", "campaigns": 2})
            _clock_campaign_schedule_os.fsync(journal.fileno())
        except BaseException as error:
            if reader is not None:
                try:
                    reader.close()
                except (OSError, ValueError, _clock_campaign_schedule_subprocess.TimeoutExpired):
                    pass
            try:
                append({"type": "failure", "error_class": type(error).__name__,
                        "reader_reaped": reader is None or reader.process.poll() is not None})
                _clock_campaign_schedule_os.fsync(journal.fileno())
            except BaseException:
                pass
            raise
    payload = raw.read_stable(root / "journal.jsonl")
    raw.publish_json(root / "complete.json", {"kind": "synthetic-clock-complete-v1",
        "plan_sha256": plan_hash, "journal_sha256": _clock_campaign_schedule_hashlib.sha256(payload).hexdigest(),
        "journal_bytes": len(payload), "records": count, "last_sha256": chain, "reader_reaped": True})
    return plan_hash


def _blank(index):
    return {**{k: None for k in _clock_campaign_schedule_BASE_FIELDS | _clock_campaign_schedule_EXTRA_FIELDS}, "index": index}


def replay_scheduled(cursor, result, recipe):
    """Recompute each fixed slot/decision/cutoff from raw virtual observations.

    The recipe scripts only the fake clock. Original response/identity violations
    are retained independently, never replaced with regenerated successful values.
    """
    validate_recipe(recipe)
    clock, nonces, trials = FakeQPC(recipe["scenario"]), set(), set()
    for ci, role in enumerate(raw.ROLES):
        campaign = {"role": role, "trial_sha256": None, "phases": []}
        shared = shared_reference = None
        shared_late = False
        if ci:
            clock.tick = max(clock.tick, ci * 4000000)
        for pi, phase in enumerate(raw.PHASES):
            start = next(cursor)
            raw.object_exact(start, {"type", "role", "phase", "trial", "identity",
                                     "origin_tick", "deadline_tick", "observed_tick"})
            trial = raw.token(start["trial"])
            expected = _phase_start(ci, pi, trial, clock)
            # Actual identity may change; the numerical reviewer must see it.
            if raw.encode({**start, "identity": expected["identity"]}) != raw.encode(expected):
                raise ValueError("Fixed phase boundary changed")
            if not pi:
                if trial in trials:
                    raise ValueError("Trial reused")
                trials.add(trial)
                campaign["trial_sha256"] = _clock_campaign_schedule_canonical_sha256(trial)
            elif campaign["trial_sha256"] != _clock_campaign_schedule_canonical_sha256(trial):
                raise ValueError("Trial changed")
            data = {"phase": phase, "identity_start": raw.identity_hashes(start["identity"]),
                    "identity_end": None, "samples": [], "external_utc": [], "qpc_calibration": [],
                    "future_probes": [], "lost_samples": 0, "interrupted": False}
            references = []
            boundary_lost = int(bool(pi) and (shared_late or shared["qpc_tick"] is None))
            if pi:
                data["samples"].append({**shared, "index": 0})
                references.append(shared_reference)
                data["lost_samples"] += boundary_lost
            for sequence in range(1 if pi else 0, 1801):
                decision = slot_decision(start["origin_tick"], sequence, clock.wake(ci, pi, sequence))
                intent = next(cursor)
                if decision["action"] == "missed":
                    if raw.encode(intent) != raw.encode({"type": "missed", "sequence": sequence,
                                                         "schedule": decision}):
                        raise ValueError("Missing fixed-slot loss record")
                    data["samples"].append(_blank(sequence))
                    references.append(None)
                    data["lost_samples"] += 1
                    shared_late = False
                    continue
                raw.object_exact(intent, {"type", "request", "before", "schedule"})
                if intent["type"] != "request" or raw.encode(intent["schedule"]) != raw.encode(decision):
                    raise ValueError("Off-lattice dispatch or deadline changed")
                request = intent["request"]
                raw.object_exact(request, {"trial", "role", "phase", "sequence", "nonce", "truth_second"})
                nonce = raw.token(request["nonce"])
                expected_request = _request(trial, role, phase, sequence, nonce, decision["due_tick"] // 1000)
                if nonce in nonces or raw.encode(request) != raw.encode(expected_request):
                    raise ValueError("Scheduled challenge changed")
                nonces.add(nonce)
                record = next(cursor)
                raw.object_exact(record, {"type", "request", "before", "response", "after", "identity",
                                          "completed_tick", "late"})
                if (record["type"] != "sample" or raw.encode(record["request"]) != raw.encode(request)
                        or raw.encode(record["before"]) != raw.encode(intent["before"])):
                    raise ValueError("Scheduled response does not match saved intent")
                completed = clock.complete(ci, pi, sequence)
                late = completed > min(decision["due_tick"] + 1000, start["deadline_tick"])
                if (raw.integer(record["completed_tick"]) != completed or type(record["late"]) is not bool
                        or record["late"] != late):
                    raise ValueError("Late response concealed")
                # The virtual guest brackets must reflect the actual scripted completion.
                second = decision["due_tick"] // 1000
                after_ns = max(1000, (completed - decision["due_tick"]) * raw.NS // 1000)
                if (raw.encode(record["before"]) != raw.encode(raw.guest_values(second, 0)) or
                        raw.encode(record["after"]) != raw.encode(raw.guest_values(second, after_ns))):
                    raise ValueError("Scheduled guest bracket changed")
                raw.validate_response(request, record["response"], raw.FREQUENCY)
                sample = {"index": sequence, "qpc_tick": record["response"]["qpc_tick"],
                          "identity_sha256": _clock_campaign_schedule_canonical_sha256(raw.identity_hashes(record["identity"]))}
                for edge in ("before", "after"):
                    for key, value in record[edge].items():
                        sample[f"{key}_{edge}_ns"] = raw.integer(value)
                    sample[f"windows_utc_{edge}_ns"] = record["response"][f"utc_{edge}_ns"]
                data["samples"].append(sample)
                references.append(record["response"]["reference"])
                data["lost_samples"] += int(late)
                shared_late = late
            end = next(cursor)
            raw.object_exact(end, {"type", "role", "phase", "identity", "observed_tick",
                                   "lost_samples", "future_probes"})
            # Shared lost boundary belongs to both phases but was not requested twice.
            own_lost = data["lost_samples"] - boundary_lost
            if (end["type"] != "phase-end" or end["role"] != role or end["phase"] != phase
                    or raw.integer(end["observed_tick"]) != clock.observe()
                    or raw.integer(end["lost_samples"]) != own_lost):
                raise ValueError("Phase accounting or finish changed")
            data["identity_end"] = raw.identity_hashes(end["identity"])
            for second in range(0, 1801, 60):
                ref = references[second]
                if ref is not None:
                    data["external_utc"].append({"boundary_seconds": second, "sample_index": second,
                        "lower_ns": ref["utc_center_ns"] - ref["uncertainty_ns"],
                        "upper_ns": ref["utc_center_ns"] + ref["uncertainty_ns"],
                        "source_sha256": result["plan"]["utc_source_sha256"]})
            for a, b in [(s, s + 60) for s in range(0, 1800, 60)] + [(0, 1800)]:
                if references[a] is None or references[b] is None:
                    continue
                elapsed = references[b]["elapsed_counter_ns"] - references[a]["elapsed_counter_ns"]
                uncertainty = references[a]["uncertainty_ns"] + references[b]["uncertainty_ns"]
                data["qpc_calibration"].append({"first_index": a, "last_index": b,
                    "reference_lower_ns": elapsed - uncertainty, "reference_upper_ns": elapsed + uncertainty,
                    "source_sha256": result["plan"]["calibration_source_sha256"]})
            data["future_probes"] = end["future_probes"]
            shared, shared_reference = data["samples"][-1], references[-1]
            campaign["phases"].append({"sha256": _clock_campaign_schedule_canonical_sha256(data), "data": data})
        result["campaigns"].append(campaign)
        if ci == 0:
            resume = next(cursor)
            if raw.encode(resume) != raw.encode(_resume()):
                raise ValueError("Synthetic resume changed")
            result["resume"] = {k: v for k, v in resume.items() if k != "type"}
    if (raw.encode(next(cursor)) != raw.encode({"type": "end", "campaigns": 2})
            or next(cursor, None) is not None):
        raise ValueError("Scheduled journal tail changed")


# ========================================================================
# clock_raw_bundle
# ========================================================================

"""Portable synthetic raw evidence only; never execute archived code or qualify clocks."""


import hashlib as _clock_raw_bundle_hashlib
import io
import os as _clock_raw_bundle_os
from pathlib import Path as _clock_raw_bundle_Path
import stat as _clock_raw_bundle_stat
import tempfile as _clock_raw_bundle_tempfile
import zipfile

from bike_mlops import clock as _clock_raw_bundle_raw

KIND = "synthetic-clock-raw-bundle-v1"
LIMIT = _clock_raw_bundle_raw.MAX_BYTES
ZIP_LIMIT = LIMIT + 65536
MANIFEST_LIMIT = 16384
NAMES = {"manifest.json", *("raw/" + name for name in _clock_raw_bundle_raw.MEMBERS)}


def _sha(payload):
    return _clock_raw_bundle_hashlib.sha256(payload).hexdigest()


def _clock_raw_bundle_identity(path):
    s = path.stat(follow_symlinks=False)
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def _replay(payloads, plan_sha256):
    # Write only fixed names to a private directory; never ZipFile.extract*().
    with _clock_raw_bundle_tempfile.TemporaryDirectory(prefix="synthetic-clock-bundle-") as directory:
        root = _clock_raw_bundle_Path(directory)
        for name in _clock_raw_bundle_raw.MEMBERS:
            (root / name).write_bytes(payloads["raw/" + name])
        return _clock_raw_bundle_raw.derive_raw(root, expected_plan_sha256=plan_sha256)


def _validate(payloads, plan_sha256):
    _clock_raw_bundle_raw.digest(plan_sha256)
    manifest = _clock_raw_bundle_raw.decode(payloads["manifest.json"])
    _clock_raw_bundle_raw.object_exact(manifest, {"kind", "plan_sha256", "members"})
    if manifest["kind"] != KIND or manifest["plan_sha256"] != plan_sha256:
        raise ValueError("Synthetic bundle plan mismatch")
    _clock_raw_bundle_raw.object_exact(manifest["members"], NAMES - {"manifest.json"})
    for name, entry in manifest["members"].items():
        _clock_raw_bundle_raw.object_exact(entry, {"bytes", "sha256"})
        _clock_raw_bundle_raw.integer(entry["bytes"])
        _clock_raw_bundle_raw.digest(entry["sha256"])
        if entry != {"bytes": len(payloads[name]), "sha256": _sha(payloads[name])}:
            raise ValueError("Synthetic bundle member mismatch")
    # Replay verifies synthetic schemas, nonce/sequence, sources, chain and marker,
    # as well as retaining known violations and missingness in the numerical review.
    campaign, report = _replay(payloads, plan_sha256)
    return campaign, report


def _zip_bytes(payloads):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(payloads):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (_clock_raw_bundle_stat.S_IFREG | 0o600) << 16
            archive.writestr(info, payloads[name])
    return output.getvalue()


def _publish(path, payload):
    temporary = None
    try:
        with _clock_raw_bundle_tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
            temporary = _clock_raw_bundle_Path(stream.name)
            stream.write(payload)
            stream.flush()
            _clock_raw_bundle_os.fsync(stream.fileno())
        _clock_raw_bundle_os.link(temporary, path)  # No overwrite, including an existing broken symlink.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_bundle(root, output, *, expected_plan_sha256):
    """Export complete fixture raw only; refuse arbitrary saved source bytes."""
    _clock_raw_bundle_raw.digest(expected_plan_sha256)
    if root.is_symlink() or not root.is_dir() or {p.name for p in root.iterdir()} != _clock_raw_bundle_raw.MEMBERS:
        raise ValueError("Incomplete synthetic raw")
    if root.resolve() in output.resolve().parents:
        raise ValueError("Bundle must be outside raw evidence")
    before = {name: _clock_raw_bundle_identity(root / name) for name in _clock_raw_bundle_raw.MEMBERS}
    payloads = {"raw/" + name: _clock_raw_bundle_raw.read_stable(root / name) for name in _clock_raw_bundle_raw.MEMBERS}
    if sum(map(len, payloads.values())) > LIMIT:
        raise ValueError("Synthetic bundle too large")
    # Narrow export allowlist: do not publish substituted code, credentials or
    # arbitrary private source text merely because an attacker rehashed it.
    saved = _clock_raw_bundle_raw.decode(payloads["raw/sources.json"])
    _, _, allowed = _clock_raw_bundle_raw.make_plan()
    if saved != allowed:
        raise ValueError("Export supports installed synthetic source bytes only")
    manifest = {"kind": KIND, "plan_sha256": expected_plan_sha256,
                "members": {name: {"bytes": len(data), "sha256": _sha(data)}
                            for name, data in payloads.items()}}
    payloads["manifest.json"] = _clock_raw_bundle_raw.encode(manifest)
    _validate(payloads, expected_plan_sha256)
    if ({p.name for p in root.iterdir()} != _clock_raw_bundle_raw.MEMBERS
            or any(_clock_raw_bundle_identity(root / name) != before[name]
                   or _clock_raw_bundle_raw.read_stable(root / name) != payloads["raw/" + name]
                   for name in _clock_raw_bundle_raw.MEMBERS)):
        raise ValueError("Synthetic originals changed during export")
    data = _zip_bytes(payloads)
    _publish(output, data)
    return {"kind": KIND, "archive_sha256": _sha(data), "archive_bytes": len(data),
            "plan_sha256": expected_plan_sha256, "synthetic_only": True,
            "clock_qualification_status": "incomplete"}


def review_bundle(path, *, expected_plan_sha256):
    """Recompute only from a stable ZIP snapshot. No saved code, path or URL executes."""
    _clock_raw_bundle_raw.digest(expected_plan_sha256)
    data = _clock_raw_bundle_raw.read_stable(path, ZIP_LIMIT)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except NotImplementedError:
        # Required ZIP versions are parsed before our member checks. Treat an
        # unsupported format as invalid input, not a verifier/qualification failure.
        raise ValueError("Unsupported synthetic ZIP version") from None
    with archive:
        infos = archive.infolist()
        if len(infos) != len(NAMES) or {item.filename for item in infos} != NAMES:
            raise ValueError("Unexpected synthetic bundle members")
        if sum(item.file_size for item in infos if item.filename != "manifest.json") > LIMIT:
            raise ValueError("Synthetic bundle too large")
        payloads = {}
        for item in infos:
            maximum = MANIFEST_LIMIT if item.filename == "manifest.json" else LIMIT
            if (item.is_dir() or item.create_system != 3
                    or not _clock_raw_bundle_stat.S_ISREG(item.external_attr >> 16)
                    or item.flag_bits != 0 or item.compress_type != zipfile.ZIP_STORED
                    or item.file_size > maximum or item.compress_size != item.file_size):
                raise ValueError("Invalid synthetic ZIP member")
            payloads[item.filename] = archive.read(item)
    # Only our deterministic stored representation is supported. Reject ZIP
    # comments, hidden prefix/trailing bytes, extra fields and metadata channels.
    if _zip_bytes(payloads) != data:
        raise ValueError("Noncanonical synthetic ZIP")
    campaign, report = _validate(payloads, expected_plan_sha256)
    return {"kind": KIND, "archive_sha256": _sha(data), "archive_bytes": len(data),
            "plan_sha256": expected_plan_sha256,
            "campaign_sha256": _clock_raw_bundle_raw._clock_campaign_capture_canonical_sha256(campaign),
            "synthetic_only": True, "members": {
                name: {"bytes": len(value), "sha256": _sha(value)}
                for name, value in payloads.items()}, "review": report}


# ========================================================================
# clock_campaign_fake_reader
# ========================================================================

"""Fixed synthetic host/external reader subprocess. No OS clock/source access."""
import sys as _clock_campaign_fake_reader_sys
import signal as _clock_campaign_fake_reader_signal
import time as _clock_campaign_fake_reader_time

_clock_campaign_fake_reader_FAULTS = FAULTS
_clock_campaign_fake_reader_decode = decode
_clock_campaign_fake_reader_encode = encode
_clock_campaign_fake_reader_fake_response = fake_response


def main():
    # Only this fixture child: do not retain the parent's short startup mask.
    _clock_campaign_fake_reader_signal.pthread_sigmask(_clock_campaign_fake_reader_signal.SIG_UNBLOCK, {_clock_campaign_fake_reader_signal.SIGINT, _clock_campaign_fake_reader_signal.SIGTERM})
    if len(_clock_campaign_fake_reader_sys.argv) != 2 or _clock_campaign_fake_reader_sys.argv[1] not in _clock_campaign_fake_reader_FAULTS:
        return 2
    fault = _clock_campaign_fake_reader_sys.argv[1]
    for index, line in enumerate(_clock_campaign_fake_reader_sys.stdin.buffer):
        if len(line) > 16384:
            return 2
        request = _clock_campaign_fake_reader_decode(line)
        response = _clock_campaign_fake_reader_fake_response(request)
        if index == 20:
            if fault == "delay":
                _clock_campaign_fake_reader_time.sleep(60)  # Terminated/reaped by the fixture controller.
            if fault == "exit":
                return 3
            if fault == "nonce":
                response["echo"]["nonce"] = "0" * 32
            if fault == "sequence":
                response["echo"]["sequence"] += 1
            if fault == "trial":
                response["echo"]["trial"] = "0" * 32
            if fault == "frequency":
                response["qpc_frequency_hz"] += 1
            if fault == "reverse":
                response["utc_before_ns"] = 0
            if fault == "missing":
                response.pop("qpc_tick")
        payload = _clock_campaign_fake_reader_encode(response)
        if index == 20 and fault == "duplicate":
            payload += payload
        _clock_campaign_fake_reader_sys.stdout.buffer.write(payload)
        _clock_campaign_fake_reader_sys.stdout.buffer.flush()
    if fault == "trailing":
        _clock_campaign_fake_reader_sys.stdout.buffer.write(b'{"unsolicited":true}\n')
        _clock_campaign_fake_reader_sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    main()
