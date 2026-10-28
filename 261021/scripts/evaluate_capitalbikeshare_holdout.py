"""Score a validation-selected, hash-verified candidate on its frozen reserved period."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from bike_mlops.candidate_artifacts import (
    candidate_identity,
    load_candidate_model,
    source_snapshot_inventory,
)
from bike_mlops.capital_data import validate_capital_manifest
from bike_mlops.config import MODEL_NAME, PROJECT_ROOT
from bike_mlops.data import sha256_file
from bike_mlops.evaluation_protocol import (
    begin_evaluation,
    finish_evaluation,
    read_protocol,
)
from bike_mlops.features import FEATURE_COLUMNS, feature_frame_for_dataset
from bike_mlops.model import load_registered_model
from bike_mlops.training import HistoricalBaseline, regression_metrics, segment_mae


def _project_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _atomic_output(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError as error:
        raise FileExistsError(f"Refusing to overwrite final-evaluation evidence: {path}") from error
    finally:
        temporary.unlink(missing_ok=True)


def _row_keys_sha256(frame: pd.DataFrame) -> str:
    payload = "".join(
        f"{timestamp.isoformat(sep=' ')}\t{int(target)}\n"
        for timestamp, target in zip(frame["timestamp"], frame["cnt"])
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def _verify_validation_evidence(
    selection: dict,
    predictions_path: Path,
    frame: pd.DataFrame,
    protocol: dict,
    candidate,
    incumbent,
) -> None:
    info = selection["validation_predictions"]
    if info.get("path") != "validation-predictions.csv":
        raise ValueError("Candidate validation-prediction path is not the canonical bundle file")
    if sha256_file(predictions_path) != info.get("sha256"):
        raise ValueError("Candidate validation-predictions SHA-256 mismatch")
    predictions = pd.read_csv(predictions_path)
    required = {
        "timestamp", "label_available_at", "actual", "candidate_prediction",
        "incumbent_prediction", "baseline_prediction",
    }
    if (
        set(predictions.columns) != required
        or len(predictions) != protocol["validation"]["rows"]
        or len(predictions) != info.get("rows")
    ):
        raise ValueError("Candidate validation-predictions columns or row count differ from the protocol")

    train_cutoff = pd.Timestamp(protocol["training"]["cutoff"])
    validation_start = pd.Timestamp(protocol["validation"]["start_inclusive"])
    validation_end = pd.Timestamp(protocol["validation"]["end_exclusive"])
    delay = pd.Timedelta(hours=protocol["label_availability"]["delay_hours"])
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
    for column, expected in (
        ("candidate_prediction", candidate_prediction),
        ("incumbent_prediction", incumbent_prediction),
        ("baseline_prediction", baseline_prediction),
    ):
        if not np.allclose(predictions[column].to_numpy(dtype=float), expected, rtol=0, atol=1e-9):
            raise ValueError(f"Candidate validation {column} does not match the saved model and data")

    validation_report = selection["validation"]
    candidate_metrics = regression_metrics(validation["cnt"], candidate_prediction)
    incumbent_metrics = regression_metrics(validation["cnt"], incumbent_prediction)
    baseline_metrics = regression_metrics(validation["cnt"], baseline_prediction)
    _assert_metrics_equal(validation_report["model"], candidate_metrics, "candidate")
    _assert_metrics_equal(validation_report["baseline"], baseline_metrics, "baseline")
    _assert_metrics_equal(selection["comparison"]["incumbent"], incumbent_metrics, "incumbent")
    _assert_segments_equal(
        validation_report["model_by_segment"], segment_mae(validation, candidate_prediction), "candidate"
    )
    _assert_segments_equal(
        validation_report["baseline_by_segment"], segment_mae(validation, baseline_prediction), "baseline"
    )
    expected_gate = (
        selection["comparison"]["incumbent_quality_gate"] == "passed"
        and candidate_metrics["mae"] < incumbent_metrics["mae"]
        and candidate_metrics["mae"] < baseline_metrics["mae"]
    )
    if bool(selection["quality_gate_passed"]) != expected_gate or not expected_gate:
        raise ValueError("Candidate validation quality gate does not pass when recomputed from row-level evidence")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--selection-report", type=Path, required=True,
        help="Canonical candidate-report.json inside the immutable candidate bundle")
    parser.add_argument("--evaluation-protocol", type=Path, help="Override the exact protocol copy bundled with the selected candidate")
    parser.add_argument("--incumbent-version")
    parser.add_argument("--evaluation-start")
    parser.add_argument("--evaluation-end")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite final-evaluation evidence: {args.output}")
    try:
        selection = json.loads(args.selection_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
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
        bundle_relative = str(args.selection_report.parent.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError as error:
        raise ValueError("Candidate bundle must be stored inside the project directory") from error
    if selection.get("candidate_bundle") != bundle_relative or not bundle_relative.startswith("data/candidates/"):
        raise ValueError("Selection report candidate bundle path does not match its directory")
    if args.selection_report.parent.resolve() != (PROJECT_ROOT / "data/candidates" / candidate_id).resolve():
        raise ValueError("Candidate bundle is not under the canonical data/candidates directory")
    try:
        computed_identity = candidate_identity(selection)
    except (KeyError, TypeError) as error:
        raise ValueError(f"Candidate report is missing frozen identity fields: {error}") from error
    if computed_identity != selection.get("candidate_identity"):
        raise ValueError("Candidate identity fields differ from the frozen candidate report")
    expected_candidate_id = hashlib.sha256(
        json.dumps(computed_identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
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
    if sha256_file(bundled_protocol_path) != protocol_info.get("sha256"):
        raise ValueError("Candidate bundle evaluation-protocol artifact hash mismatch")
    snapshot_info = selection.get("source_snapshot")
    if not isinstance(snapshot_info, dict) or snapshot_info.get("path") != "source-snapshot.tar.gz":
        raise ValueError("Candidate bundle does not include its canonical source snapshot")
    if sha256_file(snapshot_path) != snapshot_info.get("sha256"):
        raise ValueError("Candidate source-snapshot SHA-256 mismatch")
    snapshot_tree_sha, snapshot_file_count = source_snapshot_inventory(snapshot_path)
    if (
        snapshot_tree_sha != selection.get("source_revision", {}).get("source_tree_sha256")
        or snapshot_file_count != selection.get("source_revision", {}).get("source_file_count")
    ):
        raise ValueError("Candidate source snapshot differs from its recorded source-tree identity")
    protocol = read_protocol(protocol_path)
    if protocol["protocol_sha256"] != selection.get("evaluation_protocol_sha256"):
        raise ValueError("Selected candidate and evaluation protocol hashes differ")
    if selection.get("training_cutoff") != protocol["training"]["cutoff"]:
        raise ValueError("Selected candidate training cutoff differs from the frozen protocol")
    if selection.get("validation_window") != {
        "start": protocol["validation"]["start_inclusive"],
        "end_exclusive": protocol["validation"]["end_exclusive"],
    }:
        raise ValueError("Selected candidate validation window differs from the frozen protocol")
    comparison = selection.get("comparison", {})
    if (
        str(comparison.get("incumbent_model_version")) != str(protocol["incumbent"]["model_version"])
        or comparison.get("incumbent_artifact_sha256") != protocol["incumbent"]["artifact_sha256"]
        or comparison.get("incumbent_quality_gate") != protocol["incumbent"]["quality_gate"]
    ):
        raise ValueError("Selected candidate incumbent comparison differs from the frozen protocol")
    if sha256_file(artifact_path) != artifact_info.get("sha256"):
        raise ValueError("Selected candidate artifact SHA-256 mismatch")
    if args.evaluation_protocol and sha256_file(args.evaluation_protocol) != sha256_file(bundled_protocol_path):
        raise ValueError("Override protocol bytes differ from the protocol frozen with this candidate")

    manifest, frame = validate_capital_manifest(
        json.loads(args.source_manifest.read_text(encoding="utf-8")), args.csv
    )
    data_hash = sha256_file(args.csv)
    if data_hash != protocol["dataset"]["data_sha256"] or data_hash != selection.get("data_sha256"):
        raise ValueError("Final-evaluation CSV differs from the frozen candidate data")
    manifest_hash = sha256_file(args.source_manifest)
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

    incumbent_version = str(args.incumbent_version or protocol["incumbent"]["model_version"])
    if incumbent_version != str(protocol["incumbent"]["model_version"]):
        raise ValueError("Requested incumbent version differs from the frozen protocol")
    evaluation_start = pd.Timestamp(protocol["evaluation"]["start_inclusive"])
    evaluation_end = pd.Timestamp(protocol["evaluation"]["end_exclusive"])
    if args.evaluation_start and pd.Timestamp(args.evaluation_start) != evaluation_start:
        raise ValueError("Requested evaluation start differs from the frozen protocol")
    if args.evaluation_end and pd.Timestamp(args.evaluation_end) != evaluation_end:
        raise ValueError("Requested evaluation end differs from the frozen protocol")
    if protocol["evaluation"]["rows"] <= 0:
        raise ValueError("Frozen protocol contains no reserved evaluation rows")

    incumbent, incumbent_sha, incumbent_gate = load_registered_model(MODEL_NAME, incumbent_version)
    if incumbent_sha != protocol["incumbent"]["artifact_sha256"] or incumbent_gate != "passed":
        raise ValueError("Loaded incumbent artifact/gate differs from the frozen protocol")

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
        incumbent_prediction = np.maximum(incumbent.predict(evaluation_features), 0.0)
        train_cutoff = pd.Timestamp(protocol["training"]["cutoff"])
        training_start = pd.Timestamp(protocol["validation"]["start_inclusive"])
        training_mask = (frame["timestamp"] < training_start) & (
            frame["timestamp"] + pd.Timedelta(hours=protocol["label_availability"]["delay_hours"]) <= train_cutoff
        )
        if int(training_mask.sum()) != int(protocol["training"]["rows"]):
            raise ValueError("Training rows reconstructed from the frozen protocol do not match")
        training_frame = frame.loc[training_mask].reset_index(drop=True)
        baseline = HistoricalBaseline().fit(training_frame, frame)
        baseline_prediction = baseline.predict(evaluation_frame["timestamp"])

        predictions = pd.DataFrame({
            "timestamp": evaluation_frame["timestamp"].map(lambda stamp: stamp.isoformat(sep=" ")),
            "label_available_at": (evaluation_frame["timestamp"] + pd.Timedelta(hours=3)).map(
                lambda stamp: stamp.isoformat(sep=" ")
            ),
            "actual": evaluation_frame["cnt"].astype(int),
            "candidate_prediction": candidate_prediction,
            "incumbent_prediction": incumbent_prediction,
            "baseline_prediction": baseline_prediction,
        })
        predictions_path = args.output.with_suffix(".predictions.csv")
        if predictions_path.exists():
            raise FileExistsError(f"Refusing to overwrite row-level evidence: {predictions_path}")
        predictions_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=predictions_path.parent, delete=False) as stream:
            temporary_predictions = Path(stream.name)
            predictions.to_csv(stream, index=False, float_format="%.17g")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_predictions, predictions_path)
        except FileExistsError as error:
            raise FileExistsError(f"Refusing to overwrite row-level evidence: {predictions_path}") from error
        finally:
            temporary_predictions.unlink(missing_ok=True)

        summary = {
            "evaluation_kind": "frozen_candidate_reserved_out_of_time_evaluation",
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
                "row_level_predictions_sha256": sha256_file(predictions_path),
            },
            "candidate": {
                "model": regression_metrics(evaluation_frame["cnt"], candidate_prediction),
                "model_by_segment": segment_mae(evaluation_frame, candidate_prediction),
            },
            "incumbent": {
                "model_version": incumbent_version,
                "artifact_sha256": incumbent_sha,
                "quality_gate": incumbent_gate,
                "model_metrics": regression_metrics(evaluation_frame["cnt"], incumbent_prediction),
                "model_by_segment": segment_mae(evaluation_frame, incumbent_prediction),
            },
            "historical_baseline": {
                "model": regression_metrics(evaluation_frame["cnt"], baseline_prediction),
                "model_by_segment": segment_mae(evaluation_frame, baseline_prediction),
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
        detail=sha256_file(args.output),
        candidate_artifact_sha256=candidate_sha,
    )
    print(json.dumps({"report_path": _project_relative(args.output), **summary}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
