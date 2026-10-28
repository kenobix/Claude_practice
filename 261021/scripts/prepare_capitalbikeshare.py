"""Download and aggregate Capital Bikeshare monthly trip files for a time split.

Only hourly start counts are written to the derived CSV. Raw monthly archives and
intermediate ride IDs stay under ignored data/ and are never added to Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from bike_mlops.data import audit_hourly, load_hourly
from bike_mlops.profiles import load_data_profile
from bike_mlops.timezones import load_pinned_timezone, timezone_data_identity

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data/raw/capitalbikeshare/monthly"
OUTPUT_CSV = PROJECT_ROOT / "data/derived/capitalbikeshare-hourly-2023-202604.csv"
OUTPUT_MANIFEST = PROJECT_ROOT / "data/derived/capitalbikeshare-hourly-2023-202604-manifest.json"
SOURCE_ROOT = "https://s3.amazonaws.com/capitalbikeshare-data"
SOURCE_PAGE = "https://capitalbikeshare.com/system-data"
LICENSE_PAGE = "https://capitalbikeshare.com/data-license-agreement"
CAPITAL_PROFILE = load_data_profile("capital-bikeshare")
LOCAL_TIMEZONE = CAPITAL_PROFILE["target"]["timezone_name"]
LOCAL_TIMEZONE_INFO = load_pinned_timezone(LOCAL_TIMEZONE)
TIMEZONE_DATABASE_IDENTITY = timezone_data_identity(LOCAL_TIMEZONE)
DATA_START = pd.Timestamp("2023-01-01 00:00:00")
DATA_END_EXCLUSIVE = pd.Timestamp("2026-05-01 00:00:00")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def month_range(start: str, end: str) -> list[str]:
    start_month = pd.Period(start, freq="M")
    end_month = pd.Period(end, freq="M")
    if start_month > end_month:
        raise ValueError("archive start month must be no later than archive end month")
    return [str(period).replace("-", "") for period in pd.period_range(start_month, end_month, freq="M")]


def acquire_archive(month: str, destination: Path) -> dict:
    filename = f"{month}-capitalbikeshare-tripdata.zip"
    url = f"{SOURCE_ROOT}/{filename}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "bike-demand-mlops-learning-project/0.1"},
        )
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(destination) as archive:
        damaged_member = archive.testzip()
        if damaged_member is not None:
            raise ValueError(f"Corrupt ZIP member in {filename}: {damaged_member}")
        csv_members = [
            member
            for member in archive.namelist()
            if member.lower().endswith(".csv") and not member.startswith("__MACOSX/")
        ]
        if len(csv_members) != 1:
            raise ValueError(f"Expected one trip CSV in {filename}, found {csv_members}")
    return {
        "month": month,
        "filename": filename,
        "url": url,
        "archive_bytes": destination.stat().st_size,
        "archive_sha256": sha256_file(destination),
        "csv_member": csv_members[0],
    }


def _valid_local_hours(
    start: pd.Timestamp, end_exclusive: pd.Timestamp
) -> tuple[pd.DatetimeIndex, list[str], list[str]]:
    aware_hours = pd.date_range(
        start=start,
        end=end_exclusive,
        freq="h",
        inclusive="left",
        tz=LOCAL_TIMEZONE_INFO,
    )
    local_wall_hours = aware_hours.tz_localize(None)
    repeated = local_wall_hours.duplicated(keep=False)
    ambiguous_labels = sorted(set(local_wall_hours[repeated]))
    valid_hours = local_wall_hours[~repeated]
    naive_grid = pd.date_range(start=start, end=end_exclusive, freq="h", inclusive="left")
    present_labels = set(local_wall_hours)
    nonexistent_labels = [stamp for stamp in naive_grid if stamp not in present_labels]
    return (
        valid_hours,
        [stamp.isoformat(sep=" ") for stamp in ambiguous_labels],
        [stamp.isoformat(sep=" ") for stamp in nonexistent_labels],
    )


def _build_lock_has_live_owner(lock_path: Path) -> bool:
    """Return whether the recorded PID still holds this exact lock file open."""
    try:
        lines = lock_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RuntimeError(f"Cannot inspect existing build lock {lock_path}: {error}") from error
    owner_line = next((line for line in lines if line.startswith("pid=")), None)
    if owner_line is None:
        raise RuntimeError(f"Build lock has no readable owner PID; inspect before removing it: {lock_path}")
    try:
        owner_pid = int(owner_line.removeprefix("pid="))
    except ValueError as error:
        raise RuntimeError(f"Build lock owner PID is invalid; inspect before removing it: {lock_path}") from error
    fd_directory = Path(f"/proc/{owner_pid}/fd")
    if not fd_directory.is_dir():
        return False
    expected = str(lock_path.resolve())
    try:
        for descriptor in fd_directory.iterdir():
            try:
                if os.path.realpath(descriptor) == expected:
                    return True
            except OSError:
                continue
    except OSError:
        # If the process exists but its file descriptors cannot be inspected,
        # fail closed and require an operator to check it.
        return True
    return False


def aggregate_archives(
    months: list[str],
    start: pd.Timestamp,
    end_exclusive: pd.Timestamp,
    output_csv: Path,
    output_manifest: Path | None = None,
    raw_dir: Path = RAW_DIR,
) -> dict:
    if not months:
        raise ValueError("At least one monthly archive is required; an empty archive list cannot imply zero demand")
    if len(months) != len(set(months)):
        raise ValueError("Archive month list contains duplicates")
    if months != month_range(months[0], months[-1]):
        raise ValueError("Archive months must be sorted and contiguous with no missing month")
    if start.tzinfo is not None or end_exclusive.tzinfo is not None:
        raise ValueError("Data bounds must use naive local timestamps")
    if start != start.floor("h") or end_exclusive != end_exclusive.floor("h") or start >= end_exclusive:
        raise ValueError("Data bounds must be ordered hour boundaries")
    first_window_month = start.strftime("%Y%m")
    last_window_month = (end_exclusive - pd.Timedelta(nanoseconds=1)).strftime("%Y%m")
    if months[0] > first_window_month or months[-1] < last_window_month:
        raise ValueError("Declared archive months do not cover every month in the requested data window")

    output_csv = output_csv.resolve()
    output_manifest = (output_manifest or output_csv.with_name(f"{output_csv.stem}-manifest.json")).resolve()
    if output_csv == output_manifest:
        raise ValueError("CSV and manifest must use different output paths")
    if output_csv.parent != output_manifest.parent:
        raise ValueError("CSV and manifest must be in the same directory for commit-marker semantics")
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output_csv.parent / f".{output_csv.name}.build.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if _build_lock_has_live_owner(lock_path):
            raise FileExistsError(f"Another archive build is actively holding the lock: {lock_path}")
        lock_path.unlink()
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise FileExistsError(f"Another archive build acquired the lock: {lock_path}") from error
    os.write(lock_fd, f"pid={os.getpid()}\n".encode())
    try:
        if output_csv.exists() or output_manifest.exists():
            raise FileExistsError(
                "Refusing to overwrite existing data evidence; choose new output paths or inspect the existing pair"
            )
        return _aggregate_archives_locked(
            months, start, end_exclusive, output_csv, output_manifest, raw_dir
        )
    finally:
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)


def _aggregate_archives_locked(
    months: list[str],
    start: pd.Timestamp,
    end_exclusive: pd.Timestamp,
    output_csv: Path,
    output_manifest: Path,
    raw_dir: Path,
) -> dict:
    raw_dir.mkdir(parents=True, exist_ok=True)
    counts: Counter[pd.Timestamp] = Counter()
    month_reports: list[dict] = []
    raw_rows = 0
    duplicate_ids = 0
    unique_ids = 0
    unique_out_of_window = 0
    unique_dst_excluded = 0
    valid_hours, ambiguous_hours, nonexistent_hours = _valid_local_hours(start, end_exclusive)
    ambiguous_set = {pd.Timestamp(value) for value in ambiguous_hours}
    nonexistent_set = {pd.Timestamp(value) for value in nonexistent_hours}

    with TemporaryDirectory(prefix="capitalbikeshare-dedup-") as temp_dir:
        seen_path = Path(temp_dir) / "seen.sqlite"
        with sqlite3.connect(seen_path) as seen:
            seen.execute("PRAGMA journal_mode=OFF")
            seen.execute("PRAGMA synchronous=OFF")
            seen.execute("CREATE TABLE seen_trip_ids (ride_id TEXT PRIMARY KEY, started_at TEXT NOT NULL)")
            seen.execute("CREATE TEMP TABLE current_chunk (ride_id TEXT PRIMARY KEY, started_at TEXT NOT NULL)")

            for month in months:
                print(f"Processing archive {month}...", flush=True)
                archive_path = raw_dir / f"{month}-capitalbikeshare-tripdata.zip"
                archive_report = acquire_archive(month, archive_path)
                row_count = 0
                month_unique = 0
                month_duplicates = 0
                min_started_at = None
                max_started_at = None

                with zipfile.ZipFile(archive_path) as archive:
                    with archive.open(archive_report["csv_member"]) as stream:
                        for chunk in pd.read_csv(
                            stream,
                            usecols=["ride_id", "started_at"],
                            dtype={"ride_id": "string", "started_at": "string"},
                            chunksize=250_000,
                        ):
                            row_count += len(chunk)
                            if chunk["ride_id"].isna().any() or chunk["started_at"].isna().any():
                                raise ValueError(f"Missing ride_id or started_at in {archive_report['filename']}")
                            if (chunk["ride_id"].str.len() == 0).any():
                                raise ValueError(f"Empty ride_id in {archive_report['filename']}")

                            timestamps = pd.to_datetime(chunk["started_at"], format="mixed", errors="raise")
                            if timestamps.dt.tz is not None:
                                raise ValueError(
                                    f"Timezone-aware started_at appeared in {archive_report['filename']}; review the time contract"
                                )
                            minimum, maximum = timestamps.min(), timestamps.max()
                            min_started_at = minimum if min_started_at is None else min(min_started_at, minimum)
                            max_started_at = maximum if max_started_at is None else max(max_started_at, maximum)

                            start_hours = timestamps.dt.floor("h")
                            duplicate_times = chunk.assign(_start_hour=start_hours).groupby("ride_id", sort=False)[
                                "_start_hour"
                            ].nunique()
                            if (duplicate_times > 1).any():
                                raise ValueError(f"A ride_id has conflicting start hours in {archive_report['filename']}")

                            current = pd.DataFrame(
                                {
                                    "ride_id": chunk["ride_id"].astype(str),
                                    # Publisher precision changed between exports; target labels are hourly.
                                    "started_at": start_hours.dt.strftime("%Y-%m-%d %H:%M:%S"),
                                }
                            ).drop_duplicates("ride_id")
                            seen.execute("DELETE FROM current_chunk")
                            seen.executemany(
                                "INSERT OR IGNORE INTO current_chunk(ride_id,started_at) VALUES (?,?)",
                                current.itertuples(index=False, name=None),
                            )
                            conflict = seen.execute(
                                "SELECT 1 FROM seen_trip_ids AS old JOIN current_chunk AS new USING (ride_id) "
                                "WHERE old.started_at <> new.started_at LIMIT 1"
                            ).fetchone()
                            if conflict:
                                raise ValueError(f"A ride_id has conflicting start hours across archives near {month}")
                            inserted = seen.execute(
                                "INSERT OR IGNORE INTO seen_trip_ids(ride_id,started_at) "
                                "SELECT ride_id,started_at FROM current_chunk RETURNING ride_id,started_at"
                            ).fetchall()
                            month_unique += len(inserted)
                            unique_ids += len(inserted)
                            skipped = len(chunk) - len(inserted)
                            month_duplicates += skipped
                            duplicate_ids += skipped

                            for _, started_at in inserted:
                                hour = pd.Timestamp(started_at)
                                if hour < start or hour >= end_exclusive:
                                    unique_out_of_window += 1
                                elif hour in ambiguous_set or hour in nonexistent_set:
                                    unique_dst_excluded += 1
                                else:
                                    counts[hour] += 1

                raw_rows += row_count
                if row_count == 0:
                    raise ValueError(f"Monthly source archive {month} contains no trip rows; refusing to zero-fill it")
                month_reports.append(
                    {
                        **archive_report,
                        "rows_read": row_count,
                        "unique_ride_ids_added": month_unique,
                        "duplicate_rows_skipped": month_duplicates,
                        "started_at_min": min_started_at.isoformat(sep=" ") if min_started_at is not None else None,
                        "started_at_max": max_started_at.isoformat(sep=" ") if max_started_at is not None else None,
                    }
                )
                print(
                    f"Processed {month}: rows={row_count}, duplicate_rows_skipped={month_duplicates}",
                    flush=True,
                )

    source_spans = sorted(
        (
            pd.Timestamp(item["started_at_min"]).floor("h"),
            pd.Timestamp(item["started_at_max"]).floor("h") + pd.Timedelta(hours=1),
        )
        for item in month_reports
    )
    merged_spans: list[list[pd.Timestamp]] = []
    for span_start, span_end in source_spans:
        if not merged_spans or span_start > merged_spans[-1][1]:
            merged_spans.append([span_start, span_end])
        else:
            merged_spans[-1][1] = max(merged_spans[-1][1], span_end)
    if not merged_spans or merged_spans[0][0] > start or merged_spans[-1][1] < end_exclusive:
        raise ValueError(
            "Observed source timestamp bounds do not span the requested window; refusing to infer zero-count hours"
        )
    if any(merged_spans[index][1] < merged_spans[index + 1][0] for index in range(len(merged_spans) - 1)):
        raise ValueError("Observed source timestamp bounds contain an uncovered gap; refusing to zero-fill it")
    source_timestamp_coverage = [
        {"start_inclusive": span_start.isoformat(sep=" "), "end_exclusive": span_end.isoformat(sep=" ")}
        for span_start, span_end in merged_spans
    ]

    hourly = pd.Series(counts, dtype="int64").reindex(valid_hours, fill_value=0).rename("cnt")
    derived = hourly.rename_axis("timestamp").reset_index()
    derived["dteday"] = derived["timestamp"].dt.strftime("%Y-%m-%d")
    derived["hr"] = derived["timestamp"].dt.hour.astype("int64")
    derived["cnt"] = derived["cnt"].astype("int64")
    derived = derived[["dteday", "hr", "cnt"]]

    output_trip_count = int(derived["cnt"].sum())
    if raw_rows != duplicate_ids + unique_ids:
        raise AssertionError("Source row accounting failed: rows read != duplicate rows + unique IDs")
    if unique_ids != unique_out_of_window + unique_dst_excluded + output_trip_count:
        raise AssertionError(
            "Unique-trip accounting failed: unique IDs != out-of-window + DST-excluded + output trips"
        )
    if len(derived) != len(valid_hours):
        raise AssertionError("Derived row count does not match the valid local-hour grid")

    with TemporaryDirectory(prefix="capitalbikeshare-pair-", dir=output_csv.parent) as staging_name:
        staging = Path(staging_name)
        staged_csv = staging / output_csv.name
        staged_manifest = staging / output_manifest.name
        derived.to_csv(staged_csv, index=False)
        loaded = load_hourly(staged_csv)
        data_hash = sha256_file(staged_csv)
        audit = audit_hourly(loaded, data_hash)
        audit["data_profile_id"] = CAPITAL_PROFILE["profile_id"]
        audit["target_contract_id"] = CAPITAL_PROFILE["target_contract_id"]
        audit["timezone_interpretation"] = CAPITAL_PROFILE["target"]["time_zone_interpretation"]
        audit["timezone_database"] = TIMEZONE_DATABASE_IDENTITY
        delay = CAPITAL_PROFILE["label_availability"]["delay_hours"]
        delay_status = CAPITAL_PROFILE["label_availability"]["status"]
        audit["label_available_at_rule"] = f"timestamp + {delay} hours ({delay_status})"
        if loaded["timestamp"].duplicated().any():
            raise AssertionError("Derived timestamps must be unique")

        report = {
            "manifest_schema_version": 3,
            "timezone_database": TIMEZONE_DATABASE_IDENTITY,
            "source": "Capital Bikeshare monthly trip history",
            "source_page": SOURCE_PAGE,
            "license_page": LICENSE_PAGE,
            "license_summary": "Use is governed by the Capital Bikeshare Data License Agreement; do not publish the source as a standalone dataset.",
            "processing": {
                "contract_version": CAPITAL_PROFILE["target_contract_id"],
                "processing_version": "capital-hourly-aggregation-v3",
                "input_field": "started_at",
                "target": "trip starts counted by local wall-clock hour",
                "source_time_strings_have_timezone_offset": False,
                "timezone_interpretation": "America/New_York wall time inferred from the Capital Bikeshare service area; source strings themselves have no offset.",
                "ambiguous_fall_back_hours": "Excluded in full because the source cannot distinguish the two repeated local 01:00 hours.",
                "spring_forward_hours": "Remain absent because those local hours do not exist.",
                "empty_valid_hours": "Filled with zero only after every contiguous, non-empty monthly archive covering the data window was read; completeness within publisher archives is not independently proven.",
                "duplicate_ride_ids": "Deduplicated across all monthly archives after canonicalizing started_at to the local hour; conflicting hour buckets for one ID are rejected.",
                "retained_fields": ["dteday", "hr", "cnt"],
                "raw_trip_identifiers_retained_in_derived_csv": False,
            },
            "data_window": {
                "start_inclusive": start.isoformat(sep=" "),
                "end_exclusive": end_exclusive.isoformat(sep=" "),
            },
            "archive_buffer_rationale": (
                "Monthly archives outside the target window may be included to detect duplicate ride IDs across "
                "publication boundaries; only unique starts inside data_window contribute to target counts."
            ),
            "source_timestamp_coverage": source_timestamp_coverage,
            "archive_months": month_reports,
            "archive_count": len(month_reports),
            "source_rows_read": raw_rows,
            "duplicate_rows_skipped": duplicate_ids,
            "unique_ride_ids": unique_ids,
            "unique_out_of_window": unique_out_of_window,
            "unique_dst_excluded": unique_dst_excluded,
            "output_rows": int(len(derived)),
            "output_trip_count": output_trip_count,
            "ambiguous_local_hours_excluded": ambiguous_hours,
            "nonexistent_spring_forward_hours": nonexistent_hours,
            "derived_csv": str(output_csv.relative_to(PROJECT_ROOT)) if output_csv.is_relative_to(PROJECT_ROOT) else str(output_csv),
            "data_sha256": data_hash,
            "derived_csv_sha256": data_hash,
            "audit": audit,
            "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        }
        staged_manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        created_csv = False
        try:
            # The manifest is the commit marker. Readers accept a pair only after
            # both hard links exist and the manifest SHA matches the CSV.
            os.link(staged_csv, output_csv)
            created_csv = True
            os.link(staged_manifest, output_manifest)
        except Exception:
            if created_csv:
                output_csv.unlink(missing_ok=True)
            raise
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-start", default="202301", help="First monthly archive (YYYYMM)")
    parser.add_argument("--archive-end", default="202605", help="Last monthly archive inclusive (YYYYMM)")
    parser.add_argument("--data-start", default=DATA_START.isoformat(sep=" "))
    parser.add_argument("--data-end-exclusive", default=DATA_END_EXCLUSIVE.isoformat(sep=" "))
    parser.add_argument("--output-csv", type=Path, default=OUTPUT_CSV)
    parser.add_argument("--output-manifest", type=Path, default=OUTPUT_MANIFEST)
    args = parser.parse_args()
    start, end = pd.Timestamp(args.data_start), pd.Timestamp(args.data_end_exclusive)
    if start.tzinfo is not None or end.tzinfo is not None or start >= end:
        raise ValueError("data bounds must be ordered, timezone-naive timestamps")
    report = aggregate_archives(
        month_range(args.archive_start, args.archive_end), start, end, args.output_csv, args.output_manifest
    )
    print(json.dumps({key: report[key] for key in ("archive_count", "source_rows_read", "duplicate_rows_skipped", "ambiguous_local_hours_excluded", "derived_csv", "derived_csv_sha256", "audit")}, indent=2, ensure_ascii=False))
    print(f"manifest={args.output_manifest}")


if __name__ == "__main__":
    main()
