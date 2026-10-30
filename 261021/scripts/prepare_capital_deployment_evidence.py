"""Publish evidence without reviewing, approving, or switching traffic."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bike_mlops.capital_deployment_evidence_publisher import publish_deployment_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--final-report", type=Path, required=True)
    for category in ("monitoring", "recovery", "restore", "metadata"):
        parser.add_argument(f"--{category}-report", type=Path, required=True)
    args = parser.parse_args()
    paths = {category: getattr(args, f"{category}_report")
             for category in ("monitoring", "recovery", "restore", "metadata")}
    result = publish_deployment_evidence(args.candidate_id, args.attempt_id, args.final_report, paths)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
