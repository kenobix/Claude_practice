"""Load pinned IANA timezone data from the locked Python tzdata package."""

from __future__ import annotations

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
