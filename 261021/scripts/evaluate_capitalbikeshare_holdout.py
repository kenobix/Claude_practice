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
from bike_mlops.config import (
    DATA_PROFILE_ID,
    LABEL_DELAY_HOURS,
    MODEL_NAME,
    PROJECT_ROOT,
    TARGET_CONTRACT_ID,
)
from bike_mlops.data import sha256_file
from bike_mlops.evaluation_protocol import (
    begin_evaluation,
    finish_evaluation,
    read_protocol,
)
from bike_mlops.features import FEATURE_COLUMNS, feature_frame_for_dataset
from bike_mlops.model import load_registered_model
from bike_mlops.quality_gates import baseline_accuracy_gate
from bike_mlops.training import HistoricalBaseline, regression_metrics, segment_comparison, segment_mae


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
    predictions_path: Path,
    frame: pd.DataFrame,
    protocol: dict,
    candidate,
    incumbent,
) -> None:
    bootstrap_protocol = protocol.get("incumbent") is None
    info = selection["validation_predictions"]
    if info.get("path") != "validation-predictions.csv":
        raise ValueError("Candidate validation-prediction path is not the canonical bundle file")
    if sha256_file(predictions_path) != info.get("sha256"):
        raise ValueError("Candidate validation-predictions SHA-256 mismatch")
    predictions = pd.read_csv(predictions_path)
    required = {"timestamp", "label_available_at", "actual", "candidate_prediction", "baseline_prediction"}
    if not bootstrap_protocol:
        required.add("incumbent_prediction")
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

    if DATA_PROFILE_ID != "capital-bikeshare":
        raise SystemExit("Set DATA_PROFILE_ID=capital-bikeshare before final evaluation")

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
    if (
        protocol["dataset"]["data_profile_id"] != DATA_PROFILE_ID
        or protocol["dataset"]["target_contract"] != TARGET_CONTRACT_ID
    ):
        raise ValueError("Final-evaluation protocol profile/target differs from the active data profile")
    if protocol["protocol_sha256"] != selection.get("evaluation_protocol_sha256"):
        raise ValueError("Selected candidate and evaluation protocol hashes differ")
    if (
        selection.get("data_profile_id") != DATA_PROFILE_ID
        or selection.get("target_contract_id") != TARGET_CONTRACT_ID
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
        incumbent, incumbent_sha, incumbent_gate = load_registered_model(MODEL_NAME, incumbent_version)
        if incumbent_sha != protocol["incumbent"]["artifact_sha256"] or incumbent_gate != "passed":
            raise ValueError("Loaded incumbent artifact/gate differs from the frozen protocol")
    evaluation_start = pd.Timestamp(protocol["evaluation"]["start_inclusive"])
    evaluation_end = pd.Timestamp(protocol["evaluation"]["end_exclusive"])
    if args.evaluation_start and pd.Timestamp(args.evaluation_start) != evaluation_start:
        raise ValueError("Requested evaluation start differs from the frozen protocol")
    if args.evaluation_end and pd.Timestamp(args.evaluation_end) != evaluation_end:
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

        prediction_columns = {
            "timestamp": evaluation_frame["timestamp"].map(lambda stamp: stamp.isoformat(sep=" ")),
            "label_available_at": (evaluation_frame["timestamp"] + pd.Timedelta(hours=LABEL_DELAY_HOURS)).map(
                lambda stamp: stamp.isoformat(sep=" ")
            ),
            "actual": evaluation_frame["cnt"].astype(int),
            "candidate_prediction": candidate_prediction,
            "baseline_prediction": baseline_prediction,
        }
        if incumbent_prediction is not None:
            prediction_columns["incumbent_prediction"] = incumbent_prediction
        predictions = pd.DataFrame(prediction_columns)
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
            "data_profile_id": DATA_PROFILE_ID,
            "target_contract_id": TARGET_CONTRACT_ID,
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
        detail=sha256_file(args.output),
        candidate_artifact_sha256=candidate_sha,
    )
    print(json.dumps({"report_path": _project_relative(args.output), **summary}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
