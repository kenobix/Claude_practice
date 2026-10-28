from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from bike_mlops.capital_measurement_guard import FeatureSourceExpectations, hold_observation_snapshot
from bike_mlops.db import Database, stable_hash


class CapitalMeasurementGuardTests(unittest.TestCase):
    def setUp(self):
        self.hash = "a" * 64
        self.frame = pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-03 10:00", "2024-01-09 10:00", "2024-01-10 10:00"]),
            "cnt": [5, 0, 20],
        })

    def test_expected_hash_matches_exact_lags_including_zero(self):
        sources = FeatureSourceExpectations(self.frame, self.hash, label_delay_hours=3)
        expected = stable_hash({
            "lag_24": {"timestamp": "2024-01-09 10:00:00", "cnt": 0, "data_hash": self.hash},
            "lag_168": {"timestamp": "2024-01-03 10:00:00", "cnt": 5, "data_hash": self.hash},
        })
        self.assertEqual(sources.source_hash("2024-01-10 10:00:00"), expected)
        sources.validate_response("2024-01-10 10:00:00", {"data_version_sha256": expected})
        with self.assertRaisesRegex(ValueError, "provenance"):
            sources.validate_response("2024-01-10 10:00:00", {"data_version_sha256": "f" * 64})

    def test_missing_and_late_lags_are_not_treated_as_observed(self):
        missing = FeatureSourceExpectations(self.frame.iloc[1:], self.hash, label_delay_hours=3)
        self.assertEqual(missing.source_hash("2024-01-10 10:00:00"), stable_hash({
            "lag_24": {"timestamp": "2024-01-09 10:00:00", "cnt": 0, "data_hash": self.hash},
            "lag_168": None,
        }))
        late = FeatureSourceExpectations(self.frame, self.hash, label_delay_hours=169)
        self.assertEqual(late.source_hash("2024-01-10 10:00:00"), stable_hash({"lag_24": None, "lag_168": None}))

    def test_invalid_source_data_and_targets_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "lowercase SHA"):
            FeatureSourceExpectations(self.frame, "A" * 64, label_delay_hours=3)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            FeatureSourceExpectations(pd.concat([self.frame, self.frame.iloc[:1]]), self.hash, label_delay_hours=3)
        bad = self.frame.copy()
        bad.loc[0, "cnt"] = -1
        with self.assertRaisesRegex(ValueError, "Invalid"):
            FeatureSourceExpectations(bad, self.hash, label_delay_hours=3)
        sources = FeatureSourceExpectations(self.frame, self.hash, label_delay_hours=3)
        with self.assertRaisesRegex(ValueError, "naive hour"):
            sources.source_hash("2024-01-10T10:30:00")
        with self.assertRaisesRegex(ValueError, "naive hour"):
            sources.source_hash("2024-01-10T10:00:00+00:00")

    def test_sqlite_is_rejected_for_live_measurement_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"sqlite:///{Path(directory) / 'test.sqlite'}")
            db.initialize()
            with self.assertRaisesRegex(ValueError, "requires PostgreSQL"):
                with hold_observation_snapshot(db, self.frame, self.hash):
                    pass


if __name__ == "__main__":
    unittest.main()
