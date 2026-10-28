"""Load and validate the versioned dataset/target profiles used by one runtime."""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = PROJECT_ROOT / "configs/data-profiles"
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def load_data_profile(profile_id: str) -> dict:
    if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id):
        raise ValueError("DATA_PROFILE_ID must be a lowercase identifier using letters, digits, and hyphens")
    path = PROFILE_DIR / f"{profile_id}.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Unknown data profile {profile_id!r}; expected a file under {PROFILE_DIR}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Data profile {profile_id!r} is not valid JSON: {error}") from error
    if not isinstance(profile, dict):
        raise ValueError(f"Data profile {profile_id!r} must be a JSON object")
    if profile.get("schema_version") != 1 or profile.get("profile_id") != profile_id:
        raise ValueError(f"Data profile {profile_id!r} has a mismatched identifier or unsupported schema")
    for field in ("target_contract_id", "feature_contract_version", "model_name"):
        if not isinstance(profile.get(field), str) or not profile[field].strip():
            raise ValueError(f"Data profile {profile_id!r} requires a non-empty {field}")
    label_availability = profile.get("label_availability")
    if not isinstance(label_availability, dict):
        raise ValueError(f"Data profile {profile_id!r} must declare label_availability as an object")
    delay = label_availability.get("delay_hours")
    if isinstance(delay, bool) or not isinstance(delay, int) or delay < 0:
        raise ValueError(f"Data profile {profile_id!r} has an invalid label delay")
    if not isinstance(profile.get("source"), dict) or not isinstance(profile.get("target"), dict):
        raise ValueError(f"Data profile {profile_id!r} must declare source and target objects")
    timezone_name = profile["target"].get("timezone_name")
    if timezone_name is not None and (not isinstance(timezone_name, str) or not timezone_name.strip()):
        raise ValueError(f"Data profile {profile_id!r} has an invalid target.timezone_name")
    if profile.get("status") not in {"current", "migration-only"}:
        raise ValueError(f"Data profile {profile_id!r} has an unsupported status")
    for field in ("default_csv", "default_manifest"):
        value = profile.get(field)
        candidate = Path(value) if isinstance(value, str) else Path("/")
        if candidate.is_absolute() or ".." in candidate.parts or not value:
            raise ValueError(f"Data profile {profile_id!r} has an unsafe {field} path")
    reference = profile.get("monitor_reference")
    if reference is not None:
        if not isinstance(reference, dict) or not all(
            isinstance(reference.get(key), str)
            for key in ("start_inclusive", "end_exclusive", "source")
        ):
            raise ValueError(f"Data profile {profile_id!r} has an invalid monitor_reference")
    ports = profile.get("runtime_ports")
    required_ports = {"postgres", "mlflow", "api_blue", "api_green", "proxy"}
    if not isinstance(ports, dict) or set(ports) != required_ports:
        raise ValueError(f"Data profile {profile_id!r} must declare all runtime ports")
    if any(isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535 for port in ports.values()):
        raise ValueError(f"Data profile {profile_id!r} has an invalid runtime port")
    if len(set(ports.values())) != len(ports):
        raise ValueError(f"Data profile {profile_id!r} must use distinct runtime ports")
    return profile
