from __future__ import annotations

import importlib.metadata
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline

from bike_mlops.config import (
    DATASET_MANIFEST,
    DATASET_URL,
    DEFAULT_DATASET,
    DEFAULT_MLFLOW_URI,
    FEATURE_CONTRACT_VERSION,
    MODEL_NAME,
    PROJECT_ROOT,
)
from bike_mlops.data import load_hourly, sha256_file
from bike_mlops.features import FEATURE_COLUMNS, feature_frame_for_dataset
from bike_mlops.model import directory_sha256, load_registered_model

SPLITS = {
    "train_end": pd.Timestamp("2012-01-01 00:00:00"),
    "validation_end": pd.Timestamp("2012-04-01 00:00:00"),
    "holdout_end": pd.Timestamp("2012-07-01 00:00:00"),
    "training_cutoff": pd.Timestamp("2012-01-01 03:00:00"),
}
RANDOM_STATE = 17
HYPERPARAMETERS = {
    "learning_rate": 0.08,
    "max_iter": 160,
    "max_leaf_nodes": 31,
    "l2_regularization": 1.0,
    "early_stopping": False,
    "random_state": RANDOM_STATE,
}
SKOPS_TRUSTED_TYPES = (
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
)


def build_estimator() -> Pipeline:
    return Pipeline(
        [
            (
                "regressor",
                HistGradientBoostingRegressor(
                    loss="squared_error",
                    learning_rate=HYPERPARAMETERS["learning_rate"],
                    max_iter=HYPERPARAMETERS["max_iter"],
                    max_leaf_nodes=HYPERPARAMETERS["max_leaf_nodes"],
                    l2_regularization=HYPERPARAMETERS["l2_regularization"],
                    early_stopping=False,
                    random_state=RANDOM_STATE,
                ),
            )
        ]
    )


class HistoricalBaseline:
    """Previous same hour yesterday, then train-only weekday/hour mean, then global mean."""

    def fit(
        self, train_frame: pd.DataFrame, history_frame: pd.DataFrame | None = None
    ) -> "HistoricalBaseline":
        history_frame = train_frame if history_frame is None else history_frame
        self.counts = dict(zip(history_frame["timestamp"], history_frame["cnt"].astype(float)))
        groups = train_frame.assign(
            weekday=train_frame["timestamp"].dt.dayofweek,
            hour=train_frame["timestamp"].dt.hour,
        )
        self.weekday_hour = groups.groupby(["weekday", "hour"])["cnt"].mean().to_dict()
        self.global_mean = float(train_frame["cnt"].mean())
        return self

    def predict(self, timestamps) -> np.ndarray:
        predictions = []
        for timestamp in timestamps:
            target = pd.Timestamp(timestamp)
            previous_day = target - pd.Timedelta(hours=24)
            if previous_day in self.counts:
                value = self.counts[previous_day]
            else:
                value = self.weekday_hour.get((target.dayofweek, target.hour), self.global_mean)
            predictions.append(max(float(value), 0.0))
        return np.asarray(predictions, dtype=float)


def regression_metrics(actual, predicted) -> dict[str, float]:
    return {
        "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
    }


def segment_mae(frame: pd.DataFrame, predicted: np.ndarray) -> dict[str, dict[str, float | int]]:
    result = {}
    for label, mask in (
        ("weekday", frame["timestamp"].dt.dayofweek < 5),
        ("weekend", frame["timestamp"].dt.dayofweek >= 5),
        ("commute_morning_7_10", frame["timestamp"].dt.hour.between(7, 9)),
        ("commute_evening_16_19", frame["timestamp"].dt.hour.between(16, 18)),
    ):
        if bool(mask.any()):
            result[label] = {
                "n": int(mask.sum()),
                "mae": float(mean_absolute_error(frame.loc[mask, "cnt"], predicted[mask.to_numpy()])),
            }
    return result


def _source_revision() -> dict:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    digest = hashlib.sha256()
    file_count = 0
    for path in sorted(PROJECT_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(PROJECT_ROOT)
        if any(
            part in {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", "data", "build", "dist"}
            or part.endswith(".egg-info")
            for part in relative.parts
        ):
            continue
        if relative.name == ".env":
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        file_count += 1
    try:
        status = subprocess.check_output(
            ["git", "status", "--short", "--", "261021"],
            cwd=PROJECT_ROOT.parent,
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
    except (OSError, subprocess.CalledProcessError):
        status = []
    return {
        "git_commit": commit,
        "worktree_dirty": bool(status),
        "worktree_status_entries": status,
        "source_tree_sha256": digest.hexdigest(),
        "source_file_count": file_count,
    }


def _versions() -> dict:
    packages = {
        distribution.metadata["Name"]: distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    names = ("bike-demand-mlops", "numpy", "pandas", "scikit-learn", "mlflow", "psycopg", "fastapi", "uvicorn")
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "direct_dependencies": {name: packages.get(name) for name in names},
        "all_installed_packages": dict(sorted(packages.items(), key=lambda pair: pair[0].lower())),
    }


def _log_and_register(report: dict, estimator: Pipeline, x_example: pd.DataFrame) -> dict:
    import mlflow
    import mlflow.sklearn
    from mlflow.tracking import MlflowClient

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", DEFAULT_MLFLOW_URI)
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("bike-demand-operations")
    with mlflow.start_run(
        run_name="rolling-candidate-hist-gradient-boosting" if report.get("comparison") else "hourly-hist-gradient-boosting"
    ) as run:
        run_id = run.info.run_id
        mlflow.log_params(HYPERPARAMETERS)
        run_params = {
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
            "data_sha256": report["data_sha256"],
        }
        if comparison := report.get("comparison"):
            run_params.update(
                {
                    "candidate_mode": "rolling_time_series",
                    "incumbent_model_version": comparison["incumbent_model_version"],
                    "candidate_training_cutoff": report["training_cutoff"],
                    "candidate_validation_start": report["validation_window"]["start"],
                    "candidate_validation_end_exclusive": report["validation_window"]["end_exclusive"],
                }
            )
        else:
            run_params.update(
                {
                    "split_train_end_exclusive": str(SPLITS["train_end"]),
                    "split_validation_end_exclusive": str(SPLITS["validation_end"]),
                    "split_holdout_end_exclusive": str(SPLITS["holdout_end"]),
                    "training_cutoff": str(SPLITS["training_cutoff"]),
                }
            )
        mlflow.log_params(run_params)
        metric_values = {
            f"validation_model_{key}": value
            for key, value in report["validation"]["model"].items()
        } | {
            f"validation_baseline_{key}": value
            for key, value in report["validation"]["baseline"].items()
        }
        if report.get("final_holdout"):
            metric_values |= {
                f"final_holdout_model_{key}": value
                for key, value in report["final_holdout"]["model"].items()
            }
            metric_values |= {
                f"final_holdout_baseline_{key}": value
                for key, value in report["final_holdout"]["baseline"].items()
            }
        if comparison:
            metric_values["validation_incumbent_mae"] = comparison["incumbent"]["mae"]
        mlflow.log_metrics(metric_values)
        run_tags = {
            "quality_gate": "passed" if report["quality_gate_passed"] else "failed",
            "data_sha256": report["data_sha256"],
            "git_revision": report["source_revision"]["git_commit"] or "unknown",
            "source_tree_sha256": report["source_revision"]["source_tree_sha256"],
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
        }
        if comparison:
            run_tags["comparison_reference"] = "incumbent_and_historical_baseline"
            run_tags["incumbent_model_version"] = str(comparison["incumbent_model_version"])
            run_tags["incumbent_artifact_sha256"] = comparison["incumbent_artifact_sha256"]
        mlflow.set_tags(run_tags)
        mlflow.log_dict(report, "training-report.json")
        mlflow.log_dict(report["model_card"], "model-card.json")
        model_info = mlflow.sklearn.log_model(
            estimator,
            name="sklearn-model",
            input_example=x_example.head(5),
            # HistGradientBoostingRegressor contains this sklearn tree type.
            # Trust only this reviewed type; MLflow records the allowlist for loading.
            skops_trusted_types=list(SKOPS_TRUSTED_TYPES),
        )
        client = MlflowClient(tracking_uri=tracking_uri)
        from mlflow.exceptions import MlflowException

        try:
            client.get_registered_model(MODEL_NAME)
        except MlflowException as error:
            if error.error_code != "RESOURCE_DOES_NOT_EXIST":
                raise
            client.create_registered_model(MODEL_NAME)
        version_tags = {
            "quality_gate": "passed" if report["quality_gate_passed"] else "failed",
            "data_sha256": report["data_sha256"],
            "validation_mae": str(report["validation"]["model"]["mae"]),
            "validation_baseline_mae": str(report["validation"]["baseline"]["mae"]),
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
        }
        if comparison:
            version_tags["comparison_reference"] = "incumbent_and_historical_baseline"
            version_tags["incumbent_model_version"] = str(comparison["incumbent_model_version"])
            version_tags["incumbent_validation_mae"] = str(comparison["incumbent"]["mae"])
            version_tags["incumbent_artifact_sha256"] = comparison["incumbent_artifact_sha256"]
        version = client.create_model_version(
            name=MODEL_NAME,
            source=f"runs:/{run_id}/sklearn-model",
            run_id=run_id,
            tags=version_tags,
        )
    for _ in range(60):
        current = client.get_model_version(MODEL_NAME, str(version.version))
        if current.status == "READY":
            break
        if current.status == "FAILED_REGISTRATION":
            raise RuntimeError(f"MLflow registration failed: {current.status_message}")
        time.sleep(1)
    else:
        raise TimeoutError("Timed out waiting for MLflow model registration")
    model_uri = f"models:/{MODEL_NAME}/{version.version}"
    artifact_path = Path(mlflow.artifacts.download_artifacts(artifact_uri=model_uri))
    artifact_hash = directory_sha256(artifact_path)
    client.set_model_version_tag(MODEL_NAME, str(version.version), "artifact_sha256", artifact_hash)
    registration_manifest = {
        "run_id": run_id,
        "model_name": MODEL_NAME,
        "model_version": str(version.version),
        "model_uri": model_uri,
        "artifact_sha256": artifact_hash,
        "quality_gate_passed": report["quality_gate_passed"],
    }
    mlflow.log_dict(registration_manifest, "registration-manifest.json", run_id=run_id)
    return {
        "run_id": run_id,
        "model_name": MODEL_NAME,
        "model_version": str(version.version),
        "model_uri": model_uri,
        "artifact_sha256": artifact_hash,
    }


def train(
    data_path: Path = DEFAULT_DATASET,
    register: bool = True,
    evaluate_final_holdout: bool = False,
) -> dict:
    frame = load_hourly(data_path)
    data_hash = sha256_file(data_path)
    source_manifest = None
    if DATASET_MANIFEST.exists():
        try:
            candidate_manifest = json.loads(DATASET_MANIFEST.read_text(encoding="utf-8"))
            if candidate_manifest.get("data_sha256") == data_hash:
                source_manifest = candidate_manifest
        except (OSError, json.JSONDecodeError):
            source_manifest = None
    features = feature_frame_for_dataset(frame)
    train_mask = frame["timestamp"] < SPLITS["train_end"]
    validation_mask = (frame["timestamp"] >= SPLITS["train_end"]) & (
        frame["timestamp"] < SPLITS["validation_end"]
    )
    holdout_mask = (frame["timestamp"] >= SPLITS["validation_end"]) & (
        frame["timestamp"] < SPLITS["holdout_end"]
    )
    if not train_mask.any() or not validation_mask.any() or not holdout_mask.any():
        raise ValueError("Dataset does not cover the fixed train, validation, and final holdout periods")
    train_frame = frame.loc[train_mask].reset_index(drop=True)
    train_features = features.loc[train_mask, FEATURE_COLUMNS].reset_index(drop=True)
    validation_frame = frame.loc[validation_mask].reset_index(drop=True)
    validation_features = features.loc[validation_mask, FEATURE_COLUMNS].reset_index(drop=True)
    holdout_frame = frame.loc[holdout_mask].reset_index(drop=True)
    holdout_features = features.loc[holdout_mask, FEATURE_COLUMNS].reset_index(drop=True)

    max_train_label_available = train_frame["timestamp"].max() + pd.Timedelta(hours=3)
    if max_train_label_available > SPLITS["training_cutoff"]:
        raise ValueError("Training includes a label unavailable at the configured training cutoff")

    estimator = build_estimator()
    estimator.fit(train_features, train_frame["cnt"])
    validation_prediction = np.maximum(estimator.predict(validation_features), 0.0)
    baseline = HistoricalBaseline().fit(train_frame, frame)
    validation_baseline = baseline.predict(validation_frame["timestamp"])
    gate_passed = (
        regression_metrics(validation_frame["cnt"], validation_prediction)["mae"]
        < regression_metrics(validation_frame["cnt"], validation_baseline)["mae"]
    )
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_sha256": data_hash,
        "source_revision": _source_revision(),
        "environment": _versions(),
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "feature_columns": list(FEATURE_COLUMNS),
        "hyperparameters": HYPERPARAMETERS,
        "split_counts": {
            "train": int(train_mask.sum()),
            "validation": int(validation_mask.sum()),
            "final_holdout": int(holdout_mask.sum()),
        },
        "training_cutoff": SPLITS["training_cutoff"].isoformat(sep=" "),
        "training_latest_label_available_at": max_train_label_available.isoformat(sep=" "),
        "validation": {
            "model": regression_metrics(validation_frame["cnt"], validation_prediction),
            "baseline": regression_metrics(validation_frame["cnt"], validation_baseline),
            "model_by_segment": segment_mae(validation_frame, validation_prediction),
            "baseline_by_segment": segment_mae(validation_frame, validation_baseline),
        },
        "quality_gate_passed": bool(gate_passed),
        "quality_gate": "validation MAE must be strictly lower than previous-day/weekday-hour baseline MAE",
        "final_holdout": None,
        "operational_scope": "retrospective dataset replay; not a live demand performance claim",
        "data_source": {
            "dataset": "UCI Bike Sharing Dataset (ID 275)" if source_manifest else "caller-supplied UCI-schema hourly CSV",
            "url": DATASET_URL if source_manifest else None,
            "csv_sha256": data_hash,
            "archive_sha256": source_manifest.get("archive_sha256") if source_manifest else None,
            "citation": source_manifest.get("citation") if source_manifest else None,
            "license": source_manifest.get("license") if source_manifest else "not verified from a matching source manifest",
            "source_manifest_verified_for_csv": source_manifest is not None,
        },
        "model_card": {
            "name": MODEL_NAME,
            "model_family": "scikit-learn HistGradientBoostingRegressor in a Pipeline",
            "prediction_target": "hourly aggregate bike rentals (cnt) for the hour starting at target_hour",
            "intended_use": "education and controlled historical MLOps replay only",
            "out_of_scope": [
                "live rental-demand service",
                "business staffing, revenue, or inventory decisions",
                "claims of real-world operational impact",
            ],
            "data_source": {
                "dataset": "UCI Bike Sharing Dataset (ID 275)" if source_manifest else "caller-supplied UCI-schema hourly CSV",
                "csv_sha256": data_hash,
                "license": source_manifest.get("license") if source_manifest else "unverified",
                "citation": source_manifest.get("citation") if source_manifest else None,
            },
            "features": list(FEATURE_COLUMNS),
            "label_availability_assumption": "target timestamp + 3 hours; experimental, not measured from the source feed",
            "quality_gate": "validation MAE must strictly beat the previous-day/weekday-hour baseline",
            "promotion": "manual approval only; diagnostic mode is clearly labeled when the gate fails",
            "limitations": [
                "historical data covers a limited time range and one system-level aggregate",
                "actual future weather and target-hour labels are excluded",
                "results do not establish current demand accuracy or business impact",
            ],
        },
    }
    if evaluate_final_holdout:
        # Evaluate the fixed initial holdout only on the explicitly designated first run.
        holdout_prediction = np.maximum(estimator.predict(holdout_features), 0.0)
        holdout_baseline = baseline.predict(holdout_frame["timestamp"])
        report["final_holdout"] = {
            "model": regression_metrics(holdout_frame["cnt"], holdout_prediction),
            "baseline": regression_metrics(holdout_frame["cnt"], holdout_baseline),
            "model_by_segment": segment_mae(holdout_frame, holdout_prediction),
            "baseline_by_segment": segment_mae(holdout_frame, holdout_baseline),
            "usage": "fixed final holdout; reported for context, not used for candidate selection",
        }
    if register:
        report["registration"] = _log_and_register(report, estimator, train_features)
    return report


def train_candidate(
    data_path: Path = DEFAULT_DATASET,
    incumbent_version: str | int = 1,
    validation_start: str | pd.Timestamp = "2012-07-01 00:00:00",
    validation_end: str | pd.Timestamp = "2012-10-01 00:00:00",
    training_cutoff: str | pd.Timestamp = "2012-07-01 00:00:00",
    register: bool = True,
) -> dict:
    """Train a point-in-time candidate and compare it on a later time window.

    The training cutoff is the simulated time at which the model would have
    been fitted. Validation labels are only used for retrospective scoring;
    they never enter the candidate's fit.
    """
    start = pd.Timestamp(validation_start)
    end = pd.Timestamp(validation_end)
    cutoff = pd.Timestamp(training_cutoff)
    for name, value in (("validation_start", start), ("validation_end", end), ("training_cutoff", cutoff)):
        if value.tzinfo is not None:
            raise ValueError(f"{name} must be a naive dataset-local timestamp")
        if value != value.floor("h"):
            raise ValueError(f"{name} must align to the start of an hour")
    if start >= end:
        raise ValueError("validation_start must be earlier than validation_end")
    if cutoff > start:
        raise ValueError("training_cutoff must be no later than validation_start to prevent leakage")
    if int(incumbent_version) <= 0:
        raise ValueError("A positive, explicit incumbent model version is required")

    frame = load_hourly(data_path)
    data_hash = sha256_file(data_path)
    source_manifest = None
    if DATASET_MANIFEST.exists():
        try:
            candidate_manifest = json.loads(DATASET_MANIFEST.read_text(encoding="utf-8"))
            if candidate_manifest.get("data_sha256") == data_hash:
                source_manifest = candidate_manifest
        except (OSError, json.JSONDecodeError):
            source_manifest = None
    features = feature_frame_for_dataset(frame)
    train_mask = (frame["timestamp"] < start) & (
        frame["timestamp"] + pd.Timedelta(hours=3) <= cutoff
    )
    validation_mask = (frame["timestamp"] >= start) & (frame["timestamp"] < end)
    if not train_mask.any():
        raise ValueError("No training labels are available by the candidate training cutoff")
    if not validation_mask.any():
        raise ValueError("Dataset does not cover the requested candidate validation window")

    train_frame = frame.loc[train_mask].reset_index(drop=True)
    train_features = features.loc[train_mask, FEATURE_COLUMNS].reset_index(drop=True)
    validation_frame = frame.loc[validation_mask].reset_index(drop=True)
    validation_features = features.loc[validation_mask, FEATURE_COLUMNS].reset_index(drop=True)
    latest_label_available = train_frame["timestamp"].max() + pd.Timedelta(hours=3)
    if latest_label_available > cutoff:
        raise AssertionError("Candidate training mask included a label unavailable at training cutoff")

    estimator = build_estimator()
    estimator.fit(train_features, train_frame["cnt"])
    candidate_predictions = np.maximum(estimator.predict(validation_features), 0.0)
    baseline = HistoricalBaseline().fit(train_frame, frame)
    baseline_predictions = baseline.predict(validation_frame["timestamp"])

    incumbent, incumbent_artifact_sha256, incumbent_quality_gate = load_registered_model(
        MODEL_NAME, incumbent_version
    )
    incumbent_predictions = np.maximum(incumbent.predict(validation_features), 0.0)
    candidate_metrics = regression_metrics(validation_frame["cnt"], candidate_predictions)
    incumbent_metrics = regression_metrics(validation_frame["cnt"], incumbent_predictions)
    baseline_metrics = regression_metrics(validation_frame["cnt"], baseline_predictions)
    gate_passed = (
        incumbent_quality_gate == "passed"
        and candidate_metrics["mae"] < incumbent_metrics["mae"]
        and candidate_metrics["mae"] < baseline_metrics["mae"]
    )
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_mode": "rolling_time_series",
        "data_sha256": data_hash,
        "source_revision": _source_revision(),
        "environment": _versions(),
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "feature_columns": list(FEATURE_COLUMNS),
        "hyperparameters": HYPERPARAMETERS,
        "training_cutoff": cutoff.isoformat(sep=" "),
        "training_latest_label_available_at": latest_label_available.isoformat(sep=" "),
        "split_counts": {"train": int(train_mask.sum()), "validation": int(validation_mask.sum())},
        "validation_window": {"start": start.isoformat(sep=" "), "end_exclusive": end.isoformat(sep=" ")},
        "validation": {
            "model": candidate_metrics,
            "baseline": baseline_metrics,
            "model_by_segment": segment_mae(validation_frame, candidate_predictions),
            "baseline_by_segment": segment_mae(validation_frame, baseline_predictions),
        },
        "comparison": {
            "incumbent_model_version": str(incumbent_version),
            "incumbent_artifact_sha256": incumbent_artifact_sha256,
            "incumbent_quality_gate": incumbent_quality_gate,
            "incumbent": incumbent_metrics,
            "candidate_mae_improvement_vs_incumbent": incumbent_metrics["mae"] - candidate_metrics["mae"],
            "candidate_mae_improvement_vs_baseline": baseline_metrics["mae"] - candidate_metrics["mae"],
            "same_validation_timestamps": True,
        },
        "quality_gate_passed": bool(gate_passed),
        "quality_gate": (
            "candidate validation MAE must strictly beat both the explicitly selected, passed incumbent "
            "and the historical baseline on identical timestamps"
        ),
        "final_holdout": None,
        "operational_scope": "retrospective rolling-window candidate comparison; no live demand claim",
        "data_source": {
            "dataset": "UCI Bike Sharing Dataset (ID 275)" if source_manifest else "caller-supplied UCI-schema hourly CSV",
            "url": DATASET_URL if source_manifest else None,
            "csv_sha256": data_hash,
            "archive_sha256": source_manifest.get("archive_sha256") if source_manifest else None,
            "citation": source_manifest.get("citation") if source_manifest else None,
            "license": source_manifest.get("license") if source_manifest else "not verified from a matching source manifest",
            "source_manifest_verified_for_csv": source_manifest is not None,
        },
        "model_card": {
            "name": MODEL_NAME,
            "model_family": "scikit-learn HistGradientBoostingRegressor in a Pipeline",
            "prediction_target": "hourly aggregate bike rentals (cnt) for the hour starting at target_hour",
            "intended_use": "education and controlled historical MLOps replay only",
            "candidate_training_cutoff": cutoff.isoformat(sep=" "),
            "validation_window": {"start": start.isoformat(sep=" "), "end_exclusive": end.isoformat(sep=" ")},
            "incumbent_model_version": str(incumbent_version),
            "incumbent_artifact_sha256": incumbent_artifact_sha256,
            "features": list(FEATURE_COLUMNS),
            "data_source": {
                "csv_sha256": data_hash,
                "citation": source_manifest.get("citation") if source_manifest else None,
                "license": source_manifest.get("license") if source_manifest else "unverified",
            },
            "label_availability_assumption": "target timestamp + 3 hours; experimental, not measured from the source feed",
            "quality_gate": "candidate MAE must strictly beat the passed incumbent and historical baseline",
            "promotion": "manual approval only; candidate registration does not imply deployment",
            "limitations": [
                "validation metrics are retrospective and depend on the selected historical window",
                "the final replay window must remain independent of candidate selection",
                "results do not establish current demand accuracy or business impact",
            ],
        },
    }
    if register:
        report["registration"] = _log_and_register(report, estimator, train_features)
    return report
