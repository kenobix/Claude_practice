from __future__ import annotations

import copy
import json
import math
import unittest

import numpy as np

from bike_mlops.monitoring import population_stability_index
from bike_mlops.monitoring_psi_v2 import PSI_ALGORITHM_V2, evaluate_psi_reference, make_psi_reference


class VersionedPSITests(unittest.TestCase):
    def make(self, values, **kwargs):
        return make_psi_reference(values, version=PSI_ALGORITHM_V2, feature_name="lag_24", reference_id="synthetic-training", **kwargs)

    def evaluate(self, reference, values, **kwargs):
        return evaluate_psi_reference(reference, values, version=PSI_ALGORITHM_V2, **kwargs)

    def test_constant_reference_detects_changes_both_directions_and_identical_is_zero(self):
        ref = self.make([7, 7, 7, 7])
        self.assertEqual(ref["mode"], "constant_three_way")
        self.assertEqual(ref["reference_counts"], [0, 4, 0])
        self.assertEqual(self.evaluate(ref, [7, 7, 7])["psi"], 0.0)
        for current, expected in (([8, 8, 8], [0, 0, 3]), ([6, 6, 6], [3, 0, 0]),
                                  ([6, 7, 8], [1, 1, 1])):
            with self.subTest(current=current):
                result = self.evaluate(ref, current)
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["current_bin_counts"], expected)
                self.assertGreater(result["psi"], 0.2)
        # Historical implementation is intentionally unchanged.
        self.assertEqual(population_stability_index([7, 7, 7], [8, 8, 8]), 0.0)

    def test_nonconstant_bins_are_frozen_and_reusable(self):
        ref = self.make([0, 1, 2, 3, 4, 5], bins=4)
        self.assertEqual(ref["mode"], "quantile")
        same = self.evaluate(json.loads(json.dumps(ref, allow_nan=False)), [0, 1, 2, 3, 4, 5])
        self.assertEqual(same["psi"], 0.0)
        self.assertGreater(self.evaluate(ref, [20, 21, 22, 23, 24, 25])["psi"], 0.2)
        self.assertEqual(self.make([5, 4, 3, 2, 1, 0], bins=4)["reference_sha256"], ref["reference_sha256"])
        self.assertEqual(self.make([1, 1, 2, 2], bins=2)["edges"], [1.5])

    def test_missing_invalid_and_insufficient_counts_are_explicit(self):
        for values, expected in (([], "no_reference"), ([None, float("nan")], "no_reference"),
                                 ([3], "insufficient_reference"), ([2, 2, float("inf")], "invalid_reference_values")):
            with self.subTest(values=values):
                ref = self.make(values)
                self.assertEqual(ref["status"], expected)
                result = self.evaluate(ref, [4, 4])
                self.assertEqual(result["status"], expected)
                self.assertIsNone(result["psi"])
        ref = self.make([0, 1, None, float("nan")])
        self.assertEqual(ref["input_counts"], {"total": 4, "finite": 2, "missing": 2, "nonfinite": 0})
        for values, status in (([], "no_current_data"), ([None, None], "no_current_data"),
                               ([1], "insufficient_current_data"), ([1, 1, -float("inf")], "invalid_current_values")):
            with self.subTest(values=values):
                result = self.evaluate(ref, values)
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["psi"])
        result = self.evaluate(ref, [0, 1, None])
        self.assertEqual(result["current_input_counts"]["missing"], 1)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.evaluate(ref, [0, 1], minimum_count=3)["status"], "insufficient_current_data")

    def test_version_policy_and_changed_reference_are_rejected(self):
        ref = self.make([1, 1, 1])
        with self.assertRaisesRegex(ValueError, "version"):
            make_psi_reference([1, 1], version="legacy", feature_name="x", reference_id="id")
        with self.assertRaisesRegex(ValueError, "version"):
            evaluate_psi_reference(ref, [1, 1], version="legacy")
        altered = copy.deepcopy(ref)
        altered["constant_value"] = 2
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.evaluate(altered, [2, 2])
        altered["reference_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.evaluate(altered, [2, 2])
        with self.assertRaisesRegex(ValueError, "minimum"):
            self.evaluate(ref, [1, 1], minimum_count=1)

    def test_adjacent_floats_can_still_be_separated(self):
        lower, upper = 1.0, float(np.nextafter(1.0, 2.0))
        ref = self.make([lower, upper], bins=2)
        self.assertEqual(ref["reference_counts"], [1, 1])
        self.assertEqual(self.evaluate(ref, [lower, upper])["psi"], 0.0)
        self.assertGreater(self.evaluate(ref, [upper, upper])["psi"], 0.2)

    def test_input_shape_and_bad_parameters_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "one-dimensional"):
            self.make([[1, 2]])
        with self.assertRaisesRegex(ValueError, "numeric"):
            self.make([1, "bad"])
        for count in (True, 1, 101):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "bin count"):
                self.make([1, 2], bins=count)
        for count in (True, 1, 2.5):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "minimum"):
                self.make([1, 2], minimum_count=count)
        for values in ([1, float("nan")], [2, 2, 2], [3, 4, 5, 6]):
            result = self.evaluate(self.make([1, 2, 3]), values)
            if result["psi"] is not None:
                self.assertTrue(math.isfinite(result["psi"]))


if __name__ == "__main__":
    unittest.main()
