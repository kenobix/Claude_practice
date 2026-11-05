"""Synthetic acquisition protocol only. Never collect or qualify a real clock.

The child reader is a fixed fixture, not an operator-provided command or endpoint.
Raw records are replayable without the child, OS clocks, or a service connection.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

from bike_mlops.clock_campaign_review import (
    POLICY, POLICY_SHA256, canonical_sha256, review_clock_campaign,
)

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
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode() + b"\n"


def object_exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("Invalid synthetic protocol schema")


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid synthetic integer")
    return value


def digest(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Invalid digest")
    return value


def token(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{32}", value) is None:
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
    return json.loads(payload, object_pairs_hook=_pairs,
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
            re.fullmatch(r"[0-9a-f]{0,128}", config["active_bytes_hex"]) is None or config["includes"] != []):
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
    return {name + "_sha256": canonical_sha256(original) for name, original in value.items()}


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
    return {"echo": copy.deepcopy(request), "read_order": ["utc-before", "qpc", "utc-after"],
            "qpc_tick": t * FREQUENCY, "qpc_frequency_hz": FREQUENCY,
            "utc_before_ns": (1800000000 + t) * NS + 200,
            "utc_after_ns": (1800000000 + t) * NS + 800,
            "reference": {"source": copy.deepcopy(SOURCE), "utc_center_ns": (1800000000 + t) * NS,
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
            self.process = subprocess.Popen([sys.executable, "-m", "bike_mlops.clock_campaign_fake_reader", fault],
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


def make_plan():
    # Hash the exact two execution modules plus offline dependency implementations.
    files = ("clock_campaign_capture.py", "clock_campaign_fake_reader.py",
             "clock_campaign_review.py", "clock_rate_review.py")
    sources = {name: Path(__file__).with_name(name).read_bytes().hex() for name in files}
    for name in ("capture_fake_clock_campaign.py", "derive_clock_campaign.py"):
        sources[name] = (Path(__file__).resolve().parents[2] / "scripts" / name).read_bytes().hex()
    manifest = {"kind": "synthetic-module-manifest-v1", "files": {
        name: hashlib.sha256(bytes.fromhex(payload)).hexdigest() for name, payload in sources.items()}}
    manifest["python_version"] = list(sys.version_info[:3])
    plan = {"policy_sha256": POLICY_SHA256, "collector_sha256": canonical_sha256(manifest),
            "workload_sha256": canonical_sha256(RECIPE), "utc_reader_sha256": canonical_sha256(manifest),
            "utc_source_sha256": canonical_sha256(SOURCE), "calibration_source_sha256": canonical_sha256(SOURCE),
            "qpc_frequency_hz": FREQUENCY}
    return plan, manifest, sources


def capture_fake(root, *, fault="none", reader_timeout=3.0, on_record=None):
    """Exclusive synthetic attempt. Partial journal survives failures; no complete marker.

    on_record is an in-process test injection, not a CLI command/plugin surface.
    No workload, real source, or real collector exists behind this fixture entry.
    """
    root.mkdir()  # Caller selects a new path; never delete/reuse an existing one.
    plan, manifest, sources = make_plan()
    publish_json(root / "sources.json", sources)
    publish_json(root / "plan.json", plan)
    plan_hash = canonical_sha256(plan)
    reader, chain, count = None, "0" * 64, 0
    with (root / "journal.jsonl").open("xb") as journal:
        def append(data):
            nonlocal chain, count
            record = {"sequence": count, "previous_sha256": chain, "data": data}
            chain = canonical_sha256(record)
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
                    append({"type": "resume", "before_identity_sha256": canonical_sha256(identity_hashes(identity_evidence())),
                            "after_identity_sha256": canonical_sha256(identity_hashes(identity_evidence())),
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
              "journal_sha256": hashlib.sha256(payload).hexdigest(), "journal_bytes": len(payload),
              "records": count, "last_sha256": chain, "reader_reaped": True}
    publish_json(root / "complete.json", marker)
    return plan_hash


def _probes(sample):
    state = canonical_sha256({"blocked": True, "synthetic": True})
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
    if integer(plan["qpc_frequency_hz"]) != FREQUENCY or plan["policy_sha256"] != POLICY_SHA256:
        raise ValueError("Unsupported synthetic plan")
    if canonical_sha256(plan) != expected_plan_sha256:
        raise ValueError("Plan binding mismatch")
    object_exact(marker, {"kind", "plan_sha256", "journal_sha256", "journal_bytes", "records",
                          "last_sha256", "reader_reaped"})
    raw = payloads["journal.jsonl"]
    if (marker["kind"] != "synthetic-clock-complete-v1" or marker["reader_reaped"] is not True
            or marker["plan_sha256"] != expected_plan_sha256
            or marker["journal_sha256"] != hashlib.sha256(raw).hexdigest()
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
        if canonical_sha256(row) != previous:
            raise ValueError("Journal chain mismatch")
        records.append(row["data"])
    if marker["last_sha256"] != previous:
        raise ValueError("Journal final hash mismatch")
    cursor = iter(records)
    header = next(cursor)
    object_exact(header, {"type", "kind", "plan_sha256", "manifest", "source", "recipe"})
    if (header["type"] != "header" or header["kind"] != "synthetic-clock-raw-v1"
            or header["plan_sha256"] != expected_plan_sha256
            or encode(header["source"]) != encode(SOURCE) or encode(header["recipe"]) != encode(RECIPE)):
        raise ValueError("Synthetic header mismatch")
    manifest = header["manifest"]
    object_exact(manifest, {"kind", "files", "python_version"})
    object_exact(manifest["files"], {"clock_campaign_capture.py", "clock_campaign_fake_reader.py",
                                    "clock_campaign_review.py", "clock_rate_review.py",
                                    "capture_fake_clock_campaign.py", "derive_clock_campaign.py"})
    if manifest["kind"] != "synthetic-module-manifest-v1":
        raise ValueError("Unsupported manifest")
    sources = decode(payloads["sources.json"])
    object_exact(sources, manifest["files"])
    for name, value in manifest["files"].items():
        digest(value)
        if type(sources[name]) is not str or len(sources[name]) > 256 * 1024:
            raise ValueError("Invalid source bytes")
        if hashlib.sha256(bytes.fromhex(sources[name])).hexdigest() != value:
            raise ValueError("Source bytes mismatch")
    if type(manifest["python_version"]) is not list or len(manifest["python_version"]) != 3:
        raise ValueError("Invalid runtime version")
    for value in manifest["python_version"]:
        integer(value)
    for key, original in (("collector_sha256", manifest), ("utc_reader_sha256", manifest),
                          ("workload_sha256", RECIPE), ("utc_source_sha256", SOURCE),
                          ("calibration_source_sha256", SOURCE)):
        if plan[key] != canonical_sha256(original):
            raise ValueError("Recipe/source binding mismatch")
    result = {"schema_version": 1, "kind": "clock-campaign-v1", "policy": copy.deepcopy(POLICY),
              "policy_sha256": POLICY_SHA256, "plan": plan, "plan_sha256": expected_plan_sha256,
              "campaigns": [], "resume": None}
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
                campaign["trial_sha256"] = canonical_sha256(trial)
            elif campaign["trial_sha256"] != canonical_sha256(trial):
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
                          "identity_sha256": canonical_sha256(identity_hashes(record["identity"]))}
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
            campaign["phases"].append({"sha256": canonical_sha256(data), "data": data})
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
    report = review_clock_campaign(result, expected_plan_sha256=expected_plan_sha256)
    # Re-read each saved file after all computation, catching visible replacement/changes.
    if ({p.name for p in root.iterdir()} != MEMBERS
            or any(read_stable(paths[n]) != p for n, p in payloads.items())
            or any(identity(paths[n].stat(follow_symlinks=False)) != initial_identity[n] for n in MEMBERS)):
        raise ValueError("Evidence changed during replay")
    return result, report
