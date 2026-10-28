"""Persist each monitor run without replacing any prior run.

File and database commits are separate: absence of the completion marker is
ambiguous, so recover by inspecting the DB with the recorded monitor run ID.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


class MonitorPersistenceError(RuntimeError):
    """A run may have left partial evidence and needs reconciliation."""


def _write_new_file(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link creation is atomic and never replaces an existing path.
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def persist_monitor_run(database, windows, output: Path, report: dict, *, run_id: str) -> dict:
    """Write new reports, insert run-scoped windows, then create a commit marker."""
    try:
        if str(uuid.UUID(run_id)) != run_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Monitor run ID must be a canonical UUID") from error
    output = Path(output)
    if output.suffix != ".csv":
        raise ValueError("Monitor output must have a .csv extension")
    summary_path = output.with_suffix(".json")
    marker_path = output.with_suffix(".complete.json")
    for path in (output, summary_path, marker_path):
        # lexists also rejects dangling symlinks; os.link still protects races.
        if os.path.lexists(path):
            raise FileExistsError(f"Refusing to overwrite monitor output: {path}")
    if windows.empty or windows["window_end"].duplicated().any():
        raise ValueError("Monitor run must contain unique window end times")
    # The run identity survives even if the process dies after writing only CSV.
    csv_frame = windows.copy()
    if "monitor_run_id" in csv_frame.columns:
        raise ValueError("Monitor windows must not supply their own run ID")
    csv_frame.insert(0, "monitor_run_id", run_id)
    csv_bytes = csv_frame.to_csv(index=False).encode("utf-8")
    csv_hash = hashlib.sha256(csv_bytes).hexdigest()
    report = dict(report)
    report.update({"monitor_run_id": run_id, "csv_report": output.name, "csv_sha256": csv_hash,
                   "completion_marker": marker_path.name,
                   "persistence_status": "database_commit_unconfirmed_without_completion_marker",
                   "database_window_id_prefix": f"monitor:{run_id}:",
                   "persistence_note": "If the marker is missing, query monitor_windows.window_id by the prefix; DB commit may have succeeded."})
    summary_bytes = (json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    summary_hash = hashlib.sha256(summary_bytes).hexdigest()
    marker = {"schema_version": 1, "monitor_run_id": run_id, "status": "database_commit_succeeded",
              "window_count": len(windows), "csv_sha256": csv_hash, "summary_sha256": summary_hash,
              "created_at_utc": datetime.now(timezone.utc).isoformat()}
    marker_bytes = (json.dumps(marker, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    phase = "report_files"
    try:
        _write_new_file(output, csv_bytes)
        _write_new_file(summary_path, summary_bytes)
        phase = "database"
        now = datetime.now(timezone.utc).isoformat()
        with database.transaction(immediate=database.sqlite) as connection:
            for _, row in windows.iterrows():
                window_id = f"monitor:{run_id}:{row['window_end']}"
                database.execute(connection,
                    "INSERT INTO monitor_windows(window_id,replay_id,window_end,prediction_count,scored_count,"
                    "label_coverage,mae,rmse,psi_json,missing_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (window_id, report["replay_id"], row["window_end"], int(row["prediction_count"]),
                     int(row["scored_count"]), row["label_coverage"], row["mae"], row["rmse"],
                     json.dumps(row["psi"], sort_keys=True), json.dumps(row["feature_missing_rates"], sort_keys=True), now))
        phase = "completion_marker"
        _write_new_file(marker_path, marker_bytes)
    except Exception as error:
        raise MonitorPersistenceError(
            f"Monitor run {run_id} failed during {phase} ({type(error).__name__}); files may be partial and "
            f"database commit may be unknown. Inspect monitor_windows with prefix monitor:{run_id}: and files "
            f"{output}, {summary_path}, {marker_path} before retrying."
        ) from None
    return {"monitor_run_id": run_id, "report_path": output, "summary_path": summary_path,
            "completion_marker_path": marker_path, "persistence_status": "database_commit_succeeded"}
