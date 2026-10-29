from __future__ import annotations

import argparse
import json
from pathlib import Path

from bike_mlops.capital_benchmark_inputs import check_candidate_training_window
from bike_mlops.config import DATA_PROFILE_ID, DEFAULT_DATABASE_URL, TARGET_CONTRACT_ID
from bike_mlops.db import Database


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a Capital replay window or run its frozen, auditable staging measurement"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check-input-only", action="store_true",
        help="Verify candidate evidence and the training window without sending prediction requests",
    )
    mode.add_argument(
        "--run-measurement", action="store_true",
        help="Run the fixed 50 warmup + 1,000 sequential request plan against isolated staging",
    )
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--selection-report", type=Path, required=True)
    parser.add_argument("--final-report", type=Path, required=True)
    parser.add_argument("--plan", type=Path,
                        help="Frozen operational plan; required for --run-measurement")
    parser.add_argument("--start", default="2026-04-01 00:00:00",
                        help="Input-only check start; ignored during plan-bound measurement")
    parser.add_argument("--end", default="2026-04-08 00:00:00",
                        help="Input-only check end; ignored during plan-bound measurement")
    args = parser.parse_args()

    if DATA_PROFILE_ID != "capital-bikeshare" or TARGET_CONTRACT_ID != "capital-trip-starts-hourly-v1":
        parser.error("Set DATA_PROFILE_ID=capital-bikeshare before using this Capital-only command")
    if args.check_input_only:
        training_window = check_candidate_training_window(
            args.selection_report, args.final_report, args.candidate_id, start=args.start, end=args.end
        )
        print(json.dumps({"status": "training_window_verified", **training_window},
                         indent=2, ensure_ascii=False))
        return
    if args.plan is None:
        parser.error("--plan is required with --run-measurement")

    # The runner independently pins the plan, candidate evidence, PostgreSQL snapshot,
    # request source, persisted audit rows and result ledger before it can return completed.
    from bike_mlops.capital_measurement_runner import run_staging_measurement

    result = run_staging_measurement(
        args.plan, args.selection_report, args.final_report,
        Database(DEFAULT_DATABASE_URL, DATA_PROFILE_ID, TARGET_CONTRACT_ID),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("status") != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
