from __future__ import annotations

import unittest

from bike_mlops.quality_gates import baseline_accuracy_gate


class BaselineAccuracyGateTests(unittest.TestCase):
    def test_requires_strict_mae_improvement_and_allows_equal_rmse(self):
        result = baseline_accuracy_gate(
            {"mae": 9.0, "rmse": 12.0},
            {"mae": 10.0, "rmse": 12.0},
            period="validation_selection",
        )
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["passed"])
        self.assertTrue(result["conditions"]["candidate_mae_strictly_below_baseline"]["passed"])
        self.assertTrue(result["conditions"]["candidate_rmse_not_above_baseline"]["passed"])
        self.assertRegex(result["policy_sha256"], r"^[0-9a-f]{64}$")

    def test_rejects_mae_tie_or_rmse_regression(self):
        mae_tie = baseline_accuracy_gate(
            {"mae": 10.0, "rmse": 11.0},
            {"mae": 10.0, "rmse": 12.0},
            period="validation_selection",
        )
        rmse_regression = baseline_accuracy_gate(
            {"mae": 9.0, "rmse": 12.01},
            {"mae": 10.0, "rmse": 12.0},
            period="final_evaluation",
        )
        self.assertFalse(mae_tie["passed"])
        self.assertEqual(mae_tie["status"], "failed")
        self.assertFalse(rmse_regression["passed"])
        self.assertEqual(rmse_regression["status"], "failed")

    def test_missing_nonfinite_and_negative_metrics_are_not_evaluable(self):
        for candidate in (
            {"mae": 1.0},
            {"mae": float("nan"), "rmse": 1.0},
            {"mae": 1.0, "rmse": float("inf")},
            {"mae": -1.0, "rmse": 1.0},
        ):
            with self.subTest(candidate=candidate):
                result = baseline_accuracy_gate(
                    candidate,
                    {"mae": 2.0, "rmse": 2.0},
                    period="validation_selection",
                )
                self.assertFalse(result["passed"])
                self.assertEqual(result["status"], "not_evaluable")
                self.assertTrue(result["reasons"])


if __name__ == "__main__":
    unittest.main()
