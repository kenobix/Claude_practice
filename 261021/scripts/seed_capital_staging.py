from __future__ import annotations

import argparse
import json
import os
from urllib.parse import quote

from bike_mlops.cli import _seed_observations
from bike_mlops.config import DATA_PROFILE_ID, DATASET_MANIFEST, DEFAULT_DATASET, TARGET_CONTRACT_ID
from bike_mlops.db import Database


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed only the isolated Capital staging database from the verified profile dataset"
    )
    parser.add_argument("--port", type=int, default=55434)
    args = parser.parse_args()
    if DATA_PROFILE_ID != "capital-bikeshare":
        raise SystemExit("Set DATA_PROFILE_ID=capital-bikeshare before seeding staging")
    password = os.getenv("APP_DB_PASSWORD")
    if not password:
        raise SystemExit("APP_DB_PASSWORD must be set in the environment; it is not printed or stored by this script")
    if args.port != 55434:
        raise SystemExit("The isolated Capital staging PostgreSQL port is fixed at 55434")

    encoded_password = quote(password, safe="")
    database_url = f"postgresql://bike_app:{encoded_password}@127.0.0.1:{args.port}/bikeops"
    database = Database(database_url, DATA_PROFILE_ID, TARGET_CONTRACT_ID)
    database.initialize()
    inserted, data_hash, frame = _seed_observations(database, DEFAULT_DATASET)
    print(json.dumps({
        "database": "isolated-capital-staging",
        "csv_path": str(DEFAULT_DATASET),
        "manifest_path": str(DATASET_MANIFEST),
        "rows_in_source": int(len(frame)),
        "new_observations_inserted": inserted,
        "data_sha256": data_hash,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
