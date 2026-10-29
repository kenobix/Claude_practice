from __future__ import annotations

import unittest

from bike_mlops.capital_deployment_decisions import record_deployment_approval, record_operational_review
from bike_mlops.deployment_gates import require_deployment_eligible
from test_capital_deployment_decisions import eligible_registry


class DeploymentGateTests(unittest.TestCase):
    def setUp(self):
        self.registry = eligible_registry()
        record_operational_review(self.registry, "2", "reviewer-a", "passed", "all evidence reviewed")
        record_deployment_approval(self.registry, "2", "approver-b", "approve after review")
        self.tags = self.registry.tags

    def test_capital_requires_all_gates_and_matching_decision_evidence(self):
        require_deployment_eligible(
            "capital-bikeshare", self.tags,
            model_name="bike-demand-capital", model_version="2",
        )
        for key in (
            "quality_gate", "validation_gate_status", "final_accuracy_gate_status",
            "operational_gate_status", "deployment_permission", "quality_gate_policy_id",
            "quality_gate_policy_sha256", "capital_operational_evidence_status",
            "capital_operational_report_sha256", "capital_operational_measurement_status",
            "capital_operational_numeric_recheck_status", "capital_operational_input_provenance_status",
            "capital_operational_audit_status", "capital_operational_resource_context_status",
            "capital_operational_review_status", "capital_operational_review_sha256",
            "capital_deployment_approval_status", "capital_deployment_approval_sha256",
        ):
            with self.subTest(key=key):
                incomplete = dict(self.tags)
                incomplete.pop(key)
                with self.assertRaisesRegex(ValueError, "not eligible for deployment"):
                    require_deployment_eligible(
                        "capital-bikeshare", incomplete,
                        model_name="bike-demand-capital", model_version="2",
                    )

    def test_failed_gate_and_diagnostic_permission_do_not_bypass_capital_checks(self):
        for key, value in (
            ("validation_gate_status", "failed"),
            ("final_accuracy_gate_status", "not_evaluable"),
            ("operational_gate_status", "not_evaluated"),
            ("deployment_permission", "blocked"),
            ("quality_gate_policy_sha256", "0" * 64),
            ("capital_operational_measurement_status", "failed"),
        ):
            with self.subTest(key=key, value=value):
                tags = dict(self.tags, **{key: value})
                with self.assertRaisesRegex(ValueError, "not eligible for deployment"):
                    require_deployment_eligible(
                        "capital-bikeshare", tags,
                        model_name="bike-demand-capital", model_version="2",
                    )

    def test_wrong_model_version_or_modified_decision_reason_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not eligible for deployment"):
            require_deployment_eligible(
                "capital-bikeshare", self.tags,
                model_name="bike-demand-capital", model_version="3",
            )
        tags = dict(self.tags, capital_deployment_approval_reason="changed after approval")
        with self.assertRaisesRegex(ValueError, "not eligible for deployment"):
            require_deployment_eligible(
                "capital-bikeshare", tags,
                model_name="bike-demand-capital", model_version="2",
            )

    def test_existing_uci_deployment_policy_remains_unchanged(self):
        require_deployment_eligible("uci-bike-sharing", {})


if __name__ == "__main__":
    unittest.main()
