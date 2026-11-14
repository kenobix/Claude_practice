"""Explicit command tools: data_tools."""
from __future__ import annotations


# ========================================================================
# prepare_capitalbikeshare
# ========================================================================

"""Download and aggregate Capital Bikeshare monthly trip files for a time split.

Only hourly start counts are written to the derived CSV. Raw monthly archives and
intermediate ride IDs stay under ignored data/ and are never added to Git.
"""



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
from bike_mlops.config import _profiles_load_data_profile as load_data_profile
from bike_mlops.config import load_pinned_timezone, timezone_data_identity

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


def _has_conflicting_start_hours(seen: sqlite3.Connection) -> bool:
    # Iterate the bounded current chunk, probing the accumulated primary key.
    # An ordinary JOIN can scan the entire growing history on every chunk.
    return seen.execute(
        "SELECT 1 FROM current_chunk AS new WHERE EXISTS ("
        "SELECT 1 FROM seen_trip_ids AS old WHERE old.ride_id = new.ride_id "
        "AND old.started_at <> new.started_at) LIMIT 1"
    ).fetchone() is not None


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
                            if _has_conflicting_start_hours(seen):
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

                            # IDs were deduplicated and checked for conflicting
                            # hours above. Classify each canonical hour once per
                            # chunk, retaining its exact number of unique trips.
                            for started_at, added_count in Counter(value for _, value in inserted).items():
                                hour = pd.Timestamp(started_at)
                                if hour < start or hour >= end_exclusive:
                                    unique_out_of_window += added_count
                                elif hour in ambiguous_set or hour in nonexistent_set:
                                    unique_dst_excluded += added_count
                                else:
                                    counts[hour] += added_count

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


def _entry_prepare_capitalbikeshare():
    main()


# ========================================================================
# create_capital_evaluation_protocol
# ========================================================================

"""Freeze a point-in-time Capital Bikeshare candidate and final evaluation split."""



import argparse as _create_capital_evaluation_protocol_argparse
import json as _create_capital_evaluation_protocol_json
from pathlib import Path as _create_capital_evaluation_protocol_Path

from bike_mlops.config import DATA_PROFILE_ID, MODEL_NAME, TARGET_CONTRACT_ID
from bike_mlops.evaluation import build_capital_protocol, save_protocol
from bike_mlops.model import load_registered_model
from bike_mlops.training import HYPERPARAMETERS


def _create_capital_evaluation_protocol_main() -> None:
    if DATA_PROFILE_ID != "capital-bikeshare":
        raise SystemExit("Set DATA_PROFILE_ID=capital-bikeshare before freezing a Capital protocol")
    parser = _create_capital_evaluation_protocol_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=_create_capital_evaluation_protocol_Path, required=True)
    parser.add_argument("--source-manifest", type=_create_capital_evaluation_protocol_Path, required=True)
    parser.add_argument("--training-cutoff", required=True)
    parser.add_argument("--validation-start", required=True)
    parser.add_argument("--validation-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument(
        "--incumbent-version",
        help="Existing passed model version; omit to create the first baseline-only bootstrap protocol",
    )
    parser.add_argument("--output", type=_create_capital_evaluation_protocol_Path, required=True)
    args = parser.parse_args()

    if args.incumbent_version:
        _, incumbent_hash, incumbent_gate = load_registered_model(MODEL_NAME, args.incumbent_version)
    else:
        incumbent_hash = None
        incumbent_gate = None
    protocol = build_capital_protocol(
        args.csv,
        args.source_manifest,
        training_cutoff=args.training_cutoff,
        validation_start=args.validation_start,
        validation_end=args.validation_end,
        evaluation_start=args.evaluation_start,
        evaluation_end=args.evaluation_end,
        incumbent_version=args.incumbent_version,
        incumbent_artifact_sha256=incumbent_hash,
        incumbent_quality_gate=incumbent_gate,
        candidate_recipe={
            "model_family": "scikit-learn HistGradientBoostingRegressor in a Pipeline",
            "hyperparameters": HYPERPARAMETERS,
        },
    )
    if protocol["dataset"]["target_contract"] != TARGET_CONTRACT_ID:
        raise ValueError("Generated protocol target contract differs from the active data profile")
    save_protocol(protocol, args.output)
    print(_create_capital_evaluation_protocol_json.dumps({
        "protocol_path": str(args.output),
        "protocol_sha256": protocol["protocol_sha256"],
        "training_rows": protocol["training"]["rows"],
        "validation_rows": protocol["validation"]["rows"],
        "reserved_evaluation_rows": protocol["evaluation"]["rows"],
        "selection_decision_time": protocol["selection_decision_time"],
        "evaluation_start": protocol["evaluation"]["start_inclusive"],
    }, indent=2, ensure_ascii=False))


def _entry_create_capital_evaluation_protocol():
    _create_capital_evaluation_protocol_main()


# ========================================================================
# evaluate_capitalbikeshare_holdout
# ========================================================================

"""Score a validation-selected, hash-verified candidate on its frozen reserved period."""



import argparse as _evaluate_capitalbikeshare_holdout_argparse
import hashlib as _evaluate_capitalbikeshare_holdout_hashlib
import json as _evaluate_capitalbikeshare_holdout_json
import os as _evaluate_capitalbikeshare_holdout_os
import tempfile
from pathlib import Path as _evaluate_capitalbikeshare_holdout_Path

import numpy as np
import pandas as _evaluate_capitalbikeshare_holdout_pd

from bike_mlops.evaluation import candidate_identity, load_candidate_model, source_snapshot_inventory
from bike_mlops.data import validate_capital_manifest
from bike_mlops.config import DATA_PROFILE_ID as _evaluate_capitalbikeshare_holdout_DATA_PROFILE_ID, LABEL_DELAY_HOURS, MODEL_NAME as _evaluate_capitalbikeshare_holdout_MODEL_NAME, PROJECT_ROOT as _evaluate_capitalbikeshare_holdout_PROJECT_ROOT, TARGET_CONTRACT_ID as _evaluate_capitalbikeshare_holdout_TARGET_CONTRACT_ID
from bike_mlops.data import sha256_file as _evaluate_capitalbikeshare_holdout_sha256_file
from bike_mlops.evaluation import begin_evaluation, finish_evaluation, read_protocol
from bike_mlops.data import FEATURE_COLUMNS, feature_frame_for_dataset
from bike_mlops.model import load_registered_model as _evaluate_capitalbikeshare_holdout_load_registered_model
from bike_mlops.evaluation import baseline_accuracy_gate
from bike_mlops.training import HistoricalBaseline, regression_metrics, segment_comparison, segment_mae


def _project_relative(path: _evaluate_capitalbikeshare_holdout_Path) -> str:
    try:
        return str(path.resolve().relative_to(_evaluate_capitalbikeshare_holdout_PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _atomic_output(path: _evaluate_capitalbikeshare_holdout_Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = _evaluate_capitalbikeshare_holdout_Path(stream.name)
        _evaluate_capitalbikeshare_holdout_json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        _evaluate_capitalbikeshare_holdout_os.fsync(stream.fileno())
    try:
        _evaluate_capitalbikeshare_holdout_os.link(temporary, path)
    except FileExistsError as error:
        raise FileExistsError(f"Refusing to overwrite final-evaluation evidence: {path}") from error
    finally:
        temporary.unlink(missing_ok=True)


def _row_keys_sha256(frame: _evaluate_capitalbikeshare_holdout_pd.DataFrame) -> str:
    payload = "".join(
        f"{timestamp.isoformat(sep=' ')}\t{int(target)}\n"
        for timestamp, target in zip(frame["timestamp"], frame["cnt"])
    )
    return _evaluate_capitalbikeshare_holdout_hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_metrics_equal(actual: dict, expected: dict, label: str) -> None:
    for metric in ("mae", "rmse"):
        if metric not in actual or metric not in expected or not np.isclose(
            float(actual[metric]), float(expected[metric]), rtol=1e-10, atol=1e-10
        ):
            raise ValueError(f"Stored candidate {label} {metric} does not match its row-level evidence")


def _assert_segments_equal(actual: dict, expected: dict, label: str) -> None:
    if actual.keys() != expected.keys():
        raise ValueError(f"Stored candidate {label} segments do not match row-level evidence")
    for segment, values in expected.items():
        if actual[segment].get("n") != values["n"] or not np.isclose(
            float(actual[segment].get("mae")), float(values["mae"]), rtol=1e-10, atol=1e-10
        ):
            raise ValueError(f"Stored candidate {label} segment {segment} does not match row-level evidence")


def _assert_segment_comparison_equal(actual: dict, expected: dict) -> None:
    if actual.keys() != expected.keys():
        raise ValueError("Stored segment comparisons do not match row-level evidence")
    for segment, values in expected.items():
        stored = actual[segment]
        if stored.get("n") != values["n"]:
            raise ValueError(f"Stored segment comparison row count differs for {segment}")
        for metric in ("candidate_mae", "baseline_mae", "delta_mae_candidate_minus_baseline"):
            if not np.isclose(float(stored.get(metric)), float(values[metric]), rtol=1e-10, atol=1e-10):
                raise ValueError(f"Stored segment comparison {metric} differs for {segment}")


def _verify_validation_evidence(
    selection: dict,
    predictions_path: _evaluate_capitalbikeshare_holdout_Path,
    frame: _evaluate_capitalbikeshare_holdout_pd.DataFrame,
    protocol: dict,
    candidate,
    incumbent,
) -> None:
    bootstrap_protocol = protocol.get("incumbent") is None
    info = selection["validation_predictions"]
    if info.get("path") != "validation-predictions.csv":
        raise ValueError("Candidate validation-prediction path is not the canonical bundle file")
    if _evaluate_capitalbikeshare_holdout_sha256_file(predictions_path) != info.get("sha256"):
        raise ValueError("Candidate validation-predictions SHA-256 mismatch")
    predictions = _evaluate_capitalbikeshare_holdout_pd.read_csv(predictions_path)
    required = {"timestamp", "label_available_at", "actual", "candidate_prediction", "baseline_prediction"}
    if not bootstrap_protocol:
        required.add("incumbent_prediction")
    if (
        set(predictions.columns) != required
        or len(predictions) != protocol["validation"]["rows"]
        or len(predictions) != info.get("rows")
    ):
        raise ValueError("Candidate validation-predictions columns or row count differ from the protocol")

    train_cutoff = _evaluate_capitalbikeshare_holdout_pd.Timestamp(protocol["training"]["cutoff"])
    validation_start = _evaluate_capitalbikeshare_holdout_pd.Timestamp(protocol["validation"]["start_inclusive"])
    validation_end = _evaluate_capitalbikeshare_holdout_pd.Timestamp(protocol["validation"]["end_exclusive"])
    delay = _evaluate_capitalbikeshare_holdout_pd.Timedelta(hours=protocol["label_availability"]["delay_hours"])
    train_mask = (frame["timestamp"] < validation_start) & (frame["timestamp"] + delay <= train_cutoff)
    validation_mask = (frame["timestamp"] >= validation_start) & (frame["timestamp"] < validation_end)
    if int(train_mask.sum()) != protocol["training"]["rows"]:
        raise ValueError("Candidate training row count does not match the frozen protocol")
    validation = frame.loc[validation_mask].reset_index(drop=True)
    if len(validation) != protocol["validation"]["rows"]:
        raise ValueError("Candidate validation row count does not match the frozen protocol")
    if _row_keys_sha256(frame.loc[train_mask].reset_index(drop=True)) != selection["training_row_keys_sha256"]:
        raise ValueError("Candidate training row-key hash does not match the frozen data")
    if _row_keys_sha256(validation) != selection["validation_row_keys_sha256"]:
        raise ValueError("Candidate validation row-key hash does not match the frozen data")

    feature_frame = feature_frame_for_dataset(frame)
    validation_features = feature_frame.loc[validation_mask, FEATURE_COLUMNS].reset_index(drop=True)
    candidate_prediction = np.maximum(candidate.predict(validation_features), 0.0)
    if bootstrap_protocol:
        if incumbent is not None:
            raise ValueError("Baseline-only protocol must not load or compare an incumbent model")
        incumbent_prediction = None
    else:
        if incumbent is None:
            raise ValueError("Legacy incumbent-comparison protocol is missing its incumbent model")
        incumbent_prediction = np.maximum(incumbent.predict(validation_features), 0.0)
    training = frame.loc[train_mask].reset_index(drop=True)
    baseline = HistoricalBaseline().fit(training, frame)
    baseline_prediction = baseline.predict(validation["timestamp"])
    expected_timestamps = validation["timestamp"].map(lambda stamp: stamp.isoformat(sep=" ")).tolist()
    expected_labels = (validation["timestamp"] + delay).map(lambda stamp: stamp.isoformat(sep=" ")).tolist()
    if predictions["timestamp"].tolist() != expected_timestamps:
        raise ValueError("Candidate validation timestamps do not match the frozen split")
    if predictions["label_available_at"].tolist() != expected_labels:
        raise ValueError("Candidate validation label-availability timestamps are incorrect")
    if predictions["actual"].astype(int).tolist() != validation["cnt"].astype(int).tolist():
        raise ValueError("Candidate validation actual values do not match the frozen data")
    expected_predictions = [
        ("candidate_prediction", candidate_prediction),
        ("baseline_prediction", baseline_prediction),
    ]
    if incumbent_prediction is not None:
        expected_predictions.append(("incumbent_prediction", incumbent_prediction))
    for column, expected in expected_predictions:
        if not np.allclose(predictions[column].to_numpy(dtype=float), expected, rtol=0, atol=1e-9):
            raise ValueError(f"Candidate validation {column} does not match the saved model and data")

    validation_report = selection["validation"]
    candidate_metrics = regression_metrics(validation["cnt"], candidate_prediction)
    baseline_metrics = regression_metrics(validation["cnt"], baseline_prediction)
    _assert_metrics_equal(validation_report["model"], candidate_metrics, "candidate")
    _assert_metrics_equal(validation_report["baseline"], baseline_metrics, "baseline")
    if incumbent_prediction is not None:
        incumbent_metrics = regression_metrics(validation["cnt"], incumbent_prediction)
        _assert_metrics_equal(selection["comparison"]["incumbent"], incumbent_metrics, "incumbent")
    _assert_segments_equal(
        validation_report["model_by_segment"], segment_mae(validation, candidate_prediction), "candidate"
    )
    _assert_segments_equal(
        validation_report["baseline_by_segment"], segment_mae(validation, baseline_prediction), "baseline"
    )
    if bootstrap_protocol or "segment_comparison" in validation_report:
        _assert_segment_comparison_equal(
            validation_report.get("segment_comparison", {}),
            segment_comparison(validation, candidate_prediction, baseline_prediction),
        )
    if bootstrap_protocol:
        expected_gate = baseline_accuracy_gate(
            candidate_metrics, baseline_metrics, period="validation_selection"
        )
        comparison = selection.get("comparison", {})
        if (
            comparison.get("reference") != "historical_baseline_only"
            or comparison.get("incumbent_model_version") is not None
            or comparison.get("incumbent_artifact_sha256") is not None
            or comparison.get("incumbent_quality_gate") is not None
            or comparison.get("incumbent") is not None
            or comparison.get("validation_gate") != expected_gate
            or selection.get("quality_gate_policy_id") != expected_gate["policy_id"]
            or selection.get("quality_gate_policy_sha256") != expected_gate["policy_sha256"]
        ):
            raise ValueError("Candidate bootstrap validation gate/policy differs from row-level evidence")
        passed = expected_gate["passed"]
    else:
        passed = (
            selection["comparison"]["incumbent_quality_gate"] == "passed"
            and candidate_metrics["mae"] < incumbent_metrics["mae"]
            and candidate_metrics["mae"] < baseline_metrics["mae"]
        )
    if selection.get("quality_gate_passed") is not passed or not passed:
        raise ValueError("Candidate validation quality gate does not pass when recomputed from row-level evidence")


def _evaluate_capitalbikeshare_holdout_main() -> None:
    parser = _evaluate_capitalbikeshare_holdout_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=_evaluate_capitalbikeshare_holdout_Path, required=True)
    parser.add_argument("--source-manifest", type=_evaluate_capitalbikeshare_holdout_Path, required=True)
    parser.add_argument("--selection-report", type=_evaluate_capitalbikeshare_holdout_Path, required=True,
        help="Canonical candidate-report.json inside the immutable candidate bundle")
    parser.add_argument("--evaluation-protocol", type=_evaluate_capitalbikeshare_holdout_Path, help="Override the exact protocol copy bundled with the selected candidate")
    parser.add_argument("--incumbent-version")
    parser.add_argument("--evaluation-start")
    parser.add_argument("--evaluation-end")
    parser.add_argument("--output", type=_evaluate_capitalbikeshare_holdout_Path, required=True)
    args = parser.parse_args()

    if _evaluate_capitalbikeshare_holdout_DATA_PROFILE_ID != "capital-bikeshare":
        raise SystemExit("Set DATA_PROFILE_ID=capital-bikeshare before final evaluation")

    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite final-evaluation evidence: {args.output}")
    try:
        selection = _evaluate_capitalbikeshare_holdout_json.loads(args.selection_report.read_text(encoding="utf-8"))
    except (OSError, _evaluate_capitalbikeshare_holdout_json.JSONDecodeError) as error:
        raise ValueError(f"Could not read selected candidate report: {error}") from error
    if not isinstance(selection, dict) or selection.get("candidate_mode") != "rolling_time_series":
        raise ValueError("The selection report is not a rolling time-series candidate bundle")
    if args.selection_report.name != "candidate-report.json":
        raise ValueError("Selection report must use the canonical candidate-report.json filename")

    artifact_info = selection.get("candidate_artifact")
    protocol_info = selection.get("evaluation_protocol_artifact")
    if not isinstance(artifact_info, dict) or not isinstance(protocol_info, dict):
        raise ValueError("Selection report does not include a frozen model artifact and protocol")
    candidate_id = selection.get("candidate_id")
    if not isinstance(candidate_id, str) or args.selection_report.parent.name != candidate_id:
        raise ValueError("Selection report is not stored in its candidate-id bundle directory")
    try:
        bundle_relative = str(args.selection_report.parent.resolve().relative_to(_evaluate_capitalbikeshare_holdout_PROJECT_ROOT.resolve()))
    except ValueError as error:
        raise ValueError("Candidate bundle must be stored inside the project directory") from error
    if selection.get("candidate_bundle") != bundle_relative or not bundle_relative.startswith("data/candidates/"):
        raise ValueError("Selection report candidate bundle path does not match its directory")
    if args.selection_report.parent.resolve() != (_evaluate_capitalbikeshare_holdout_PROJECT_ROOT / "data/candidates" / candidate_id).resolve():
        raise ValueError("Candidate bundle is not under the canonical data/candidates directory")
    try:
        computed_identity = candidate_identity(selection)
    except (KeyError, TypeError) as error:
        raise ValueError(f"Candidate report is missing frozen identity fields: {error}") from error
    if computed_identity != selection.get("candidate_identity"):
        raise ValueError("Candidate identity fields differ from the frozen candidate report")
    expected_candidate_id = _evaluate_capitalbikeshare_holdout_hashlib.sha256(
        _evaluate_capitalbikeshare_holdout_json.dumps(computed_identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    if expected_candidate_id != candidate_id:
        raise ValueError("Candidate ID does not match its frozen artifact and validation evidence")
    if artifact_info.get("path") != "model.skops" or protocol_info.get("path") != "evaluation-protocol.json":
        raise ValueError("Candidate bundle contains non-canonical artifact paths")
    artifact_path = (args.selection_report.parent / artifact_info["path"]).resolve()
    predictions_path = (args.selection_report.parent / "validation-predictions.csv").resolve()
    snapshot_path = (args.selection_report.parent / "source-snapshot.tar.gz").resolve()
    bundled_protocol_path = (args.selection_report.parent / protocol_info["path"]).resolve()
    protocol_path = args.evaluation_protocol or bundled_protocol_path
    if _evaluate_capitalbikeshare_holdout_sha256_file(bundled_protocol_path) != protocol_info.get("sha256"):
        raise ValueError("Candidate bundle evaluation-protocol artifact hash mismatch")
    snapshot_info = selection.get("source_snapshot")
    if not isinstance(snapshot_info, dict) or snapshot_info.get("path") != "source-snapshot.tar.gz":
        raise ValueError("Candidate bundle does not include its canonical source snapshot")
    if _evaluate_capitalbikeshare_holdout_sha256_file(snapshot_path) != snapshot_info.get("sha256"):
        raise ValueError("Candidate source-snapshot SHA-256 mismatch")
    snapshot_tree_sha, snapshot_file_count = source_snapshot_inventory(snapshot_path)
    if (
        snapshot_tree_sha != selection.get("source_revision", {}).get("source_tree_sha256")
        or snapshot_file_count != selection.get("source_revision", {}).get("source_file_count")
    ):
        raise ValueError("Candidate source snapshot differs from its recorded source-tree identity")
    protocol = read_protocol(protocol_path)
    if (
        protocol["dataset"]["data_profile_id"] != _evaluate_capitalbikeshare_holdout_DATA_PROFILE_ID
        or protocol["dataset"]["target_contract"] != _evaluate_capitalbikeshare_holdout_TARGET_CONTRACT_ID
    ):
        raise ValueError("Final-evaluation protocol profile/target differs from the active data profile")
    if protocol["protocol_sha256"] != selection.get("evaluation_protocol_sha256"):
        raise ValueError("Selected candidate and evaluation protocol hashes differ")
    if (
        selection.get("data_profile_id") != _evaluate_capitalbikeshare_holdout_DATA_PROFILE_ID
        or selection.get("target_contract_id") != _evaluate_capitalbikeshare_holdout_TARGET_CONTRACT_ID
    ):
        raise ValueError("Selected candidate profile/target differs from the active profile")
    if selection.get("training_cutoff") != protocol["training"]["cutoff"]:
        raise ValueError("Selected candidate training cutoff differs from the frozen protocol")
    if selection.get("validation_window") != {
        "start": protocol["validation"]["start_inclusive"],
        "end_exclusive": protocol["validation"]["end_exclusive"],
    }:
        raise ValueError("Selected candidate validation window differs from the frozen protocol")
    comparison = selection.get("comparison", {})
    bootstrap_protocol = protocol.get("incumbent") is None
    if bootstrap_protocol:
        gate_policy = protocol["selection_gate"]
        if (
            comparison.get("reference") != "historical_baseline_only"
            or comparison.get("incumbent_model_version") is not None
            or comparison.get("incumbent_artifact_sha256") is not None
            or comparison.get("incumbent_quality_gate") is not None
            or comparison.get("incumbent") is not None
            or selection.get("quality_gate_policy_id") != gate_policy["policy_id"]
            or selection.get("quality_gate_policy_sha256") != gate_policy["policy_sha256"]
        ):
            raise ValueError("Selected candidate bootstrap comparison differs from the frozen policy")
        if not selection.get("quality_gate_passed"):
            raise ValueError("Candidate did not pass validation selection; final evaluation is not permitted")
    elif (
        str(comparison.get("incumbent_model_version")) != str(protocol["incumbent"]["model_version"])
        or comparison.get("incumbent_artifact_sha256") != protocol["incumbent"]["artifact_sha256"]
        or comparison.get("incumbent_quality_gate") != protocol["incumbent"]["quality_gate"]
    ):
        raise ValueError("Selected candidate incumbent comparison differs from the frozen protocol")
    if _evaluate_capitalbikeshare_holdout_sha256_file(artifact_path) != artifact_info.get("sha256"):
        raise ValueError("Selected candidate artifact SHA-256 mismatch")
    if args.evaluation_protocol and _evaluate_capitalbikeshare_holdout_sha256_file(args.evaluation_protocol) != _evaluate_capitalbikeshare_holdout_sha256_file(bundled_protocol_path):
        raise ValueError("Override protocol bytes differ from the protocol frozen with this candidate")

    manifest, frame = validate_capital_manifest(
        _evaluate_capitalbikeshare_holdout_json.loads(args.source_manifest.read_text(encoding="utf-8")), args.csv
    )
    data_hash = _evaluate_capitalbikeshare_holdout_sha256_file(args.csv)
    if data_hash != protocol["dataset"]["data_sha256"] or data_hash != selection.get("data_sha256"):
        raise ValueError("Final-evaluation CSV differs from the frozen candidate data")
    manifest_hash = _evaluate_capitalbikeshare_holdout_sha256_file(args.source_manifest)
    if manifest_hash != protocol["dataset"]["source_manifest_sha256"]:
        raise ValueError("Final-evaluation source manifest differs from the frozen protocol")
    if protocol["dataset"]["target_contract"] != manifest["processing"]["contract_version"]:
        raise ValueError("Final-evaluation target contract differs from the protocol")
    if selection.get("feature_contract_version") != protocol["features"]["contract_version"]:
        raise ValueError("Selected candidate feature contract differs from the protocol")
    if selection.get("feature_columns") != protocol["features"]["columns"] or list(FEATURE_COLUMNS) != protocol["features"]["columns"]:
        raise ValueError("Selected candidate feature columns differ from the protocol/runtime")
    if selection.get("hyperparameters") != protocol["candidate_recipe"]["hyperparameters"]:
        raise ValueError("Selected candidate model recipe differs from the frozen protocol")
    if selection.get("training_row_keys_sha256") is None or selection.get("validation_row_keys_sha256") is None:
        raise ValueError("Selected candidate report is missing row-level split lineage")
    if "monitor_reference" in selection:
        from bike_mlops.monitoring import read_candidate_reference
        read_candidate_reference(selection, args.selection_report.parent, frame=frame)

    if bootstrap_protocol:
        if args.incumbent_version:
            raise ValueError("A baseline-only bootstrap evaluation must not specify an incumbent version")
        incumbent_version = None
        incumbent = None
        incumbent_sha = None
        incumbent_gate = None
    else:
        incumbent_version = str(args.incumbent_version or protocol["incumbent"]["model_version"])
        if incumbent_version != str(protocol["incumbent"]["model_version"]):
            raise ValueError("Requested incumbent version differs from the frozen protocol")
        incumbent, incumbent_sha, incumbent_gate = _evaluate_capitalbikeshare_holdout_load_registered_model(_evaluate_capitalbikeshare_holdout_MODEL_NAME, incumbent_version)
        if incumbent_sha != protocol["incumbent"]["artifact_sha256"] or incumbent_gate != "passed":
            raise ValueError("Loaded incumbent artifact/gate differs from the frozen protocol")
    evaluation_start = _evaluate_capitalbikeshare_holdout_pd.Timestamp(protocol["evaluation"]["start_inclusive"])
    evaluation_end = _evaluate_capitalbikeshare_holdout_pd.Timestamp(protocol["evaluation"]["end_exclusive"])
    if args.evaluation_start and _evaluate_capitalbikeshare_holdout_pd.Timestamp(args.evaluation_start) != evaluation_start:
        raise ValueError("Requested evaluation start differs from the frozen protocol")
    if args.evaluation_end and _evaluate_capitalbikeshare_holdout_pd.Timestamp(args.evaluation_end) != evaluation_end:
        raise ValueError("Requested evaluation end differs from the frozen protocol")
    if protocol["evaluation"]["rows"] <= 0:
        raise ValueError("Frozen protocol contains no reserved evaluation rows")

    candidate_sha = artifact_info["sha256"]
    candidate, loaded_sha = load_candidate_model(artifact_path, candidate_sha)
    if loaded_sha != candidate_sha:
        raise AssertionError("Candidate artifact changed during verification")
    _verify_validation_evidence(selection, predictions_path, frame, protocol, candidate, incumbent)
    attempt_id = begin_evaluation(protocol, "reserved_final_evaluation", candidate_sha)
    try:
        feature_frame = feature_frame_for_dataset(frame)
        evaluation_mask = (frame["timestamp"] >= evaluation_start) & (frame["timestamp"] < evaluation_end)
        if int(evaluation_mask.sum()) != int(protocol["evaluation"]["rows"]):
            raise ValueError("Final-evaluation row count differs from the frozen protocol")
        evaluation_frame = frame.loc[evaluation_mask].reset_index(drop=True)
        evaluation_features = feature_frame.loc[evaluation_mask, FEATURE_COLUMNS].reset_index(drop=True)

        # The selected artifact is loaded as-is. This script never calls fit().
        candidate_prediction = np.maximum(candidate.predict(evaluation_features), 0.0)
        incumbent_prediction = (
            np.maximum(incumbent.predict(evaluation_features), 0.0) if incumbent is not None else None
        )
        train_cutoff = _evaluate_capitalbikeshare_holdout_pd.Timestamp(protocol["training"]["cutoff"])
        training_start = _evaluate_capitalbikeshare_holdout_pd.Timestamp(protocol["validation"]["start_inclusive"])
        training_mask = (frame["timestamp"] < training_start) & (
            frame["timestamp"] + _evaluate_capitalbikeshare_holdout_pd.Timedelta(hours=protocol["label_availability"]["delay_hours"]) <= train_cutoff
        )
        if int(training_mask.sum()) != int(protocol["training"]["rows"]):
            raise ValueError("Training rows reconstructed from the frozen protocol do not match")
        training_frame = frame.loc[training_mask].reset_index(drop=True)
        baseline = HistoricalBaseline().fit(training_frame, frame)
        baseline_prediction = baseline.predict(evaluation_frame["timestamp"])

        prediction_columns = {
            "timestamp": evaluation_frame["timestamp"].map(lambda stamp: stamp.isoformat(sep=" ")),
            "label_available_at": (evaluation_frame["timestamp"] + _evaluate_capitalbikeshare_holdout_pd.Timedelta(hours=LABEL_DELAY_HOURS)).map(
                lambda stamp: stamp.isoformat(sep=" ")
            ),
            "actual": evaluation_frame["cnt"].astype(int),
            "candidate_prediction": candidate_prediction,
            "baseline_prediction": baseline_prediction,
        }
        if incumbent_prediction is not None:
            prediction_columns["incumbent_prediction"] = incumbent_prediction
        predictions = _evaluate_capitalbikeshare_holdout_pd.DataFrame(prediction_columns)
        predictions_path = args.output.with_suffix(".predictions.csv")
        if predictions_path.exists():
            raise FileExistsError(f"Refusing to overwrite row-level evidence: {predictions_path}")
        predictions_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=predictions_path.parent, delete=False) as stream:
            temporary_predictions = _evaluate_capitalbikeshare_holdout_Path(stream.name)
            predictions.to_csv(stream, index=False, float_format="%.17g")
            stream.flush()
            _evaluate_capitalbikeshare_holdout_os.fsync(stream.fileno())
        try:
            _evaluate_capitalbikeshare_holdout_os.link(temporary_predictions, predictions_path)
        except FileExistsError as error:
            raise FileExistsError(f"Refusing to overwrite row-level evidence: {predictions_path}") from error
        finally:
            temporary_predictions.unlink(missing_ok=True)

        candidate_metrics = regression_metrics(evaluation_frame["cnt"], candidate_prediction)
        baseline_metrics = regression_metrics(evaluation_frame["cnt"], baseline_prediction)
        final_accuracy_gate = (
            baseline_accuracy_gate(candidate_metrics, baseline_metrics, period="final_evaluation")
            if bootstrap_protocol
            else None
        )
        deployment_blockers = []
        if final_accuracy_gate is None:
            deployment_blockers.append("final accuracy gate has not been computed under the bootstrap policy")
        elif not final_accuracy_gate["passed"]:
            deployment_blockers.append("final accuracy gate failed or could not be evaluated")
        deployment_blockers.extend(
            ["operational gate has not been measured", "manual approval has not been recorded"]
        )
        summary = {
            "evaluation_kind": "frozen_candidate_reserved_out_of_time_evaluation",
            "data_profile_id": _evaluate_capitalbikeshare_holdout_DATA_PROFILE_ID,
            "target_contract_id": _evaluate_capitalbikeshare_holdout_TARGET_CONTRACT_ID,
            "candidate_id": selection.get("candidate_id"),
            "candidate_artifact_sha256": candidate_sha,
            "evaluation_protocol_sha256": protocol["protocol_sha256"],
            "selection_report": _project_relative(args.selection_report),
            "model_fit_performed_during_evaluation": False,
            "data_source": {
                "dataset": manifest["source"],
                "target_contract": manifest["processing"]["contract_version"],
                "csv_sha256": data_hash,
                "source_manifest_sha256": manifest_hash,
            },
            "training_cutoff": protocol["training"]["cutoff"],
            "selection_decision_time": protocol["selection_decision_time"],
            "evaluation_window": {
                **protocol["evaluation"],
                "used_for_candidate_selection": False,
                "row_level_predictions": _project_relative(predictions_path),
                "row_level_predictions_sha256": _evaluate_capitalbikeshare_holdout_sha256_file(predictions_path),
            },
            "candidate": {
                "model": candidate_metrics,
                "model_by_segment": segment_mae(evaluation_frame, candidate_prediction),
            },
            "incumbent": (
                {
                    "model_version": incumbent_version,
                    "artifact_sha256": incumbent_sha,
                    "quality_gate": incumbent_gate,
                    "model_metrics": regression_metrics(evaluation_frame["cnt"], incumbent_prediction),
                    "model_by_segment": segment_mae(evaluation_frame, incumbent_prediction),
                }
                if incumbent_prediction is not None
                else None
            ),
            "historical_baseline": {
                "model": baseline_metrics,
                "model_by_segment": segment_mae(evaluation_frame, baseline_prediction),
            },
            "segment_comparison": segment_comparison(
                evaluation_frame, candidate_prediction, baseline_prediction
            ),
            "validation_gate_status": selection.get("validation_gate_status"),
            "quality_gate_policy_id": final_accuracy_gate["policy_id"] if final_accuracy_gate else None,
            "quality_gate_policy_sha256": final_accuracy_gate["policy_sha256"] if final_accuracy_gate else None,
            "final_accuracy_gate": final_accuracy_gate,
            "operational_gate_status": "not_evaluated",
            "deployment_permission": {
                "status": "blocked",
                "reasons": deployment_blockers,
            },
            "limitations": protocol["limitations"],
        }
        _atomic_output(args.output, summary)
    except Exception as error:
        finish_evaluation(attempt_id, "failed", detail=f"{type(error).__name__}: {error}")
        raise
    finish_evaluation(
        attempt_id,
        "completed",
        detail=_evaluate_capitalbikeshare_holdout_sha256_file(args.output),
        candidate_artifact_sha256=candidate_sha,
    )
    print(_evaluate_capitalbikeshare_holdout_json.dumps({"report_path": _project_relative(args.output), **summary}, indent=2, ensure_ascii=False))


def _entry_evaluate_capitalbikeshare_holdout():
    _evaluate_capitalbikeshare_holdout_main()


# ========================================================================
# evaluation_ledger
# ========================================================================

"""Inspect the local evaluation ledger or explicitly review an interrupted window."""



import argparse as _evaluation_ledger_argparse
import json as _evaluation_ledger_json
from pathlib import Path as _evaluation_ledger_Path

from bike_mlops.evaluation import DEFAULT_LEDGER, read_ledger, review_reopen_evaluation


def _evaluation_ledger_main() -> None:
    parser = _evaluation_ledger_argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    listing = subparsers.add_parser("list", help="Print all consumed evaluation windows")
    listing.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    reopen = subparsers.add_parser("reopen", help="Record human review before retrying a failed/interrupted window")
    reopen.add_argument("--key", required=True)
    reopen.add_argument("--operator", required=True)
    reopen.add_argument("--reason", required=True)
    reopen.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    args = parser.parse_args()
    if args.command == "list":
        print(_evaluation_ledger_json.dumps(read_ledger(_evaluation_ledger_Path(args.ledger)), indent=2, ensure_ascii=False))
    else:
        review_reopen_evaluation(args.key, args.operator, args.reason, _evaluation_ledger_Path(args.ledger))
        print(_evaluation_ledger_json.dumps({"status": "reviewed_reopen", "consumption_key": args.key}, ensure_ascii=False))


def _entry_evaluation_ledger():
    _evaluation_ledger_main()


def _dispatch_cli():
    import sys as _dispatch_sys
    commands = {'prepare': _entry_prepare_capitalbikeshare, 'protocol': _entry_create_capital_evaluation_protocol, 'evaluate': _entry_evaluate_capitalbikeshare_holdout, 'evaluation-ledger': _entry_evaluation_ledger}
    if len(_dispatch_sys.argv) < 2 or _dispatch_sys.argv[1] in {"-h", "--help"}:
        print("Usage: data_tools.py <command> [options]\nCommands: prepare, protocol, evaluate, evaluation-ledger")
        return 0
    command = _dispatch_sys.argv.pop(1)
    if command not in commands:
        raise SystemExit("Unknown command: " + command)
    return commands[command]()


if __name__ == "__main__":
    raise SystemExit(_dispatch_cli())
