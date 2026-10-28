from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from bike_mlops.capital_benchmark_inputs import check_candidate_training_window
from bike_mlops.capital_operational_plan import (
    build_operational_plan, local_registry_url, save_operational_plan, validate_plan_registration,
)
from bike_mlops.capital_staging import verify_candidate_for_staging
from bike_mlops.config import DATA_PROFILE_ID, MODEL_NAME
from bike_mlops.operational_benchmark import _machine_and_container_resources


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze the Capital staging operational plan without sending predictions")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--selection-report", type=Path, required=True)
    parser.add_argument("--final-report", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8110")
    args = parser.parse_args()
    if DATA_PROFILE_ID != "capital-bikeshare" or MODEL_NAME != "bike-demand-capital":
        raise ValueError("Set DATA_PROFILE_ID=capital-bikeshare and use the Capital model name")
    version = args.model_version
    if not version.isdecimal() or int(version) <= 0:
        raise ValueError("A positive explicit model version is required")
    tracking_uri = local_registry_url(os.getenv("MLFLOW_TRACKING_URI", ""))

    window = check_candidate_training_window(
        args.selection_report, args.final_report, args.candidate_id, start=args.start, end=args.end,
    )
    evidence = verify_candidate_for_staging(args.selection_report, args.final_report)
    if (evidence["candidate_id"] != args.candidate_id
            or evidence["selection_report_sha256"] != window["candidate_report_sha256"]
            or evidence["final_report_sha256"] != window["final_report_sha256"]
            or evidence["evaluation_protocol_sha256"] != window["evaluation_protocol_sha256"]):
        raise ValueError("Candidate evidence changed after the training-window check")
    from mlflow.tracking import MlflowClient
    import mlflow
    import mlflow.artifacts

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_registry_uri(tracking_uri)
    registered = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri).get_model_version(MODEL_NAME, version)
    validate_plan_registration(evidence, registered)
    with tempfile.TemporaryDirectory(prefix="capital-plan-artifact-") as directory:
        artifact_dir = Path(mlflow.artifacts.download_artifacts(
            artifact_uri=f"models:/{MODEL_NAME}/{version}", dst_path=directory,
        ))
        resources = _machine_and_container_resources()
        try:
            resources["host_load_average"] = list(os.getloadavg())
        except (OSError, AttributeError):
            resources["host_load_average"] = None
        plan = build_operational_plan(evidence=evidence, window=window, registered=registered,
                                      artifact_dir=artifact_dir, api_url=args.api_url,
                                      registry_uri=tracking_uri, resource_context=resources)
        output = save_operational_plan(plan, args.output)
    print(json.dumps({"plan_path": str(output), "plan_sha256": plan["plan_sha256"],
                      "candidate_id": plan["identity"]["candidate_id"], "model_version": version,
                      "status": "plan_frozen_no_predictions_sent"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
