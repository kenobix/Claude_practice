"""Inspect operational measurement attempts or review an interrupted retry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bike_mlops.capital_operational_ledger import read_operational_ledger, review_operational_retry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Show local candidate attempts and reviews")
    review = commands.add_parser("review-retry", help="Record reasoned review after a failed/interrupted attempt")
    review.add_argument("--candidate-id", required=True)
    review.add_argument("--operator", required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--next-plan", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "list":
        print(json.dumps(read_operational_ledger(), ensure_ascii=False, indent=2))
    else:
        review_operational_retry(args.candidate_id, args.operator, args.reason, next_plan_path=args.next_plan)
        print(json.dumps({"candidate_id": args.candidate_id, "status": "reviewed_reopen"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
