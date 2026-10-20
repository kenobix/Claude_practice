import unittest

import numpy as np
import pandas as pd

from bike_mlops.features import FEATURE_COLUMNS, build_feature_row, feature_frame_for_dataset


class FeatureContractTests(unittest.TestCase):
    def setUp(self):
        self.target = pd.Timestamp("2012-07-01 10:00:00")
        self.history = pd.DataFrame(
            [
                {
                    "timestamp": self.target - pd.Timedelta(hours=24),
                    "cnt": 120,
                    "available_at": self.target - pd.Timedelta(hours=21),
                },
                {
                    "timestamp": self.target - pd.Timedelta(hours=168),
                    "cnt": 95,
                    "available_at": self.target - pd.Timedelta(hours=165),
                },
                {
                    "timestamp": self.target - pd.Timedelta(hours=1),
                    "cnt": 500,
                    "available_at": self.target + pd.Timedelta(hours=2),
                },
                {
                    "timestamp": self.target,
                    "cnt": 999,
                    "available_at": self.target + pd.Timedelta(hours=3),
                },
            ]
        )

    def test_only_exact_lags_available_at_prediction_time_are_used(self):
        features = build_feature_row(self.target, self.target, self.history)
        self.assertEqual(features["lag_24"], 120.0)
        self.assertEqual(features["lag_168"], 95.0)
        self.assertEqual(features["lag_24_missing"], 0.0)
        self.assertEqual(features["hour"], 10.0)
        self.assertEqual(set(features), set(FEATURE_COLUMNS))
        self.assertNotIn(999.0, features.values())
        self.assertNotIn(500.0, features.values())

    def test_missing_exact_hour_is_not_replaced_by_nearest_row(self):
        history = self.history.loc[
            self.history["timestamp"] != self.target - pd.Timedelta(hours=24)
        ]
        # A neighboring row exists, but the exact previous-day timestamp does not.
        history = pd.concat(
            [
                history,
                pd.DataFrame(
                    [{
                        "timestamp": self.target - pd.Timedelta(hours=25),
                        "cnt": 777,
                        "available_at": self.target - pd.Timedelta(hours=22),
                    }]
                ),
            ],
            ignore_index=True,
        )
        features = build_feature_row(self.target, self.target, history)
        self.assertTrue(np.isnan(features["lag_24"]))
        self.assertEqual(features["lag_24_missing"], 1.0)

    def test_contract_rejects_non_hour_and_different_as_of(self):
        with self.assertRaisesRegex(ValueError, "as_of"):
            build_feature_row(self.target, self.target + pd.Timedelta(minutes=1), self.history)
        with self.assertRaisesRegex(ValueError, "aligned"):
            build_feature_row(self.target + pd.Timedelta(minutes=1), self.target, self.history)

    def test_batch_features_join_by_timestamp_and_preserve_gaps(self):
        start = pd.Timestamp("2012-01-01 00:00:00")
        timestamps = pd.date_range(start, periods=180, freq="h").delete(100)
        frame = pd.DataFrame({"timestamp": timestamps, "cnt": np.arange(len(timestamps))})
        features = feature_frame_for_dataset(frame)
        target_index = frame.index[frame["timestamp"] == start + pd.Timedelta(hours=124)][0]
        target_time = frame.loc[target_index, "timestamp"]
        self.assertTrue(np.isnan(features.loc[target_index, "lag_24"]))
        self.assertEqual(features.loc[target_index, "lag_24_missing"], 1.0)
        # A 168-hour source may still exist independently of the 24-hour gap.
        if target_time - pd.Timedelta(hours=168) in set(frame["timestamp"]):
            self.assertEqual(features.loc[target_index, "lag_168_missing"], 0.0)


if __name__ == "__main__":
    unittest.main()
