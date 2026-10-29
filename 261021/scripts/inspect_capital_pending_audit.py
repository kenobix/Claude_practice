"""Inspect an ambiguous pending request in the isolated staging DB without retrying."""
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import quote

from bike_mlops.capital_pending_audit import inspect_pending_audit
from bike_mlops.config import DATA_PROFILE_ID, TARGET_CONTRACT_ID
from bike_mlops.db import Database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    if DATA_PROFILE_ID != "capital-bikeshare":
        raise SystemExit("Set DATA_PROFILE_ID=capital-bikeshare")
    password = os.getenv("APP_DB_PASSWORD")
    if not password:
        raise SystemExit("APP_DB_PASSWORD must be set in the environment")
    url = f"postgresql://bike_app:{quote(password, safe='')}@127.0.0.1:55434/bikeops"
    database = Database(url, DATA_PROFILE_ID, TARGET_CONTRACT_ID)
    print(json.dumps(inspect_pending_audit(args.candidate_id, args.attempt_id, database), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
