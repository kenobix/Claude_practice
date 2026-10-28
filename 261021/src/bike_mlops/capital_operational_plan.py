"""Freeze a candidate-specific operational plan before issuing prediction requests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd

from bike_mlops.capital_staging import _registration_tags, validate_staging_model_tags
from bike_mlops.config import LABEL_DELAY_HOURS, PROJECT_ROOT
from bike_mlops.model import directory_sha256
from bike_mlops.profiles import load_data_profile
from bike_mlops.operational_benchmark import (
    INPUT_WINDOW_HOURS, MEASURED_REQUESTS, P95_LIMIT_MS, REQUEST_TIMEOUT_SECONDS,
    WARMUP_REQUESTS, _validate_timezone_grid,
)

PLAN_POLICY = {
    "warmup_requests": WARMUP_REQUESTS,
    "measured_requests": MEASURED_REQUESTS,
    "concurrency": 1,
    "retries_allowed": 0,
    "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
    "p95_limit_ms": P95_LIMIT_MS,
    "p95_method": "nearest_rank",
    "target_sequence": "cycle_168_hours_from_start_reset_per_phase",
    "request_id": "unique_benchmark_uuid_phase_index",
    "as_of": "equal_to_target_hour",
    "accuracy_scoring": False,
}


def _digest(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "plan_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _sha(value: object, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _staging_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.port != 8110
            or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Operational plan requires the local staging API on port 8110")
    return url.rstrip("/")


def local_registry_url(url: str) -> str:
    parsed = urlsplit(url)
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


def build_operational_plan(*, evidence: dict, window: dict, registered, artifact_dir: Path,
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
    start = pd.Timestamp(window["start_inclusive"])
    end = pd.Timestamp(window["end_exclusive"])
    if (pd.isna(start) or pd.isna(end) or start.tzinfo is not None or end.tzinfo is not None
            or start != start.floor("h") or end != end.floor("h")
            or end - start != pd.Timedelta(hours=INPUT_WINDOW_HOURS) or window.get("hours") != INPUT_WINDOW_HOURS):
        raise ValueError("Operational input window must contain exactly 168 aligned naive hours")
    cutoff = pd.Timestamp(window["training_cutoff"])
    delay = window.get("label_delay_hours")
    latest = end - pd.Timedelta(hours=1) + pd.Timedelta(hours=delay) if isinstance(delay, int) and not isinstance(delay, bool) else pd.NaT
    if (pd.isna(cutoff) or cutoff.tzinfo is not None or cutoff != cutoff.floor("h") or pd.isna(latest)
            or delay != LABEL_DELAY_HOURS or latest > cutoff
            or latest.isoformat(sep=" ") != window.get("latest_replay_label_available_at")):
        raise ValueError("Operational input labels are not available by the candidate training cutoff")
    _validate_timezone_grid(start)
    if not isinstance(resource_context, dict):
        raise ValueError("Operational plan requires an object describing the resource context")
    plan = {
        "schema_version": 2,
        "kind": "capital-operational-plan",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "identity": {**expected, "model_name": registered.name, "model_version": version,
                     "data_profile_id": profile["profile_id"], "target_contract_id": profile["target_contract_id"],
                     "candidate_artifact_sha256": evidence["candidate_artifact_sha256"],
                     "registered_artifact_sha256": artifact_sha,
                     "feature_contract_version": selection["feature_contract_version"]},
        "input_window": dict(window),
        "api_url": _staging_url(api_url),
        "registry_uri": local_registry_url(registry_uri),
        "policy": dict(PLAN_POLICY),
        "resource_context_before_requests": resource_context,
        "other_host_load": "not fully measured; assess separately before deciding operational eligibility",
        "deployment_permission": "blocked",
    }
    plan["plan_sha256"] = _digest(plan)
    return plan


def validate_operational_plan(plan: object) -> dict:
    """Validate integrity and fixed policy; actual candidate/DB must be rechecked before use."""
    if (not isinstance(plan, dict) or type(plan.get("schema_version")) is not int
            or plan.get("schema_version") != 2 or plan.get("kind") != "capital-operational-plan"):
        raise ValueError("Unsupported operational plan")
    if plan.get("plan_sha256") != _digest(plan):
        raise ValueError("Operational plan SHA-256 mismatch")
    if (json.dumps(plan.get("policy"), sort_keys=True, separators=(",", ":"))
            != json.dumps(PLAN_POLICY, sort_keys=True, separators=(",", ":"))
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
    start, end = pd.Timestamp(window.get("start_inclusive")), pd.Timestamp(window.get("end_exclusive"))
    if (pd.isna(start) or pd.isna(end) or start.tzinfo is not None or end.tzinfo is not None
            or start != start.floor("h") or end != end.floor("h")
            or end - start != pd.Timedelta(hours=INPUT_WINDOW_HOURS) or window.get("hours") != INPUT_WINDOW_HOURS):
        raise ValueError("Operational plan training window is invalid")
    cutoff = pd.Timestamp(window.get("training_cutoff"))
    delay = window.get("label_delay_hours")
    rows = window.get("training_rows")
    if (pd.isna(cutoff) or cutoff.tzinfo is not None or cutoff != cutoff.floor("h")
            or isinstance(delay, bool) or not isinstance(delay, int) or delay != LABEL_DELAY_HOURS
            or isinstance(rows, bool) or not isinstance(rows, int) or rows < INPUT_WINDOW_HOURS):
        raise ValueError("Operational plan training cutoff, label delay or row count is invalid")
    latest = end - pd.Timedelta(hours=1) + pd.Timedelta(hours=delay)
    if latest > cutoff or latest.isoformat(sep=" ") != window.get("latest_replay_label_available_at"):
        raise ValueError("Operational plan contains labels unavailable at the training cutoff")
    _validate_timezone_grid(start)
    if not isinstance(plan.get("resource_context_before_requests"), dict):
        raise ValueError("Operational plan resource context is missing")
    return plan


def verify_operational_plan_binding(plan: dict, *, evidence: dict, window: dict, registered, artifact_dir: Path,
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


def save_operational_plan(plan: dict, output: Path) -> Path:
    validate_operational_plan(plan)
    resolved = output.resolve()
    root = (PROJECT_ROOT / "data/evaluations/capital-bikeshare/operational-plans").resolve()
    if not resolved.is_relative_to(root) or resolved.suffix != ".json":
        raise ValueError("Operational plan must be a JSON file inside the operational-plans directory")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=resolved.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(plan, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, resolved)
    except FileExistsError as error:
        raise FileExistsError(f"Refusing to overwrite operational plan: {resolved}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return resolved


def read_operational_plan(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read operational plan: {error}") from error
    return validate_operational_plan(value)
