"""Inspect operational attempt evidence without modifying or adopting reports."""
from __future__ import annotations

import argparse
import json

from bike_mlops.capital_operational_recovery import inspect_operational_reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_operational_reports(args.candidate_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
