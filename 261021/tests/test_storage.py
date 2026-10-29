import tempfile
import unittest
from pathlib import Path
import sqlite3

import pandas as pd

from bike_mlops.db import Database, RequestConflict, SCHEMA


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(f"sqlite:///{Path(self.tempdir.name) / 'ops.sqlite'}")
        self.database.initialize()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_prediction_schema_declares_double_precision(self):
        prediction_schema = next(statement for statement in SCHEMA
                                 if "CREATE TABLE IF NOT EXISTS predictions" in statement)
        self.assertIn("predicted DOUBLE PRECISION", prediction_schema)
        precise_value = 15.189926280821982
        record = {
            "request_id": "precision-check",
            "input_hash": "same-input",
            "target_hour": "2012-07-01 10:00:00",
            "as_of": "2012-07-01 10:00:00",
            "predicted": precise_value,
            "model_name": "bike-demand",
            "model_version": "1",
            "model_quality_gate": "passed",
            "artifact_sha256": "model-sha",
            "data_version_sha256": "data-sha",
            "feature_contract_version": "bike-demand-features-v1",
            "features_json": "{}",
            "created_at": "2012-07-01T10:00:00+00:00",
        }
        self.assertEqual(self.database.save_prediction(record)["predicted"], precise_value)

    def test_query_excludes_late_and_future_observations(self):
        target = pd.Timestamp("2012-07-01 10:00:00")
        rows = pd.DataFrame(
            [
                (target - pd.Timedelta(hours=24), 120, target - pd.Timedelta(hours=21)),
                (target - pd.Timedelta(hours=1), 400, target + pd.Timedelta(hours=2)),
                (target, 800, target + pd.Timedelta(hours=3)),
            ],
            columns=["timestamp", "cnt", "available_at"],
        )
        self.database.add_observations(rows, "dataset-sha")
        available = self.database.observations_as_of(target, target)
        self.assertEqual(available["timestamp"].tolist(), ["2012-06-30 10:00:00"])

    def test_prediction_is_idempotent_and_rejects_changed_input(self):
        self.database.add_observation(
            "2012-06-30 10:00:00", 120, "2012-06-30 13:00:00", "dataset-sha"
        )
        record = {
            "request_id": "idempotency-key-1",
            "input_hash": "same-input",
            "target_hour": "2012-07-01 10:00:00",
            "as_of": "2012-07-01 10:00:00",
            "predicted": 123.4,
            "model_name": "bike-demand",
            "model_version": "1",
            "model_quality_gate": "passed",
            "artifact_sha256": "model-sha",
            "data_version_sha256": "data-sha",
            "feature_contract_version": "bike-demand-features-v1",
            "features_json": "{}",
            "created_at": "2012-07-01T10:00:00+00:00",
        }
        first = self.database.save_prediction(record)
        second = self.database.save_prediction(record)
        self.assertEqual(first["predicted"], second["predicted"])
        self.assertEqual(first["model_version"], second["model_version"])
        changed = {**record, "input_hash": "different-input", "predicted": 999.0}
        with self.assertRaises(RequestConflict):
            self.database.save_prediction(changed)

    def test_observation_timestamp_cannot_be_rewritten(self):
        self.database.add_observation(
            "2012-06-30 10:00:00", 120, "2012-06-30 13:00:00", "dataset-sha"
        )
        with self.assertRaises(ValueError):
            self.database.add_observation(
                "2012-06-30 10:00:00", 121, "2012-06-30 13:00:00", "dataset-sha"
            )

    def test_database_is_bound_to_its_data_profile(self):
        with self.assertRaisesRegex(ValueError, "Database profile mismatch"):
            Database(
                self.database.url,
                data_profile_id="capital-bikeshare",
                target_contract_id="capital-trip-starts-hourly-v1",
            ).initialize()

    def test_legacy_populated_database_cannot_be_claimed_by_capital_profile(self):
        path = Path(self.tempdir.name) / "legacy.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE observations (timestamp TEXT PRIMARY KEY, cnt INTEGER NOT NULL, "
                "available_at TEXT NOT NULL, data_hash TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO observations VALUES ('2012-01-01 00:00:00',1,'2012-01-01 03:00:00','old-hash')"
            )
        capital = Database(
            f"sqlite:///{path}",
            data_profile_id="capital-bikeshare",
            target_contract_id="capital-trip-starts-hourly-v1",
        )
        with self.assertRaisesRegex(ValueError, "Refusing to assign a non-UCI profile"):
            capital.initialize()

    def test_legacy_prediction_table_is_migrated_and_bound_as_uci(self):
        path = Path(self.tempdir.name) / "legacy-uci.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE observations (timestamp TEXT PRIMARY KEY, cnt INTEGER NOT NULL, "
                "available_at TEXT NOT NULL, data_hash TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE predictions (request_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,"
                "target_hour TEXT NOT NULL,as_of TEXT NOT NULL,predicted REAL NOT NULL,model_name TEXT NOT NULL,"
                "model_version TEXT NOT NULL,model_quality_gate TEXT NOT NULL,artifact_sha256 TEXT NOT NULL,"
                "data_version_sha256 TEXT NOT NULL,feature_contract_version TEXT NOT NULL,"
                "features_json TEXT NOT NULL,created_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO observations VALUES ('2012-01-01 00:00:00',1,'2012-01-01 03:00:00','old-hash')"
            )
            connection.execute(
                "INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("legacy-request", "input-hash", "2012-01-01 01:00:00", "2012-01-01 01:00:00",
                 1.0, "bike-demand", "1", "passed", "model-hash", "data-hash",
                 "bike-demand-features-v1", "{}", "2012-01-01T01:00:00+00:00"),
            )
        Database(f"sqlite:///{path}").initialize()
        with sqlite3.connect(path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(predictions)")}
            profile = connection.execute(
                "SELECT data_profile_id,target_contract_id FROM data_profile_metadata WHERE singleton=1"
            ).fetchone()
            candidate = connection.execute(
                "SELECT candidate_id FROM predictions WHERE request_id='legacy-request'"
            ).fetchone()
        self.assertIn("data_profile_id", columns)
        self.assertIn("target_contract_id", columns)
        self.assertIn("candidate_id", columns)
        self.assertEqual(candidate, (None,))
        self.assertEqual(profile, ("uci-bike-sharing", "uci-bike-sharing-cnt-dataset-local-hour"))

    def test_deployment_audit_row_is_bound_to_data_profile(self):
        capital = Database(
            f"sqlite:///{Path(self.tempdir.name) / 'capital.sqlite'}",
            data_profile_id="capital-bikeshare",
            target_contract_id="capital-trip-starts-hourly-v1",
        )
        capital.initialize()
        with capital.transaction(immediate=True) as connection:
            capital.execute(
                connection,
                "INSERT INTO deployments(deployment_id,status,phase,data_profile_id,target_contract_id,"
                "to_slot,to_version,approver,reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "deployment-capital-1", "completed", "bootstrap", "capital-bikeshare",
                    "capital-trip-starts-hourly-v1", "blue", "1", "reviewer",
                    "test fixture", "2026-10-28T00:00:00+00:00", "2026-10-28T00:00:00+00:00",
                ),
            )
            row = capital.execute(
                connection,
                "SELECT data_profile_id,target_contract_id FROM deployments WHERE deployment_id=?",
                ("deployment-capital-1",),
            ).fetchone()
        self.assertEqual(
            (row["data_profile_id"], row["target_contract_id"]),
            ("capital-bikeshare", "capital-trip-starts-hourly-v1"),
        )


if __name__ == "__main__":
    unittest.main()
