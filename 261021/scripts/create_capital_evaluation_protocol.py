"""Freeze a point-in-time Capital Bikeshare candidate and final evaluation split."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bike_mlops.config import DATA_PROFILE_ID, MODEL_NAME, TARGET_CONTRACT_ID
from bike_mlops.evaluation_protocol import build_capital_protocol, save_protocol
from bike_mlops.model import load_registered_model
from bike_mlops.training import HYPERPARAMETERS


def main() -> None:
    if DATA_PROFILE_ID != "capital-bikeshare":
        raise SystemExit("Set DATA_PROFILE_ID=capital-bikeshare before freezing a Capital protocol")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--training-cutoff", required=True)
    parser.add_argument("--validation-start", required=True)
    parser.add_argument("--validation-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--incumbent-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    _, incumbent_hash, incumbent_gate = load_registered_model(MODEL_NAME, args.incumbent_version)
    protocol = build_capital_protocol(
        args.csv,
        args.source_manifest,
        training_cutoff=args.training_cutoff,
        validation_start=args.validation_start,
        validation_end=args.validation_end,
        evaluation_start=args.evaluation_start,
        evaluation_end=args.evaluation_end,
        incumbent_version=args.incumbent_version,
        incumbent_artifact_sha256=incumbent_hash,
        incumbent_quality_gate=incumbent_gate,
        candidate_recipe={
            "model_family": "scikit-learn HistGradientBoostingRegressor in a Pipeline",
            "hyperparameters": HYPERPARAMETERS,
        },
    )
    if protocol["dataset"]["target_contract"] != TARGET_CONTRACT_ID:
        raise ValueError("Generated protocol target contract differs from the active data profile")
    save_protocol(protocol, args.output)
    print(json.dumps({
        "protocol_path": str(args.output),
        "protocol_sha256": protocol["protocol_sha256"],
        "training_rows": protocol["training"]["rows"],
        "validation_rows": protocol["validation"]["rows"],
        "reserved_evaluation_rows": protocol["evaluation"]["rows"],
        "selection_decision_time": protocol["selection_decision_time"],
        "evaluation_start": protocol["evaluation"]["start_inclusive"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
