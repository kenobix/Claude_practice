from __future__ import annotations

import unittest
from types import SimpleNamespace

from bike_mlops.capital_deployment_decisions import (
    CAPITAL_MODEL_NAME,
    record_deployment_approval,
    record_operational_review,
)
from bike_mlops.deployment_gates import require_deployment_eligible
from bike_mlops.evaluation_protocol import (
    BOOTSTRAP_QUALITY_GATE_POLICY,
    BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
)
from bike_mlops.profiles import load_data_profile


class FakeRegistry:
    def __init__(self, tags=None, version="2"):
        self.version = version
        self.tags = dict(tags or {})

    def get_model_version(self, name, version):
        return SimpleNamespace(name=name, version=str(version), status="READY", tags=dict(self.tags))

    def set_model_version_tag(self, name, version, key, value):
        self.tags[key] = value


def eligible_registry():
    profile = load_data_profile("capital-bikeshare")
    tags = {
        "data_profile_id": profile["profile_id"],
        "target_contract_id": profile["target_contract_id"],
        "quality_gate": "passed",
        "validation_gate_status": "passed",
        "final_accuracy_gate_status": "passed",
        "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
        "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
        "operational_gate_status": "not_evaluated",
        "deployment_permission": "blocked",
        "candidate_id": "a" * 64,
        "candidate_model_sha256": "b" * 64,
        "candidate_report_sha256": "c" * 64,
        "final_report_sha256": "d" * 64,
        "evaluation_protocol_sha256": "e" * 64,
        "data_sha256": "f" * 64,
        "artifact_sha256": "1" * 64,
        "capital_operational_evidence_status": "recorded",
        "capital_operational_attempt_id": "11111111-1111-4111-8111-111111111111",
        "capital_operational_report_sha256": "2" * 64,
        "capital_operational_plan_sha256": "3" * 64,
        "capital_operational_plan_file_sha256": "4" * 64,
        "capital_operational_measurement_status": "passed",
        "capital_operational_numeric_recheck_status": "matched",
        "capital_operational_input_provenance_status": "passed",
        "capital_operational_audit_status": "passed",
        "capital_operational_resource_context_status": "captured",
        "capital_operational_request_id_sha256": "5" * 64,
        "capital_operational_p95_ms": "250",
    }
    client = FakeRegistry(tags)
    return client


class CapitalDeploymentDecisionTests(unittest.TestCase):
    def setUp(self):
        self.client = eligible_registry()

    def test_review_and_separate_approval_bind_exact_version_and_evidence(self):
        reviewed = record_operational_review(self.client, "2", "reviewer-a", "passed", "all fixed checks reviewed")
        self.assertEqual(reviewed["operational_gate_status"], "passed")
        self.assertEqual(reviewed["deployment_permission"], "blocked")
        with self.assertRaisesRegex(ValueError, "must differ"):
            record_deployment_approval(self.client, "2", "Reviewer-A", "approve")

        approved = record_deployment_approval(self.client, "2", "approver-b", "review evidence and authorize")
        self.assertEqual(approved["deployment_permission"], "eligible")
        require_deployment_eligible(
            "capital-bikeshare", self.client.tags,
            model_name=CAPITAL_MODEL_NAME, model_version="2",
        )

    def test_review_is_idempotent_for_same_record_but_immutable_on_conflict(self):
        first = record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")
        second = record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")
        self.assertEqual(first["review_sha256"], second["review_sha256"])
        with self.assertRaisesRegex(ValueError, "overwrite a different decision tag"):
            record_operational_review(self.client, "2", "reviewer-a", "rejected", "changed decision")

    def test_p95_over_limit_cannot_pass_review_or_deploy(self):
        self.client.tags["capital_operational_p95_ms"] = "250.0001"
        with self.assertRaisesRegex(ValueError, "fixed gate"):
            record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")

    def test_failed_measurement_cannot_be_reviewed_as_passed(self):
        self.client.tags["capital_operational_measurement_status"] = "failed"
        with self.assertRaisesRegex(ValueError, "fixed gate"):
            record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")
        record_operational_review(self.client, "2", "reviewer-a", "rejected", "measurement failed")
        with self.assertRaisesRegex(ValueError, "passed operational review"):
            record_deployment_approval(self.client, "2", "approver-b", "approve")

    def test_missing_or_changed_evidence_and_wrong_version_are_rejected(self):
        self.client.tags.pop("capital_operational_audit_status")
        with self.assertRaisesRegex(ValueError, "capital_operational_audit_status"):
            record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")

        self.client = eligible_registry()
        record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")
        record_deployment_approval(self.client, "2", "approver-b", "approved")
        with self.assertRaisesRegex(ValueError, "different model version"):
            require_deployment_eligible(
                "capital-bikeshare", self.client.tags,
                model_name=CAPITAL_MODEL_NAME, model_version="3",
            )
        self.client.tags["capital_operational_review_reason"] = "tampered"
        with self.assertRaisesRegex(ValueError, "record hash"):
            require_deployment_eligible(
                "capital-bikeshare", self.client.tags,
                model_name=CAPITAL_MODEL_NAME, model_version="2",
            )

    def test_rejected_review_keeps_deployment_blocked(self):
        result = record_operational_review(self.client, "2", "reviewer-a", "rejected", "evidence is incomplete")
        self.assertEqual(result["operational_gate_status"], "failed")
        self.assertEqual(self.client.tags["deployment_permission"], "blocked")


if __name__ == "__main__":
    unittest.main()
