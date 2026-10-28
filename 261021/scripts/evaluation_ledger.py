"""Inspect the local evaluation ledger or explicitly review an interrupted window."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bike_mlops.evaluation_protocol import DEFAULT_LEDGER, read_ledger, review_reopen_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    listing = subparsers.add_parser("list", help="Print all consumed evaluation windows")
    listing.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    reopen = subparsers.add_parser("reopen", help="Record human review before retrying a failed/interrupted window")
    reopen.add_argument("--key", required=True)
    reopen.add_argument("--operator", required=True)
    reopen.add_argument("--reason", required=True)
    reopen.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    args = parser.parse_args()
    if args.command == "list":
        print(json.dumps(read_ledger(Path(args.ledger)), indent=2, ensure_ascii=False))
    else:
        review_reopen_evaluation(args.key, args.operator, args.reason, Path(args.ledger))
        print(json.dumps({"status": "reviewed_reopen", "consumption_key": args.key}, ensure_ascii=False))


if __name__ == "__main__":
    main()
