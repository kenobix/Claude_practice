from __future__ import annotations

import argparse
import json

from bike_mlops.capital_operational_registry import record_completed_operational_evidence


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Attach a linked, rechecked operational report to one exact blocked Capital MLflow version"
    )
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    print(json.dumps(record_completed_operational_evidence(args.candidate_id, args.attempt_id),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
