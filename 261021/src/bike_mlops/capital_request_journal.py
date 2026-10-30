"""Durable, append-only request intent and outcome evidence; never replay requests."""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

JOURNAL_POLICY = {
    "policy_id": "capital-request-journal-v1",
    "durability": "header_and_each_event_fsync_before_next_action_parent_fsync_on_create",
    "http_latency": "request_callable_only_including_transport_json_decode_excluding_observer_and_journal",
    "http_timeout_scope": "httpx_connect_read_write_pool_each_not_total_deadline",
    "whole_elapsed": "request_loop_including_observer_journal_no_overall_threshold",
    "timeout_outcome": "unknown_may_commit_later_never_retry_in_attempt",
    "interrupted_attempt": "retain_all_evidence_reasoned_review_new_uuid_only",
}


def _bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _hash(value) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def request_schedule(attempt_id: str, plan: dict) -> list[dict]:
    if str(uuid.UUID(attempt_id)) != attempt_id:
        raise ValueError("Journal requires a canonical attempt UUID")
    start = datetime.fromisoformat(plan["input_window"]["start_inclusive"])
    result = []
    for phase, count in (("warmup", 50), ("measured", 1000)):
        for index in range(count):
            target = (start + timedelta(hours=index % 168)).isoformat(sep=" ")
            result.append({"request_id": f"capital-op:{attempt_id}:{phase}:{index:04d}",
                           "target_hour": target, "as_of": target,
                           **{key: plan["identity"][key] for key in
                              ("data_profile_id", "target_contract_id", "feature_contract_version")}})
    return result


class RequestJournal:
    """One writer owned by the candidate lease; any write failure prevents further sends."""

    def __init__(self, path: Path, attempt, plan: dict):
        self.path = path
        self.schedule = request_schedule(attempt.attempt_id, plan)
        self.index = 0
        self.pending = False
        self.sequence = 0
        self.previous = None
        self.fsync_ms = 0.0
        self.poisoned = False
        # Persist new directory entries too, rather than only the journal inode.
        absent = []
        ancestor = path.parent
        while not ancestor.exists():
            absent.append(ancestor)
            ancestor = ancestor.parent
        for directory_path in reversed(absent):
            directory_path.mkdir()
            parent_fd = os.open(directory_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            self._append({"event": "schedule", "schema_version": 1, "attempt_id": attempt.attempt_id,
                          "candidate_id": attempt.candidate_id, "plan_sha256": attempt.plan_sha256,
                          "plan_file_sha256": attempt.plan_file_sha256, "policy": JOURNAL_POLICY,
                          "requests": self.schedule})
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            self.close()
            raise

    def _append(self, value: dict) -> None:
        if self.poisoned or self.fd is None:
            raise RuntimeError("Request journal is unavailable")
        body = {**value, "sequence": self.sequence, "previous_sha256": self.previous,
                "recorded_at_utc": datetime.now(timezone.utc).isoformat()}
        encoded = _bytes({**body, "event_sha256": _hash(body)})
        began = time.perf_counter()
        try:
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(self.fd, remaining)
                if written <= 0:
                    raise OSError("Journal write made no progress")
                remaining = remaining[written:]
            os.fsync(self.fd)
        except BaseException:
            self.poisoned = True
            raise
        finally:
            self.fsync_ms += (time.perf_counter() - began) * 1000
        self.previous = _hash(body)
        self.sequence += 1

    def sending(self, payload: dict) -> None:
        if self.pending or self.index >= len(self.schedule) or payload != self.schedule[self.index]:
            raise ValueError("Request differs from the fixed journal schedule")
        self._append({"event": "sending", "request_id": payload["request_id"]})
        self.pending = True

    def outcome(self, payload: dict, *, http_status, latency_ms, error: str | None) -> None:
        if not self.pending or payload != self.schedule[self.index]:
            raise ValueError("Journal outcome has no matching pending request")
        if not math.isfinite(latency_ms) or latency_ms < 0:
            raise ValueError("Journal latency must be finite and nonnegative")
        self._append({"event": "response_verified" if error is None else "outcome_unknown",
                      "request_id": payload["request_id"], "http_status": http_status,
                      "latency_ms": latency_ms, "error_type": error})
        # Unknown outcomes stop the attempt; they must never advance to another request.
        if error is None:
            self.index += 1
            self.pending = False
        else:
            self.poisoned = True

    def descriptor(self) -> dict:
        return {"file_name": self.path.name, "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                "events": self.sequence, "write_and_fsync_ms": self.fsync_ms,
                "policy_id": JOURNAL_POLICY["policy_id"]}

    def assert_complete(self) -> None:
        if self.poisoned or self.pending or self.index != len(self.schedule):
            raise ValueError("Durable journal does not confirm the complete fixed workload")

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def inspect_request_journal(path: Path, *, attempt_id: str, candidate_id: str,
                            plan: dict, plan_file_sha256: str) -> dict:
    """Read a bounded regular file. A torn tail is uncertain, never repaired or adopted."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {"status": "missing", "interpretation": "no_request_outcome_can_be_inferred"}
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 8 * 1024 * 1024:
            raise ValueError("Journal must be a bounded regular file")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            raw = stream.read(8 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if signature(before) != signature(after) or signature(os.stat(path, follow_symlinks=False)) != signature(after):
            raise ValueError("Journal changed during inspection")
    finally:
        if fd is not None:
            os.close(fd)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Journal is too large")
    lines = raw.splitlines(keepends=True)
    torn = bool(lines and not lines[-1].endswith(b"\n"))
    if torn:
        lines.pop()
    previous, events = None, []
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate journal JSON key")
            result[key] = value
        return result
    for index, line in enumerate(lines):
        body = json.loads(line, object_pairs_hook=unique_object)
        digest = body.pop("event_sha256", None)
        if body.get("sequence") != index or body.get("previous_sha256") != previous or digest != _hash(body):
            raise ValueError("Journal event hash or sequence mismatch")
        previous = digest
        events.append(body)
    schedule = request_schedule(attempt_id, plan)
    if not events or any(events[0].get(key) != value for key, value in {
        "event": "schedule", "schema_version": 1, "attempt_id": attempt_id,
        "candidate_id": candidate_id, "plan_sha256": plan["plan_sha256"],
        "plan_file_sha256": plan_file_sha256, "policy": JOURNAL_POLICY, "requests": schedule,
    }.items()):
        raise ValueError("Journal header identity or schedule mismatch")
    states = {item["request_id"]: "scheduled_only_no_durable_send_intent" for item in schedule}
    cursor, pending, stopped = 0, False, False
    for event in events[1:]:
        if stopped or cursor >= len(schedule) or event.get("request_id") != schedule[cursor]["request_id"]:
            raise ValueError("Journal contains an out-of-order request")
        kind = event.get("event")
        if kind == "sending" and not pending:
            states[event["request_id"]] = "outcome_unknown_may_have_been_sent"
            pending = True
        elif kind in {"response_verified", "outcome_unknown"} and pending:
            value = event.get("latency_ms")
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise ValueError("Journal contains invalid latency")
            if kind == "response_verified":
                if event.get("http_status") != 200 or event.get("error_type") is not None:
                    raise ValueError("Journal claims an invalid verified response")
                states[event["request_id"]] = "response_verified"
                cursor += 1
                pending = False
            else:
                stopped = True
        else:
            raise ValueError("Invalid journal transition")
    return {"status": "torn_tail" if torn else "readable", "sha256": hashlib.sha256(raw).hexdigest(),
            "events": len(events), "requests": states,
            "verified_latencies_ms": {event["request_id"]: event["latency_ms"] for event in events[1:]
                                      if event["event"] == "response_verified"},
            "counts": {state: list(states.values()).count(state) for state in set(states.values())},
            "interpretation": "client_evidence_only_db_absence_is_not_proof_of_no_send_or_no_later_commit"}
