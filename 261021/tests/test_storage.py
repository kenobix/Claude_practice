import tempfile
import unittest
from pathlib import Path

import pandas as pd

from bike_mlops.db import Database, RequestConflict


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(f"sqlite:///{Path(self.tempdir.name) / 'ops.sqlite'}")
        self.database.initialize()

    def tearDown(self):
        self.tempdir.cleanup()

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


if __name__ == "__main__":
    unittest.main()
