"""Offline, untrusted campaign evidence inspection; never grant clock qualification."""
from __future__ import annotations

import copy
from fractions import Fraction
import hashlib
import json
import re

from bike_mlops.clock_rate_review import POLICY_SHA256 as RATE_POLICY_SHA256
from bike_mlops.clock_rate_review import review_clock_rate_phase


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


POLICY = {
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
POLICY_SHA256 = canonical_sha256(POLICY)
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


def _integer(value, minimum=0):
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
    frequency = _integer(plan["qpc_frequency_hz"], 1)
    for digest in (value["policy_sha256"], value["plan_sha256"]):
        _hash(digest)
    # Compare serialized representations as well as hashes: bool cannot stand in for integer.
    if (canonical_sha256(value["policy"]) != POLICY_SHA256 or value["policy_sha256"] != POLICY_SHA256
            or plan["policy_sha256"] != POLICY_SHA256):
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
        if campaign["role"] != POLICY["campaigns"][ci]:
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
            if data["phase"] != POLICY["phases"][pi]:
                raise ValueError("Phase order mismatch")
            where = {"campaign": ci, "phase": pi}
            _identity(data["identity_start"])
            _identity(data["identity_end"])
            start, end = data["identity_start"], data["identity_end"]
            if start is None or end is None:
                issue("identity_missing", **where)
            elif start != end:
                issue("identity_changed", "failed", **where)
            if _integer(data["lost_samples"]):
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
                        _integer(sample[key])
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
                rate = review_clock_rate_phase({"schema_version": 1, "kind": "clock-rate-phase-v1",
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
            if not complete_base and len(boundaries) > 1:
                # Missing unrelated values do not erase an observed rate violation.
                pairs = list(zip(list(boundaries.values()), list(boundaries.values())[1:]))
                if 1800 in boundaries:
                    pairs.append((0, boundaries[1800]))
                for first, last in pairs:
                    if first == last:
                        continue
                    elapsed = Fraction(samples[last]["qpc_tick"] - samples[first]["qpc_tick"], frequency)
                    for clock in ("monotonic", "raw"):
                        b0, a0 = (samples[first][f"{clock}_{edge}_ns"] for edge in ("before", "after"))
                        b1, a1 = (samples[last][f"{clock}_{edge}_ns"] for edge in ("before", "after"))
                        if all(v is not None for v in (b0, a0, b1, a1)) and b0 <= a0 < b1 <= a1:
                            check_bounds("partial_elapsed_rate", Fraction(b1 - a0, 10**9) / elapsed,
                                         Fraction(a1 - b0, 10**9) / elapsed, Fraction(999, 1000),
                                         Fraction(1001, 1000), clock=clock, first_index=first, last_index=last, **where)
            if len(boundaries) != 31:
                issue("coverage_missing", **where)
            utc_seen = set()
            for item in _list(data["external_utc"], 31):
                _object(item, {"boundary_seconds", "sample_index", "lower_ns", "upper_ns", "source_sha256"})
                seconds, index = _integer(item["boundary_seconds"]), _integer(item["sample_index"])
                lower, upper = _integer(item["lower_ns"]), _integer(item["upper_ns"])
                _hash(item["source_sha256"])
                if seconds in utc_seen or seconds not in range(0, 1801, 60):
                    issue("utc_boundary_duplicate_or_extra", "failed", **where)
                utc_seen.add(seconds)
                if item["source_sha256"] != plan["utc_source_sha256"]:
                    issue("utc_source_changed", "failed", **where)
                if seconds not in boundaries:
                    issue("utc_sample_unobserved", **where)
                    continue
                if boundaries[seconds] != index:
                    issue("utc_wrong_sample", "failed", **where)
                    continue
                if lower > upper:
                    issue("utc_reference_reversed", "failed", **where)
                    continue
                for clock in ("windows_utc", "realtime"):
                    b, a = samples[index][f"{clock}_before_ns"], samples[index][f"{clock}_after_ns"]
                    if b is not None and a is not None:
                        check_bounds("external_utc_offset", Fraction(b - upper), Fraction(a - lower),
                                     Fraction(-10**9), Fraction(10**9), boundary_seconds=seconds, clock=clock, **where)
            if utc_seen != set(range(0, 1801, 60)):
                issue("utc_boundaries_missing", **where)
            expected_pairs = {(boundaries[a], boundaries[b]) for a, b in zip(range(0, 1800, 60), range(60, 1801, 60))
                              if a in boundaries and b in boundaries and boundaries[a] != boundaries[b]}
            if 1800 in boundaries:
                expected_pairs.add((0, boundaries[1800]))
            cal_seen = set()
            for item in _list(data["qpc_calibration"], 31):
                _object(item, {"first_index", "last_index", "reference_lower_ns", "reference_upper_ns", "source_sha256"})
                pair = (_integer(item["first_index"]), _integer(item["last_index"]))
                lower, upper = _integer(item["reference_lower_ns"], 1), _integer(item["reference_upper_ns"], 1)
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
                    q = Fraction(samples[pair[1]]["qpc_tick"] - samples[pair[0]]["qpc_tick"], frequency)
                    check_bounds("qpc_calibration", q * 10**9 / upper, q * 10**9 / lower,
                                 Fraction(9999, 10000), Fraction(10001, 10000), first_index=pair[0], last_index=pair[1], **where)
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
                delta = _integer(probe["generated_utc_ns"]) - _integer(probe["validated_utc_ns"])
                if not isinstance(probe["decision"], str) or probe["decision"] not in {"allow", "reject"}:
                    raise ValueError("Invalid future decision")
                for key in {"blocked_before_sha256", "blocked_after_sha256", "tags_before_sha256", "tags_after_sha256"}:
                    _hash(probe[key])
                if _integer(probe["generated_monotonic_ns"]) > _integer(probe["validated_monotonic_ns"]):
                    issue("future_probe_monotonic_backwards", "failed", **where)
                index = probe["sample_index"]
                if case == "live":
                    if index is None or _integer(index) >= len(samples):
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
                _integer(resume[key])
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
            "policy": copy.deepcopy(POLICY), "policy_sha256": POLICY_SHA256,
            "plan_sha256": value["plan_sha256"], "offline_checks_status": status,
            "clock_qualification_status": "failed" if status == "failed" else "incomplete",
            "qualification_prerequisites_not_established": list(PREREQUISITES),
            "issues": issues, "phases": phase_reports, "calculations": calculations}
