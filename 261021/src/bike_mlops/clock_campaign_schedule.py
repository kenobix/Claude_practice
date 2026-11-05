"""Virtual QPC scheduler and raw replay. No real timer, source, or workload.

The fake counter advances by script, never by local UTC or elapsed test time.
OS MONOTONIC in the fixed pipe reader remains a separate transport safety limit.
"""
from __future__ import annotations

import copy
import hashlib
import os
import secrets
import signal
import subprocess

from bike_mlops import clock_campaign_capture as raw
from bike_mlops.clock_campaign_review import BASE_FIELDS, EXTRA_FIELDS, canonical_sha256

SCENARIOS = {"punctual", "wake-late", "response-late", "deadline", "endpoint-late",
             "qpc-backwards", "interrupt"}
SCHEDULER = {"kind": "fake-qpc-fixed-period-v1", "period_ticks": 1000,
             "phase_ticks": 1800000, "frequency_hz": 1000,
             "dispatch_lateness_ticks": 0, "catch_up": False, "extend_phase": False}


def recipe_for(scenario):
    if type(scenario) is not str or scenario not in SCENARIOS:
        raise ValueError("Unknown synthetic schedule")
    return {**raw.RECIPE, "scheduler": copy.deepcopy(SCHEDULER), "scenario": scenario}


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
            "before_identity_sha256": canonical_sha256(raw.identity_hashes(raw.identity_evidence())),
            "after_identity_sha256": canonical_sha256(raw.identity_hashes(raw.identity_evidence())),
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
    plan_hash = canonical_sha256(plan)
    reader, chain, count = None, "0" * 64, 0
    dispatched = 0
    clock = FakeQPC(scenario)
    with (root / "journal.jsonl").open("xb") as journal:
        def append(data):
            nonlocal chain, count
            record = {"sequence": count, "previous_sha256": chain, "data": data}
            chain = canonical_sha256(record)
            journal.write(raw.encode({**record, "sha256": chain}))
            journal.flush()
            count += 1
            if on_record is not None:
                on_record(data)
        try:
            append({"type": "header", "kind": "synthetic-clock-scheduled-raw-v1",
                    "plan_sha256": plan_hash, "manifest": manifest,
                    "source": raw.SOURCE, "recipe": recipe})
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
            try:
                reader = raw.Reader(fault=fault, timeout=reader_timeout)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            for ci, role in enumerate(raw.ROLES):
                trial = secrets.token_hex(16)
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
                        request = _request(trial, role, phase, sequence, secrets.token_hex(16), second)
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
            os.fsync(journal.fileno())
        except BaseException as error:
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
                pass
            raise
    payload = raw.read_stable(root / "journal.jsonl")
    raw.publish_json(root / "complete.json", {"kind": "synthetic-clock-complete-v1",
        "plan_sha256": plan_hash, "journal_sha256": hashlib.sha256(payload).hexdigest(),
        "journal_bytes": len(payload), "records": count, "last_sha256": chain, "reader_reaped": True})
    return plan_hash


def _blank(index):
    return {**{k: None for k in BASE_FIELDS | EXTRA_FIELDS}, "index": index}


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
                campaign["trial_sha256"] = canonical_sha256(trial)
            elif campaign["trial_sha256"] != canonical_sha256(trial):
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
                          "identity_sha256": canonical_sha256(raw.identity_hashes(record["identity"]))}
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
            campaign["phases"].append({"sha256": canonical_sha256(data), "data": data})
        result["campaigns"].append(campaign)
        if ci == 0:
            resume = next(cursor)
            if raw.encode(resume) != raw.encode(_resume()):
                raise ValueError("Synthetic resume changed")
            result["resume"] = {k: v for k, v in resume.items() if k != "type"}
    if (raw.encode(next(cursor)) != raw.encode({"type": "end", "campaigns": 2})
            or next(cursor, None) is not None):
        raise ValueError("Scheduled journal tail changed")
