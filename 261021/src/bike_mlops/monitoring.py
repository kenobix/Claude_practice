"""Product components: monitoring."""
from __future__ import annotations


# ========================================================================
# monitoring_psi_v2
# ========================================================================

"""Versioned PSI reference bins with explicit constant-reference handling.

Capital monitoring uses these frozen bins; historical UCI reports retain PSI v1.
A reference SHA detects accidental changes; it is not proof of authenticity.
"""


import hashlib
import json as _monitoring_psi_v2_json
import math
from typing import Any

import numpy as _monitoring_psi_v2_np

PSI_ALGORITHM_V2 = "psi-v2-constant-bins"
_SMOOTHING = 1e-6


def _sha(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "reference_sha256"}
    return hashlib.sha256(_monitoring_psi_v2_json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _numbers(values) -> tuple[_monitoring_psi_v2_np.ndarray, dict[str, int]]:
    try:
        arr = _monitoring_psi_v2_np.asarray(values, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError("PSI inputs must be a one-dimensional sequence of numeric or missing values") from error
    if arr.ndim != 1:
        raise ValueError("PSI inputs must be one-dimensional")
    finite = arr[_monitoring_psi_v2_np.isfinite(arr)]
    return finite, {"total": int(arr.size), "finite": int(finite.size),
                    "missing": int(_monitoring_psi_v2_np.isnan(arr).sum()), "nonfinite": int(_monitoring_psi_v2_np.isinf(arr).sum())}


def _validate_options(version: str, feature_name: str, reference_id: str, bins: int, minimum_count: int) -> None:
    if version != PSI_ALGORITHM_V2:
        raise ValueError("Unsupported PSI algorithm version")
    if not isinstance(feature_name, str) or not feature_name.strip() or not isinstance(reference_id, str) or not reference_id.strip():
        raise ValueError("A feature name and reference ID are required")
    if type(bins) is not int or not 2 <= bins <= 100:
        raise ValueError("PSI bin count must be an integer between 2 and 100")
    if type(minimum_count) is not int or minimum_count < 2:
        raise ValueError("PSI minimum count must be an integer of at least two")


def make_psi_reference(values, *, version: str, feature_name: str, reference_id: str,
                       bins: int = 10, minimum_count: int = 2) -> dict[str, Any]:
    """Freeze the reference bins and counts; the caller owns source/protocol binding."""
    _validate_options(version, feature_name, reference_id, bins, minimum_count)
    finite, counts = _numbers(values)
    result = {"schema_version": 1, "algorithm": version, "feature_name": feature_name,
              "reference_id": reference_id, "requested_bins": bins, "minimum_count": minimum_count,
              "smoothing": _SMOOTHING, "input_counts": counts, "status": None,
              "mode": None, "constant_value": None, "edges": [], "reference_counts": []}
    if counts["nonfinite"]:
        result["status"] = "invalid_reference_values"
    elif not counts["finite"]:
        result["status"] = "no_reference"
    elif counts["finite"] < minimum_count:
        result["status"] = "insufficient_reference"
    else:
        boundaries = _monitoring_psi_v2_np.unique(_monitoring_psi_v2_np.quantile(finite, _monitoring_psi_v2_np.linspace(0, 1, bins + 1)))
        if not _monitoring_psi_v2_np.all(_monitoring_psi_v2_np.isfinite(boundaries)):
            raise ValueError("Reference quantiles produced non-finite boundaries")
        if len(boundaries) == 1:
            value = float(boundaries[0])
            result.update(status="ready", mode="constant_three_way", constant_value=value,
                          reference_counts=[0, int(finite.size), 0])
        else:
            if len(boundaries) == 2:
                lower, upper = float(boundaries[0]), float(boundaries[1])
                midpoint = lower * 0.5 + upper * 0.5
                # Adjacent representable floats can round the midpoint down to
                # the lower value; the upper value still separates them.
                edges = _monitoring_psi_v2_np.array([upper if midpoint <= lower else midpoint])
            else:
                edges = boundaries[1:-1]
            if not _monitoring_psi_v2_np.all(_monitoring_psi_v2_np.isfinite(edges)):
                raise ValueError("Reference boundaries must be finite")
            indices = _monitoring_psi_v2_np.searchsorted(edges, finite, side="right")
            result.update(status="ready", mode="quantile", edges=edges.tolist(),
                          reference_counts=_monitoring_psi_v2_np.bincount(indices, minlength=len(edges) + 1).tolist())
    result["reference_sha256"] = _sha(result)
    return result


def _validate_reference(reference: object, version: str) -> dict:
    if not isinstance(reference, dict) or type(reference.get("schema_version")) is not int or reference["schema_version"] != 1:
        raise ValueError("Unsupported PSI reference schema")
    _validate_options(version, reference.get("feature_name"), reference.get("reference_id"),
                      reference.get("requested_bins"), reference.get("minimum_count"))
    if reference.get("algorithm") != version or reference.get("smoothing") != _SMOOTHING:
        raise ValueError("PSI reference algorithm or smoothing differs from the requested version")
    try:
        digest = _sha(reference)
    except (TypeError, ValueError) as error:
        raise ValueError("PSI reference is not JSON-compatible") from error
    if reference.get("reference_sha256") != digest:
        raise ValueError("PSI reference SHA-256 mismatch")
    counts = reference.get("input_counts")
    if (not isinstance(counts, dict) or set(counts) != {"total", "finite", "missing", "nonfinite"}
            or any(type(value) is not int or value < 0 for value in counts.values())
            or counts["total"] != counts["finite"] + counts["missing"] + counts["nonfinite"]):
        raise ValueError("PSI reference input counts are invalid")
    status = reference.get("status")
    expected_status = ("invalid_reference_values" if counts["nonfinite"] else
                       "no_reference" if counts["finite"] == 0 else
                       "insufficient_reference" if counts["finite"] < reference["minimum_count"] else "ready")
    if status != expected_status:
        raise ValueError("PSI reference state disagrees with its counts")
    mode, edges, bin_counts = reference.get("mode"), reference.get("edges"), reference.get("reference_counts")
    if not isinstance(edges, list) or not isinstance(bin_counts, list):
        raise ValueError("PSI reference bins must be lists")
    if status != "ready":
        if mode is not None or edges or bin_counts or reference.get("constant_value") is not None:
            raise ValueError("Unavailable PSI reference must not contain bins")
        return reference
    if any(type(value) is not int or value < 0 for value in bin_counts) or sum(bin_counts) != counts["finite"]:
        raise ValueError("PSI reference bin counts are invalid")
    if mode == "constant_three_way":
        constant = reference.get("constant_value")
        if (type(constant) not in {int, float} or not math.isfinite(constant) or edges
                or bin_counts != [0, counts["finite"], 0]):
            raise ValueError("Constant PSI reference bins are invalid")
    elif mode == "quantile":
        if (reference.get("constant_value") is not None or not 1 <= len(edges) <= reference["requested_bins"] - 1
                or len(bin_counts) != len(edges) + 1
                or any(type(value) not in {int, float} or not math.isfinite(value) for value in edges)
                or any(left >= right for left, right in zip(edges, edges[1:]))):
            raise ValueError("Quantile PSI reference bins are invalid")
    else:
        raise ValueError("Unsupported PSI reference bin mode")
    return reference


def evaluate_psi_reference(reference: dict, values, *, version: str, minimum_count: int | None = None) -> dict[str, Any]:
    """Evaluate only with the supplied fixed bins; missing and invalid values stay visible."""
    ref = _validate_reference(reference, version)
    if minimum_count is None:
        minimum_count = ref["minimum_count"]
    if type(minimum_count) is not int or minimum_count < 2:
        raise ValueError("PSI minimum current count must be an integer of at least two")
    finite, current_counts = _numbers(values)
    result = {"algorithm": version, "reference_sha256": ref["reference_sha256"],
              "reference_status": ref["status"], "reference_input_counts": dict(ref["input_counts"]),
              "current_input_counts": current_counts, "minimum_current_count": minimum_count,
              "status": None, "psi": None, "current_bin_counts": []}
    if ref["status"] != "ready":
        result["status"] = ref["status"]
    elif current_counts["nonfinite"]:
        result["status"] = "invalid_current_values"
    elif not current_counts["finite"]:
        result["status"] = "no_current_data"
    elif current_counts["finite"] < minimum_count:
        result["status"] = "insufficient_current_data"
    else:
        if ref["mode"] == "constant_three_way":
            constant = ref["constant_value"]
            observed = _monitoring_psi_v2_np.array([(finite < constant).sum(), (finite == constant).sum(), (finite > constant).sum()], dtype=float)
        else:
            indices = _monitoring_psi_v2_np.searchsorted(ref["edges"], finite, side="right")
            observed = _monitoring_psi_v2_np.bincount(indices, minlength=len(ref["reference_counts"])).astype(float)
        expected = _monitoring_psi_v2_np.asarray(ref["reference_counts"], dtype=float)
        # Smooth proportions, rather than counts, so identical distributions
        # have PSI zero even when their sample sizes differ.
        expected_share = (expected / expected.sum() + _SMOOTHING) / (1.0 + _SMOOTHING * len(expected))
        observed_share = (observed / observed.sum() + _SMOOTHING) / (1.0 + _SMOOTHING * len(observed))
        psi = float(_monitoring_psi_v2_np.sum((observed_share - expected_share) * _monitoring_psi_v2_np.log(observed_share / expected_share)))
        result.update(status="ready", psi=psi, current_bin_counts=observed.astype(int).tolist())
    return result


# ========================================================================
# monitoring_window
# ========================================================================

"""Resolve monitoring clocks from a replay report or explicit boundaries."""


import json as _monitoring_window_json
import re
from pathlib import Path

import pandas as _monitoring_window_pd


def _boundaries(start: object, end: object) -> tuple[_monitoring_window_pd.Timestamp, _monitoring_window_pd.Timestamp]:
    if not isinstance(start, str) or not start.strip() or not isinstance(end, str) or not end.strip():
        raise ValueError("Monitoring boundaries must be valid naive hourly timestamps")
    try:
        first, last = _monitoring_window_pd.Timestamp(start), _monitoring_window_pd.Timestamp(end)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("Monitoring boundaries must be valid naive hourly timestamps") from error
    if (_monitoring_window_pd.isna(first) or _monitoring_window_pd.isna(last) or first.tzinfo is not None or last.tzinfo is not None
            or first != first.floor("h") or last != last.floor("h") or first >= last):
        raise ValueError("Monitoring boundaries must be valid naive hourly timestamps with start < end")
    return first, last


def resolve_monitor_window(*, replay_id: str, model_version: str, data_sha256: str, profile_id: str,
                           target_contract_id: str, report_dir: Path, start: str | None,
                           end: str | None) -> tuple[_monitoring_window_pd.Timestamp, _monitoring_window_pd.Timestamp, dict]:
    """Inherit the replay's requested period, or constrain an explicitly selected subperiod.

    Older replay reports may not exist; an explicit pair of bounds is then required.
    The caller must still verify the actual prediction rows and data provenance.
    """
    if not isinstance(replay_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", replay_id):
        raise ValueError("Replay ID contains unsupported characters")
    if not isinstance(model_version, str) or not model_version.isdecimal() or int(model_version) <= 0:
        raise ValueError("Monitor requires a positive explicit model version")
    if (start is None) != (end is None):
        raise ValueError("Specify both --start and --end, or omit both to use the replay report")
    root = report_dir.resolve()
    path = root / f"replay-{replay_id}-v{model_version}.json"
    report = None
    if path.exists() or path.is_symlink():
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_file() or not resolved.is_relative_to(root):
                raise ValueError("Replay report path is outside the report directory")
            report = _monitoring_window_json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, _monitoring_window_json.JSONDecodeError) as error:
            raise ValueError("Could not read the replay report") from error
        expected = {"replay_id": replay_id, "model_version": model_version, "data_sha256": data_sha256}
        if not isinstance(report, dict) or any(str(report.get(k)) != v for k, v in expected.items()):
            raise ValueError("Replay report identity does not match the selected model and dataset")
        legacy_uci = ("data_profile_id" not in report and "target_contract_id" not in report
                      and profile_id == "uci-bike-sharing"
                      and target_contract_id == "uci-bike-sharing-cnt-dataset-local-hour"
                      and data_sha256 == "e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f")
        if not legacy_uci and (report.get("data_profile_id") != profile_id
                               or report.get("target_contract_id") != target_contract_id):
            raise ValueError("Replay report profile and target do not match the active profile")
        identity_source = "legacy_uci_dataset_hash_inference" if legacy_uci else "report"
        report_start, report_end = _boundaries(report.get("start"), report.get("end_exclusive"))
    if start is None:
        if report is None:
            raise ValueError("Replay report is missing; specify both --start and --end explicitly")
        return report_start, report_end, {"source": "replay_report", "replay_report": path.name,
                                          "profile_identity_source": identity_source}
    first, last = _boundaries(start, end)
    if report is not None and (first < report_start or last > report_end):
        raise ValueError("Explicit monitoring period exceeds the replay report period")
    return first, last, {"source": "explicit", "replay_report": path.name if report is not None else None,
                         "profile_identity_source": identity_source if report is not None else "not_in_report"}


# ========================================================================
# monitoring
# ========================================================================

import json
from datetime import timedelta

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error


def population_stability_index(reference, current, bins: int = 10) -> float | None:
    reference = np.asarray(reference, dtype=float)
    current = np.asarray(current, dtype=float)
    reference = reference[np.isfinite(reference)]
    current = current[np.isfinite(current)]
    if len(reference) < 2 or len(current) < 2:
        return None
    boundaries = np.unique(np.quantile(reference, np.linspace(0.0, 1.0, bins + 1)))
    if len(boundaries) < 2:
        edges = np.array([-np.inf, np.inf])
    elif len(boundaries) == 2:
        edges = np.array([-np.inf, boundaries.mean(), np.inf])
    else:
        edges = np.concatenate(([-np.inf], boundaries[1:-1], [np.inf]))
        edges = np.unique(edges)
    reference_counts = np.histogram(reference, bins=edges)[0].astype(float)
    current_counts = np.histogram(current, bins=edges)[0].astype(float)
    reference_share = (reference_counts + 1e-6) / (reference_counts.sum() + 1e-6 * len(reference_counts))
    current_share = (current_counts + 1e-6) / (current_counts.sum() + 1e-6 * len(current_counts))
    return float(np.sum((current_share - reference_share) * np.log(current_share / reference_share)))


def score_monitor_windows(
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
    reference_features: pd.DataFrame,
    start: pd.Timestamp,
    end_exclusive: pd.Timestamp,
    window_hours: int = 168,
    minimum_labels: int = 24,
    validation_mae: float | None = None,
    *,
    psi_version: str | None = None,
    frozen_reference: dict | None = None,
) -> pd.DataFrame:
    """Replay hourly monitoring clocks; labels count only after their declared availability."""
    from bike_mlops.monitoring import PSI_ALGORITHM_V2, evaluate_psi_reference
    if psi_version not in {None, PSI_ALGORITHM_V2}:
        raise ValueError("Unsupported monitoring PSI algorithm")
    version2 = psi_version == PSI_ALGORITHM_V2
    if version2:
        from bike_mlops.deployment import canonical_bytes, digest
        from bike_mlops.monitoring import POLICY
        if (not isinstance(frozen_reference, dict)
                or canonical_bytes(frozen_reference.get("policy")) != canonical_bytes(POLICY)
                or frozen_reference.get("reference_sha256") != digest({k: v for k, v in frozen_reference.items() if k != "reference_sha256"})):
            raise ValueError("PSI v2 requires a valid frozen training reference")
        if validation_mae is not None and (type(validation_mae) not in {int, float}
                                          or not np.isfinite(validation_mae) or validation_mae < 0):
            raise ValueError("Performance reference MAE must be finite and non-negative")
    elif frozen_reference is not None:
        raise ValueError("Frozen references require an explicit PSI algorithm version")
    predictions = predictions.copy()
    labels = labels.copy()
    predictions["target_hour"] = pd.to_datetime(predictions["target_hour"])
    predictions["features"] = predictions["features_json"].map(json.loads)
    labels["timestamp"] = pd.to_datetime(labels["timestamp"])
    labels["available_at"] = pd.to_datetime(labels["available_at"])
    label_by_time = dict(zip(labels["timestamp"], labels["cnt"].astype(float)))
    available_by_time = dict(zip(labels["timestamp"], labels["available_at"]))
    rows = []
    clock = start
    while clock < end_exclusive:
        lower = clock - pd.Timedelta(hours=window_hours)
        window = predictions.loc[
            (predictions["target_hour"] > lower) & (predictions["target_hour"] <= clock)
        ]
        scored_actual, scored_prediction = [], []
        for row in window.itertuples(index=False):
            target = row.target_hour
            if target in label_by_time and available_by_time[target] <= clock:
                scored_actual.append(label_by_time[target])
                scored_prediction.append(float(row.predicted))
        feature_psi = {}
        psi_details = {}
        for name in ("lag_24", "lag_168", "hour"):
            current = np.asarray([row.get(name, np.nan) for row in window["features"]], dtype=float)
            if version2:
                psi_details[name] = evaluate_psi_reference(
                    frozen_reference["features"][name], current, version=psi_version,
                    minimum_count=frozen_reference["policy"]["minimum_current_count"],
                )
                feature_psi[name] = psi_details[name]["psi"]
            else:
                reference = reference_features[name].to_numpy(dtype=float)
                feature_psi[name] = population_stability_index(reference, current)
        missing_rates = {
            feature_name: (
                float(np.mean([float(item.get(flag_name, 0.0)) for item in window["features"]]))
                if len(window)
                else None
            )
            for feature_name, flag_name in (
                ("lag_24", "lag_24_missing"),
                ("lag_168", "lag_168_missing"),
            )
        }
        if version2:
            missing_rates = {name: (psi_details[name]["current_input_counts"]["missing"] / len(window)
                                   if len(window) else None) for name in ("lag_24", "lag_168")}
        n_predictions, n_scored = int(len(window)), len(scored_actual)
        mae = float(mean_absolute_error(scored_actual, scored_prediction)) if n_scored else None
        rmse = (
            float(np.sqrt(mean_squared_error(scored_actual, scored_prediction)))
            if n_scored
            else None
        )
        max_psi = max((value for value in feature_psi.values() if value is not None), default=None)
        rows.append(
            {
                "window_end": clock.isoformat(sep=" "),
                "window_start": lower.isoformat(sep=" "),
                "prediction_count": n_predictions,
                "scored_count": n_scored,
                "label_coverage": n_scored / n_predictions if n_predictions else None,
                "mae": mae if n_scored >= minimum_labels else None,
                "rmse": rmse if n_scored >= minimum_labels else None,
                "quality_status": (
                    "insufficient_labels"
                    if n_scored < minimum_labels
                    else (
                        "investigate_error_increase"
                        if validation_mae is not None and mae > validation_mae * 1.25
                        else ("performance_reference_unset" if version2 and validation_mae is None
                              else "within_review_threshold")
                    )
                ),
                "psi": feature_psi,
                "feature_missing_rates": missing_rates,
                "distribution_status": (
                    "insufficient_data"
                    if max_psi is None
                    else ("investigate_distribution_change" if max_psi >= 0.2 else "no_threshold_crossing")
                ),
                **({"psi_algorithm": psi_version, "psi_details": psi_details,
                    "training_reference_sha256": frozen_reference["reference_sha256"]} if version2 else {}),
            }
        )
        if version2:
            states = {item["status"] for item in psi_details.values()}
            if states & {"invalid_reference_values", "invalid_current_values"}:
                rows[-1]["distribution_status"] = "invalid_values"
            elif states & {"no_reference", "insufficient_reference"}:
                rows[-1]["distribution_status"] = "reference_unavailable"
            elif max_psi is None or (states != {"ready"} and max_psi < 0.2):
                rows[-1]["distribution_status"] = "insufficient_data"
        clock += timedelta(hours=1)
    return pd.DataFrame(rows)


# ========================================================================
# monitor_persistence
# ========================================================================

"""Persist each monitor run without replacing any prior run.

File and database commits are separate: absence of the completion marker is
ambiguous, so recover by inspecting the DB with the recorded monitor run ID.
"""


import hashlib as _monitor_persistence_hashlib
import json as _monitor_persistence_json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path as _monitor_persistence_Path


class MonitorPersistenceError(RuntimeError):
    """A run may have left partial evidence and needs reconciliation."""


def _write_new_file(path: _monitor_persistence_Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = _monitor_persistence_Path(stream.name)
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


def persist_monitor_run(database, windows, output: _monitor_persistence_Path, report: dict, *, run_id: str) -> dict:
    """Write new reports, insert run-scoped windows, then create a commit marker."""
    try:
        if str(uuid.UUID(run_id)) != run_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Monitor run ID must be a canonical UUID") from error
    output = _monitor_persistence_Path(output)
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
    csv_hash = _monitor_persistence_hashlib.sha256(csv_bytes).hexdigest()
    report = dict(report)
    report.update({"monitor_run_id": run_id, "csv_report": output.name, "csv_sha256": csv_hash,
                   "completion_marker": marker_path.name,
                   "persistence_status": "database_commit_unconfirmed_without_completion_marker",
                   "database_window_id_prefix": f"monitor:{run_id}:",
                   "persistence_note": "If the marker is missing, query monitor_windows.window_id by the prefix; DB commit may have succeeded."})
    summary_bytes = (_monitor_persistence_json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    summary_hash = _monitor_persistence_hashlib.sha256(summary_bytes).hexdigest()
    marker = {"schema_version": 1, "monitor_run_id": run_id, "status": "database_commit_succeeded",
              "window_count": len(windows), "csv_sha256": csv_hash, "summary_sha256": summary_hash,
              "created_at_utc": datetime.now(timezone.utc).isoformat()}
    marker_bytes = (_monitor_persistence_json.dumps(marker, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

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
                     _monitor_persistence_json.dumps(row["psi"], sort_keys=True), _monitor_persistence_json.dumps(row["feature_missing_rates"], sort_keys=True), now))
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


# ========================================================================
# training_monitor_reference
# ========================================================================

"""Freeze training-only PSI bins and bind them to a Capital candidate.

Hashes detect inconsistent evidence; local files and registry administrators are
trusted. No later evaluation data is used to select bins or thresholds.
"""


import hashlib as _training_monitor_reference_hashlib
import copy
import math as _training_monitor_reference_math
import tempfile as _training_monitor_reference_tempfile
from pathlib import Path as _training_monitor_reference_Path

import pandas as _training_monitor_reference_pd

from bike_mlops.deployment import canonical_bytes, digest, strict_json
from bike_mlops.data import FEATURE_COLUMNS, feature_frame_for_dataset
_training_monitor_reference_PSI_ALGORITHM_V2 = PSI_ALGORITHM_V2
_training_monitor_reference_evaluate_psi_reference = evaluate_psi_reference
_training_monitor_reference_make_psi_reference = make_psi_reference
from bike_mlops.config import _profiles_load_data_profile as load_data_profile

PROFILE = load_data_profile("capital-bikeshare")
FEATURES = ("lag_24", "lag_168", "hour")
POLICY = {"policy_id": "capital-training-monitor-v1", "algorithm": _training_monitor_reference_PSI_ALGORITHM_V2,
          "bins": 10, "minimum_reference_count": 24, "minimum_current_count": 24,
          "psi_investigation_value": 0.2, "error_investigation_ratio": 1.25,
          "features": list(FEATURES)}
REFERENCE_PATH = "evidence/monitor-reference.json"
MAX_BYTES = 32 * 1024 * 1024


def frozen_monitor_policy() -> dict:
    return {"policy": copy.deepcopy(POLICY), "policy_sha256": digest(POLICY)}


def validate_frozen_monitor_policy(protocol: dict) -> None:
    if canonical_bytes(protocol.get("monitoring_reference_policy")) != canonical_bytes(frozen_monitor_policy()):
        raise ValueError("Capital fitting requires the frozen training monitoring policy in its protocol")


def row_keys_sha256(frame: _training_monitor_reference_pd.DataFrame) -> str:
    body = "".join(f"{t.isoformat(sep=' ')}\t{int(y)}\n" for t, y in zip(frame["timestamp"], frame["cnt"]))
    return _training_monitor_reference_hashlib.sha256(body.encode()).hexdigest()


def _identity(selection: dict) -> dict:
    result = {key: selection[key] for key in (
        "data_profile_id", "target_contract_id", "feature_contract_version", "feature_columns",
        "data_sha256", "evaluation_protocol_sha256", "training_row_keys_sha256", "training_cutoff",
    )}
    result.update(candidate_model_sha256=selection["candidate_artifact"]["sha256"],
                  source_manifest_sha256=selection["data_source"]["source_manifest_sha256"])
    if (result["data_profile_id"] != PROFILE["profile_id"]
            or result["target_contract_id"] != PROFILE["target_contract_id"]
            or result["feature_contract_version"] != PROFILE["feature_contract_version"]
            or result["feature_columns"] != list(FEATURE_COLUMNS)):
        raise ValueError("Training monitoring reference has a different profile/feature contract")
    for key, value in result.items():
        if key.endswith("sha256") and (not isinstance(value, str) or len(value) != 64
                                       or any(c not in "0123456789abcdef" for c in value)):
            raise ValueError(f"Training reference requires a SHA-256: {key}")
    return result


def make_training_reference(selection: dict, training: _training_monitor_reference_pd.DataFrame, features: _training_monitor_reference_pd.DataFrame) -> dict:
    identity = _identity(selection)
    training, features = training.reset_index(drop=True), features.reset_index(drop=True)
    times = _training_monitor_reference_pd.to_datetime(training["timestamp"], errors="raise")
    cutoff = _training_monitor_reference_pd.Timestamp(identity["training_cutoff"])
    validation_start = _training_monitor_reference_pd.Timestamp(selection["validation_window"]["start"])
    delay = PROFILE["label_availability"]["delay_hours"]
    counts = _training_monitor_reference_pd.to_numeric(training["cnt"], errors="raise")
    if (not len(training) or times.dt.tz is not None or cutoff.tzinfo is not None
            or validation_start.tzinfo is not None or times.isna().any()
            or _training_monitor_reference_pd.isna(cutoff) or _training_monitor_reference_pd.isna(validation_start)
            or not counts.map(_training_monitor_reference_math.isfinite).all() or (counts < 0).any() or (counts % 1 != 0).any()
            or not times.is_unique or not times.is_monotonic_increasing
            or (times != times.dt.floor("h")).any() or (times >= validation_start).any()
            or (times + _training_monitor_reference_pd.Timedelta(hours=delay) > cutoff).any()
            or len(training) != selection["split_counts"]["train"]
            or row_keys_sha256(training) != identity["training_row_keys_sha256"]
            or len(features) != len(training) or list(features.columns) != list(FEATURE_COLUMNS)):
        raise ValueError("Training reference rows differ from the candidate training split")
    # Hash the features actually supplied to fit, preserving NaN explicitly.
    feature_rows = [[None if _training_monitor_reference_pd.isna(value) else float(value) for value in row]
                    for row in features.itertuples(index=False, name=None)]
    if any(value is not None and not _training_monitor_reference_math.isfinite(value) for row in feature_rows for value in row):
        raise ValueError("Training reference features contain non-finite values")
    ref_id = digest(identity)
    reference = {
        "schema_version": 1, "kind": "capital-training-monitor-reference",
        "policy": copy.deepcopy(POLICY), "identity": identity,
        "training_window": {"start_inclusive": times.min().isoformat(sep=" "),
                            "end_exclusive": (times.max() + _training_monitor_reference_pd.Timedelta(hours=1)).isoformat(sep=" "),
                            "latest_label_available_at": (times.max() + _training_monitor_reference_pd.Timedelta(hours=delay)).isoformat(sep=" "),
                            "label_delay_hours": delay, "rows": len(training)},
        "training_features_sha256": digest({"columns": list(FEATURE_COLUMNS), "rows": feature_rows}),
        "features": {name: _training_monitor_reference_make_psi_reference(features[name], version=_training_monitor_reference_PSI_ALGORITHM_V2,
                     feature_name=name, reference_id=ref_id, bins=POLICY["bins"],
                     minimum_count=POLICY["minimum_reference_count"]) for name in FEATURES},
        "feature_missing_rates": {name: float(features[name].isna().mean()) for name in FEATURES},
    }
    reference["reference_sha256"] = digest(reference)
    return reference


def validate_training_reference(reference: dict, selection: dict, *, frame: _training_monitor_reference_pd.DataFrame | None = None) -> None:
    if (type(reference.get("schema_version")) is not int or reference.get("schema_version") != 1
            or reference.get("kind") != "capital-training-monitor-reference"
            or canonical_bytes(reference.get("policy")) != canonical_bytes(POLICY)
            or reference.get("identity") != _identity(selection)
            or reference.get("reference_sha256") != digest({k: v for k, v in reference.items() if k != "reference_sha256"})):
        raise ValueError("Training monitoring reference policy, hash, or candidate identity differs")
    components = reference.get("features")
    if not isinstance(components, dict) or set(components) != set(FEATURES):
        raise ValueError("Training reference is missing monitored features")
    for name, component in components.items():
        if (component.get("feature_name") != name or component.get("reference_id") != digest(reference["identity"])
                or component.get("requested_bins") != POLICY["bins"]
                or component.get("minimum_count") != POLICY["minimum_reference_count"]):
            raise ValueError("Training reference component differs from the fixed policy")
        _training_monitor_reference_evaluate_psi_reference(component, [], version=_training_monitor_reference_PSI_ALGORITHM_V2)
    window = reference.get("training_window", {})
    if (window.get("rows") != selection["split_counts"]["train"]
            or window.get("label_delay_hours") != PROFILE["label_availability"]["delay_hours"]
            or _training_monitor_reference_pd.Timestamp(window["latest_label_available_at"]) > _training_monitor_reference_pd.Timestamp(selection["training_cutoff"])):
        raise ValueError("Training reference window differs from training evidence")
    if frame is not None:
        times = frame["timestamp"]
        mask = ((times < _training_monitor_reference_pd.Timestamp(selection["validation_window"]["start"]))
                & (times + _training_monitor_reference_pd.Timedelta(hours=PROFILE["label_availability"]["delay_hours"])
                   <= _training_monitor_reference_pd.Timestamp(selection["training_cutoff"])))
        expected = make_training_reference(selection, frame.loc[mask], feature_frame_for_dataset(frame).loc[mask])
        if canonical_bytes(expected) != canonical_bytes(reference):
            raise ValueError("Training reference bins/features differ from the original training rows")


def read_candidate_reference(selection: dict, bundle: _training_monitor_reference_Path, *, frame: _training_monitor_reference_pd.DataFrame | None = None) -> dict:
    descriptor = selection.get("monitor_reference")
    if not isinstance(descriptor, dict) or descriptor.get("path") != "monitor-reference.json":
        raise ValueError("Candidate has no frozen training monitoring reference")
    path = bundle / descriptor["path"]
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Training reference must be a regular bounded file")
    body = path.read_bytes()
    if _training_monitor_reference_hashlib.sha256(body).hexdigest() != descriptor.get("sha256"):
        raise ValueError("Training monitoring reference file SHA-256 mismatch")
    reference = strict_json(body.decode())
    validate_training_reference(reference, selection, frame=frame)
    if descriptor != reference_descriptor(reference, body):
        raise ValueError("Training reference descriptor differs from its content")
    return reference


def reference_descriptor(reference: dict, body: bytes) -> dict:
    return {"path": "monitor-reference.json", "sha256": _training_monitor_reference_hashlib.sha256(body).hexdigest(),
            "reference_sha256": reference["reference_sha256"], "algorithm": _training_monitor_reference_PSI_ALGORITHM_V2, "schema_version": 1}


def reference_registry_tags(selection: dict) -> dict:
    descriptor = selection.get("monitor_reference")
    if descriptor is None:  # Historical candidates are not silently upgraded.
        return {}
    return {"monitor_reference_sha256": descriptor["sha256"],
            "monitor_reference_content_sha256": descriptor["reference_sha256"],
            "monitor_reference_path": REFERENCE_PATH, "monitor_psi_algorithm": _training_monitor_reference_PSI_ALGORITHM_V2}


def registered_training_reference(client, version: str, *, data_sha256: str, manifest_path: _training_monitor_reference_Path,
                                  frame: _training_monitor_reference_pd.DataFrame) -> tuple[dict, dict, dict]:
    from bike_mlops.evaluation import candidate_identity, sha256_file
    import json

    if not version.isdecimal() or str(int(version)) != version or int(version) < 1:
        raise ValueError("Monitoring requires a positive exact model version")
    before = client.get_model_version(PROFILE["model_name"], version)
    if (before.name != PROFILE["model_name"] or str(before.version) != version or before.status != "READY"
            or not before.run_id or before.tags.get("data_sha256") != data_sha256
            or before.tags.get("data_profile_id") != PROFILE["profile_id"]
            or before.tags.get("target_contract_id") != PROFILE["target_contract_id"]
            or before.tags.get("feature_contract_version") != PROFILE["feature_contract_version"]
            or before.tags.get("monitor_reference_path") != REFERENCE_PATH
            or before.tags.get("monitor_psi_algorithm") != _training_monitor_reference_PSI_ALGORITHM_V2):
        raise ValueError("Registered version lacks the matching Capital training reference")
    tags = dict(before.tags)
    artifact_sha = tags.get("artifact_sha256")
    if (not isinstance(artifact_sha, str) or len(artifact_sha) != 64
            or any(c not in "0123456789abcdef" for c in artifact_sha)):
        raise ValueError("Registered model must have a verified artifact SHA-256")
    with _training_monitor_reference_tempfile.TemporaryDirectory(prefix="capital-monitor-reference-") as directory:
        def download(artifact_path):
            path = _training_monitor_reference_Path(client.download_artifacts(before.run_id, artifact_path, directory))
            if (path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES
                    or not path.resolve().is_relative_to(_training_monitor_reference_Path(directory).resolve())):
                raise ValueError("Monitoring evidence download is not a regular temporary file")
            return path.read_bytes()
        body = download("evidence/candidate-report.json")
        if _training_monitor_reference_hashlib.sha256(body).hexdigest() != tags.get("candidate_report_sha256"):
            raise ValueError("Registered candidate report SHA-256 mismatch")
        selection = strict_json(body.decode())
        ci = candidate_identity(selection)
        cid = _training_monitor_reference_hashlib.sha256(json.dumps(ci, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        if (ci != selection.get("candidate_identity") or cid != selection.get("candidate_id")
                or cid != tags.get("candidate_id") or selection["data_sha256"] != data_sha256
                or selection["candidate_artifact"]["sha256"] != tags.get("candidate_model_sha256")
                or selection["evaluation_protocol_sha256"] != tags.get("evaluation_protocol_sha256")
                or sha256_file(manifest_path) != selection["data_source"]["source_manifest_sha256"]):
            raise ValueError("Monitoring candidate, manifest, data, or artifact identity differs")
        reference_body = download(REFERENCE_PATH)
        reference = strict_json(reference_body.decode())
        if (selection.get("monitor_reference") != reference_descriptor(reference, reference_body)
                or any(tags.get(k) != v for k, v in reference_registry_tags(selection).items())
                or not reference_registry_tags(selection)):
            raise ValueError("Registered monitoring reference SHA-256/descriptor mismatch")
        validate_training_reference(reference, selection, frame=frame)
    after = client.get_model_version(PROFILE["model_name"], version)
    if (after.name != before.name or str(after.version) != version or after.status != "READY"
            or after.run_id != before.run_id or after.tags != tags):
        raise ValueError("Registered monitoring evidence changed during retrieval")
    return reference, selection, tags
