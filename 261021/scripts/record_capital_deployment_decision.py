"""Record a human operational review or a separate deployment approval."""
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlsplit

from bike_mlops.capital_deployment_decisions import (
    record_deployment_approval,
    record_operational_review,
)
from bike_mlops.config import MODEL_NAME


def _client():
    uri = os.getenv("MLFLOW_TRACKING_URI", "")
    parsed = urlsplit(uri)
    if (MODEL_NAME != "bike-demand-capital" or parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1"}
            or parsed.port != 5001 or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("Set DATA_PROFILE_ID=capital-bikeshare and local Capital MLflow URI at port 5001")
    if os.getenv("MLFLOW_REGISTRY_URI", uri).rstrip("/") != uri.rstrip("/"):
        raise ValueError("MLFLOW_REGISTRY_URI must match MLFLOW_TRACKING_URI")
    from mlflow.tracking import MlflowClient

    return MlflowClient(tracking_uri=uri, registry_uri=uri)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    review = subparsers.add_parser("review", help="record the independent operational review")
    review.add_argument("--version", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=("passed", "rejected"), required=True)
    review.add_argument("--reason", required=True)
    approve = subparsers.add_parser("approve", help="record separate reasoned deployment approval")
    approve.add_argument("--version", required=True)
    approve.add_argument("--approver", required=True)
    approve.add_argument("--reason", required=True)
    args = parser.parse_args()
    client = _client()
    if args.action == "review":
        result = record_operational_review(
            client, args.version, args.reviewer, args.decision, args.reason
        )
    else:
        result = record_deployment_approval(client, args.version, args.approver, args.reason)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
