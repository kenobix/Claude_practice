from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.capital_deployment_fixture import fixture
from test_capital_deployment_decisions import FakeRegistry
from bike_mlops import capital_deployment_evidence as evidence
from bike_mlops import capital_operational_registry as registry
from bike_mlops.capital_deployment_decisions import record_operational_review, record_deployment_approval
from bike_mlops.deployment_gates import require_deployment_eligible


class CompleteDeploymentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        change = patch.object(registry, "PROJECT_ROOT", self.root)
        change.start()
        self.addCleanup(change.stop)
        self.tags, self.bundle, self.body = fixture()
        self.client = FakeRegistry(self.tags, body=self.body)

    def approve(self):
        record_operational_review(self.client, "2", "reviewer-a", "passed", "synthetic evidence")
        return record_deployment_approval(self.client, "2", "approver-b", "synthetic evidence")

    def check(self):
        return require_deployment_eligible("capital-bikeshare", self.client.tags,
            model_name=evidence.MODEL, model_version="2", client=self.client)

    def replace_bundle(self, bundle):
        self.client.body = evidence.canonical_bytes(bundle)
        self.client.tags.update(evidence.reference_tags(self.client.body, self.client.run_id))
        self.client.artifact_path = self.client.tags["capital_deployment_evidence_path"]

    def test_real_report_bodies_and_independent_saved_row_recheck_are_required(self):
        result = evidence.validate_bundle(self.bundle, self.tags, "2", "synthetic-run")
        self.assertEqual(result["p95_ms"], 100.0)
        self.approve()
        self.check()
        self.client.body = None
        with self.assertRaisesRegex(ValueError, "not eligible"):
            self.check()

    def test_all_bound_tag_mutations_and_invalid_numbers_are_rejected_after_approval(self):
        self.approve()
        original = dict(self.client.tags)
        changes = [
            ("final_report_sha256", "9"*64), ("capital_operational_plan_sha256", "8"*64),
            ("evaluation_protocol_sha256", "7"*64), ("data_sha256", "6"*64),
            ("capital_operational_request_id_sha256", "5"*64),
            ("capital_operational_p95_ms", "-1"), ("capital_operational_p95_ms", "nan"),
            ("capital_operational_p95_ms", "inf"), ("capital_operational_p95_ms", "250.01"),
            ("capital_deployment_approval_at_utc", "2020-01-01T00:00:00+00:00"),
            ("capital_operational_review_at_utc", "2024-01-01T00:00:00"),
            ("capital_deployment_decision_schema_version", "1"),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.client.tags = dict(original, **{key: value})
                with self.assertRaises(ValueError):
                    self.check()
        self.client.tags = original
        self.check()

    def test_missing_prerequisites_cannot_review_or_become_eligible(self):
        for category in evidence.CASES:
            with self.subTest(category=category):
                changed = copy.deepcopy(self.bundle)
                del changed["reports"][category]
                self.replace_bundle(changed)
                with self.assertRaisesRegex(ValueError, "report bodies"):
                    record_operational_review(self.client, "2", "reviewer-a", "passed", "synthetic")
                self.assertEqual(self.client.tags["deployment_permission"], "blocked")

    def test_hash_consistent_failed_uncertain_foreign_and_missing_cases_are_rejected(self):
        for category in evidence.CASES:
            for change in ("failed", "uncertain", "foreign", "missing_case", "bad_outcome", "future", "no_observations"):
                with self.subTest(category=category, change=change):
                    bundle = copy.deepcopy(self.bundle)
                    report = evidence.unpack(bundle["reports"][category])
                    first = next(iter(report["checks"]))
                    if change == "failed":
                        report["status"] = "failed"
                    elif change == "uncertain":
                        report["evidence_state"] = "database_commit_unknown"
                    elif change == "foreign":
                        report["identity"]["model_version"] = "99"
                    elif change == "missing_case":
                        del report["checks"][first]
                    elif change == "future":
                        report["completed_at_utc"] = "2999-01-01T00:00:00+00:00"
                    else:
                        case = evidence.unpack(report["checks"][first])
                        if change == "bad_outcome":
                            case["observed_outcome"] = "failed"
                        else:
                            case["observations"] = {}
                        report["checks"][first] = evidence.document(evidence.canonical_bytes(case))
                    bundle["reports"][category] = evidence.document(evidence.canonical_bytes(report))
                    # Rehash every changed descriptor: this must fail semantic checks, not just SHA checks.
                    self.replace_bundle(bundle)
                    with self.assertRaises(ValueError):
                        evidence.verified_registered_evidence(self.client, "2", self.client.tags)

    def test_bundle_corruption_duplicate_json_keys_and_unknown_policy_fail_closed(self):
        for mode in ("bytes", "duplicate", "policy"):
            with self.subTest(mode=mode):
                self.client = FakeRegistry(self.tags, body=self.body)
                if mode == "bytes":
                    self.client.body = self.body + b"changed"
                elif mode == "duplicate":
                    self.client.body = b'{"schema_version":1,"schema_version":1}'
                    self.client.tags.update(evidence.reference_tags(self.client.body, self.client.run_id))
                    self.client.artifact_path = self.client.tags["capital_deployment_evidence_path"]
                else:
                    changed = copy.deepcopy(self.bundle)
                    changed["policy"]["policy_id"] = "different"
                    self.replace_bundle(changed)
                with self.assertRaises(ValueError):
                    record_operational_review(self.client, "2", "reviewer-a", "passed", "synthetic")
                self.assertEqual(self.client.tags["deployment_permission"], "blocked")

    def test_saved_operational_latency_cannot_disagree_with_reported_p95(self):
        changed = copy.deepcopy(self.bundle)
        report = evidence.unpack(changed["reports"]["operational"])
        for row in report["measurements"]:
            row["latency_ms"] = 200.0
        changed["reports"]["operational"] = evidence.document(evidence.canonical_bytes(report))
        self.client.tags["capital_operational_report_sha256"] = changed["reports"]["operational"]["sha256"]
        changed["identity"] = evidence.evidence_identity(self.client.tags, "2", self.client.run_id)
        self.replace_bundle(changed)
        with self.assertRaisesRegex(ValueError, "p95"):
            evidence.verified_registered_evidence(self.client, "2", self.client.tags)

    def test_changed_accuracy_rows_cannot_support_the_original_metrics(self):
        rows = evidence.unpack(self.bundle["reports"]["final_rows"], csv=True)
        rows = rows.replace(",2,1.0,4.0", ",200,1.0,4.0")
        with self.assertRaisesRegex(ValueError, "accuracy differs"):
            evidence._accuracy_rows(rows, evidence.unpack(self.bundle["reports"]["protocol"])["evaluation"],
                                   3, {"mae": 1.0, "rmse": 1.0}, {"mae": 2.0, "rmse": 2.0}, "final_evaluation")

    def test_report_identity_changes_during_download_are_rejected(self):
        self.approve()
        original = self.client.download_artifacts
        def changing(*args):
            path = original(*args)
            self.client.tags["final_report_sha256"] = "7"*64
            return path
        self.client.download_artifacts = changing
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.check()

    def test_decision_metadata_changes_during_download_are_rejected(self):
        self.approve()
        original = self.client.download_artifacts
        def changing(*args):
            path = original(*args)
            self.client.tags["capital_deployment_approval_reason"] = "changed"
            return path
        self.client.download_artifacts = changing
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.check()

    def test_last_eligible_write_lost_response_is_queryable_and_same_retry_is_idempotent(self):
        record_operational_review(self.client, "2", "reviewer-a", "passed", "synthetic")
        self.client.fail_after_tag = "deployment_permission"
        with self.assertRaises(RuntimeError):
            record_deployment_approval(self.client, "2", "approver-b", "synthetic")
        self.check()  # Server completed all bound fields before the response was lost.
        previous = dict(self.client.tags)
        record_deployment_approval(self.client, "2", "approver-b", "synthetic")
        self.assertEqual(previous, self.client.tags)

    def test_oversized_or_outside_download_cannot_supply_report_evidence(self):
        with patch.object(evidence, "MAX_BUNDLE_BYTES", 1024):
            with self.assertRaisesRegex(ValueError, "too large"):
                evidence.verified_registered_evidence(self.client, "2", self.client.tags)
        outside = self.root / "outside.json"
        outside.write_bytes(self.body)
        self.client.download_artifacts = lambda *args: str(outside)
        with self.assertRaisesRegex(ValueError, "temporary directory"):
            evidence.verified_registered_evidence(self.client, "2", self.client.tags)

    def test_noncanonical_policy_types_and_missing_body_are_rejected(self):
        for change in ("bool_schema", "bool_policy", "missing_content"):
            with self.subTest(change=change):
                bundle = copy.deepcopy(self.bundle)
                if change == "bool_schema":
                    bundle["schema_version"] = True
                elif change == "bool_policy":
                    bundle["policy"]["schema_version"] = True
                else:
                    del bundle["reports"]["metadata"]["content"]
                self.replace_bundle(bundle)
                with self.assertRaises(ValueError):
                    evidence.verified_registered_evidence(self.client, "2", self.client.tags)

    def test_capital_normal_loader_pins_registry_and_rejects_bad_body_before_model_download(self):
        import mlflow.artifacts
        from bike_mlops import model
        self.approve()
        self.client.body = b"corrupt"
        with (patch.object(model, "DATA_PROFILE_ID", "capital-bikeshare"),
              patch.object(model, "TARGET_CONTRACT_ID", self.tags["target_contract_id"]),
              patch.dict(os.environ, {"MLFLOW_REGISTRY_URI": "http://127.0.0.1:5999"}),
              patch("mlflow.set_tracking_uri"),
              patch("mlflow.tracking.MlflowClient", return_value=self.client) as factory,
              patch.object(mlflow.artifacts, "download_artifacts") as download):
            with self.assertRaises(ValueError):
                model.load_registered_model(evidence.MODEL, "2", "http://127.0.0.1:5001")
            factory.assert_called_once_with(tracking_uri="http://127.0.0.1:5001", registry_uri="http://127.0.0.1:5001")
            download.assert_not_called()

    def test_capital_cli_client_pins_registry_even_with_conflicting_environment(self):
        from bike_mlops import cli
        with (patch.object(cli, "DATA_PROFILE_ID", "capital-bikeshare"),
              patch.dict(os.environ, {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5001",
                                      "MLFLOW_REGISTRY_URI": "http://127.0.0.1:5999"}),
              patch("mlflow.set_tracking_uri"),
              patch("mlflow.tracking.MlflowClient", return_value=self.client) as factory):
            self.assertIs(cli._model_registry_client(), self.client)
            factory.assert_called_once_with(tracking_uri="http://127.0.0.1:5001", registry_uri="http://127.0.0.1:5001")

    def test_runtime_init_rejects_corrupt_reports_before_writing_slot_or_state_files(self):
        from bike_mlops import cli
        self.approve()
        self.client.body = b"corrupt"
        runtime = self.root / "runtime"
        args = SimpleNamespace(version="2", approver="operator", reason="synthetic", diagnostic=False)
        with (patch.object(cli, "DATA_PROFILE_ID", "capital-bikeshare"),
              patch.object(cli, "TARGET_CONTRACT_ID", self.tags["target_contract_id"]),
              patch.object(cli, "MODEL_NAME", evidence.MODEL),
              patch.object(cli, "RUNTIME_DIR", runtime),
              patch.object(cli, "_model_registry_client", return_value=self.client)):
            with self.assertRaises(ValueError):
                cli.command_runtime_init(args)
        self.assertFalse((runtime / "blue.env").exists())
        self.assertFalse((runtime / "green.env").exists())
        self.assertFalse((runtime / "deployment-state.json").exists())


if __name__ == "__main__":
    unittest.main()
