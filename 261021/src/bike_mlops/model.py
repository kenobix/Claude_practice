from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

from bike_mlops.config import DATA_PROFILE_ID, DEFAULT_MLFLOW_URI, TARGET_CONTRACT_ID
from bike_mlops.deployment_gates import require_deployment_eligible


def directory_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(file_path.relative_to(path).as_posix().encode("utf-8"))
        with file_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def load_registered_model(model_name: str, model_version: str | int, tracking_uri: str | None = None):
    if int(model_version) <= 0:
        raise ValueError("A positive, explicit MLflow model version is required")
    import mlflow
    import mlflow.sklearn
    from mlflow.tracking import MlflowClient

    resolved_uri = tracking_uri or os.getenv("MLFLOW_TRACKING_URI", DEFAULT_MLFLOW_URI)
    mlflow.set_tracking_uri(resolved_uri)
    registered_version = MlflowClient(tracking_uri=resolved_uri).get_model_version(
        model_name, str(int(model_version))
    )
    registered_profile = registered_version.tags.get("data_profile_id")
    registered_target = registered_version.tags.get("target_contract_id")
    if DATA_PROFILE_ID != "uci-bike-sharing" and (
        registered_profile != DATA_PROFILE_ID or registered_target != TARGET_CONTRACT_ID
    ):
        raise ValueError(
            f"Registered model version {model_version} profile/target contract does not match "
            f"{DATA_PROFILE_ID}/{TARGET_CONTRACT_ID}"
        )
    if registered_profile and registered_profile != DATA_PROFILE_ID:
        raise ValueError(
            f"Registered model version {model_version} belongs to data profile {registered_profile!r}, "
            f"not {DATA_PROFILE_ID!r}"
        )
    if registered_target and registered_target != TARGET_CONTRACT_ID:
        raise ValueError(
            f"Registered model version {model_version} uses target contract {registered_target!r}, "
            f"not {TARGET_CONTRACT_ID!r}"
        )
    require_deployment_eligible(
        DATA_PROFILE_ID, registered_version.tags,
        model_name=registered_version.name, model_version=str(registered_version.version),
    )
    expected_hash = registered_version.tags.get("artifact_sha256")
    if not expected_hash:
        raise ValueError(f"Registered model version {model_version} has no artifact SHA-256")
    quality_gate = registered_version.tags.get("quality_gate", "missing")
    if quality_gate not in {"passed", "failed"}:
        raise ValueError(f"Registered model version {model_version} has no recognized quality-gate status")
    uri = f"models:/{model_name}/{int(model_version)}"
    local_path = Path(mlflow.artifacts.download_artifacts(artifact_uri=uri))
    if not (local_path / "MLmodel").is_file():
        raise ValueError(f"Registered model version {model_version} artifact is missing its MLmodel file")
    actual_hash = directory_sha256(local_path)
    if actual_hash != expected_hash:
        raise ValueError(
            f"Model artifact hash mismatch for version {model_version}: expected {expected_hash}, got {actual_hash}"
        )
    # Verify the artifact before skops deserializes any estimator objects.
    model = mlflow.sklearn.load_model(str(local_path))
    return model, actual_hash, quality_gate


def predict_non_negative(model, features) -> float:
    values = model.predict(features)
    prediction = float(np.asarray(values).reshape(-1)[0])
    return max(0.0, prediction)
