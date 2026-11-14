"""Product components: config."""
from __future__ import annotations


# ========================================================================
# profiles
# ========================================================================

"""Load and validate the versioned dataset/target profiles used by one runtime."""



import json
import re
from pathlib import Path as _profiles_Path

_profiles_PROJECT_ROOT = _profiles_Path(__file__).resolve().parents[2]
PROFILE_DIR = _profiles_PROJECT_ROOT / "configs/data-profiles"
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def _profiles_load_data_profile(profile_id: str) -> dict:
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
        candidate = _profiles_Path(value) if isinstance(value, str) else _profiles_Path("/")
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


# ========================================================================
# timezones
# ========================================================================

"""Load pinned IANA timezone data from the locked Python tzdata package."""



import hashlib
from functools import lru_cache
from importlib.resources import files
from io import BytesIO
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

import tzdata


def _validated_key(key: str) -> tuple[str, ...]:
    if not isinstance(key, str) or not key or "\\" in key:
        raise ValueError("Timezone key must be a non-empty IANA path")
    parts = PurePosixPath(key).parts
    if PurePosixPath(key).is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Timezone key must be a relative IANA path without traversal components")
    return parts


@lru_cache(maxsize=64)
def _timezone_tzif(key: str) -> bytes:
    resource = files("tzdata.zoneinfo")
    for part in _validated_key(key):
        resource = resource.joinpath(part)
    try:
        with resource.open("rb") as stream:
            return stream.read()
    except (FileNotFoundError, IsADirectoryError) as error:
        raise ValueError(f"Timezone {key!r} is not present in the locked tzdata package") from error


@lru_cache(maxsize=64)
def load_pinned_timezone(key: str) -> ZoneInfo:
    """Create a ZoneInfo from tzdata package bytes, bypassing host TZPATH."""
    return ZoneInfo.from_file(BytesIO(_timezone_tzif(key)), key=key)


def timezone_data_identity(key: str) -> dict[str, str | int]:
    """Return a reproducible identity for the exact TZif bytes used by the app."""
    tzif = _timezone_tzif(key)
    return {
        "provider": "python-tzdata-package",
        "package": "tzdata",
        "package_version": tzdata.__version__,
        "zone_name": key,
        "zone_file_bytes": len(tzif),
        "zone_file_sha256": hashlib.sha256(tzif).hexdigest(),
    }


# ========================================================================
# config
# ========================================================================

import os
from pathlib import Path

load_data_profile = _profiles_load_data_profile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REPORT_DIR = DATA_DIR / "reports"
DATA_PROFILE_ID = os.getenv("DATA_PROFILE_ID", "uci-bike-sharing")
DATA_PROFILE = load_data_profile(DATA_PROFILE_ID)
TARGET_CONTRACT_ID = DATA_PROFILE["target_contract_id"]
profile_suffix = "" if DATA_PROFILE_ID == "uci-bike-sharing" else f"-{DATA_PROFILE_ID}"
if profile_suffix:
    REPORT_DIR = REPORT_DIR / DATA_PROFILE_ID
RUNTIME_DIR = DATA_DIR / (
    "runtime" if DATA_PROFILE_ID == "uci-bike-sharing" else f"runtime-{DATA_PROFILE_ID}"
)
DATASET_URL = "https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip"
DATASET_ARCHIVE = RAW_DIR / "bike-sharing-dataset.zip"
DATASET_MANIFEST = PROJECT_ROOT / DATA_PROFILE["default_manifest"]
DEFAULT_DATASET = PROJECT_ROOT / DATA_PROFILE["default_csv"]
MODEL_NAME = os.getenv("MLFLOW_MODEL_NAME", DATA_PROFILE["model_name"])
FEATURE_CONTRACT_VERSION = os.getenv(
    "FEATURE_CONTRACT_VERSION", DATA_PROFILE["feature_contract_version"]
)
LABEL_DELAY_HOURS = DATA_PROFILE["label_availability"]["delay_hours"]
DEFAULT_DATABASE_URL = os.getenv(
    "DATABASE_URL", f"sqlite:///{DATA_DIR / f'bikeops{profile_suffix}.db'}"
)
DEFAULT_MLFLOW_URI = os.getenv(
    "MLFLOW_TRACKING_URI", f"sqlite:///{DATA_DIR / f'mlflow{profile_suffix}.db'}"
)
