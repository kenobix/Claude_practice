"""Product components: operational."""
from __future__ import annotations


# ========================================================================
# capital_request_journal
# ========================================================================

"""Durable, append-only request intent and outcome evidence; never replay requests."""


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


# ========================================================================
# operational_benchmark
# ========================================================================

"""Fixed, sequential local operational benchmark for the Capital API."""



import json as _operational_benchmark_json
import hashlib as _operational_benchmark_hashlib
import importlib.metadata
import math as _operational_benchmark_math
import os as _operational_benchmark_os
import platform
import re
import subprocess
import sys
import time as _operational_benchmark_time
import uuid as _operational_benchmark_uuid
from datetime import datetime as _operational_benchmark_datetime, timedelta as _operational_benchmark_timedelta, timezone as _operational_benchmark_timezone
from pathlib import Path as _operational_benchmark_Path
from typing import Callable
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

from bike_mlops.config import DATA_PROFILE, DATA_PROFILE_ID, PROJECT_ROOT, TARGET_CONTRACT_ID
from bike_mlops.config import load_pinned_timezone

WARMUP_REQUESTS = 50
MEASURED_REQUESTS = 1000
REQUEST_TIMEOUT_SECONDS = 10.0
P95_LIMIT_MS = 250.0
INPUT_WINDOW_HOURS = 168


def validate_local_api_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Operational benchmark may call only a local http URL without credentials or query data")
    if parsed.path not in {"", "/"}:
        raise ValueError("Operational benchmark API URL must be the local service root")
    return value.rstrip("/")


def percentile_nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if not 0 < percentile <= 100:
        raise ValueError("Percentile must be in (0, 100]")
    ordered = sorted(float(value) for value in values)
    rank = max(1, _operational_benchmark_math.ceil((percentile / 100.0) * len(ordered)))
    return ordered[rank - 1]


def assess_operational_measurement(
    *,
    warmup_requests: int,
    warmup_errors: int,
    measured_requests: int,
    measured_errors: int,
    latencies_ms: list[float],
    unique_request_ids: int,
    retry_count: int,
    completed: bool = True,
    expected_warmup_requests: int = WARMUP_REQUESTS,
    expected_measured_requests: int = MEASURED_REQUESTS,
    p95_limit_ms: float = P95_LIMIT_MS,
) -> dict:
    latency_values_valid = all(_operational_benchmark_math.isfinite(float(value)) and float(value) >= 0 for value in latencies_ms)
    p95 = percentile_nearest_rank(latencies_ms, 95)
    checks = {
        "warmup_count": warmup_requests == expected_warmup_requests,
        "warmup_errors": warmup_errors == 0,
        "measured_count": measured_requests == expected_measured_requests,
        "measured_errors": measured_errors == 0,
        "latency_count": len(latencies_ms) == measured_requests,
        "unique_request_ids": unique_request_ids == expected_warmup_requests + expected_measured_requests,
        "retries": retry_count == 0,
        "completed": completed,
        "latency_values": latency_values_valid,
        "p95_latency": latency_values_valid and p95 is not None and p95 <= p95_limit_ms,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "policy": {
            "warmup_requests": expected_warmup_requests,
            "measured_requests": expected_measured_requests,
            "concurrency": 1,
            "retries_allowed": 0,
            "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "p95_limit_ms": p95_limit_ms,
            "p95_method": "nearest_rank",
        },
        "observed": {
            "warmup_requests": warmup_requests,
            "warmup_errors": warmup_errors,
            "measured_requests": measured_requests,
            "measured_errors": measured_errors,
            "unique_request_ids": unique_request_ids,
            "retry_count": retry_count,
            "p95_ms": p95,
            "latency_count": len(latencies_ms),
        },
        "checks": checks,
    }


class _PersistentJsonClient:
    """One local HTTP client with connection reuse and disabled transport retries."""

    def __init__(self) -> None:
        try:
            import httpx
        except ImportError as error:
            raise RuntimeError("Install the project development dependencies: python -m pip install -e '.[dev]'") from error
        self._client = httpx.Client(
            timeout=REQUEST_TIMEOUT_SECONDS,
            transport=httpx.HTTPTransport(retries=0),
            trust_env=False,
        )

    def __call__(self, url: str, payload: dict | None = None) -> tuple[int, dict]:
        response = self._client.get(url) if payload is None else self._client.post(url, json=payload)
        try:
            body = response.json()
        except (_operational_benchmark_json.JSONDecodeError, ValueError):
            body = {"detail": "non-JSON response"}
        return response.status_code, body

    def close(self) -> None:
        self._client.close()


def _prediction_is_valid(
    status_code: int,
    response: dict,
    *,
    request_id: str,
    target_hour: str,
    model_name: str,
    model_version: str,
    artifact_sha256: str,
    candidate_id: str,
) -> str | None:
    if status_code != 200:
        return f"unexpected HTTP status {status_code}"
    if not isinstance(response, dict):
        return "response body is not a JSON object"
    expected = {
        "request_id": request_id,
        "target_hour": target_hour,
        "data_profile_id": DATA_PROFILE_ID,
        "target_contract_id": TARGET_CONTRACT_ID,
        "model_name": model_name,
        "model_version": model_version,
        "candidate_id": candidate_id,
        "deployment_permission": "blocked",
        "artifact_sha256": artifact_sha256,
        "feature_contract_version": DATA_PROFILE["feature_contract_version"],
    }
    for key, value in expected.items():
        if response.get(key) != value:
            return f"response {key} did not match the preflight identity"
    prediction = response.get("prediction")
    if isinstance(prediction, bool) or not isinstance(prediction, (int, float)):
        return "prediction is not numeric"
    if not _operational_benchmark_math.isfinite(float(prediction)) or float(prediction) < 0:
        return "prediction is not finite and non-negative"
    data_hash = response.get("data_version_sha256")
    if (
        not isinstance(data_hash, str)
        or len(data_hash) != 64
        or any(character not in "0123456789abcdef" for character in data_hash.lower())
    ):
        return "response data-version SHA-256 is missing or malformed"
    return None


def _machine_and_container_resources() -> dict:
    cpu_model = None
    try:
        for line in _operational_benchmark_Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith(("model name", "hardware")) and ":" in line:
                cpu_model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    memory_total_bytes = None
    try:
        for line in _operational_benchmark_Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                memory_total_bytes = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass

    dependencies = {}
    for distribution in (
        "bike-demand-mlops", "fastapi", "httpx", "mlflow", "numpy", "pandas",
        "psycopg", "scikit-learn", "skops", "tzdata", "uvicorn",
    ):
        try:
            dependencies[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            dependencies[distribution] = None
    result = {
        "host": {
            "os": platform.platform(),
            "architecture": platform.machine(),
            "logical_cpu_count": _operational_benchmark_os.cpu_count(),
            "cpu_model": cpu_model,
            "memory_total_bytes": memory_total_bytes,
            "python": sys.version.split()[0],
            "dependencies": dependencies,
        },
        "capital_stack_containers": {"status": "unavailable", "containers": [], "stats": []},
    }
    project_name = _operational_benchmark_os.getenv("CAPITAL_COMPOSE_PROJECT_NAME", "bike-mlops-capital")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}", project_name):
        result["capital_stack_containers"] = {
            "status": "unavailable", "containers": [], "stats": [],
            "detail": "Invalid isolated Compose project name",
        }
        return result
    compose_env_file = _operational_benchmark_os.getenv("CAPITAL_COMPOSE_ENV_FILE")
    compose_env_args = []
    if compose_env_file:
        env_path = _operational_benchmark_Path(compose_env_file).resolve()
        try:
            mode = env_path.stat().st_mode & 0o777
        except OSError:
            mode = None
        if not env_path.is_file() or mode != 0o600:
            result["capital_stack_containers"] = {
                "status": "unavailable", "containers": [], "stats": [],
                "detail": "Compose environment file is missing or has unsafe permissions",
            }
            return result
        compose_env_args = ["--env-file", str(env_path)]
    compose = [
        "docker", "compose", *compose_env_args, "--project-name", project_name,
        "-f", str(PROJECT_ROOT / "compose.yaml"),
        "-f", str(PROJECT_ROOT / "compose.capital.yaml"),
        "-f", str(PROJECT_ROOT / "compose.capital-staging.yaml"),
    ]
    stage = "compose_ps"
    try:
        listed = subprocess.run(
            compose + ["ps", "--status", "running", "-q", "postgres", "mlflow", "api-blue", "api-green", "caddy", "postgres-staging", "api-staging"],
            check=True, capture_output=True, text=True, timeout=15,
        )
        container_ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
        if not container_ids:
            result["capital_stack_containers"]["status"] = "no_running_services"
            return result
        stage = "docker_inspect"
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{.Name}}|{{.Config.Image}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}", *container_ids],
            check=True, capture_output=True, text=True, timeout=15,
        )
        stage = "docker_stats"
        stats = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Container}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}", *container_ids],
            check=True, capture_output=True, text=True, timeout=15,
        )
        stage = "docker_version"
        docker_version = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            check=True, capture_output=True, text=True, timeout=15,
        )
        result["capital_stack_containers"] = {
            "status": "captured",
            "docker_engine_version": docker_version.stdout.strip(),
            "containers": inspected.stdout.splitlines(),
            "stats": stats.stdout.splitlines(),
        }
    except (OSError, subprocess.SubprocessError) as error:
        detail = f"{stage}: {type(error).__name__}"
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            detail += ": " + error.stderr.strip()[-500:]
        result["capital_stack_containers"] = {
            "status": "unavailable",
            "containers": [],
            "stats": [],
            "detail": detail,
        }
    return result


def _validate_timezone_grid(start_time: pd.Timestamp) -> None:
    zone_name = DATA_PROFILE["target"].get("timezone_name")
    if not zone_name:
        return
    zone = load_pinned_timezone(zone_name)
    for offset in range(INPUT_WINDOW_HOURS):
        wall = (start_time + _operational_benchmark_timedelta(hours=offset)).to_pydatetime()
        first = wall.replace(tzinfo=zone, fold=0)
        second = wall.replace(tzinfo=zone, fold=1)
        roundtrip = first.astimezone(_operational_benchmark_timezone.utc).astimezone(zone).replace(tzinfo=None)
        if roundtrip != wall:
            raise ValueError(f"Benchmark input window contains nonexistent local hour: {wall}")
        if first.utcoffset() != second.utcoffset():
            raise ValueError(f"Benchmark input window contains ambiguous repeated local hour: {wall}")


def run_capital_operational_benchmark(
    *,
    api_url: str,
    model_version: str,
    candidate_id: str,
    start: str,
    end: str,
    request_fn: Callable | None = None,
    resource_fn: Callable = _machine_and_container_resources,
    warmup_count: int = WARMUP_REQUESTS,
    measured_count: int = MEASURED_REQUESTS,
    attempt_id: str | None = None,
    expected_artifact_sha256: str | None = None,
    request_observer=None,
) -> dict:
    if DATA_PROFILE_ID != "capital-bikeshare":
        raise ValueError("Run the operational benchmark with DATA_PROFILE_ID=capital-bikeshare")
    if not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("Benchmark requires the exact lowercase SHA-256 candidate ID")
    base_url = validate_local_api_url(api_url)
    start_time, end_time = pd.Timestamp(start), pd.Timestamp(end)
    if start_time.tzinfo or end_time.tzinfo or start_time >= end_time:
        raise ValueError("Benchmark window must use naive local timestamps with start < end")
    if start_time != start_time.floor("h") or end_time != end_time.floor("h"):
        raise ValueError("Benchmark window boundaries must align to the start of an hour")
    if int((end_time - start_time).total_seconds()) != INPUT_WINDOW_HOURS * 3600:
        raise ValueError(f"Benchmark input window must be exactly {INPUT_WINDOW_HOURS} hours")
    _validate_timezone_grid(start_time)
    if warmup_count <= 0 or measured_count <= 0:
        raise ValueError("Warmup and measured request counts must be positive")
    if attempt_id is not None:
        try:
            if str(_operational_benchmark_uuid.UUID(attempt_id)) != attempt_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Attempt ID must be a canonical UUID") from error
    if expected_artifact_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_artifact_sha256):
        raise ValueError("Expected artifact SHA-256 must be lowercase hexadecimal")
    if request_observer is not None and (attempt_id is None or expected_artifact_sha256 is None):
        raise ValueError("An observer requires the pinned attempt ID and artifact SHA-256")

    request_client = _PersistentJsonClient() if request_fn is None else None
    if request_fn is None:
        request_fn = request_client
    try:
        ready_status, ready = request_fn(f"{base_url}/ready")
        version_status, version = request_fn(f"{base_url}/version")
        if ready_status != 200 or ready.get("status") != "ready":
            raise RuntimeError(f"Capital API is not ready: HTTP {ready_status}, response={ready}")
        if version_status != 200:
            raise RuntimeError(f"Could not read the active Capital API version: HTTP {version_status}")
        if (
            version.get("data_profile_id") != DATA_PROFILE_ID
            or version.get("target_contract_id") != TARGET_CONTRACT_ID
            or version.get("model_name") != DATA_PROFILE["model_name"]
            or str(version.get("model_version")) != str(model_version)
            or version.get("candidate_id") != candidate_id
            or version.get("deployment_permission") != "blocked"
            or version.get("quality_gate_status") != "passed"
            or not isinstance(version.get("artifact_sha256"), str)
            or len(version["artifact_sha256"]) != 64
            or (expected_artifact_sha256 is not None and version.get("artifact_sha256") != expected_artifact_sha256)
        ):
            raise RuntimeError("Active API identity/readiness does not match the requested Capital model")
        if (
            ready.get("data_profile_id") != DATA_PROFILE_ID
            or ready.get("target_contract_id") != TARGET_CONTRACT_ID
            or ready.get("model_name") != version["model_name"]
            or str(ready.get("model_version")) != str(model_version)
            or ready.get("candidate_id") != candidate_id
            or ready.get("deployment_permission") != "blocked"
        ):
            raise RuntimeError("Capital API /ready identity differs from /version")
    except BaseException:
        if request_client is not None:
            request_client.close()
        raise

    benchmark_id = attempt_id or str(_operational_benchmark_uuid.uuid4())
    request_ids: list[str] = []
    errors: list[dict] = []
    latencies_ms: list[float] = []
    measurements: list[dict] = []
    error_counts = {"warmup": 0, "measured": 0}
    total = warmup_count + measured_count
    interrupted = False
    interruption_requested = False
    execution_error = None
    pending = None
    loop_started = _operational_benchmark_time.perf_counter()

    def record_measurement(
        phase: str,
        phase_index: int,
        request_id: str,
        target_text: str,
        status_code: int | None,
        latency_ms: float | None,
        response: dict | None,
        error: str | None,
    ) -> None:
        request_ids.append(request_id)
        measurements.append({
            "phase": phase,
            "index": phase_index,
            "request_id": request_id,
            "target_hour": target_text,
            "http_status": status_code,
            "latency_ms": latency_ms,
            "prediction": response.get("prediction") if isinstance(response, dict) else None,
            "data_version_sha256": response.get("data_version_sha256") if isinstance(response, dict) else None,
            "error": error,
        })
        if phase == "measured" and latency_ms is not None:
            latencies_ms.append(latency_ms)
        if error:
            errors.append({"phase": phase, "index": phase_index, "reason": error})
            error_counts[phase] += 1

    try:
        for index in range(total):
            phase = "warmup" if index < warmup_count else "measured"
            phase_index = index if phase == "warmup" else index - warmup_count
            request_id = f"capital-op:{benchmark_id}:{phase}:{phase_index:04d}"
            target = start_time + _operational_benchmark_timedelta(hours=phase_index % INPUT_WINDOW_HOURS)
            target_text = target.isoformat(sep=" ")
            payload = {
                "request_id": request_id,
                "target_hour": target_text,
                "as_of": target_text,
                "feature_contract_version": DATA_PROFILE["feature_contract_version"],
                "data_profile_id": DATA_PROFILE_ID,
                "target_contract_id": TARGET_CONTRACT_ID,
            }
            if request_observer is not None:
                try:
                    request_observer.before_request(payload)
                except Exception as exception:
                    execution_error = f"before_request: {type(exception).__name__}"
                    interrupted = True
                    break
            began = _operational_benchmark_time.perf_counter()
            pending = (phase, phase_index, request_id, target_text, began)
            status_code = None
            response = None
            latency_ms = None
            try:
                try:
                    status_code, response = request_fn(f"{base_url}/predict", payload)
                finally:
                    # Freeze transport time before response/source checks or durable I/O.
                    latency_ms = (_operational_benchmark_time.perf_counter() - began) * 1000.0
                error = _prediction_is_valid(
                    status_code,
                    response,
                    request_id=request_id,
                    target_hour=target_text,
                    model_name=DATA_PROFILE["model_name"],
                    model_version=str(model_version),
                    artifact_sha256=version["artifact_sha256"],
                    candidate_id=candidate_id,
                )
                if error is None and request_observer is not None:
                    request_observer.after_response(payload, response)
            except KeyboardInterrupt:
                error = "KeyboardInterrupt: request interrupted; benchmark is incomplete"
                interrupted = True
                interruption_requested = True
            except Exception as exception:  # Preserve every failed request; never retry.
                # A guard or client exception may contain connection details; the
                # integrated measurement report records its type, not secrets.
                error = (type(exception).__name__ if request_observer is not None
                         else f"{type(exception).__name__}: {exception}")
            if request_observer is not None and hasattr(request_observer, "request_result"):
                # A failed journal write propagates: do not issue the next HTTP call.
                request_observer.request_result(payload, http_status=status_code, latency_ms=latency_ms, error=error)
            record_measurement(
                phase, phase_index, request_id, target_text, status_code, latency_ms, response, error
            )
            pending = None
            if error and request_observer is not None:
                interrupted = True
            if interrupted:
                break
    except KeyboardInterrupt:
        interrupted = True
        interruption_requested = True
        if pending is not None:
            phase, phase_index, request_id, target_text, began = pending
            record_measurement(
                phase,
                phase_index,
                request_id,
                target_text,
                None,
                latency_ms if latency_ms is not None else (_operational_benchmark_time.perf_counter() - began) * 1000.0,
                None,
                "KeyboardInterrupt: request interrupted; benchmark is incomplete",
            )
    finally:
        if request_client is not None:
            request_client.close()
    loop_elapsed_ms = (_operational_benchmark_time.perf_counter() - loop_started) * 1000.0

    gate = assess_operational_measurement(
        warmup_requests=sum(item["phase"] == "warmup" for item in measurements),
        warmup_errors=error_counts["warmup"],
        measured_requests=sum(item["phase"] == "measured" for item in measurements),
        measured_errors=error_counts["measured"],
        latencies_ms=latencies_ms,
        unique_request_ids=len(set(request_ids)),
        retry_count=0,
        completed=not interrupted,
        expected_warmup_requests=WARMUP_REQUESTS,
        expected_measured_requests=MEASURED_REQUESTS,
        p95_limit_ms=P95_LIMIT_MS,
    )
    gate["observed"]["mean_ms"] = float(np.mean(latencies_ms)) if latencies_ms else None
    gate["observed"]["median_ms"] = percentile_nearest_rank(latencies_ms, 50)
    gate["observed"]["min_ms"] = min(latencies_ms) if latencies_ms else None
    gate["observed"]["max_ms"] = max(latencies_ms) if latencies_ms else None
    gate["observed"]["errors"] = errors
    gate["observed"]["request_id_sha256"] = _operational_benchmark_hashlib.sha256(
        "\n".join(request_ids).encode("utf-8")
    ).hexdigest()
    try:
        resource_context = resource_fn()
    except KeyboardInterrupt:
        interrupted = True
        interruption_requested = True
        gate["status"] = "failed"
        gate["checks"]["completed"] = False
        resource_context = {"status": "interrupted", "detail": "Resource capture interrupted"}
    # Numerical success alone cannot prove the still-missing input/DB provenance checks.
    gate["measurement_status"] = gate["status"]
    if gate["status"] == "passed":
        gate["status"] = "not_evaluated"
    gate["input_provenance_status"] = "not_evaluated"
    return {
        "schema_version": 1,
        "benchmark_id": benchmark_id,
        "created_at_utc": _operational_benchmark_datetime.now(_operational_benchmark_timezone.utc).isoformat(),
        "data_profile_id": DATA_PROFILE_ID,
        "target_contract_id": TARGET_CONTRACT_ID,
        "api_url": base_url,
        "api_preflight": {"ready": ready, "version": version},
        "load_conditions": {
            "database_readiness": "passed by API /ready database SELECT 1 check",
            "other_host_load": "not measured; review manually and note it with the benchmark evidence",
        },
        "model_identity": {
            "model_name": version["model_name"],
            "model_version": str(version["model_version"]),
            "candidate_id": version["candidate_id"],
            "artifact_sha256": version["artifact_sha256"],
            "feature_contract_version": version["feature_contract_version"],
        },
        "input_window": {
            "start_inclusive": start_time.isoformat(sep=" "),
            "end_exclusive": end_time.isoformat(sep=" "),
            "hours": INPUT_WINDOW_HOURS,
            "use": "operational replay only; no accuracy scoring",
            "target_sequence": "cycle through the fixed 168 hourly timestamps",
        },
        "completed": not interrupted,
        "execution_error": execution_error,
        "interruption_requested": interruption_requested,
        "timing": {"request_loop_elapsed_ms": loop_elapsed_ms,
                   "http_latency_scope": "request_callable_only_including_transport_json_decode",
                   "loop_scope": "all_requests_including_observer_and_journal_excluding_resource_capture",
                   "overall_elapsed_gate": "not_defined"},
        "operational_gate": gate,
        "measurements": measurements,
        "deployment_permission": {
            "status": "blocked",
            "reason": "Operational benchmark alone does not establish final accuracy or manual approval",
        },
        "resource_context": resource_context,
        "limitations": [
            "This is a local learning-PC benchmark, not a production SLO.",
            "No accuracy metric is computed from the replay window.",
            "DB snapshot and API data provenance are not verified; numerical success is not an operational gate pass.",
            "A successful measurement does not independently authorize model deployment.",
        ],
    }


def write_benchmark_report(path: _operational_benchmark_Path, report: dict) -> None:
    resolved = path.resolve()
    report_root = (PROJECT_ROOT / "data/reports/capital-bikeshare").resolve()
    if not resolved.is_relative_to(report_root) or resolved.suffix.lower() != ".json":
        raise ValueError("Benchmark reports must be new JSON files under data/reports/capital-bikeshare/")
    if resolved.exists():
        raise FileExistsError(f"Refusing to overwrite operational benchmark report: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.{_operational_benchmark_uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            _operational_benchmark_json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            _operational_benchmark_os.fsync(stream.fileno())
        _operational_benchmark_os.link(temporary, resolved)
        directory_fd = _operational_benchmark_os.open(resolved.parent, _operational_benchmark_os.O_RDONLY | _operational_benchmark_os.O_DIRECTORY)
        try:
            _operational_benchmark_os.fsync(directory_fd)
        finally:
            _operational_benchmark_os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


# ========================================================================
# capital_operational_plan
# ========================================================================

"""Freeze a candidate-specific operational plan before issuing prediction requests."""



import hashlib as _operational_plan_hashlib
from copy import deepcopy
import json as _operational_plan_json
import os as _operational_plan_os
import re as _operational_plan_re
import tempfile
from datetime import datetime as _operational_plan_datetime, timezone as _operational_plan_timezone
from pathlib import Path as _operational_plan_Path
from urllib.parse import urlsplit as _operational_plan_urlsplit

import pandas as _operational_plan_pd

_operational_plan_JOURNAL_POLICY = JOURNAL_POLICY

from bike_mlops.staging import _registration_tags, validate_staging_model_tags
from bike_mlops.config import LABEL_DELAY_HOURS, PROJECT_ROOT as _operational_plan_PROJECT_ROOT
from bike_mlops.model import directory_sha256
from bike_mlops.config import _profiles_load_data_profile as load_data_profile
_operational_plan_INPUT_WINDOW_HOURS = INPUT_WINDOW_HOURS
_operational_plan_MEASURED_REQUESTS = MEASURED_REQUESTS
_operational_plan_P95_LIMIT_MS = P95_LIMIT_MS
_operational_plan_REQUEST_TIMEOUT_SECONDS = REQUEST_TIMEOUT_SECONDS
_operational_plan_WARMUP_REQUESTS = WARMUP_REQUESTS
_operational_plan_validate_timezone_grid = _validate_timezone_grid

PLAN_POLICY = {
    "warmup_requests": _operational_plan_WARMUP_REQUESTS,
    "measured_requests": _operational_plan_MEASURED_REQUESTS,
    "concurrency": 1,
    "retries_allowed": 0,
    "timeout_seconds": _operational_plan_REQUEST_TIMEOUT_SECONDS,
    "p95_limit_ms": _operational_plan_P95_LIMIT_MS,
    "p95_method": "nearest_rank",
    "target_sequence": "cycle_168_hours_from_start_reset_per_phase",
    "request_id": "unique_benchmark_uuid_phase_index",
    "as_of": "equal_to_target_hour",
    "accuracy_scoring": False,
    "request_journal": dict(_operational_plan_JOURNAL_POLICY),
}


def _digest(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "plan_sha256"}
    return _operational_plan_hashlib.sha256(_operational_plan_json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _sha(value: object, name: str) -> str:
    if not isinstance(value, str) or not _operational_plan_re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _staging_url(url: str) -> str:
    parsed = _operational_plan_urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.port != 8110
            or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Operational plan requires the local staging API on port 8110")
    return url.rstrip("/")


def local_registry_url(url: str) -> str:
    parsed = _operational_plan_urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.port != 5001
            or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Operational plan requires local Capital MLflow on port 5001")
    return url.rstrip("/")


def validate_plan_registration(evidence: dict, registered) -> str:
    """Reject a foreign or ineligible registration before downloading its artifact."""
    if registered.status != "READY" or registered.name != "bike-demand-capital":
        raise ValueError("Operational plan requires a READY Capital model registration")
    version = str(registered.version)
    if not version.isdecimal() or int(version) <= 0:
        raise ValueError("Operational plan requires a positive registered model version")
    validate_staging_model_tags(registered.tags, _registration_tags(evidence))
    return _sha(registered.tags.get("artifact_sha256"), "registered artifact SHA-256")


def build_operational_plan(*, evidence: dict, window: dict, registered, artifact_dir: _operational_plan_Path,
                           api_url: str, registry_uri: str, resource_context: dict) -> dict:
    """Validate supplied evidence and freeze conditions; this function sends no predictions."""
    artifact_sha = validate_plan_registration(evidence, registered)
    version = str(registered.version)
    if not (artifact_dir / "MLmodel").is_file() or directory_sha256(artifact_dir) != artifact_sha:
        raise ValueError("Downloaded registered artifact does not match its MLflow version hash")

    selection = evidence["selection"]
    profile = load_data_profile("capital-bikeshare")
    if (selection.get("data_profile_id") != profile["profile_id"]
            or selection.get("target_contract_id") != profile["target_contract_id"]
            or selection.get("feature_contract_version") != profile["feature_contract_version"]):
        raise ValueError("Operational candidate profile, target or feature contract is not Capital")
    expected = {
        "candidate_id": evidence["candidate_id"],
        "candidate_report_sha256": evidence["selection_report_sha256"],
        "final_report_sha256": evidence["final_report_sha256"],
        "data_sha256": selection["data_sha256"],
        "manifest_sha256": selection["data_source"]["source_manifest_sha256"],
        "evaluation_protocol_sha256": evidence["evaluation_protocol_sha256"],
    }
    for key, value in expected.items():
        _sha(value, key)
        if window.get(key) != value:
            raise ValueError(f"Training window {key} differs from the verified candidate")
    _sha(evidence["candidate_artifact_sha256"], "candidate artifact SHA-256")
    _sha(window.get("training_row_keys_sha256"), "training row SHA-256")
    if (window["training_row_keys_sha256"] != selection.get("training_row_keys_sha256")
            or window.get("training_cutoff") != selection.get("training_cutoff")
            or window.get("training_rows") != selection.get("split_counts", {}).get("train")):
        raise ValueError("Operational input window differs from the candidate training split")
    start = _operational_plan_pd.Timestamp(window["start_inclusive"])
    end = _operational_plan_pd.Timestamp(window["end_exclusive"])
    if (_operational_plan_pd.isna(start) or _operational_plan_pd.isna(end) or start.tzinfo is not None or end.tzinfo is not None
            or start != start.floor("h") or end != end.floor("h")
            or end - start != _operational_plan_pd.Timedelta(hours=_operational_plan_INPUT_WINDOW_HOURS) or window.get("hours") != _operational_plan_INPUT_WINDOW_HOURS):
        raise ValueError("Operational input window must contain exactly 168 aligned naive hours")
    cutoff = _operational_plan_pd.Timestamp(window["training_cutoff"])
    delay = window.get("label_delay_hours")
    latest = end - _operational_plan_pd.Timedelta(hours=1) + _operational_plan_pd.Timedelta(hours=delay) if isinstance(delay, int) and not isinstance(delay, bool) else _operational_plan_pd.NaT
    if (_operational_plan_pd.isna(cutoff) or cutoff.tzinfo is not None or cutoff != cutoff.floor("h") or _operational_plan_pd.isna(latest)
            or delay != LABEL_DELAY_HOURS or latest > cutoff
            or latest.isoformat(sep=" ") != window.get("latest_replay_label_available_at")):
        raise ValueError("Operational input labels are not available by the candidate training cutoff")
    _operational_plan_validate_timezone_grid(start)
    if not isinstance(resource_context, dict):
        raise ValueError("Operational plan requires an object describing the resource context")
    plan = {
        "schema_version": 2,
        "kind": "capital-operational-plan",
        "created_at_utc": _operational_plan_datetime.now(_operational_plan_timezone.utc).isoformat(),
        "identity": {**expected, "model_name": registered.name, "model_version": version,
                     "data_profile_id": profile["profile_id"], "target_contract_id": profile["target_contract_id"],
                     "candidate_artifact_sha256": evidence["candidate_artifact_sha256"],
                     "registered_artifact_sha256": artifact_sha,
                     "feature_contract_version": selection["feature_contract_version"]},
        "input_window": dict(window),
        "api_url": _staging_url(api_url),
        "registry_uri": local_registry_url(registry_uri),
        "policy": deepcopy(PLAN_POLICY),
        "resource_context_before_requests": resource_context,
        "other_host_load": "not fully measured; assess separately before deciding operational eligibility",
        "deployment_permission": "blocked",
    }
    plan["plan_sha256"] = _digest(plan)
    return plan


def validate_operational_plan(plan: object, *, allow_legacy: bool = False) -> dict:
    """Validate integrity and fixed policy; actual candidate/DB must be rechecked before use."""
    if (not isinstance(plan, dict) or type(plan.get("schema_version")) is not int
            or plan.get("schema_version") != 2 or plan.get("kind") != "capital-operational-plan"):
        raise ValueError("Unsupported operational plan")
    if plan.get("plan_sha256") != _digest(plan):
        raise ValueError("Operational plan SHA-256 mismatch")
    expected_policy = PLAN_POLICY
    if allow_legacy and isinstance(plan.get("policy"), dict) and "request_journal" not in plan["policy"]:
        expected_policy = {key: value for key, value in PLAN_POLICY.items() if key != "request_journal"}
    if (_operational_plan_json.dumps(plan.get("policy"), sort_keys=True, separators=(",", ":"))
            != _operational_plan_json.dumps(expected_policy, sort_keys=True, separators=(",", ":"))
            or plan.get("deployment_permission") != "blocked"):
        raise ValueError("Operational plan policy or deployment permission differs from the fixed conditions")
    _staging_url(plan.get("api_url", ""))
    local_registry_url(plan.get("registry_uri", ""))
    identity = plan.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("Operational plan candidate identity is missing")
    for key in ("candidate_id", "candidate_report_sha256", "final_report_sha256", "data_sha256",
                "manifest_sha256", "evaluation_protocol_sha256", "candidate_artifact_sha256",
                "registered_artifact_sha256"):
        _sha(identity.get(key), key)
    profile = load_data_profile("capital-bikeshare")
    if (identity.get("model_name") != profile["model_name"]
            or identity.get("data_profile_id") != profile["profile_id"]
            or identity.get("target_contract_id") != profile["target_contract_id"]
            or identity.get("feature_contract_version") != profile["feature_contract_version"]
            or not str(identity.get("model_version", "")).isdecimal() or int(identity["model_version"]) <= 0):
        raise ValueError("Operational plan model identity is invalid")
    window = plan.get("input_window")
    if not isinstance(window, dict) or any(window.get(key) != identity[key] for key in (
        "candidate_id", "candidate_report_sha256", "final_report_sha256", "data_sha256",
        "manifest_sha256", "evaluation_protocol_sha256",
    )):
        raise ValueError("Operational plan training-window identity differs from its candidate")
    _sha(window.get("training_row_keys_sha256"), "training row SHA-256")
    start, end = _operational_plan_pd.Timestamp(window.get("start_inclusive")), _operational_plan_pd.Timestamp(window.get("end_exclusive"))
    if (_operational_plan_pd.isna(start) or _operational_plan_pd.isna(end) or start.tzinfo is not None or end.tzinfo is not None
            or start != start.floor("h") or end != end.floor("h")
            or end - start != _operational_plan_pd.Timedelta(hours=_operational_plan_INPUT_WINDOW_HOURS) or window.get("hours") != _operational_plan_INPUT_WINDOW_HOURS):
        raise ValueError("Operational plan training window is invalid")
    cutoff = _operational_plan_pd.Timestamp(window.get("training_cutoff"))
    delay = window.get("label_delay_hours")
    rows = window.get("training_rows")
    if (_operational_plan_pd.isna(cutoff) or cutoff.tzinfo is not None or cutoff != cutoff.floor("h")
            or isinstance(delay, bool) or not isinstance(delay, int) or delay != LABEL_DELAY_HOURS
            or isinstance(rows, bool) or not isinstance(rows, int) or rows < _operational_plan_INPUT_WINDOW_HOURS):
        raise ValueError("Operational plan training cutoff, label delay or row count is invalid")
    latest = end - _operational_plan_pd.Timedelta(hours=1) + _operational_plan_pd.Timedelta(hours=delay)
    if latest > cutoff or latest.isoformat(sep=" ") != window.get("latest_replay_label_available_at"):
        raise ValueError("Operational plan contains labels unavailable at the training cutoff")
    _operational_plan_validate_timezone_grid(start)
    if not isinstance(plan.get("resource_context_before_requests"), dict):
        raise ValueError("Operational plan resource context is missing")
    return plan


def verify_operational_plan_binding(plan: dict, *, evidence: dict, window: dict, registered, artifact_dir: _operational_plan_Path,
                                    registry_uri: str) -> None:
    """Recheck a frozen plan against current verified evidence and the exact registry artifact."""
    validate_operational_plan(plan)
    fresh = build_operational_plan(evidence=evidence, window=window, registered=registered,
                                   artifact_dir=artifact_dir, api_url=plan["api_url"],
                                   registry_uri=registry_uri,
                                   resource_context=plan["resource_context_before_requests"])
    for key in ("identity", "input_window", "api_url", "registry_uri", "policy"):
        if plan.get(key) != fresh.get(key):
            raise ValueError(f"Frozen operational plan {key} differs from current candidate evidence")


def save_operational_plan(plan: dict, output: _operational_plan_Path) -> _operational_plan_Path:
    validate_operational_plan(plan)
    resolved = output.resolve()
    root = (_operational_plan_PROJECT_ROOT / "data/evaluations/capital-bikeshare/operational-plans").resolve()
    if not resolved.is_relative_to(root) or resolved.suffix != ".json":
        raise ValueError("Operational plan must be a JSON file inside the operational-plans directory")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=resolved.parent, delete=False) as stream:
            temporary = _operational_plan_Path(stream.name)
            _operational_plan_json.dump(plan, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            _operational_plan_os.fsync(stream.fileno())
        _operational_plan_os.link(temporary, resolved)
    except FileExistsError as error:
        raise FileExistsError(f"Refusing to overwrite operational plan: {resolved}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return resolved


def read_operational_plan(path: _operational_plan_Path) -> dict:
    try:
        value = _operational_plan_json.loads(path.read_text(encoding="utf-8"))
    except (OSError, _operational_plan_json.JSONDecodeError) as error:
        raise ValueError(f"Could not read operational plan: {error}") from error
    return validate_operational_plan(value)


# ========================================================================
# capital_operational_ledger
# ========================================================================

"""Record operational measurement attempts before requests can be sent."""



import fcntl
import hashlib as _operational_ledger_hashlib
import json as _operational_ledger_json
import os as _operational_ledger_os
import re as _operational_ledger_re
import tempfile as _operational_ledger_tempfile
import uuid as _operational_ledger_uuid
from contextlib import contextmanager
from datetime import datetime as _operational_ledger_datetime, timezone as _operational_ledger_timezone
from pathlib import Path as _operational_ledger_Path

_operational_ledger_validate_operational_plan = validate_operational_plan
from bike_mlops.config import PROJECT_ROOT as _operational_ledger_PROJECT_ROOT

DEFAULT_LEDGER = _operational_ledger_PROJECT_ROOT / "data/evaluations/capital-bikeshare/operational-attempts.json"
DEFAULT_LOCK_DIR = _operational_ledger_PROJECT_ROOT / "data/evaluations/capital-bikeshare/operational-locks"
_SHA = _operational_ledger_re.compile(r"[0-9a-f]{64}")
_ATTEMPT_STATES = {"started", "completed", "failed", "interrupted"}
_RECORD_STATES = _ATTEMPT_STATES | {"reviewed_reopen"}
_FAILURE_CODES = {"preflight_failed", "snapshot_lost", "request_failed", "audit_mismatch", "interrupted", "other"}


def _now() -> str:
    return _operational_ledger_datetime.now(_operational_ledger_timezone.utc).isoformat()


def _read_json_bytes(path: _operational_ledger_Path, description: str) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
        value = _operational_ledger_json.loads(raw)
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read {description}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object")
    return value, _operational_ledger_hashlib.sha256(raw).hexdigest()


def _project_file(path: _operational_ledger_Path, directory: str) -> tuple[_operational_ledger_Path, str]:
    root = _operational_ledger_PROJECT_ROOT.resolve()
    allowed = (root / directory).resolve()
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(allowed) or resolved.suffix != ".json":
        raise ValueError(f"Evidence must be a JSON file under {directory}")
    return resolved, resolved.relative_to(root).as_posix()


def _read_plan(path: _operational_ledger_Path, *, allow_legacy: bool = False) -> tuple[dict, str, str]:
    resolved, relative = _project_file(path, "data/evaluations/capital-bikeshare/operational-plans")
    plan, file_sha = _read_json_bytes(resolved, "operational plan")
    _operational_ledger_validate_operational_plan(plan, allow_legacy=allow_legacy)
    return plan, relative, file_sha


def read_operational_ledger(path: _operational_ledger_Path = DEFAULT_LEDGER) -> dict:
    if not path.exists():
        return {"schema_version": 1, "records": []}
    ledger, _ = _read_json_bytes(path, "operational attempt ledger")
    if type(ledger.get("schema_version")) is not int or ledger["schema_version"] != 1 or not isinstance(ledger.get("records"), list):
        raise ValueError("Operational attempt ledger has an unsupported schema")
    candidates, attempt_ids = set(), set()
    for record in ledger["records"]:
        if not isinstance(record, dict) or not isinstance(record.get("candidate_id"), str) or not _SHA.fullmatch(record["candidate_id"]):
            raise ValueError("Operational ledger has an invalid candidate")
        if record["candidate_id"] in candidates or record.get("status") not in _RECORD_STATES:
            raise ValueError("Operational ledger has duplicate candidates or invalid state")
        candidates.add(record["candidate_id"])
        attempts = record.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ValueError("Operational ledger record has no attempts")
        reviews = record.get("reviews")
        if not isinstance(reviews, list):
            raise ValueError("Operational ledger reviews must be a list")
        for index, attempt in enumerate(attempts):
            if (not isinstance(attempt, dict) or not isinstance(attempt.get("attempt_id"), str)
                    or attempt["attempt_id"] in attempt_ids or attempt.get("status") not in _ATTEMPT_STATES
                    or not isinstance(attempt.get("plan_sha256"), str) or not _SHA.fullmatch(attempt["plan_sha256"])
                    or not isinstance(attempt.get("plan_file_sha256"), str) or not _SHA.fullmatch(attempt["plan_file_sha256"])
                    or (index < len(attempts) - 1 and attempt.get("status") == "started")):
                raise ValueError("Operational ledger contains an invalid or duplicate attempt")
            attempt_ids.add(attempt["attempt_id"])
        if record["status"] != "reviewed_reopen" and record["status"] != attempts[-1]["status"]:
            raise ValueError("Operational ledger record and latest attempt states differ")
        if record["status"] == "reviewed_reopen":
            review = reviews[-1] if reviews else None
            if (attempts[-1]["status"] not in {"failed", "interrupted"} or not isinstance(review, dict)
                    or review.get("attempt_id") != attempts[-1]["attempt_id"]
                    or not isinstance(review.get("operator"), str) or not review["operator"].strip()
                    or not isinstance(review.get("reason"), str) or not review["reason"].strip()
                    or not isinstance(review.get("next_plan_sha256"), str) or not _SHA.fullmatch(review["next_plan_sha256"])
                    or not isinstance(review.get("next_plan_file_sha256"), str) or not _SHA.fullmatch(review["next_plan_file_sha256"])):
                raise ValueError("Operational ledger retry state lacks a valid review")
    return ledger


def _write_ledger(path: _operational_ledger_Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with _operational_ledger_tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = _operational_ledger_Path(stream.name)
            _operational_ledger_json.dump(ledger, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            _operational_ledger_os.fsync(stream.fileno())
        _operational_ledger_os.replace(temporary, path)
        directory_fd = _operational_ledger_os.open(path.parent, _operational_ledger_os.O_RDONLY | _operational_ledger_os.O_DIRECTORY)
        try:
            _operational_ledger_os.fsync(directory_fd)
        finally:
            _operational_ledger_os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_lock(path: _operational_ledger_Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _operational_ledger_os.open(path, _operational_ledger_os.O_CREAT | _operational_ledger_os.O_RDWR | _operational_ledger_os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Operational measurement is locked by another process") from error
        yield fd
    finally:
        _operational_ledger_os.close(fd)


def _mutate(path: _operational_ledger_Path, action):
    with _exclusive_lock(path.with_suffix(path.suffix + ".lock")):
        ledger = read_operational_ledger(path)
        result = action(ledger)
        _write_ledger(path, ledger)
        return result


class MeasurementAttempt:
    """Attempt identity held while the per-candidate process lock is open."""

    def __init__(self, attempt_id: str, candidate_id: str, plan_sha256: str, plan_file_sha256: str,
                 ledger_path: Path):
        self.attempt_id = attempt_id
        self.candidate_id = candidate_id
        self.plan_sha256 = plan_sha256
        self.plan_file_sha256 = plan_file_sha256
        self.ledger_path = ledger_path
        self._active = True
        self._finished = False
        self._finish_action = None

    @property
    def request_prefix(self) -> str:
        return f"capital-op:{self.attempt_id}:"

    def mark_requests_started(self) -> None:
        """Persist intent before the first prediction request is sent."""
        if not self._active or self._finished:
            raise ValueError("Operational attempt is no longer active")

        def change(ledger):
            record = next((item for item in ledger["records"] if item["candidate_id"] == self.candidate_id), None)
            if not record or record["status"] != "started" or record["attempts"][-1]["attempt_id"] != self.attempt_id:
                raise ValueError("Only the current started attempt can mark requests")
            attempt = record["attempts"][-1]
            if attempt.get("requests_started_at_utc") is not None:
                raise ValueError("Operational attempt has already marked request start")
            attempt["requests_started_at_utc"] = _now()

        _mutate(self.ledger_path, change)

    def finish(self, *, status: str, report_path: Path | None = None, failure_code: str | None = None) -> None:
        if not self._active or self._finished:
            raise ValueError("Operational attempt is no longer active")
        if status not in {"completed", "failed"}:
            raise ValueError("Operational attempt must be completed or failed")
        if status == "completed" and (report_path is None or failure_code is not None):
            raise ValueError("A completed operational attempt requires a report and no failure code")
        if status == "failed" and failure_code not in _FAILURE_CODES:
            raise ValueError("A failed operational attempt requires a recognized failure code")
        report = None
        if report_path is not None:
            resolved, relative = _project_file(report_path, "data/reports/capital-bikeshare")
            body, report_hash = _read_json_bytes(resolved, "operational report")
            gate = body.get("operational_gate")
            if (body.get("operational_attempt_id") != self.attempt_id
                    or body.get("operational_plan_sha256") != self.plan_sha256
                    or body.get("operational_plan_file_sha256") != self.plan_file_sha256
                    or not isinstance(gate, dict) or gate.get("status") not in {"passed", "failed", "not_evaluated"}
                    or type(body.get("completed")) is not bool
                    or (status == "completed" and (body["completed"] is not True or gate["status"] not in {"passed", "not_evaluated"}))
                    or (status == "failed" and body["completed"] is True and gate["status"] == "passed")):
                raise ValueError("Operational report does not match the attempt, plan or outcome")
            report = {"path": relative, "sha256": report_hash, "gate_status": gate["status"]}

        def change(ledger):
            record = next((item for item in ledger["records"] if item["candidate_id"] == self.candidate_id), None)
            if not record or record["status"] != "started" or record["attempts"][-1]["attempt_id"] != self.attempt_id:
                raise ValueError("Only the current started attempt may be finished")
            attempt = record["attempts"][-1]
            if status == "failed" and attempt.get("requests_started_at_utc") and report is None:
                raise ValueError("A failed attempt after request start requires a partial report")
            if status == "completed" and not attempt.get("requests_started_at_utc"):
                raise ValueError("A completed attempt must record request start first")
            if status == "completed":
                plan, _, plan_file_sha = _read_plan(_operational_ledger_PROJECT_ROOT / attempt["plan_path"])
                identity = plan["identity"]
                if (plan["plan_sha256"] != self.plan_sha256 or plan_file_sha != self.plan_file_sha256
                        or not isinstance(body.get("model_identity"), dict)
                        or any(body["model_identity"].get(key) != value for key, value in {
                            "candidate_id": self.candidate_id, "model_name": identity["model_name"],
                            "model_version": identity["model_version"],
                            "artifact_sha256": identity["registered_artifact_sha256"],
                        }.items())):
                    raise ValueError("Operational plan or report model identity changed during the attempt")
            if report is not None:
                _, current_hash = _read_json_bytes(_operational_ledger_PROJECT_ROOT / report["path"], "operational report")
                if current_hash != report["sha256"]:
                    raise ValueError("Operational report changed before ledger commit")
            attempt.update({"status": status, "finished_at_utc": _now(), "failure_code": failure_code, "report": report})
            record["status"] = status

        # Validate before accepting the result, but defer the terminal ledger
        # write until the surrounding measurement context exits successfully.
        change(read_operational_ledger(self.ledger_path))
        self._finish_action = change
        self._finished = True

    def _interrupt_without_report(self) -> None:
        def change(ledger):
            record = next((item for item in ledger["records"] if item["candidate_id"] == self.candidate_id), None)
            if not record or record["status"] != "started" or record["attempts"][-1]["attempt_id"] != self.attempt_id:
                raise ValueError("Only the current started attempt can be interrupted")
            attempt = record["attempts"][-1]
            attempt.update({"status": "interrupted", "finished_at_utc": _now(), "failure_code": "interrupted",
                            "partial_evidence_missing": bool(attempt.get("requests_started_at_utc"))})
            record["status"] = "interrupted"

        _mutate(self.ledger_path, change)


@contextmanager
def measurement_attempt(plan_path: _operational_ledger_Path, *, ledger_path: _operational_ledger_Path = DEFAULT_LEDGER,
                        lock_dir: _operational_ledger_Path = DEFAULT_LOCK_DIR):
    """Record a started attempt durably before yielding; retain its lock until finished."""
    first, _, first_file_sha = _read_plan(plan_path)
    candidate_id = first["identity"]["candidate_id"]
    with _exclusive_lock(lock_dir / f"{candidate_id}.lock"):
        plan, relative, file_sha = _read_plan(plan_path)
        if file_sha != first_file_sha or plan["identity"]["candidate_id"] != candidate_id:
            raise ValueError("Operational plan changed while acquiring the candidate lock")
        attempt_id = str(_operational_ledger_uuid.uuid4())
        attempt = {"attempt_id": attempt_id, "status": "started", "plan_path": relative,
                   "plan_sha256": plan["plan_sha256"], "plan_file_sha256": file_sha,
                   "model_version": plan["identity"]["model_version"], "registry_uri": plan["registry_uri"],
                   "started_at_utc": _now(), "requests_started_at_utc": None, "report": None, "failure_code": None}

        def start(ledger):
            record = next((item for item in ledger["records"] if item["candidate_id"] == candidate_id), None)
            if record is None:
                record = {"candidate_id": candidate_id, "status": "started", "attempts": [], "reviews": []}
                ledger["records"].append(record)
            elif record["status"] != "reviewed_reopen":
                raise ValueError(f"Candidate already has an operational attempt (status={record['status']})")
            elif (record["reviews"][-1]["next_plan_sha256"] != plan["plan_sha256"]
                  or record["reviews"][-1]["next_plan_file_sha256"] != file_sha):
                raise ValueError("Operational retry plan differs from the reviewed plan")
            record["attempts"].append(attempt)
            record["status"] = "started"

        _mutate(ledger_path, start)
        lease = MeasurementAttempt(attempt_id, candidate_id, plan["plan_sha256"], file_sha, ledger_path)
        try:
            yield lease
        except BaseException as error:
            try:
                lease._interrupt_without_report()
            except Exception as ledger_error:
                error.add_note(f"Could not persist interrupted attempt: {type(ledger_error).__name__}")
            raise
        else:
            if lease._finished:
                try:
                    _mutate(ledger_path, lease._finish_action)
                except BaseException:
                    # If validation or the write fails, never claim success.
                    # An atomic replace may already have succeeded before a
                    # directory fsync error; preserve the original exception.
                    try:
                        lease._interrupt_without_report()
                    except Exception:
                        pass
                    raise
            else:
                lease._interrupt_without_report()
        finally:
            lease._active = False


def review_operational_retry(candidate_id: str, operator: str, reason: str, *, next_plan_path: _operational_ledger_Path,
                             ledger_path: _operational_ledger_Path = DEFAULT_LEDGER, lock_dir: _operational_ledger_Path = DEFAULT_LOCK_DIR) -> None:
    """Require reasoned review and prove no cooperative runner still owns the lock."""
    if not isinstance(candidate_id, str) or not _SHA.fullmatch(candidate_id):
        raise ValueError("A lowercase candidate ID is required")
    if not isinstance(operator, str) or not operator.strip() or not isinstance(reason, str) or not reason.strip():
        raise ValueError("A named operator and a reason are required for retry review")
    if len(operator) > 128 or len(reason) > 1000:
        raise ValueError("Retry review operator or reason is too long")
    plan, relative, file_sha = _read_plan(next_plan_path)
    if plan["identity"]["candidate_id"] != candidate_id:
        raise ValueError("Retry plan belongs to a different candidate")
    with _exclusive_lock(lock_dir / f"{candidate_id}.lock"):
        current, _, current_file_sha = _read_plan(next_plan_path)
        if current_file_sha != file_sha or current["plan_sha256"] != plan["plan_sha256"]:
            raise ValueError("Retry plan changed while acquiring the candidate lock")
        def review(ledger):
            record = next((item for item in ledger["records"] if item["candidate_id"] == candidate_id), None)
            if not record or record["status"] not in {"failed", "started", "interrupted"}:
                raise ValueError("Only a failed or interrupted operational attempt can be reviewed")
            previous = record["attempts"][-1]
            if previous["status"] == "started":
                previous.update({"status": "interrupted", "finished_at_utc": _now(), "failure_code": "interrupted",
                                 "partial_evidence_missing": bool(previous.get("requests_started_at_utc"))})
            record.setdefault("reviews", []).append({"attempt_id": previous["attempt_id"], "operator": operator.strip(),
                                                     "reason": reason.strip(), "reviewed_at_utc": _now(),
                                                     "next_plan_path": relative, "next_plan_sha256": plan["plan_sha256"],
                                                     "next_plan_file_sha256": file_sha})
            record["status"] = "reviewed_reopen"

        _mutate(ledger_path, review)
