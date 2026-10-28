"""Validation helpers for the derived Capital Bikeshare hourly dataset."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from bike_mlops.data import load_hourly, sha256_file

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_SCHEMA_VERSION = 2
DEFAULT_RAW_ARCHIVE_DIR = Path(__file__).resolve().parents[2] / "data/raw/capitalbikeshare/monthly"


def _integer(value, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"Capital Bikeshare manifest field {name!r} must be an integer >= {minimum}")
    return value


def _timestamp(value, name: str) -> pd.Timestamp:
    if not isinstance(value, str):
        raise ValueError(f"Capital Bikeshare manifest field {name!r} must be a timestamp string")
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Capital Bikeshare manifest field {name!r} is not a valid timestamp") from error
    if stamp.tzinfo is not None or stamp != stamp.floor("h"):
        raise ValueError(f"Capital Bikeshare manifest field {name!r} must be a naive hour boundary")
    return stamp


def _source_timestamp(value, name: str) -> pd.Timestamp:
    if not isinstance(value, str):
        raise ValueError(f"Capital Bikeshare manifest field {name!r} must be a timestamp string")
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Capital Bikeshare manifest field {name!r} is not a valid timestamp") from error
    if stamp.tzinfo is not None:
        raise ValueError(f"Capital Bikeshare manifest field {name!r} must be timezone-naive")
    return stamp


def validate_capital_manifest(
    manifest: object,
    csv_path: Path,
    raw_dir: Path = DEFAULT_RAW_ARCHIVE_DIR,
) -> tuple[dict, pd.DataFrame]:
    """Validate manifest schema, archive coverage, row accounting, and CSV identity.

    This is intentionally stricter than the original 2026-10-28 acquisition
    manifest. Old manifests remain valid historical records, but are not accepted
    as input to newly frozen candidate/evaluation protocols.
    """
    if not isinstance(manifest, dict):
        raise ValueError("Capital Bikeshare source manifest must be a JSON object")
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            "Capital Bikeshare source manifest schema is unsupported; regenerate into new output paths "
            f"(expected schema {MANIFEST_SCHEMA_VERSION})"
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
    if processing.get("processing_version") != "capital-hourly-aggregation-v2":
        raise ValueError("Capital Bikeshare processing version is missing or unsupported")
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
    expected_months = pd.period_range(
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
            parsed_month = pd.Period(f"{month[:4]}-{month[4:]}", freq="M")
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
        if sha256_file(local_archive) != archive["archive_sha256"]:
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
        source_spans.append((source_min.floor("h"), source_max.floor("h") + pd.Timedelta(hours=1)))
        total_archive_rows += rows
        total_archive_duplicates += duplicates
        total_archive_unique += unique

    first_month = pd.Period(start, freq="M")
    last_month = pd.Period(end - pd.Timedelta(nanoseconds=1), freq="M")
    if pd.Period(f"{seen_months[0][:4]}-{seen_months[0][4:]}", freq="M") > first_month:
        raise ValueError("Declared archives do not include the data window's first month")
    if pd.Period(f"{seen_months[-1][:4]}-{seen_months[-1][4:]}", freq="M") < last_month:
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
    if csv_hash != sha256_file(csv_path) or manifest.get("derived_csv_sha256") != csv_hash:
        raise ValueError("Capital Bikeshare source manifest SHA-256 does not match the selected CSV")
    raw_columns = set(pd.read_csv(csv_path, nrows=0).columns)
    if raw_columns != {"dteday", "hr", "cnt"}:
        raise ValueError("Capital Bikeshare derived CSV must contain exactly dteday, hr, and cnt; ride IDs are not retained")
    frame = load_hourly(csv_path)
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
    expected_local = pd.date_range(
        start=start, end=end, freq="h", inclusive="left", tz="America/New_York"
    ).tz_localize(None)
    expected_local = expected_local[~expected_local.duplicated(keep=False)]
    if not frame["timestamp"].reset_index(drop=True).equals(pd.Series(expected_local, name="timestamp")):
        raise ValueError("Capital Bikeshare derived timestamps do not match the valid local-hour grid")
    return manifest, frame
