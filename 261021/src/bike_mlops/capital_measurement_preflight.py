"""Revalidate a locked operational attempt before opening the staging snapshot.

This function sends no prediction requests and grants no operational eligibility.
It must run inside the candidate's measurement_attempt context; its returned frame
must be used while the caller holds and validates the staging observation lock.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from bike_mlops.candidate_artifacts import sha256_file
from bike_mlops.capital_benchmark_inputs import check_candidate_training_window
from bike_mlops.capital_operational_ledger import _read_plan
from bike_mlops.capital_operational_plan import validate_plan_registration, verify_operational_plan_binding
from bike_mlops.capital_staging import verify_candidate_for_staging
from bike_mlops.cli import _load_profile_hourly
from bike_mlops.config import DATA_PROFILE_ID, DATASET_MANIFEST, DEFAULT_DATASET, MODEL_NAME


@dataclass(frozen=True)
class VerifiedMeasurementInputs:
    plan: dict
    frame: pd.DataFrame
    evidence: dict
    window: dict
    plan_file_sha256: str


def verify_measurement_inputs(attempt, plan_path: Path, selection_path: Path, final_path: Path) -> VerifiedMeasurementInputs:
    """Recheck plan, candidate, dataset and registry against the started attempt."""
    if DATA_PROFILE_ID != "capital-bikeshare" or MODEL_NAME != "bike-demand-capital":
        raise ValueError("Measurement preflight requires the Capital data profile and model")
    if not getattr(attempt, "_active", False) or getattr(attempt, "_finished", True):
        raise ValueError("Measurement preflight requires an active unfinished attempt")
    plan, _, file_hash = _read_plan(plan_path)
    identity = plan["identity"]
    if (attempt.candidate_id != identity["candidate_id"] or attempt.plan_sha256 != plan["plan_sha256"]
            or attempt.plan_file_sha256 != file_hash):
        raise ValueError("Operational plan differs from the started attempt")
    window = check_candidate_training_window(selection_path, final_path, identity["candidate_id"],
                                             start=plan["input_window"]["start_inclusive"],
                                             end=plan["input_window"]["end_exclusive"])
    evidence = verify_candidate_for_staging(selection_path, final_path)
    expected_data = identity["data_sha256"]
    expected_manifest = identity["manifest_sha256"]

    def check_data_files():
        if sha256_file(DEFAULT_DATASET) != expected_data or sha256_file(DATASET_MANIFEST) != expected_manifest:
            raise ValueError("Operational CSV or manifest differs from the frozen candidate")

    check_data_files()
    frame = _load_profile_hourly(DEFAULT_DATASET)
    check_data_files()
    from mlflow.tracking import MlflowClient
    import mlflow.artifacts

    registry_uri = plan["registry_uri"]
    client = MlflowClient(tracking_uri=registry_uri, registry_uri=registry_uri)
    registered = client.get_model_version(identity["model_name"], identity["model_version"])
    validate_plan_registration(evidence, registered)
    with tempfile.TemporaryDirectory(prefix="capital-preflight-artifact-") as directory:
        artifact_dir = Path(mlflow.artifacts.download_artifacts(
            artifact_uri=f"models:/{identity['model_name']}/{identity['model_version']}",
            dst_path=directory, tracking_uri=registry_uri, registry_uri=registry_uri,
        ))
        verify_operational_plan_binding(plan, evidence=evidence, window=window, registered=registered,
                                        artifact_dir=artifact_dir, registry_uri=registry_uri)
        # Detect registry changes that happened during the download as well.
        registered_after = client.get_model_version(identity["model_name"], identity["model_version"])
        verify_operational_plan_binding(plan, evidence=evidence, window=window, registered=registered_after,
                                        artifact_dir=artifact_dir, registry_uri=registry_uri)
    latest_evidence = verify_candidate_for_staging(selection_path, final_path)
    if any(latest_evidence.get(key) != evidence.get(key) for key in (
        "candidate_id", "selection_report_sha256", "final_report_sha256", "evaluation_protocol_sha256",
        "candidate_artifact_sha256",
    )):
        raise ValueError("Candidate evidence changed during preflight")
    check_data_files()
    current_plan, _, current_file_hash = _read_plan(plan_path)
    if current_file_hash != file_hash or current_plan["plan_sha256"] != plan["plan_sha256"]:
        raise ValueError("Operational plan changed during preflight")
    if not getattr(attempt, "_active", False) or getattr(attempt, "_finished", True):
        raise ValueError("Measurement attempt ended during preflight")
    return VerifiedMeasurementInputs(plan=plan, frame=frame, evidence=evidence, window=window,
                                     plan_file_sha256=file_hash)
