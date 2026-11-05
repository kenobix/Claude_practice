"""Reconstruct a synthetic campaign from raw evidence; no real clock qualification."""
import argparse
from pathlib import Path
import sys

from bike_mlops.clock_campaign_capture import derive_raw, publish_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.input_dir.resolve() in args.output.resolve().parents:
            raise ValueError("Derived output must be outside the original evidence directory")
        value, report = derive_raw(args.input_dir, expected_plan_sha256=args.expected_plan_sha256)
        publish_json(args.output, value)
        print("synthetic_offline_checks_status=" + report["offline_checks_status"])
        return 1 if report["clock_qualification_status"] == "failed" else 2
    except (ValueError, OSError, UnicodeError, RecursionError, StopIteration, KeyError):
        print("Synthetic raw evidence invalid or unavailable; no qualification", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
