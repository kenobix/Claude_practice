from __future__ import annotations

import hashlib
import json
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pandas as pd

from bike_mlops.config import DATASET_ARCHIVE, DATASET_MANIFEST, DATASET_URL, DEFAULT_DATASET, LABEL_DELAY_HOURS

REQUIRED_COLUMNS = {"dteday", "hr", "cnt"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_dataset(
    archive_path: Path = DATASET_ARCHIVE,
    csv_path: Path = DEFAULT_DATASET,
    manifest_path: Path = DATASET_MANIFEST,
) -> dict:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        DATASET_URL,
        headers={"User-Agent": "bike-demand-mlops-learning-project/0.1"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        archive_path.write_bytes(response.read())
    with zipfile.ZipFile(archive_path) as archive:
        matches = [name for name in archive.namelist() if PurePosixPath(name).name == "hour.csv"]
        if len(matches) != 1:
            raise ValueError(f"Expected one hour.csv in UCI archive, found {matches}")
        csv_path.write_bytes(archive.read(matches[0]))
    manifest = {
        "source": "UCI Bike Sharing Dataset",
        "url": DATASET_URL,
        "citation": "Fanaee-T, H. (2013). Bike Sharing [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5W894.",
        "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
        "archive_file": archive_path.name,
        "archive_sha256": sha256_file(archive_path),
        "data_file": csv_path.name,
        "data_sha256": sha256_file(csv_path),
        "license": "CC BY 4.0",
        "processing": "Extracted hour.csv from the source ZIP; no row edits.",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def load_hourly(path: Path = DEFAULT_DATASET) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing_columns = sorted(REQUIRED_COLUMNS.difference(frame.columns))
    if missing_columns:
        raise ValueError(f"Missing required UCI columns: {missing_columns}")
    date = pd.to_datetime(frame["dteday"], errors="raise")
    hour = pd.to_numeric(frame["hr"], errors="raise")
    if not hour.between(0, 23).all() or not (hour % 1 == 0).all():
        raise ValueError("hr must contain integer values from 0 to 23")
    cnt = pd.to_numeric(frame["cnt"], errors="raise")
    if (cnt < 0).any() or not (cnt % 1 == 0).all():
        raise ValueError("cnt must contain non-negative integer values")
    result = pd.DataFrame(
        {
            "timestamp": date + pd.to_timedelta(hour, unit="h"),
            "cnt": cnt.astype("int64"),
        }
    ).sort_values("timestamp", kind="stable").reset_index(drop=True)
    if result["timestamp"].duplicated().any():
        duplicated = result.loc[result["timestamp"].duplicated(), "timestamp"].head(5)
        raise ValueError(f"Duplicate dataset timestamps: {duplicated.tolist()}")
    if result.empty:
        raise ValueError("Dataset is empty")
    if result["timestamp"].dt.tz is not None:
        raise ValueError("Expected dataset-local naive timestamps; do not silently convert timezone")
    return result


def audit_hourly(frame: pd.DataFrame, data_hash: str | None = None) -> dict:
    timestamps = frame["timestamp"].sort_values()
    gaps = timestamps.diff().dropna()
    gap_counts = gaps.value_counts().sort_index()
    start, end = timestamps.iloc[0], timestamps.iloc[-1]
    expected_hours = int((end - start) / timedelta(hours=1)) + 1
    return {
        "rows": int(len(frame)),
        "start": start.isoformat(sep=" "),
        "end": end.isoformat(sep=" "),
        "expected_hours_in_range": expected_hours,
        "missing_hour_slots_in_range": int(expected_hours - len(frame)),
        "duplicate_timestamps": int(frame["timestamp"].duplicated().sum()),
        "target_min": int(frame["cnt"].min()),
        "target_max": int(frame["cnt"].max()),
        "target_mean": float(frame["cnt"].mean()),
        "gap_intervals": {str(key): int(value) for key, value in gap_counts.items()},
        "data_sha256": data_hash,
        "timezone_interpretation": "naive dataset-local timestamp; source evidence does not define conversion",
        "label_available_at_rule": f"timestamp + {LABEL_DELAY_HOURS} hours (experiment assumption)",
    }
