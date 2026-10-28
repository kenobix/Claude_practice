from __future__ import annotations

import unittest

from bike_mlops.deployment_gates import require_deployment_eligible
from bike_mlops.evaluation_protocol import (
    BOOTSTRAP_QUALITY_GATE_POLICY,
    BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
)


class DeploymentGateTests(unittest.TestCase):
    def setUp(self):
        self.tags = {
            "quality_gate": "passed",
            "validation_gate_status": "passed",
            "final_accuracy_gate_status": "passed",
            "operational_gate_status": "passed",
            "deployment_permission": "eligible",
            "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
            "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
        }

    def test_capital_requires_all_gates_and_matching_policy(self):
        require_deployment_eligible("capital-bikeshare", self.tags)

        for key in (
            "quality_gate",
            "validation_gate_status",
            "final_accuracy_gate_status",
            "operational_gate_status",
            "deployment_permission",
            "quality_gate_policy_id",
            "quality_gate_policy_sha256",
        ):
            with self.subTest(key=key):
                incomplete = dict(self.tags)
                incomplete.pop(key)
                with self.assertRaisesRegex(ValueError, "not eligible for deployment"):
                    require_deployment_eligible("capital-bikeshare", incomplete)

    def test_failed_gate_and_diagnostic_permission_do_not_bypass_capital_checks(self):
        for key, value in (
            ("validation_gate_status", "failed"),
            ("final_accuracy_gate_status", "not_evaluable"),
            ("operational_gate_status", "not_evaluated"),
            ("deployment_permission", "blocked"),
            ("quality_gate_policy_sha256", "0" * 64),
        ):
            with self.subTest(key=key, value=value):
                tags = dict(self.tags, **{key: value})
                with self.assertRaisesRegex(ValueError, "not eligible for deployment"):
                    require_deployment_eligible("capital-bikeshare", tags)

    def test_existing_uci_deployment_policy_remains_unchanged(self):
        require_deployment_eligible("uci-bike-sharing", {})


if __name__ == "__main__":
    unittest.main()
