from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.capital_deployment_fixture import fixture
from bike_mlops.capital_request_journal import RequestJournal
from test_capital_deployment_decisions import FakeRegistry
from bike_mlops import capital_deployment_evidence as evidence
from bike_mlops.capital_deployment_decisions import record_operational_review, record_deployment_approval
from bike_mlops.capital_deployment_evidence_publisher import publish_deployment_evidence
from bike_mlops.deployment_gates import require_deployment_eligible


class DeploymentEvidencePublisherTests(unittest.TestCase):
    def setUp(self):
        from bike_mlops import config, capital_operational_ledger as ledger
        from bike_mlops import capital_operational_plan as plans
        from bike_mlops import capital_operational_recovery as recovery
        from bike_mlops import capital_operational_registry as registry
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module in (config, ledger, plans, recovery, registry):
            change = patch.object(module, "PROJECT_ROOT", self.root)
            change.start()
            self.addCleanup(change.stop)
        profile = patch.object(config, "DATA_PROFILE_ID", "capital-bikeshare")
        profile.start()
        self.addCleanup(profile.stop)
        self.ledger_path = self.root / "data/evaluations/capital-bikeshare/operational-attempts.json"
        self.lock_dir = self.ledger_path.parent / "locks"
        for name, value in (("DEFAULT_LEDGER", self.ledger_path), ("DEFAULT_LOCK_DIR", self.lock_dir)):
            change = patch.object(ledger, name, value)
            change.start()
            self.addCleanup(change.stop)
        self.tags, bundle, body = fixture()
        selection = evidence.unpack(bundle["reports"]["candidate"])
        self.cid = selection["candidate_id"]
        self.selection_path = self.root / "data/candidates" / self.cid / "candidate-report.json"
        self.final_path = self.root / "data/reports/capital-bikeshare/final.json"
        self.plan_path = self.ledger_path.parent / "operational-plans/test.json"
        paths = {
            "candidate": self.selection_path, "final": self.final_path,
            "protocol": self.selection_path.parent / "evaluation-protocol.json",
            "validation_rows": self.selection_path.parent / "validation-predictions.csv",
            "final_rows": self.final_path.parent / "final.predictions.csv",
        }
        for name, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bundle["reports"][name]["content"].encode())
        plan = evidence.unpack(bundle["reports"]["plan"])
        plans.save_operational_plan(plan, self.plan_path)
        with ledger.measurement_attempt(self.plan_path, ledger_path=self.ledger_path, lock_dir=self.lock_dir) as attempt:
            self.attempt_id = attempt.attempt_id
            attempt.mark_requests_started()
            report = evidence.unpack(bundle["reports"]["operational"])
            old_id = report["operational_attempt_id"]
            report.update(operational_attempt_id=attempt.attempt_id, benchmark_id=attempt.attempt_id)
            report["operational_plan_file_sha256"] = attempt.plan_file_sha256
            for row in report["measurements"]:
                row["request_id"] = row["request_id"].replace(old_id, attempt.attempt_id)
            ids_sha = hashlib.sha256("\n".join(row["request_id"] for row in report["measurements"]).encode()).hexdigest()
            report["operational_gate"]["observed"]["request_id_sha256"] = ids_sha
            report["audit"]["request_id_sha256"] = ids_sha
            self.journal_path = self.final_path.parent / f"operational-{attempt.attempt_id}.requests.jsonl"
            journal = RequestJournal(self.journal_path, attempt, plan)
            try:
                # Fixture preparation does not test disk durability; dedicated interruption tests do.
                with patch("bike_mlops.capital_request_journal.os.fsync"):
                    for payload, row in zip(journal.schedule, report["measurements"], strict=True):
                        journal.sending(payload)
                        journal.outcome(payload, http_status=200, latency_ms=row["latency_ms"], error=None)
                report["request_journal"] = journal.descriptor()
            finally:
                journal.close()
            self.report_path = self.final_path.parent / f"operational-{attempt.attempt_id}.json"
            self.report_path.write_bytes(evidence.canonical_bytes(report))
            attempt.finish(status="completed", report_path=self.report_path)
        self.tags.update(registry._validate_completed_evidence(
            plan, report, hashlib.sha256(self.report_path.read_bytes()).hexdigest(),
            self.attempt_id, hashlib.sha256(self.plan_path.read_bytes()).hexdigest()))
        for key in evidence.REF_KEYS:
            self.tags.pop(key, None)
        self.client = FakeRegistry(self.tags, body=None)
        self.paths = {}
        for category in evidence.CASES:
            path = self.final_path.parent / f"{category}.json"
            path.write_bytes(bundle["reports"][category]["content"].encode())
            self.paths[category] = path
        self.verified = {"selection_report_path": self.selection_path, "final_report_path": self.final_path,
                         "selection": selection, "final": evidence.unpack(bundle["reports"]["final"])}

    def publish(self):
        with (patch.dict(os.environ, {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5001",
                                      "MLFLOW_REGISTRY_URI": "http://127.0.0.1:5001"}),
              patch("mlflow.tracking.MlflowClient", return_value=self.client),
              patch("bike_mlops.capital_staging.verify_candidate_for_staging", return_value=self.verified)):
            return publish_deployment_evidence(self.cid, self.attempt_id, self.final_path, self.paths)

    def test_publisher_reads_completed_attempt_and_report_files_without_approving(self):
        result = self.publish()
        self.assertEqual(result["deployment_permission"], "blocked")
        self.assertEqual(self.client.tags["operational_gate_status"], "not_evaluated")
        evidence.verified_registered_evidence(self.client, "2", self.client.tags)
        previous = dict(self.client.tags)
        self.publish()
        self.assertEqual(previous, self.client.tags)
        record_operational_review(self.client, "2", "synthetic-reviewer", "passed", "fixture")
        record_deployment_approval(self.client, "2", "synthetic-approver", "fixture")
        require_deployment_eligible("capital-bikeshare", self.client.tags,
            model_name=evidence.MODEL, model_version="2", client=self.client)

    def test_missing_or_torn_journal_is_rejected_before_upload(self):
        original = self.journal_path.read_bytes()
        for content in (None, original + b'{"event":'):
            with self.subTest(missing=content is None):
                if content is None: self.journal_path.unlink()
                else: self.journal_path.write_bytes(content)
                with patch.object(self.client, "log_artifact") as upload:
                    with self.assertRaisesRegex(ValueError, "journal"):
                        self.publish()
                    upload.assert_not_called()
                self.assertEqual(self.client.tags["deployment_permission"], "blocked")

    def test_changed_operational_report_is_rejected_before_upload(self):
        self.report_path.write_bytes(self.report_path.read_bytes()+b" ")
        with patch.object(self.client, "log_artifact") as upload:
            with self.assertRaisesRegex(ValueError, "report differs"):
                self.publish()
            upload.assert_not_called()
        self.assertEqual(self.client.tags["deployment_permission"], "blocked")

    def test_failed_prerequisite_is_rejected_before_upload(self):
        report = evidence.strict_json(self.paths["monitoring"].read_text())
        report["status"] = "failed"
        self.paths["monitoring"].write_bytes(evidence.canonical_bytes(report))
        with patch.object(self.client, "log_artifact") as upload:
            with self.assertRaises(ValueError):
                self.publish()
            upload.assert_not_called()
        self.assertNotIn(evidence.REF_KEYS[0], self.client.tags)

    def test_upload_response_loss_stays_blocked_and_same_retry_recovers(self):
        original = self.client.log_artifact
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("synthetic lost upload response")
        with patch.object(self.client, "log_artifact", side_effect=lost):
            with self.assertRaises(RuntimeError):
                self.publish()
        self.assertNotIn(evidence.REF_KEYS[0], self.client.tags)
        self.assertEqual(self.client.tags["deployment_permission"], "blocked")
        self.publish()
        evidence.verified_registered_evidence(self.client, "2", self.client.tags)


class IsolatedMlflowDeploymentEvidenceTests(unittest.TestCase):
    def test_report_artifact_review_approval_and_tamper_rejection_in_temporary_sqlite_registry(self):
        from mlflow.tracking import MlflowClient
        from bike_mlops import capital_operational_registry as registry
        with tempfile.TemporaryDirectory(prefix="capital-decision-mlflow-test-") as directory:
            root = Path(directory)
            uri = f"sqlite:///{root / 'mlflow.db'}"
            client = MlflowClient(tracking_uri=uri, registry_uri=uri)
            experiment = client.create_experiment("synthetic-only", artifact_location=(root / "artifacts").as_uri())
            run = client.create_run(experiment)
            run_id = run.info.run_id
            client.create_registered_model(evidence.MODEL)
            # The reusable fixture binds exact version 2. Version 1 is synthetic metadata only.
            source = root / "synthetic-model"
            source.mkdir()
            client.create_model_version(evidence.MODEL, str(source), run_id=run_id)
            version = client.create_model_version(evidence.MODEL, str(source), run_id=run_id)
            self.assertEqual(str(version.version), "2")
            tags, bundle, _ = fixture()
            bundle["identity"]["run_id"] = run_id
            body = evidence.canonical_bytes(bundle)
            refs = evidence.reference_tags(body, run_id)
            tags.update(refs)
            path = root / "bundle.json"
            path.write_bytes(body)
            client.log_artifact(run_id, str(path), artifact_path=refs["capital_deployment_evidence_path"].rsplit("/", 1)[0])
            for key, value in tags.items():
                client.set_model_version_tag(evidence.MODEL, "2", key, value)
            with patch.object(registry, "PROJECT_ROOT", root):
                record_operational_review(client, "2", "synthetic-reviewer", "passed", "temporary test only")
                record_deployment_approval(client, "2", "synthetic-approver", "temporary test only")
            current = client.get_model_version(evidence.MODEL, "2")
            require_deployment_eligible("capital-bikeshare", current.tags,
                model_name=evidence.MODEL, model_version="2", client=client)
            from urllib.parse import unquote, urlsplit
            artifact_root = Path(unquote(urlsplit(client.get_run(run_id).info.artifact_uri).path))
            artifact = artifact_root / refs["capital_deployment_evidence_path"]
            self.assertTrue(artifact.is_file())
            artifact.write_bytes(b"synthetic corruption")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                require_deployment_eligible("capital-bikeshare", current.tags,
                    model_name=evidence.MODEL, model_version="2", client=client)


if __name__ == "__main__":
    unittest.main()
