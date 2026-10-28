"""Download and aggregate Capital Bikeshare monthly trip files for a time split.

Only hourly start counts are written to the derived CSV. Raw monthly archives and
intermediate ride IDs stay under ignored data/ and are never added to Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from bike_mlops.data import audit_hourly, load_hourly

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data/raw/capitalbikeshare/monthly"
OUTPUT_CSV = PROJECT_ROOT / "data/derived/capitalbikeshare-hourly-2023-202604.csv"
OUTPUT_MANIFEST = PROJECT_ROOT / "data/derived/capitalbikeshare-hourly-2023-202604-manifest.json"
SOURCE_ROOT = "https://s3.amazonaws.com/capitalbikeshare-data"
SOURCE_PAGE = "https://capitalbikeshare.com/system-data"
LICENSE_PAGE = "https://capitalbikeshare.com/data-license-agreement"
LOCAL_TIMEZONE = "America/New_York"
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
        tz=LOCAL_TIMEZONE,
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


def aggregate_archives(
    months: list[str],
    start: pd.Timestamp,
    end_exclusive: pd.Timestamp,
    output_csv: Path,
) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    counts: Counter[pd.Timestamp] = Counter()
    month_reports: list[dict] = []
    raw_rows = 0
    duplicate_ids = 0

    with TemporaryDirectory(prefix="capitalbikeshare-dedup-") as temp_dir:
        seen_path = Path(temp_dir) / "seen.sqlite"
        with sqlite3.connect(seen_path) as seen:
            seen.execute("PRAGMA journal_mode=OFF")
            seen.execute("PRAGMA synchronous=OFF")
            seen.execute("CREATE TABLE seen_trip_ids (ride_id TEXT PRIMARY KEY, started_at TEXT NOT NULL)")
            seen.execute("CREATE TEMP TABLE current_chunk (ride_id TEXT PRIMARY KEY, started_at TEXT NOT NULL)")

            for month in months:
                print(f"Processing archive {month}...", flush=True)
                archive_path = RAW_DIR / f"{month}-capitalbikeshare-tripdata.zip"
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
                            skipped = len(chunk) - len(inserted)
                            month_duplicates += skipped
                            duplicate_ids += skipped

                            new_times = pd.Series(
                                [pd.Timestamp(started_at) for _, started_at in inserted], dtype="datetime64[ns]"
                            )
                            in_range = new_times.loc[(new_times >= start) & (new_times < end_exclusive)]
                            if not in_range.empty:
                                counts.update(in_range.dt.floor("h").value_counts().to_dict())

                raw_rows += row_count
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

    valid_hours, ambiguous_hours, nonexistent_hours = _valid_local_hours(start, end_exclusive)
    hourly = pd.Series(counts, dtype="int64").reindex(valid_hours, fill_value=0).rename("cnt")
    derived = hourly.rename_axis("timestamp").reset_index()
    derived["dteday"] = derived["timestamp"].dt.strftime("%Y-%m-%d")
    derived["hr"] = derived["timestamp"].dt.hour.astype("int64")
    derived["cnt"] = derived["cnt"].astype("int64")
    derived = derived[["dteday", "hr", "cnt"]]

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    derived.to_csv(output_csv, index=False)
    loaded = load_hourly(output_csv)
    audit = audit_hourly(loaded, sha256_file(output_csv))
    audit["timezone_interpretation"] = (
        "naive America/New_York local wall time inferred from the service area; no UTC conversion applied"
    )
    if loaded["timestamp"].duplicated().any():
        raise AssertionError("Derived timestamps must be unique")

    return {
        "source": "Capital Bikeshare monthly trip history",
        "source_page": SOURCE_PAGE,
        "license_page": LICENSE_PAGE,
        "license_summary": "Use is governed by the Capital Bikeshare Data License Agreement; do not publish the source as a standalone dataset.",
        "processing": {
            "input_field": "started_at",
            "target": "trip starts counted by local wall-clock hour",
            "source_time_strings_have_timezone_offset": False,
            "timezone_interpretation": "America/New_York wall time inferred from the Capital Bikeshare service area; source strings themselves have no offset.",
            "ambiguous_fall_back_hours": "Excluded in full because the source cannot distinguish the two repeated local 01:00 hours.",
            "spring_forward_hours": "Remain absent because those local hours do not exist.",
            "empty_valid_hours": "Filled with zero after all monthly source archives in the declared range were acquired.",
            "duplicate_ride_ids": "Deduplicated across all monthly archives after canonicalizing started_at to the local hour; conflicting hour buckets for one ID are rejected.",
            "retained_fields": ["dteday", "hr", "cnt"],
            "raw_trip_identifiers_retained_in_derived_csv": False,
        },
        "data_window": {
            "start_inclusive": start.isoformat(sep=" "),
            "end_exclusive": end_exclusive.isoformat(sep=" "),
        },
        "archive_months": month_reports,
        "archive_count": len(month_reports),
        "source_rows_read": raw_rows,
        "duplicate_rows_skipped": duplicate_ids,
        "ambiguous_local_hours_excluded": ambiguous_hours,
        "nonexistent_spring_forward_hours": nonexistent_hours,
        "derived_csv": str(output_csv.relative_to(PROJECT_ROOT)) if output_csv.is_relative_to(PROJECT_ROOT) else str(output_csv),
        "derived_csv_sha256": sha256_file(output_csv),
        "audit": audit,
        "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
    }


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
        month_range(args.archive_start, args.archive_end), start, end, args.output_csv
    )
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("archive_count", "source_rows_read", "duplicate_rows_skipped", "ambiguous_local_hours_excluded", "derived_csv", "derived_csv_sha256", "audit")}, indent=2, ensure_ascii=False))
    print(f"manifest={args.output_manifest}")


if __name__ == "__main__":
    main()
