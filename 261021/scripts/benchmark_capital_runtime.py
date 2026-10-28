from __future__ import annotations

import argparse
import json
from pathlib import Path

from bike_mlops.capital_benchmark_inputs import check_candidate_training_window


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Capital operational benchmark input while measurement is disabled")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--selection-report", type=Path, required=True)
    parser.add_argument("--final-report", type=Path, required=True)
    parser.add_argument("--check-input-only", action="store_true", help="Verify candidate and training window without sending requests")
    parser.add_argument("--start", default="2026-04-01 00:00:00")
    parser.add_argument("--end", default="2026-04-08 00:00:00")
    args = parser.parse_args()
    training_window = check_candidate_training_window(
        args.selection_report, args.final_report, args.candidate_id, start=args.start, end=args.end
    )
    if args.check_input_only:
        print(json.dumps({"status": "training_window_verified", **training_window}, indent=2, ensure_ascii=False))
        return
    raise SystemExit(
        "Operational benchmark is disabled until staging DB locking and API data provenance checks are integrated; "
        "use --check-input-only to validate the candidate training window without sending requests"
    )


if __name__ == "__main__":
    main()
