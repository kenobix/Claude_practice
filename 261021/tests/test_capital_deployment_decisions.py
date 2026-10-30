from __future__ import annotations

import unittest
from types import SimpleNamespace
from pathlib import Path
import tempfile
from unittest.mock import patch
from tests.capital_deployment_fixture import fixture

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
    def __init__(self, tags=None, version="2", fail_after_tag=None, body=None):
        self.version = version
        self.tags = dict(tags or {})
        self.fail_after_tag = fail_after_tag
        self.body = body
        self.run_id = "synthetic-run"
        self.artifact_path = self.tags.get("capital_deployment_evidence_path")

    def get_model_version(self, name, version):
        return SimpleNamespace(name=name, version=str(version), status="READY", run_id=self.run_id, tags=dict(self.tags))

    def download_artifacts(self, run_id, artifact_path, dst_path):
        if run_id != self.run_id or artifact_path != self.artifact_path or self.body is None:
            raise FileNotFoundError("Synthetic evidence not found")
        path = Path(dst_path) / "bundle.json"
        path.write_bytes(self.body)
        return str(path)

    def log_artifact(self, run_id, local_path, artifact_path):
        if run_id != self.run_id:
            raise ValueError("Wrong synthetic source run")
        self.body = Path(local_path).read_bytes()
        self.artifact_path = artifact_path + "/" + Path(local_path).name

    def set_model_version_tag(self, name, version, key, value):
        self.tags[key] = value
        if self.fail_after_tag == key:
            self.fail_after_tag = None
            raise RuntimeError("simulated lost response after registry tag write")


def eligible_registry():
    tags, _bundle, body = fixture()
    return FakeRegistry(tags, body=body)


class CapitalDeploymentDecisionTests(unittest.TestCase):
    def setUp(self):
        from bike_mlops import capital_operational_registry
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        change = patch.object(capital_operational_registry, "PROJECT_ROOT", Path(directory.name))
        change.start()
        self.addCleanup(change.stop)
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
            model_name=CAPITAL_MODEL_NAME, model_version="2", client=self.client,
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
                model_name=CAPITAL_MODEL_NAME, model_version="2", client=self.client,
            )

    def test_rejected_review_keeps_deployment_blocked(self):
        result = record_operational_review(self.client, "2", "reviewer-a", "rejected", "evidence is incomplete")
        self.assertEqual(result["operational_gate_status"], "failed")
        self.assertEqual(self.client.tags["deployment_permission"], "blocked")

    def test_partial_review_or_approval_tag_write_fails_closed_and_same_retry_recovers(self):
        client = eligible_registry()
        client.fail_after_tag = "capital_operational_review_reviewer"
        with self.assertRaisesRegex(RuntimeError, "simulated lost response"):
            record_operational_review(client, "2", "reviewer-a", "passed", "reviewed")
        self.assertEqual(client.tags["operational_gate_status"], "not_evaluated")
        self.assertEqual(client.tags["deployment_permission"], "blocked")
        record_operational_review(client, "2", "reviewer-a", "passed", "reviewed")

        client.fail_after_tag = "capital_deployment_approver"
        with self.assertRaisesRegex(RuntimeError, "simulated lost response"):
            record_deployment_approval(client, "2", "approver-b", "approved")
        self.assertEqual(client.tags["deployment_permission"], "blocked")
        record_deployment_approval(client, "2", "approver-b", "approved")
        self.assertEqual(client.tags["deployment_permission"], "eligible")
        require_deployment_eligible(
            "capital-bikeshare", client.tags,
            model_name=CAPITAL_MODEL_NAME, model_version="2", client=client,
        )


if __name__ == "__main__":
    unittest.main()
