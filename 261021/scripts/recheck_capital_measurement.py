"""Recheck saved measurement numbers without changing the ledger or approving deployment."""
from __future__ import annotations

import argparse
import json

from bike_mlops.capital_measurement_recheck import recheck_linked_measurement


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    print(json.dumps(recheck_linked_measurement(args.candidate_id, args.attempt_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
