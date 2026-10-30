"""Read-only reconciliation of one monitor run (T06a: SQLite only)."""
from __future__ import annotations

import ast
import csv
import hashlib
import io
import math
import os
import re
import sqlite3
import stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from bike_mlops.capital_deployment_evidence import strict_json

POLICY_ID = "monitor-run-reconciliation-v1"
LIMITS = {"csv": 128 * 1024 * 1024, "summary": 16 * 1024 * 1024, "marker": 64 * 1024}
MAX_ROWS, MAX_FIELD = 100_000, 1024 * 1024
_CSV_LIMIT_LOCK = RLock()
BASE_COLUMNS = (
    "monitor_run_id", "window_end", "window_start", "prediction_count", "scored_count",
    "label_coverage", "mae", "rmse", "quality_status", "psi", "feature_missing_rates",
    "distribution_status",
)
CAPITAL_COLUMNS = BASE_COLUMNS + ("psi_algorithm", "psi_details", "training_reference_sha256")
METRICS = ("label_coverage", "mae", "rmse")
REQUIRED_JSON = (
    "monitor_run_id", "replay_id", "model_name", "model_version", "data_profile_id",
    "target_contract_id", "data_sha256", "window_count", "monitor_period", "thresholds",
    "latest_window", "csv_report", "csv_sha256", "completion_marker", "database_window_id_prefix",
)


class InspectionFailure(Exception):
    def __init__(self, status: str, reason: str, exit_code: int, *, priority=None):
        self.status, self.reason, self.exit_code = status, reason, exit_code
        self.priority = priority if priority is not None else {
            "changed_during_inspection": 0, "invalid_evidence": 1, "mismatch": 2,
            "database_profile_mismatch": 3, "database_unavailable": 4,
            "legacy_unverifiable": 5, "unsupported_input": 5,
        }.get(status, 7)


def _descriptor(path: Path, state="missing", size=None, sha=None):
    return {"state": state, "path": str(path), "bytes": size, "sha256": sha}


def _base(run_id, path, profile, target):
    # Invalid CLI paths still get a JSON diagnostic without opening fallback paths.
    stem = path.name or "invalid"
    summary_path = path.with_suffix(".json") if path.name else path / (stem + ".json")
    marker_path = path.with_suffix(".complete.json") if path.name else path / (stem + ".complete.json")
    return {
        "schema_version": 1, "kind": "monitor-run-inspection", "policy_id": POLICY_ID,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "monitor_run_id": run_id, "status": "inspection_error", "reason": "inspection_error",
        "exit_code": 2, "data_profile_id": profile, "target_contract_id": target,
        "model_name": None, "model_version": None, "model_binding": "report_only",
        "files": {"csv": _descriptor(path), "summary": _descriptor(summary_path),
                  "marker": _descriptor(marker_path)},
        "database": {"backend": "unsupported", "profile_status": "not_evaluated",
                     "row_count": None, "snapshot": "not_queried", "numeric_storage": {},
                     "missing_representations": {}},
        "checks": {key: "not_evaluated" for key in (
            "file_snapshot", "input_format", "run_identity", "file_hashes", "database_profile",
            "database_rows", "latest_window")},
        "differences": [], "difference_count": 0, "differences_truncated": False,
        "decision": "inspection_only_no_retry_or_approval", "automatic_repair": False,
        "automatic_promotion": False, "later_commit_possible": False,
        "limitations": ["Inspection does not prove registry/model authenticity.",
                        "A later database commit may change a no-marker observation."],
    }


def _finish(out, status, reason, code):
    if status == "unconfirmed":
        out["later_commit_possible"] = True
        out["operation_note"] = "Check whether the producer is running, preserve originals, and inspect again."
    out.update(status=status, reason=reason, exit_code=code)
    return out


def _read_file(path: Path, kind: str):
    limit = LIMITS[kind]
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None, _descriptor(path), None
    except OSError:
        raise InspectionFailure("invalid_evidence", "file_unreadable", 1)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise InspectionFailure("invalid_evidence", "unsafe_file_type", 1)
    if before.st_size > limit:
        raise InspectionFailure("unsupported_input", "file_size_limit_exceeded", 2)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise InspectionFailure("invalid_evidence", "unsafe_file_type", 1)
            identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise InspectionFailure("changed_during_inspection", "file_changed_during_read", 2)
    except InspectionFailure:
        raise
    except OSError:
        raise InspectionFailure("invalid_evidence", "file_unreadable", 1)
    if len(data) > limit:
        raise InspectionFailure("unsupported_input", "file_size_limit_exceeded", 2)
    if len(data) != identity[2]:
        raise InspectionFailure("changed_during_inspection", "file_size_changed_during_read", 2)
    return data, _descriptor(path, "present", len(data), hashlib.sha256(data).hexdigest()), identity


def _unchanged(paths, identities, descriptors):
    unchanged = True
    for name, path in paths.items():
        try:
            info = path.lstat()
        except FileNotFoundError:
            if identities[name] is not None:
                descriptors[name]["state"] = "changed"
                unchanged = False
            continue
        except OSError:
            descriptors[name]["state"] = "changed"
            unchanged = False
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            descriptors[name]["state"] = "changed"
            unchanged = False
            continue
        try:
            _, observed, opened_identity = _read_file(path, name)
            final = path.lstat()
            final_identity = (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
            same = ((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                    == opened_identity == final_identity == identities[name]
                    and observed["sha256"] == descriptors[name]["sha256"]
                    and stat.S_ISREG(final.st_mode))
        except (InspectionFailure, OSError):
            same = False
        if not same:
            descriptors[name]["state"] = "changed"
            unchanged = False
    return unchanged


def _finite(value, bool_ok=True):
    if value is None or isinstance(value, str):
        return True
    if isinstance(value, bool):
        return bool_ok
    if type(value) in (int, float):
        try:
            return math.isfinite(value)
        except OverflowError:
            return False
    if isinstance(value, list):
        return all(_finite(item, bool_ok) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _finite(item, bool_ok) for key, item in value.items())
    return False


def _dict_literal(value, json_compatible=False):
    try:
        result = ast.literal_eval(value)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError) as error:
        raise ValueError("invalid dictionary literal") from error
    if not isinstance(result, dict) or not _finite(result, json_compatible):
        raise ValueError("invalid dictionary values")
    for key, item in result.items():
        if not isinstance(key, str):
            raise ValueError("dictionary key is not text")
        if not json_compatible and item is not None and (type(item) not in (int, float) or not math.isfinite(item)):
            raise ValueError("dictionary metric is not finite numeric/null")
    return result


def _time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is not None or result.minute or result.second or result.microsecond:
        raise ValueError("not a naive exact hour")
    return result


def _read_csv(data, run_id):
    # csv's field limit is process-global. Serialize our temporary limit changes.
    with _CSV_LIMIT_LOCK:
        return _read_csv_with_limit(data, run_id)


def _read_csv_with_limit(data, run_id):
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(MAX_FIELD)
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""), strict=True)
        columns = tuple(reader.fieldnames or ())
        if len(columns) != len(set(columns)):
            raise InspectionFailure("invalid_evidence", "duplicate_csv_header", 1)
        if "monitor_run_id" not in columns:
            raise InspectionFailure("legacy_unverifiable", "legacy_csv_without_run_id", 2)
        if set(columns) not in (set(BASE_COLUMNS), set(CAPITAL_COLUMNS)):
            raise InspectionFailure("unsupported_input", "unknown_csv_columns", 2)
        rows, seen, identity_matches = [], set(), True
        for index, raw in enumerate(reader):
            if index >= MAX_ROWS:
                raise InspectionFailure("unsupported_input", "csv_row_limit_exceeded", 2)
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError("malformed row")
            if any(len(value.encode("utf-8")) > MAX_FIELD for value in raw.values()):
                raise InspectionFailure("unsupported_input", "csv_field_limit_exceeded", 2)
            if raw["monitor_run_id"] != run_id:
                identity_matches = False
            row = dict(raw)
            row["_end"], row["_start"] = _time(raw["window_end"]), _time(raw["window_start"])
            if row["_end"] in seen:
                raise InspectionFailure("invalid_evidence", "duplicate_window_timestamp", 1)
            seen.add(row["_end"])
            for key in ("prediction_count", "scored_count"):
                if not re.fullmatch(r"(?:0|[1-9][0-9]*)", raw[key]):
                    raise ValueError("invalid count")
                row[key] = int(raw[key])
            if row["scored_count"] > row["prediction_count"]:
                raise ValueError("scored count exceeds prediction count")
            for key in METRICS:
                if raw[key] == "":
                    row[key] = None
                else:
                    number = float(raw[key])
                    if not math.isfinite(number) or number < 0 or (key == "label_coverage" and number > 1):
                        raise ValueError("invalid metric")
                    row[key] = number
            for key in ("psi", "feature_missing_rates"):
                row[key] = _dict_literal(raw[key])
                if any(value is not None and (
                    value < 0 or (key == "feature_missing_rates" and value > 1)
                ) for value in row[key].values()):
                    raise ValueError("metric out of range")
            if not raw["quality_status"] or not raw["distribution_status"]:
                raise ValueError("missing status")
            if set(columns) == set(CAPITAL_COLUMNS):
                if not raw["psi_algorithm"] or not re.fullmatch(r"[0-9a-f]{64}", raw["training_reference_sha256"]):
                    raise ValueError("invalid reference binding")
                row["psi_details"] = _dict_literal(raw["psi_details"], True)
            rows.append(row)
        if not rows:
            raise ValueError("current CSV is empty")
        if [row["_end"] for row in rows] != sorted(row["_end"] for row in rows):
            raise ValueError("unordered windows")
        if not identity_matches:
            raise InspectionFailure("mismatch", "run_identity_mismatch", 1)
        return rows, columns
    except InspectionFailure:
        raise
    except csv.Error as error:
        if "field larger than field limit" in str(error):
            raise InspectionFailure("unsupported_input", "csv_field_limit_exceeded", 2) from None
        raise InspectionFailure("invalid_evidence", "invalid_csv_evidence", 1) from None
    except (UnicodeDecodeError, ValueError, OverflowError, RecursionError) as error:
        raise InspectionFailure("invalid_evidence", "invalid_csv_evidence", 1) from error
    finally:
        csv.field_size_limit(previous_limit)


def _read_json(data):
    try:
        value = strict_json(data.decode("utf-8"))
    except Exception as error:
        raise InspectionFailure("invalid_evidence", "invalid_json_evidence", 1) from error
    if not _finite(value):
        raise InspectionFailure("invalid_evidence", "invalid_json_evidence", 1)
    return value


def _sqlite_path(url):
    if not url.startswith("sqlite:///"):
        raise InspectionFailure("unsupported_input", "unsupported_database_backend", 2)
    raw = url[len("sqlite:///"):]
    if not raw or raw == ":memory:" or "?" in raw or "#" in raw:
        raise InspectionFailure("unsupported_input", "unsupported_database_url", 2)
    return Path("/" + raw.lstrip("/")) if url.startswith("sqlite:////") else Path(raw)


def _database_rows(url, run_id, expected_profile, expected_target, out, *, read_rows=True):
    if not url.startswith("sqlite:///"):
        raise InspectionFailure("unsupported_input", "unsupported_database_backend", 2, priority=4)
    path = _sqlite_path(url)
    out["database"]["backend"] = "sqlite"
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise OSError
    except OSError:
        raise InspectionFailure("database_unavailable", "sqlite_database_unavailable", 2)
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        out["database"]["snapshot"] = "sqlite_read_transaction"
        tables = {row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "data_profile_metadata" not in tables:
            out["database"]["profile_status"] = "missing_metadata"
            out["checks"]["database_profile"] = "unsupported"
            raise InspectionFailure("legacy_unverifiable", "missing_profile_metadata", 2)
        metadata_columns = {row["name"] for row in connection.execute(
            "PRAGMA table_info(data_profile_metadata)").fetchall()}
        if not {"singleton", "data_profile_id", "target_contract_id"} <= metadata_columns:
            raise InspectionFailure("unsupported_input", "metadata_schema_unsupported", 2)
        metadata = connection.execute(
            "SELECT data_profile_id,target_contract_id FROM data_profile_metadata WHERE singleton=1"
        ).fetchone()
        if metadata is None:
            out["database"]["profile_status"] = "missing_metadata"
            out["checks"]["database_profile"] = "unsupported"
            raise InspectionFailure("legacy_unverifiable", "missing_profile_metadata", 2)
        out["database"]["profile_status"] = "matched"
        out["checks"]["database_profile"] = "passed"
        if (metadata["data_profile_id"] != expected_profile
                or metadata["target_contract_id"] != expected_target):
            out["database"]["profile_status"] = "mismatch"
            out["checks"]["database_profile"] = "failed"
            raise InspectionFailure("database_profile_mismatch", "database_profile_mismatch", 1)
        if "monitor_windows" not in tables:
            raise InspectionFailure("unsupported_input", "monitor_table_missing", 2)
        columns = {row["name"]: row["type"].strip().upper()
                   for row in connection.execute("PRAGMA table_info(monitor_windows)")}
        required = {"window_id", "replay_id", "window_end", "prediction_count", "scored_count",
                    "label_coverage", "mae", "rmse", "psi_json", "missing_json"}
        if not required <= columns.keys():
            raise InspectionFailure("unsupported_input", "monitor_schema_unsupported", 2)
        if any(columns[name] not in {"REAL", "DOUBLE", "DOUBLE PRECISION", "FLOAT"} for name in METRICS):
            raise InspectionFailure("unsupported_input", "monitor_numeric_type_unsupported", 2)
        out["database"]["numeric_storage"] = {key: "binary64" for key in METRICS}
        if not read_rows:
            return None
        found = connection.execute(
            "SELECT window_id,replay_id,window_end,prediction_count,scored_count,label_coverage,mae,rmse,"
            "psi_json,missing_json FROM monitor_windows WHERE window_id LIKE ? ORDER BY window_id LIMIT ?",
            (f"monitor:{run_id}:%", MAX_ROWS + 1),
        ).fetchall()
        if len(found) > MAX_ROWS:
            raise InspectionFailure("unsupported_input", "database_row_limit_exceeded", 2)
        rows = [dict(row) for row in found]
        out["database"].update(
            row_count=len(rows), numeric_storage={key: "binary64" for key in METRICS},
            missing_representations={"sql_null": sum(
                row[key] is None for row in rows for key in METRICS), "sql_nan_missing": 0},
        )
        return rows
    except InspectionFailure:
        raise
    except Exception as error:
        raise InspectionFailure("database_unavailable", f"sqlite_query_failed_{type(error).__name__}", 2) from None
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()


def _show(value):
    text = repr(value)
    return text if len(text) <= 512 else text[:509] + "..."


def _add_difference(out, window, field, expected, observed):
    out["difference_count"] += 1
    if len(out["differences"]) < 50:
        out["differences"].append({"window": window[:512] if isinstance(window, str) else _show(window), "field": field,
                                   "expected": _show(expected), "observed": _show(observed)})
    else:
        out["differences_truncated"] = True


def _compare_latest(csv_rows, summary, out):
    last = csv_rows[-1]
    latest = {key: value for key, value in last.items()
              if key not in {"monitor_run_id", "_end", "_start"}}
    if not _typed_equal(summary.get("latest_window"), latest):
        _add_difference(out, last["window_end"], "latest_window", latest, summary.get("latest_window"))
        out["checks"]["latest_window"] = "failed"
        return False
    out["checks"]["latest_window"] = "passed"
    return True


def _compare(csv_rows, db_rows, summary, out):
    expected = {row["window_end"]: row for row in csv_rows}
    observed = {}
    for row in db_rows:
        key = row["window_end"]
        if key in observed:
            _add_difference(out, key, "duplicate_database_window", "one row", "multiple rows")
        observed[key] = row
    for end in sorted(set(expected) | set(observed)):
        left, right = expected.get(end), observed.get(end)
        if left is None or right is None:
            _add_difference(out, end, "database_window", "present" if left else "absent",
                            "present" if right else "absent")
            continue
        fields = {"window_id": (f"monitor:{summary['monitor_run_id']}:{end}", right.get("window_id")),
                  "replay_id": (summary["replay_id"], right.get("replay_id")),
                  "prediction_count": (left["prediction_count"], right.get("prediction_count")),
                  "scored_count": (left["scored_count"], right.get("scored_count"))}
        for key, (want, got) in fields.items():
            if type(want) is not type(got) or want != got:
                _add_difference(out, end, key, want, got)
        for key in METRICS:
            got = right.get(key)
            equal = got is None if left[key] is None else (
                type(got) in (int, float) and math.isfinite(got) and float(left[key]) == float(got))
            if not equal:
                _add_difference(out, end, key, left[key], got)
        for key, dbkey in (("psi", "psi_json"), ("feature_missing_rates", "missing_json")):
            try:
                got = strict_json(right[dbkey])
            except Exception:
                got = "invalid_database_json"
            if not _metric_map_valid(got, upper=1 if key == "feature_missing_rates" else None) or not _typed_equal(got, left[key]):
                _add_difference(out, end, dbkey, left[key], got)
    out["checks"]["database_rows"] = "failed" if out["difference_count"] else "passed"


def _number(value, *, upper=None, nullable=True):
    if value is None:
        return nullable
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0 and (upper is None or value <= upper)
    except OverflowError:
        return False


def _metric_map_valid(value, *, upper=None):
    return (isinstance(value, dict) and all(
        isinstance(key, str) and _number(item, upper=upper) for key, item in value.items()))


def _typed_equal(left, right):
    # bool must never inherit numeric equality from Python's True == 1.
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_typed_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def _validate_latest(latest):
    if not isinstance(latest, dict):
        raise InspectionFailure("invalid_evidence", "invalid_latest_window", 1)
    for key in ("prediction_count", "scored_count"):
        if key in latest and (type(latest[key]) is not int or latest[key] < 0):
            raise InspectionFailure("invalid_evidence", "invalid_latest_window", 1)
    if ("prediction_count" in latest and "scored_count" in latest
            and latest["scored_count"] > latest["prediction_count"]):
        raise InspectionFailure("invalid_evidence", "invalid_latest_window", 1)
    for key in METRICS:
        if key in latest and not _number(latest[key], upper=1 if key == "label_coverage" else None):
            raise InspectionFailure("invalid_evidence", "invalid_latest_window", 1)
    for key in ("psi", "feature_missing_rates"):
        if key in latest and not _metric_map_valid(latest[key], upper=1 if key == "feature_missing_rates" else None):
            raise InspectionFailure("invalid_evidence", "invalid_latest_window", 1)
    for key in ("window_end", "window_start"):
        if key in latest:
            try:
                _time(latest[key])
            except (ValueError, TypeError):
                raise InspectionFailure("invalid_evidence", "invalid_latest_window", 1) from None
    for key in ("quality_status", "distribution_status", "psi_algorithm"):
        if key in latest and (not isinstance(latest[key], str) or not latest[key]):
            raise InspectionFailure("invalid_evidence", "invalid_latest_window", 1)
    if "psi_details" in latest and (not isinstance(latest["psi_details"], dict) or not _finite(latest["psi_details"])):
        raise InspectionFailure("invalid_evidence", "invalid_latest_window", 1)


def _validate_summary(summary, run_id, profile, target, paths, out):
    if "monitor_run_id" not in summary:
        raise InspectionFailure("legacy_unverifiable", "legacy_summary_without_run_id", 2)
    if any(key not in summary for key in REQUIRED_JSON):
        raise InspectionFailure("unsupported_input", "summary_format_unsupported", 2)
    for key in ("monitor_run_id", "replay_id", "model_name", "model_version", "data_profile_id",
                "target_contract_id", "csv_report", "completion_marker", "database_window_id_prefix"):
        if not isinstance(summary[key], str) or not summary[key]:
            raise InspectionFailure("invalid_evidence", "invalid_summary_identity", 1)
    for key in ("data_sha256", "csv_sha256"):
        if not isinstance(summary[key], str) or not re.fullmatch(r"[0-9a-f]{64}", summary[key]):
            raise InspectionFailure("invalid_evidence", "invalid_summary_identity", 1)
    if not re.fullmatch(r"[1-9][0-9]*", summary["model_version"]):
        raise InspectionFailure("invalid_evidence", "invalid_summary_identity", 1)
    if type(summary["window_count"]) is not int or summary["window_count"] < 1:
        raise InspectionFailure("invalid_evidence", "invalid_summary_window_count", 1)
    period, thresholds = summary["monitor_period"], summary["thresholds"]
    if not isinstance(period, dict) or not isinstance(thresholds, dict):
        raise InspectionFailure("invalid_evidence", "invalid_summary_period", 1)
    if (not {"start_inclusive", "end_exclusive"} <= period.keys() or "window_hours" not in thresholds):
        raise InspectionFailure("unsupported_input", "summary_period_contract_missing", 2)
    try:
        start, end = _time(period["start_inclusive"]), _time(period["end_exclusive"])
        hours = thresholds["window_hours"]
        if type(hours) is not int or hours <= 0 or start >= end:
            raise ValueError
        timedelta(hours=hours)
    except (ValueError, TypeError, OverflowError):
        raise InspectionFailure("invalid_evidence", "invalid_summary_period", 1) from None
    _validate_latest(summary["latest_window"])
    if (summary["monitor_run_id"] != run_id or summary["data_profile_id"] != profile
            or summary["target_contract_id"] != target):
        raise InspectionFailure("mismatch", "run_identity_mismatch", 1)
    if (summary["csv_report"] != paths["csv"].name
            or summary["completion_marker"] != paths["marker"].name
            or summary["database_window_id_prefix"] != f"monitor:{run_id}:"):
        raise InspectionFailure("mismatch", "file_identity_mismatch", 1)
    out.update(model_name=summary["model_name"], model_version=summary["model_version"])


def _validate_marker(marker, run_id):
    if type(marker.get("schema_version")) is not int:
        raise InspectionFailure("invalid_evidence", "invalid_completion_marker", 1)
    if marker["schema_version"] != 1:
        raise InspectionFailure("unsupported_input", "marker_schema_unsupported", 2)
    if (marker.get("status") != "database_commit_succeeded"
            or not isinstance(marker.get("monitor_run_id"), str)
            or type(marker.get("window_count")) is not int or marker["window_count"] < 1
            or any(not isinstance(marker.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", marker[key])
                   for key in ("csv_sha256", "summary_sha256"))):
        raise InspectionFailure("invalid_evidence", "invalid_completion_marker", 1)
    if marker["monitor_run_id"] != run_id:
        raise InspectionFailure("mismatch", "run_identity_mismatch", 1)


def _file_consistency(rows, summary, marker, contents, paths, out):
    if contents["marker"] is not None and (contents["csv"] is None or contents["summary"] is None):
        raise InspectionFailure("mismatch", "marker_without_required_files", 1)
    if rows is None or summary is None:
        return
    if summary["window_count"] != len(rows):
        raise InspectionFailure("mismatch", "window_count_mismatch", 1)
    period = summary["monitor_period"]
    start, end = _time(period["start_inclusive"]), _time(period["end_exclusive"])
    hours = (end - start) // timedelta(hours=1)
    if (hours != len(rows) or [row["_end"] for row in rows]
            != [start + timedelta(hours=i) for i in range(len(rows))]):
        raise InspectionFailure("invalid_evidence", "window_grid_mismatch", 1)
    try:
        starts_match = all(
            row["_start"] == row["_end"] - timedelta(hours=summary["thresholds"]["window_hours"])
            for row in rows)
    except OverflowError:
        starts_match = False
    if not starts_match:
        raise InspectionFailure("invalid_evidence", "window_start_mismatch", 1)
    if summary["csv_sha256"] != out["files"]["csv"]["sha256"]:
        out["checks"]["file_hashes"] = "failed"
        raise InspectionFailure("mismatch", "csv_hash_mismatch", 1)
    if marker is not None and (
        marker["window_count"] != len(rows)
        or marker["csv_sha256"] != out["files"]["csv"]["sha256"]
        or marker["summary_sha256"] != out["files"]["summary"]["sha256"]
    ):
        out["checks"]["file_hashes"] = "failed"
        raise InspectionFailure("mismatch", "completion_marker_mismatch", 1)
    out["checks"]["file_hashes"] = "passed"
    if not _compare_latest(rows, summary, out):
        raise InspectionFailure("mismatch", "latest_window_mismatch", 1)


def inspect_monitor_run(csv_path: Path, run_id: str, *, database_url: str,
                        data_profile_id: str, target_contract_id: str) -> dict:
    """Return one finite diagnostic JSON object; never initialize or mutate storage."""
    path = Path(csv_path)
    out = _base(str(run_id), path, data_profile_id, target_contract_id)
    out["database"]["backend"] = "sqlite" if database_url.startswith("sqlite:") else "unsupported"
    paths = {name: Path(value["path"]) for name, value in out["files"].items()}
    contents, identities, failures = {}, {}, []
    snapshot_complete = False

    def record(failure, check="input_format"):
        failures.append(failure)
        out["checks"][check] = "failed" if failure.exit_code == 1 else "unsupported"
        if failure.reason in {"run_identity_mismatch", "file_identity_mismatch"}:
            out["checks"]["run_identity"] = "failed"

    try:
        try:
            canonical = str(uuid.UUID(run_id)) == run_id
        except (ValueError, TypeError, AttributeError):
            canonical = False
        if not canonical:
            raise InspectionFailure("invalid_evidence", "invalid_run_id", 1)
        if path.suffix != ".csv":
            raise InspectionFailure("unsupported_input", "csv_extension_required", 2)
        for key, target in paths.items():
            try:
                contents[key], out["files"][key], identities[key] = _read_file(target, key)
            except InspectionFailure as failure:
                out["files"][key]["state"] = "changed" if failure.status == "changed_during_inspection" else "invalid"
                raise
        snapshot_complete = True
        rows = summary = marker = None
        if contents["csv"] is not None:
            try:
                rows, _ = _read_csv(contents["csv"], run_id)
                out["checks"].update(input_format="passed", run_identity="passed")
            except InspectionFailure as failure:
                if failure.status == "invalid_evidence":
                    out["files"]["csv"]["state"] = "invalid"
                record(failure)
        for key in ("summary", "marker"):
            if contents[key] is None:
                continue
            try:
                value = _read_json(contents[key])
                if key == "summary":
                    _validate_summary(value, run_id, data_profile_id, target_contract_id, paths, out)
                    summary = value
                else:
                    _validate_marker(value, run_id)
                    marker = value
            except InspectionFailure as failure:
                if failure.status == "invalid_evidence":
                    out["files"][key]["state"] = "invalid"
                record(failure)
        # Do not join an unrecognized legacy file to a current run by appearance.
        try:
            _file_consistency(rows, summary, marker, contents, paths, out)
        except InspectionFailure as failure:
            record(failure)
        if not _unchanged(paths, identities, out["files"]):
            record(InspectionFailure("changed_during_inspection", "file_snapshot_changed", 2), "file_snapshot")
        db_rows = None
        # Stable corruption/file mismatch outranks database access. Legacy and
        # unsupported formats still require the higher-priority profile/access check.
        if not any(failure.priority < 3 for failure in failures):
            try:
                read_rows = (not failures and (all(item is None for item in contents.values())
                                               or (rows is not None and summary is not None)))
                db_rows = _database_rows(database_url, run_id, data_profile_id, target_contract_id,
                                         out, read_rows=read_rows)
            except InspectionFailure as failure:
                check = "database_rows" if out["database"]["profile_status"] == "matched" else "database_profile"
                record(failure, check)
        if not failures and rows is not None and summary is not None and db_rows is not None:
            if marker is not None or db_rows:
                _compare(rows, db_rows, summary, out)
                if out["difference_count"]:
                    failures.append(InspectionFailure("mismatch", "database_rows_differ", 1, priority=6))
        if failures:
            selected = min(failures, key=lambda failure: failure.priority)
            result = _finish(out, selected.status, selected.reason, selected.exit_code)
        elif rows is not None and summary is not None and marker is not None:
            result = _finish(out, "matched", "files_and_database_match", 0)
        else:
            if all(item is None for item in contents.values()):
                reason = "database_only" if db_rows else "not_observed"
            elif rows is None or summary is None:
                reason = "incomplete_files"
            else:
                reason = "database_without_marker" if db_rows else "files_only"
            result = _finish(out, "unconfirmed", reason, 2)
    except InspectionFailure as failure:
        out["checks"]["input_format"] = "failed" if failure.exit_code == 1 else "unsupported"
        result = _finish(out, failure.status, failure.reason, failure.exit_code)
    except Exception as error:
        out["diagnostic_code"] = type(error).__name__
        result = _finish(out, "inspection_error", "inspection_internal_error", 2)
    # Every completed initial file snapshot is rechecked after database access,
    # including errors and legacy/unsupported outcomes. Race wins over corruption.
    if snapshot_complete:
        if not _unchanged(paths, identities, out["files"]):
            out["checks"]["file_snapshot"] = "failed"
            return _finish(out, "changed_during_inspection", "file_snapshot_changed", 2)
        out["checks"]["file_snapshot"] = "passed"
    return result
