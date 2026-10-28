import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from bike_mlops.data import audit_hourly, load_hourly
from bike_mlops.monitoring import population_stability_index, score_monitor_windows


class DataAndMonitoringTests(unittest.TestCase):
    def test_loader_reports_profile_neutral_required_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hour.csv"
            path.write_text("timestamp,cnt\n2012-01-01 00:00:00,1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing required hourly data columns"):
                load_hourly(path)

    def test_loader_rejects_duplicate_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hour.csv"
            path.write_text(
                "dteday,hr,cnt\n2012-01-01,0,1\n2012-01-01,0,2\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_hourly(path)

    def test_audit_counts_missing_hours_without_inventing_rows(self):
        frame = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    ["2012-01-01 00:00:00", "2012-01-01 01:00:00", "2012-01-01 03:00:00"]
                ),
                "cnt": [1, 2, 3],
            }
        )
        report = audit_hourly(frame)
        self.assertEqual(report["rows"], 3)
        self.assertEqual(report["expected_hours_in_range"], 4)
        self.assertEqual(report["missing_hour_slots_in_range"], 1)

    def test_population_stability_detects_shift(self):
        self.assertAlmostEqual(
            population_stability_index([0, 1, 2, 3, 4, 5], [0, 1, 2, 3, 4, 5]), 0.0
        )
        self.assertGreater(
            population_stability_index([0, 1, 2, 3, 4, 5], [20, 21, 22, 23, 24, 25]), 0.2
        )

    def test_monitor_waits_for_label_availability(self):
        target = pd.Timestamp("2012-07-01 00:00:00")
        predictions = pd.DataFrame(
            [
                {
                    "target_hour": target,
                    "predicted": 8.0,
                    "features_json": '{"lag_24": 1, "lag_168": 2, "hour": 0}',
                }
            ]
        )
        labels = pd.DataFrame(
            [{
                "timestamp": target,
                "cnt": 10,
                "available_at": target + pd.Timedelta(hours=3),
            }]
        )
        reference = pd.DataFrame({"lag_24": [1, 2, 3], "lag_168": [1, 2, 3], "hour": [0, 1, 2]})
        report = score_monitor_windows(
            predictions,
            labels,
            reference,
            target,
            target + pd.Timedelta(hours=5),
            window_hours=168,
            minimum_labels=1,
        )
        at_target = report.loc[report["window_end"] == target.isoformat(sep=" ")].iloc[0]
        at_arrival = report.loc[
            report["window_end"] == (target + pd.Timedelta(hours=3)).isoformat(sep=" ")
        ].iloc[0]
        self.assertEqual(at_target["scored_count"], 0)
        self.assertTrue(np.isnan(at_target["mae"]))
        self.assertEqual(at_arrival["scored_count"], 1)
        self.assertEqual(at_arrival["mae"], 2.0)


if __name__ == "__main__":
    unittest.main()
