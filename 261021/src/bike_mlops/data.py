"""Product components: data."""
from __future__ import annotations


# ========================================================================
# data
# ========================================================================

import hashlib
import json
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pandas as pd

from bike_mlops.config import DATA_PROFILE, DATA_PROFILE_ID, DATASET_ARCHIVE, DATASET_MANIFEST, DATASET_URL, DEFAULT_DATASET, LABEL_DELAY_HOURS, TARGET_CONTRACT_ID

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
    if DATA_PROFILE_ID != "uci-bike-sharing":
        raise ValueError("The built-in ZIP downloader is only valid for the uci-bike-sharing profile")
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
        raise ValueError(f"Missing required hourly data columns: {missing_columns}")
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
        "data_profile_id": DATA_PROFILE_ID,
        "target_contract_id": TARGET_CONTRACT_ID,
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
        "timezone_interpretation": frame.attrs.get(
            "timezone_interpretation", DATA_PROFILE["target"]["time_zone_interpretation"]
        ),
        "label_available_at_rule": (
            f"timestamp + {LABEL_DELAY_HOURS} hours ({DATA_PROFILE['label_availability']['status']})"
        ),
    }


# ========================================================================
# capital_data
# ========================================================================

"""Validation helpers for the derived Capital Bikeshare hourly dataset."""



import re
from pathlib import Path as _data_Path

import pandas as _data_pd

_data_load_hourly = load_hourly
_data_sha256_file = sha256_file
from bike_mlops.config import _profiles_load_data_profile as load_data_profile
from bike_mlops.config import load_pinned_timezone, timezone_data_identity

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_SCHEMA_VERSION = 3
LEGACY_MANIFEST_SCHEMA_VERSION = 2
CAPITAL_PROFILE = load_data_profile("capital-bikeshare")
CAPITAL_TIMEZONE = CAPITAL_PROFILE["target"]["timezone_name"]
DEFAULT_RAW_ARCHIVE_DIR = _data_Path(__file__).resolve().parents[2] / "data/raw/capitalbikeshare/monthly"


def _integer(value, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"Capital Bikeshare manifest field {name!r} must be an integer >= {minimum}")
    return value


def _timestamp(value, name: str) -> _data_pd.Timestamp:
    if not isinstance(value, str):
        raise ValueError(f"Capital Bikeshare manifest field {name!r} must be a timestamp string")
    try:
        stamp = _data_pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Capital Bikeshare manifest field {name!r} is not a valid timestamp") from error
    if stamp.tzinfo is not None or stamp != stamp.floor("h"):
        raise ValueError(f"Capital Bikeshare manifest field {name!r} must be a naive hour boundary")
    return stamp


def _source_timestamp(value, name: str) -> _data_pd.Timestamp:
    if not isinstance(value, str):
        raise ValueError(f"Capital Bikeshare manifest field {name!r} must be a timestamp string")
    try:
        stamp = _data_pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Capital Bikeshare manifest field {name!r} is not a valid timestamp") from error
    if stamp.tzinfo is not None:
        raise ValueError(f"Capital Bikeshare manifest field {name!r} must be timezone-naive")
    return stamp


def validate_capital_manifest(
    manifest: object,
    csv_path: _data_Path,
    raw_dir: _data_Path = DEFAULT_RAW_ARCHIVE_DIR,
) -> tuple[dict, _data_pd.DataFrame]:
    """Validate manifest schema, archive coverage, row accounting, and CSV identity.

    This is intentionally stricter than the original 2026-10-28 acquisition
    manifest. Old manifests remain valid historical records, but are not accepted
    as input to newly frozen candidate/evaluation protocols.
    """
    if not isinstance(manifest, dict):
        raise ValueError("Capital Bikeshare source manifest must be a JSON object")
    schema_version = manifest.get("manifest_schema_version")
    if schema_version not in {LEGACY_MANIFEST_SCHEMA_VERSION, MANIFEST_SCHEMA_VERSION}:
        raise ValueError(
            "Capital Bikeshare source manifest schema is unsupported; regenerate into new output paths "
            f"(supported schemas {LEGACY_MANIFEST_SCHEMA_VERSION} and {MANIFEST_SCHEMA_VERSION})"
        )
    required_strings = ("source", "source_page", "license_page", "license_summary")
    for key in required_strings:
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            raise ValueError(f"Capital Bikeshare source manifest requires non-empty string {key!r}")
    processing = manifest.get("processing")
    if not isinstance(processing, dict):
        raise ValueError("Capital Bikeshare source manifest field 'processing' must be an object")
    if processing.get("contract_version") != "capital-trip-starts-hourly-v1":
        raise ValueError("Capital Bikeshare target contract is missing or unsupported")
    expected_processing_version = (
        "capital-hourly-aggregation-v3"
        if schema_version == MANIFEST_SCHEMA_VERSION
        else "capital-hourly-aggregation-v2"
    )
    if processing.get("processing_version") != expected_processing_version:
        raise ValueError("Capital Bikeshare processing version is missing or unsupported")
    if schema_version == MANIFEST_SCHEMA_VERSION:
        if manifest.get("timezone_database") != timezone_data_identity(CAPITAL_TIMEZONE):
            raise ValueError("Capital Bikeshare manifest timezone database identity does not match the locked provider")
    if processing.get("input_field") != "started_at" or processing.get("source_time_strings_have_timezone_offset") is not False:
        raise ValueError("Capital Bikeshare timestamp input contract is missing or unsupported")
    window = manifest.get("data_window")
    if not isinstance(window, dict):
        raise ValueError("Capital Bikeshare source manifest field 'data_window' must be an object")
    start = _timestamp(window.get("start_inclusive"), "data_window.start_inclusive")
    end = _timestamp(window.get("end_exclusive"), "data_window.end_exclusive")
    if start >= end:
        raise ValueError("Capital Bikeshare data window must be non-empty and ordered")

    archives = manifest.get("archive_months")
    if not isinstance(archives, list) or not archives:
        raise ValueError("Capital Bikeshare source manifest must contain a non-empty archive_months list")
    first_archive_month = archives[0].get("month") if isinstance(archives[0], dict) else None
    if not isinstance(first_archive_month, str) or not re.fullmatch(r"\d{6}", first_archive_month):
        raise ValueError("Capital Bikeshare archive_months[0].month must use YYYYMM")
    expected_months = _data_pd.period_range(
        f"{first_archive_month[:4]}-{first_archive_month[4:]}", periods=len(archives), freq="M"
    )
    seen_months: list[str] = []
    total_archive_rows = 0
    total_archive_duplicates = 0
    total_archive_unique = 0
    source_spans = []
    for index, archive in enumerate(archives):
        if not isinstance(archive, dict):
            raise ValueError(f"Capital Bikeshare archive_months[{index}] must be an object")
        month = archive.get("month")
        if not isinstance(month, str) or not re.fullmatch(r"\d{6}", month):
            raise ValueError(f"Capital Bikeshare archive_months[{index}].month must use YYYYMM")
        if month in seen_months:
            raise ValueError(f"Duplicate Capital Bikeshare archive month: {month}")
        seen_months.append(month)
        try:
            parsed_month = _data_pd.Period(f"{month[:4]}-{month[4:]}", freq="M")
        except (ValueError, TypeError) as error:
            raise ValueError(f"Invalid Capital Bikeshare archive month: {month}") from error
        if parsed_month != expected_months[index]:
            raise ValueError("Capital Bikeshare archive months must be sorted and contiguous")
        filename = f"{month}-capitalbikeshare-tripdata.zip"
        if archive.get("filename") != filename:
            raise ValueError(f"Capital Bikeshare archive filename does not match month {month}")
        if not isinstance(archive.get("url"), str) or not archive["url"].startswith("https://"):
            raise ValueError(f"Capital Bikeshare archive {month} must include an HTTPS source URL")
        if not isinstance(archive.get("csv_member"), str) or not archive["csv_member"].lower().endswith(".csv"):
            raise ValueError(f"Capital Bikeshare archive {month} has an invalid CSV member")
        _integer(archive.get("archive_bytes"), f"archive_months[{index}].archive_bytes", minimum=1)
        if not isinstance(archive.get("archive_sha256"), str) or not SHA256_RE.fullmatch(archive["archive_sha256"]):
            raise ValueError(f"Capital Bikeshare archive {month} has an invalid SHA-256")
        local_archive = raw_dir / filename
        if not local_archive.is_file():
            raise FileNotFoundError(f"Capital Bikeshare source archive is missing: {local_archive}")
        if local_archive.stat().st_size != archive.get("archive_bytes"):
            raise ValueError(f"Capital Bikeshare archive byte count changed for {month}")
        if _data_sha256_file(local_archive) != archive["archive_sha256"]:
            raise ValueError(f"Capital Bikeshare archive SHA-256 changed for {month}")
        rows = _integer(archive.get("rows_read"), f"archive_months[{index}].rows_read", minimum=1)
        duplicates = _integer(archive.get("duplicate_rows_skipped"), f"archive_months[{index}].duplicate_rows_skipped")
        unique = _integer(archive.get("unique_ride_ids_added"), f"archive_months[{index}].unique_ride_ids_added")
        if rows != duplicates + unique:
            raise ValueError(f"Capital Bikeshare archive {month} row accounting does not balance")
        source_min = _source_timestamp(archive.get("started_at_min"), f"archive_months[{index}].started_at_min")
        source_max = _source_timestamp(archive.get("started_at_max"), f"archive_months[{index}].started_at_max")
        if source_min > source_max:
            raise ValueError(f"Capital Bikeshare archive {month} has inverted timestamp bounds")
        source_spans.append((source_min.floor("h"), source_max.floor("h") + _data_pd.Timedelta(hours=1)))
        total_archive_rows += rows
        total_archive_duplicates += duplicates
        total_archive_unique += unique

    first_month = _data_pd.Period(start, freq="M")
    last_month = _data_pd.Period(end - _data_pd.Timedelta(nanoseconds=1), freq="M")
    if _data_pd.Period(f"{seen_months[0][:4]}-{seen_months[0][4:]}", freq="M") > first_month:
        raise ValueError("Declared archives do not include the data window's first month")
    if _data_pd.Period(f"{seen_months[-1][:4]}-{seen_months[-1][4:]}", freq="M") < last_month:
        raise ValueError("Declared archives do not include the data window's last month")
    if _integer(manifest.get("archive_count"), "archive_count") != len(archives):
        raise ValueError("Capital Bikeshare archive_count does not match archive_months")
    if _integer(manifest.get("source_rows_read"), "source_rows_read") != total_archive_rows:
        raise ValueError("Capital Bikeshare source_rows_read does not match monthly row totals")
    if _integer(manifest.get("duplicate_rows_skipped"), "duplicate_rows_skipped") != total_archive_duplicates:
        raise ValueError("Capital Bikeshare duplicate_rows_skipped does not match monthly row totals")
    if _integer(manifest.get("unique_ride_ids"), "unique_ride_ids") != total_archive_unique:
        raise ValueError("Capital Bikeshare unique_ride_ids does not match monthly row totals")
    coverage = manifest.get("source_timestamp_coverage")
    if not isinstance(coverage, list) or not coverage:
        raise ValueError("Capital Bikeshare source manifest must include source_timestamp_coverage")
    expected_coverage = []
    for left, right in sorted(source_spans):
        if not expected_coverage or left > expected_coverage[-1][1]:
            expected_coverage.append([left, right])
        else:
            expected_coverage[-1][1] = max(expected_coverage[-1][1], right)
    parsed_coverage = []
    for index, interval in enumerate(coverage):
        if not isinstance(interval, dict):
            raise ValueError(f"source_timestamp_coverage[{index}] must be an object")
        parsed_coverage.append(
            (_timestamp(interval.get("start_inclusive"), f"source_timestamp_coverage[{index}].start_inclusive"),
             _timestamp(interval.get("end_exclusive"), f"source_timestamp_coverage[{index}].end_exclusive"))
        )
    if parsed_coverage != [tuple(pair) for pair in expected_coverage]:
        raise ValueError("source_timestamp_coverage does not match the monthly archive timestamp bounds")
    if not expected_coverage or expected_coverage[0][0] > start or expected_coverage[-1][1] < end:
        raise ValueError("Monthly archive timestamp bounds do not span the declared data window")
    if any(expected_coverage[index][1] < expected_coverage[index + 1][0] for index in range(len(expected_coverage) - 1)):
        raise ValueError("Monthly archive timestamp bounds contain an uncovered gap")

    csv_hash = manifest.get("data_sha256")
    if not isinstance(csv_hash, str) or not SHA256_RE.fullmatch(csv_hash):
        raise ValueError("Capital Bikeshare source manifest has an invalid data_sha256")
    if csv_hash != _data_sha256_file(csv_path) or manifest.get("derived_csv_sha256") != csv_hash:
        raise ValueError("Capital Bikeshare source manifest SHA-256 does not match the selected CSV")
    raw_columns = set(_data_pd.read_csv(csv_path, nrows=0).columns)
    if raw_columns != {"dteday", "hr", "cnt"}:
        raise ValueError("Capital Bikeshare derived CSV must contain exactly dteday, hr, and cnt; ride IDs are not retained")
    frame = _data_load_hourly(csv_path)
    if len(frame) != _integer(manifest.get("output_rows"), "output_rows", minimum=1):
        raise ValueError("Capital Bikeshare output_rows does not match the derived CSV")
    if int(frame["cnt"].sum()) != _integer(manifest.get("output_trip_count"), "output_trip_count"):
        raise ValueError("Capital Bikeshare output_trip_count does not match the derived CSV")
    if total_archive_unique != (
        _integer(manifest.get("unique_out_of_window"), "unique_out_of_window")
        + _integer(manifest.get("unique_dst_excluded"), "unique_dst_excluded")
        + _integer(manifest.get("output_trip_count"), "output_trip_count")
    ):
        raise ValueError("Capital Bikeshare unique-trip accounting does not balance")
    if frame["timestamp"].min() < start or frame["timestamp"].max() >= end:
        raise ValueError("Capital Bikeshare derived timestamps fall outside the declared data window")
    expected_local = _data_pd.date_range(
        start=start, end=end, freq="h", inclusive="left", tz=load_pinned_timezone(CAPITAL_TIMEZONE)
    ).tz_localize(None)
    expected_local = expected_local[~expected_local.duplicated(keep=False)]
    if not frame["timestamp"].reset_index(drop=True).equals(_data_pd.Series(expected_local, name="timestamp")):
        raise ValueError("Capital Bikeshare derived timestamps do not match the valid local-hour grid")
    return manifest, frame


# ========================================================================
# features
# ========================================================================

from datetime import datetime as _features_datetime

import numpy as np
import pandas as _features_pd

from bike_mlops.config import LABEL_DELAY_HOURS as _features_LABEL_DELAY_HOURS

FEATURE_COLUMNS = (
    "hour",
    "weekday",
    "month",
    "day_of_year",
    "is_weekend",
    "lag_24",
    "lag_24_missing",
    "lag_168",
    "lag_168_missing",
)


def _features_timestamp(value: _features_datetime | _features_pd.Timestamp | str) -> _features_pd.Timestamp:
    stamp = _features_pd.Timestamp(value)
    if stamp.tzinfo is not None:
        raise ValueError("Feature contract requires a naive dataset-local timestamp")
    return stamp


def build_feature_row(
    target_hour: _features_datetime | _features_pd.Timestamp | str,
    as_of: _features_datetime | _features_pd.Timestamp | str,
    observations: _features_pd.DataFrame,
) -> dict[str, float]:
    target = _features_timestamp(target_hour)
    decision_time = _features_timestamp(as_of)
    if target != target.floor("h"):
        raise ValueError("target_hour must be aligned to the start of an hour")
    if decision_time != target:
        raise ValueError("MVP contract requires as_of == target_hour")

    required = {"timestamp", "cnt", "available_at"}
    if not required.issubset(observations.columns):
        raise ValueError(f"Observations must include {sorted(required)}")
    available_values: dict[_features_pd.Timestamp, float] = {}
    for row in observations.itertuples(index=False):
        observed_at = _features_timestamp(row.timestamp)
        available_at = _features_timestamp(row.available_at)
        if available_at <= decision_time and observed_at < target:
            available_values[observed_at] = float(row.cnt)

    feature_values: dict[str, float] = {
        "hour": float(target.hour),
        "weekday": float(target.dayofweek),
        "month": float(target.month),
        "day_of_year": float(target.dayofyear),
        "is_weekend": float(target.dayofweek >= 5),
    }
    for lag_hours in (24, 168):
        source_time = target - _features_pd.Timedelta(hours=lag_hours)
        count = available_values.get(source_time)
        feature_values[f"lag_{lag_hours}"] = float(count) if count is not None else float("nan")
        feature_values[f"lag_{lag_hours}_missing"] = float(count is None)
    return feature_values


def feature_frame_for_dataset(frame: _features_pd.DataFrame) -> _features_pd.DataFrame:
    """Build each row using only exact-time observations available at that row's start."""
    counts = dict(zip(frame["timestamp"], frame["cnt"]))
    rows = []
    for row in frame.itertuples(index=False):
        target = _features_timestamp(row.timestamp)
        feature_values = {
            "hour": float(target.hour),
            "weekday": float(target.dayofweek),
            "month": float(target.month),
            "day_of_year": float(target.dayofyear),
            "is_weekend": float(target.dayofweek >= 5),
        }
        for lag_hours in (24, 168):
            source_time = target - _features_pd.Timedelta(hours=lag_hours)
            observed = counts.get(source_time)
            is_available = source_time + _features_pd.Timedelta(hours=_features_LABEL_DELAY_HOURS) <= target
            count = float(observed) if observed is not None and is_available else float("nan")
            feature_values[f"lag_{lag_hours}"] = count
            feature_values[f"lag_{lag_hours}_missing"] = float(np.isnan(count))
        rows.append(feature_values)
    return _features_pd.DataFrame(rows, columns=FEATURE_COLUMNS, index=frame.index)


def available_observation_frame(frame: _features_pd.DataFrame) -> _features_pd.DataFrame:
    history = frame[["timestamp", "cnt"]].copy()
    history["available_at"] = history["timestamp"] + _features_pd.Timedelta(hours=_features_LABEL_DELAY_HOURS)
    return history
