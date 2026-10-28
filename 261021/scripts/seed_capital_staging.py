from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote
from urllib.parse import urlsplit

from bike_mlops.cli import _load_profile_hourly
from bike_mlops.capital_staging import verify_candidate_for_staging
from bike_mlops.capital_staging_seed import seed_verified_candidate
from bike_mlops.candidate_artifacts import sha256_file
from bike_mlops.config import DATA_PROFILE_ID, DATASET_MANIFEST, DEFAULT_DATASET, TARGET_CONTRACT_ID, MODEL_NAME
from bike_mlops.db import Database


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed only the isolated Capital staging database from the verified profile dataset"
    )
    parser.add_argument("--port", type=int, default=55434)
    parser.add_argument("--selection-report", type=Path, required=True)
    parser.add_argument("--final-report", type=Path, required=True)
    parser.add_argument("--model-version", required=True)
    args = parser.parse_args()
    if DATA_PROFILE_ID != "capital-bikeshare":
        raise SystemExit("Set DATA_PROFILE_ID=capital-bikeshare before seeding staging")
    password = os.getenv("APP_DB_PASSWORD")
    if not password:
        raise SystemExit("APP_DB_PASSWORD must be set in the environment; it is not printed or stored by this script")
    if args.port != 55434:
        raise SystemExit("The isolated Capital staging PostgreSQL port is fixed at 55434")

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "")
    uri = urlsplit(tracking_uri)
    if (uri.scheme != "http" or uri.hostname not in {"localhost", "127.0.0.1"} or uri.port != 5001
            or uri.username or uri.password or uri.path not in {"", "/"} or uri.query or uri.fragment):
        raise SystemExit("MLFLOW_TRACKING_URI must point to local Capital MLflow at port 5001")
    if not args.model_version.isdecimal() or int(args.model_version) <= 0:
        raise SystemExit("A positive model version is required")
    evidence = verify_candidate_for_staging(args.selection_report, args.final_report)
    if (sha256_file(DEFAULT_DATASET) != evidence["selection"]["data_sha256"]
            or sha256_file(DATASET_MANIFEST) != evidence["selection"]["data_source"]["source_manifest_sha256"]):
        raise ValueError("Staging CSV or manifest does not match the verified candidate")
    frame = _load_profile_hourly(DEFAULT_DATASET)
    from mlflow.tracking import MlflowClient
    registered = MlflowClient(tracking_uri=tracking_uri).get_model_version(MODEL_NAME, args.model_version)

    encoded_password = quote(password, safe="")
    database_url = f"postgresql://bike_app:{encoded_password}@127.0.0.1:{args.port}/bikeops"
    database = Database(database_url, DATA_PROFILE_ID, TARGET_CONTRACT_ID)
    database.initialize()
    result = seed_verified_candidate(database, frame, DEFAULT_DATASET, DATASET_MANIFEST, evidence, registered)
    print(json.dumps({
        "database": "isolated-capital-staging",
        "csv_path": str(DEFAULT_DATASET),
        "manifest_path": str(DATASET_MANIFEST),
        **result,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
