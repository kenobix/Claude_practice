"""Fixed, sequential local operational benchmark for the Capital API."""

from __future__ import annotations

import json
import hashlib
import importlib.metadata
import math
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

from bike_mlops.config import DATA_PROFILE, DATA_PROFILE_ID, PROJECT_ROOT, TARGET_CONTRACT_ID
from bike_mlops.timezones import load_pinned_timezone

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
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
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
    latency_values_valid = all(math.isfinite(float(value)) and float(value) >= 0 for value in latencies_ms)
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
        except (json.JSONDecodeError, ValueError):
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
    if not math.isfinite(float(prediction)) or float(prediction) < 0:
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
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith(("model name", "hardware")) and ":" in line:
                cpu_model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    memory_total_bytes = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
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
            "logical_cpu_count": os.cpu_count(),
            "cpu_model": cpu_model,
            "memory_total_bytes": memory_total_bytes,
            "python": sys.version.split()[0],
            "dependencies": dependencies,
        },
        "capital_stack_containers": {"status": "unavailable", "containers": [], "stats": []},
    }
    compose = [
        "docker", "compose", "--project-name", "bike-mlops-capital",
        "-f", str(PROJECT_ROOT / "compose.yaml"),
        "-f", str(PROJECT_ROOT / "compose.capital.yaml"),
        "-f", str(PROJECT_ROOT / "compose.capital-staging.yaml"),
    ]
    try:
        listed = subprocess.run(
            compose + ["ps", "--status", "running", "-q", "postgres", "mlflow", "api-blue", "api-green", "caddy", "postgres-staging", "api-staging"],
            check=True, capture_output=True, text=True, timeout=15,
        )
        container_ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
        if not container_ids:
            result["capital_stack_containers"]["status"] = "no_running_services"
            return result
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{.Name}}|{{.Config.Image}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}", *container_ids],
            check=True, capture_output=True, text=True, timeout=15,
        )
        stats = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Container}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}", *container_ids],
            check=True, capture_output=True, text=True, timeout=15,
        )
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
        result["capital_stack_containers"] = {
            "status": "unavailable",
            "containers": [],
            "stats": [],
            "detail": f"{type(error).__name__}: {error}",
        }
    return result


def _validate_timezone_grid(start_time: pd.Timestamp) -> None:
    zone_name = DATA_PROFILE["target"].get("timezone_name")
    if not zone_name:
        return
    zone = load_pinned_timezone(zone_name)
    for offset in range(INPUT_WINDOW_HOURS):
        wall = (start_time + timedelta(hours=offset)).to_pydatetime()
        first = wall.replace(tzinfo=zone, fold=0)
        second = wall.replace(tzinfo=zone, fold=1)
        roundtrip = first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
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
    except Exception:
        if request_client is not None:
            request_client.close()
        raise

    benchmark_id = str(uuid.uuid4())
    request_ids: list[str] = []
    errors: list[dict] = []
    latencies_ms: list[float] = []
    measurements: list[dict] = []
    error_counts = {"warmup": 0, "measured": 0}
    total = warmup_count + measured_count
    interrupted = False
    pending = None

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
            target = start_time + timedelta(hours=phase_index % INPUT_WINDOW_HOURS)
            target_text = target.isoformat(sep=" ")
            payload = {
                "request_id": request_id,
                "target_hour": target_text,
                "as_of": target_text,
                "feature_contract_version": DATA_PROFILE["feature_contract_version"],
                "data_profile_id": DATA_PROFILE_ID,
                "target_contract_id": TARGET_CONTRACT_ID,
            }
            began = time.perf_counter()
            pending = (phase, phase_index, request_id, target_text, began)
            status_code = None
            response = None
            try:
                status_code, response = request_fn(f"{base_url}/predict", payload)
                latency_ms = (time.perf_counter() - began) * 1000.0
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
            except KeyboardInterrupt:
                latency_ms = (time.perf_counter() - began) * 1000.0
                error = "KeyboardInterrupt: request interrupted; benchmark is incomplete"
                interrupted = True
            except Exception as exception:  # Preserve every failed request; never retry.
                latency_ms = (time.perf_counter() - began) * 1000.0
                error = f"{type(exception).__name__}: {exception}"
            record_measurement(
                phase, phase_index, request_id, target_text, status_code, latency_ms, response, error
            )
            pending = None
            if interrupted:
                break
    except KeyboardInterrupt:
        interrupted = True
        if pending is not None:
            phase, phase_index, request_id, target_text, began = pending
            record_measurement(
                phase,
                phase_index,
                request_id,
                target_text,
                None,
                (time.perf_counter() - began) * 1000.0,
                None,
                "KeyboardInterrupt: request interrupted; benchmark is incomplete",
            )
    finally:
        if request_client is not None:
            request_client.close()

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
    gate["observed"]["request_id_sha256"] = hashlib.sha256(
        "\n".join(request_ids).encode("utf-8")
    ).hexdigest()
    try:
        resource_context = resource_fn()
    except KeyboardInterrupt:
        interrupted = True
        gate["status"] = "failed"
        gate["checks"]["completed"] = False
        resource_context = {"status": "interrupted", "detail": "Resource capture interrupted"}
    return {
        "schema_version": 1,
        "benchmark_id": benchmark_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
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
            "A successful measurement does not independently authorize model deployment.",
        ],
    }


def write_benchmark_report(path: Path, report: dict) -> None:
    resolved = path.resolve()
    report_root = (PROJECT_ROOT / "data/reports/capital-bikeshare").resolve()
    if not resolved.is_relative_to(report_root) or resolved.suffix.lower() != ".json":
        raise ValueError("Benchmark reports must be new JSON files under data/reports/capital-bikeshare/")
    if resolved.exists():
        raise FileExistsError(f"Refusing to overwrite operational benchmark report: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.link(temporary, resolved)
    finally:
        temporary.unlink(missing_ok=True)
