"""Score a validation-selected Capital Bikeshare candidate on the reserved period."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from bike_mlops.config import MODEL_NAME, PROJECT_ROOT
from bike_mlops.data import load_hourly, sha256_file
from bike_mlops.features import FEATURE_COLUMNS, feature_frame_for_dataset
from bike_mlops.model import load_registered_model
from bike_mlops.training import (
    HYPERPARAMETERS,
    HistoricalBaseline,
    build_estimator,
    regression_metrics,
    segment_mae,
)


DEFAULT_CSV = PROJECT_ROOT / "data/derived/capitalbikeshare-hourly-2023-202604.csv"
DEFAULT_MANIFEST = PROJECT_ROOT / "data/derived/capitalbikeshare-hourly-2023-202604-manifest.json"
DEFAULT_SELECTION_REPORT = PROJECT_ROOT / "data/reports/candidate-capitalbikeshare-2025h1-v3.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/reports/capitalbikeshare-2025h2-202604-final-evaluation.json"


def _project_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--selection-report", type=Path, default=DEFAULT_SELECTION_REPORT)
    parser.add_argument("--incumbent-version", default="3")
    parser.add_argument("--evaluation-start", default="2025-07-01 00:00:00")
    parser.add_argument("--evaluation-end", default="2026-05-01 00:00:00")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.output.exists():
        raise FileExistsError(
            f"Refusing to score the reserved period again because the report already exists: {args.output}"
        )

    selection = json.loads(args.selection_report.read_text(encoding="utf-8"))
    if selection.get("candidate_mode") != "rolling_time_series" or not selection.get("quality_gate_passed"):
        raise ValueError("The selection report must be a passed rolling candidate evaluation")
    if str(selection.get("comparison", {}).get("incumbent_model_version")) != str(args.incumbent_version):
        raise ValueError("The requested incumbent does not match the validation comparison report")
    if selection.get("hyperparameters") != HYPERPARAMETERS:
        raise ValueError("Candidate hyperparameters changed after validation; refusing an untracked refit")
    if selection.get("feature_columns") != list(FEATURE_COLUMNS):
        raise ValueError("Candidate feature contract changed after validation; refusing an untracked refit")

    evaluation_start = pd.Timestamp(args.evaluation_start)
    evaluation_end = pd.Timestamp(args.evaluation_end)
    validation_end = pd.Timestamp(selection["validation_window"]["end_exclusive"])
    training_cutoff = pd.Timestamp(selection["training_cutoff"])
    validation_start = pd.Timestamp(selection["validation_window"]["start"])
    for name, timestamp in (
        ("evaluation_start", evaluation_start),
        ("evaluation_end", evaluation_end),
        ("validation_end", validation_end),
        ("training_cutoff", training_cutoff),
        ("validation_start", validation_start),
    ):
        if timestamp.tzinfo is not None or timestamp != timestamp.floor("h"):
            raise ValueError(f"{name} must be a timezone-naive hour boundary")
    if not validation_end <= evaluation_start < evaluation_end:
        raise ValueError("The final evaluation window must begin at or after validation end")

    data_hash = sha256_file(args.csv)
    if data_hash != selection.get("data_sha256"):
        raise ValueError("The selected CSV differs from the CSV used for candidate validation")
    manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    manifest_hash = manifest.get("data_sha256", manifest.get("derived_csv_sha256"))
    if manifest_hash != data_hash:
        raise ValueError("The source manifest does not identify the selected CSV")

    frame = load_hourly(args.csv)
    features = feature_frame_for_dataset(frame)
    train_mask = (frame["timestamp"] < validation_start) & (
        frame["timestamp"] + pd.Timedelta(hours=3) <= training_cutoff
    )
    evaluation_mask = (frame["timestamp"] >= evaluation_start) & (
        frame["timestamp"] < evaluation_end
    )
    if not train_mask.any() or not evaluation_mask.any():
        raise ValueError("The selected data does not cover both training and final evaluation windows")
    if int(train_mask.sum()) != int(selection["split_counts"]["train"]):
        raise ValueError("Refit training rows differ from the validation-selected candidate report")

    train_frame = frame.loc[train_mask].reset_index(drop=True)
    train_features = features.loc[train_mask, FEATURE_COLUMNS].reset_index(drop=True)
    evaluation_frame = frame.loc[evaluation_mask].reset_index(drop=True)
    evaluation_features = features.loc[evaluation_mask, FEATURE_COLUMNS].reset_index(drop=True)
    latest_training_label = train_frame["timestamp"].max() + pd.Timedelta(hours=3)
    if latest_training_label > training_cutoff:
        raise AssertionError("The refit included a label unavailable at the original training cutoff")

    candidate = build_estimator()
    candidate.fit(train_features, train_frame["cnt"])
    candidate_prediction = np.maximum(candidate.predict(evaluation_features), 0.0)

    incumbent, incumbent_artifact_sha256, incumbent_gate = load_registered_model(
        MODEL_NAME, args.incumbent_version
    )
    expected_incumbent_sha = selection["comparison"]["incumbent_artifact_sha256"]
    if incumbent_artifact_sha256 != expected_incumbent_sha or incumbent_gate != "passed":
        raise ValueError("The incumbent artifact or gate state changed after candidate validation")
    incumbent_prediction = np.maximum(incumbent.predict(evaluation_features), 0.0)

    baseline = HistoricalBaseline().fit(train_frame, frame)
    baseline_prediction = baseline.predict(evaluation_frame["timestamp"])
    summary = {
        "evaluation_kind": "reserved_out_of_time_final_evaluation",
        "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "candidate_selection_report": _project_relative(args.selection_report),
        "candidate_selection_quality_gate_passed": True,
        "candidate_model_version": None,
        "candidate_fit": {
            "same_training_cutoff_as_selected_report": training_cutoff.isoformat(sep=" "),
            "latest_label_available_at": latest_training_label.isoformat(sep=" "),
            "train_rows": int(train_mask.sum()),
            "hyperparameters": HYPERPARAMETERS,
            "feature_columns": list(FEATURE_COLUMNS),
        },
        "incumbent": {
            "model_version": str(args.incumbent_version),
            "artifact_sha256": incumbent_artifact_sha256,
            "quality_gate": incumbent_gate,
        },
        "data_source": {
            "dataset": manifest.get("source"),
            "source_page": manifest.get("source_page"),
            "license_page": manifest.get("license_page"),
            "csv_path": _project_relative(args.csv),
            "csv_sha256": data_hash,
            "source_manifest_path": _project_relative(args.source_manifest),
            "source_manifest_sha256": sha256_file(args.source_manifest),
            "target_definition_matches_incumbent_training_data": False,
        },
        "evaluation_window": {
            "start_inclusive": evaluation_start.isoformat(sep=" "),
            "end_exclusive": evaluation_end.isoformat(sep=" "),
            "rows_scored": int(evaluation_mask.sum()),
            "used_for_candidate_selection": False,
            "used_for_hyperparameter_tuning": False,
            "source_data_quality_audit_had_seen_aggregate_statistics": True,
        },
        "candidate": {
            "model": regression_metrics(evaluation_frame["cnt"], candidate_prediction),
            "model_by_segment": segment_mae(evaluation_frame, candidate_prediction),
        },
        "incumbent_metrics_on_same_timestamps": {
            "model": regression_metrics(evaluation_frame["cnt"], incumbent_prediction),
            "model_by_segment": segment_mae(evaluation_frame, incumbent_prediction),
        },
        "historical_baseline": {
            "model": regression_metrics(evaluation_frame["cnt"], baseline_prediction),
            "model_by_segment": segment_mae(evaluation_frame, baseline_prediction),
        },
        "monthly_mae": {},
        "interpretation": (
            "This one-time out-of-time score reports the frozen candidate beside the incumbent and baseline. "
            "It is not a new candidate-selection or hyperparameter-tuning window."
        ),
        "limitations": [
            "Capital Bikeshare trip-start counts do not have exactly the same target definition as the UCI incumbent data.",
            "The three-hour label-availability delay is an experiment assumption, not a measured source-feed guarantee.",
            "The source data was inspected for aggregate data-quality statistics, so this is not an unseen blind holdout.",
            "Metrics do not establish live demand accuracy or business impact.",
        ],
    }
    for month, group in evaluation_frame.groupby(evaluation_frame["timestamp"].dt.strftime("%Y-%m")):
        positions = group.index.to_numpy()
        summary["monthly_mae"][month] = {
            "rows": int(len(group)),
            "candidate": regression_metrics(group["cnt"], candidate_prediction[positions])["mae"],
            "incumbent": regression_metrics(group["cnt"], incumbent_prediction[positions])["mae"],
            "baseline": regression_metrics(group["cnt"], baseline_prediction[positions])["mae"],
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(summary, output, indent=2, ensure_ascii=False)
        output.write("\n")
    print(json.dumps({"report_path": _project_relative(args.output), **summary}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
