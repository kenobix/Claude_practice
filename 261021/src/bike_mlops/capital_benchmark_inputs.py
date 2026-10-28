"""Validate operational replay hours against a frozen candidate training split."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from bike_mlops.config import LABEL_DELAY_HOURS


def check_candidate_training_window(selection_path: Path, final_path: Path, candidate_id: str, *, start: str, end: str) -> dict:
    """Verify frozen evidence and the configured dataset before checking replay hours."""
    from bike_mlops.candidate_artifacts import sha256_file
    from bike_mlops.capital_staging import verify_candidate_for_staging
    from bike_mlops.cli import _load_profile_hourly
    from bike_mlops.config import DATASET_MANIFEST, DEFAULT_DATASET
    from bike_mlops.evaluation_protocol import read_protocol

    evidence = verify_candidate_for_staging(selection_path, final_path)
    selection = evidence["selection"]
    if candidate_id != evidence["candidate_id"]:
        raise ValueError("Requested candidate ID differs from the verified candidate")
    expected_csv = selection["data_sha256"]
    expected_manifest = selection["data_source"]["source_manifest_sha256"]
    if sha256_file(DEFAULT_DATASET) != expected_csv or sha256_file(DATASET_MANIFEST) != expected_manifest:
        raise ValueError("Benchmark CSV or manifest differs from the verified candidate")
    frame = _load_profile_hourly(DEFAULT_DATASET)
    if sha256_file(DEFAULT_DATASET) != expected_csv or sha256_file(DATASET_MANIFEST) != expected_manifest:
        raise ValueError("Benchmark CSV or manifest changed during loading")
    protocol_path = evidence["selection_report_path"].parent / "evaluation-protocol.json"
    expected_protocol_file = selection["evaluation_protocol_artifact"]["sha256"]
    if sha256_file(protocol_path) != expected_protocol_file:
        raise ValueError("Benchmark protocol file differs from the candidate report")
    protocol = read_protocol(protocol_path)
    if (sha256_file(protocol_path) != expected_protocol_file
            or protocol.get("protocol_sha256") != evidence["evaluation_protocol_sha256"]):
        raise ValueError("Benchmark protocol identity changed during loading")
    result = validate_training_window(frame, protocol, selection, start=start, end=end)
    result.update({"candidate_id": candidate_id, "candidate_report_sha256": evidence["selection_report_sha256"],
                   "final_report_sha256": evidence["final_report_sha256"], "data_sha256": expected_csv,
                   "manifest_sha256": expected_manifest, "evaluation_protocol_sha256": evidence["evaluation_protocol_sha256"]})
    return result


def validate_training_window(frame: pd.DataFrame, protocol: dict, selection: dict, *, start: str, end: str) -> dict:
    """Check the frozen training rows and require every replay hour to be among them.

    The caller must independently verify the candidate evidence, source CSV and
    manifest, and hold a stable DB snapshot during measurement.
    """
    if not {"timestamp", "cnt"}.issubset(frame.columns) or frame.empty:
        raise ValueError("Candidate dataset must contain timestamp and cnt rows")
    times = frame["timestamp"]
    if not pd.api.types.is_datetime64_dtype(times.dtype) or times.isna().any():
        raise ValueError("Candidate timestamps must be naive, non-null datetimes")
    if not times.is_monotonic_increasing or times.duplicated().any():
        raise ValueError("Candidate timestamps must be sorted and unique")
    if (times != times.dt.floor("h")).any():
        raise ValueError("Candidate timestamps must be hour-aligned")

    start_time, end_time = pd.Timestamp(start), pd.Timestamp(end)
    cutoff = pd.Timestamp(protocol["training"]["cutoff"])
    validation_start = pd.Timestamp(protocol["validation"]["start_inclusive"])
    for stamp in (start_time, end_time, cutoff, validation_start):
        if pd.isna(stamp) or stamp.tzinfo is not None or stamp != stamp.floor("h"):
            raise ValueError("Window and frozen split boundaries must be naive, hour-aligned timestamps")
    if end_time - start_time != pd.Timedelta(hours=168):
        raise ValueError("Operational replay window must be exactly 168 hours")
    delay = protocol["label_availability"]["delay_hours"]
    if isinstance(delay, bool) or not isinstance(delay, int) or delay != LABEL_DELAY_HOURS:
        raise ValueError("Frozen label delay differs from the active data profile")
    if selection.get("training_cutoff") != protocol["training"]["cutoff"]:
        raise ValueError("Candidate training cutoff differs from the frozen protocol")
    mask = (times < validation_start) & (times + pd.Timedelta(hours=delay) <= cutoff)
    training = frame.loc[mask]
    if len(training) != protocol["training"]["rows"] or len(training) != selection["split_counts"]["train"]:
        raise ValueError("Frozen training row count differs from the dataset or candidate report")
    payload = "".join(
        f"{timestamp.isoformat(sep=' ')}\t{int(target)}\n"
        for timestamp, target in zip(training["timestamp"], training["cnt"])
    )
    row_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if row_hash != selection.get("training_row_keys_sha256"):
        raise ValueError("Frozen training rows differ from the candidate report")

    requested = pd.date_range(start_time, end_time, freq="h", inclusive="left")
    training_times = pd.DatetimeIndex(training["timestamp"])
    excluded = requested.difference(training_times)
    if not excluded.empty:
        raise ValueError(f"Replay window includes a missing or non-training hour: {excluded[0]}")
    return {
        "start_inclusive": start_time.isoformat(sep=" "),
        "end_exclusive": end_time.isoformat(sep=" "),
        "hours": len(requested),
        "training_cutoff": cutoff.isoformat(sep=" "),
        "label_delay_hours": delay,
        "training_rows": len(training),
        "training_row_keys_sha256": row_hash,
        "latest_replay_label_available_at": (requested[-1] + pd.Timedelta(hours=delay)).isoformat(sep=" "),
    }
