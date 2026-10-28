from __future__ import annotations

import copy
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from bike_mlops import capital_measurement_preflight as preflight
from bike_mlops import capital_operational_ledger as ledger
from bike_mlops import capital_operational_plan as plans
from bike_mlops import capital_staging
from bike_mlops.candidate_artifacts import sha256_file
import test_capital_operational_plan as plan_tests


class MeasurementPreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = plan_tests.OperationalPlanTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.csv = self.root / "input.csv"
        self.manifest = self.root / "manifest.json"
        self.csv.write_text("synthetic dataset")
        self.manifest.write_text("synthetic manifest")
        self.evidence = self.fixture.evidence
        self.window = self.fixture.window
        self.evidence["selection"]["data_sha256"] = self.window["data_sha256"] = sha256_file(self.csv)
        self.evidence["selection"]["data_source"]["source_manifest_sha256"] = self.window["manifest_sha256"] = sha256_file(self.manifest)
        with patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
            tags = capital_staging._registration_tags(self.evidence)
        tags["artifact_sha256"] = self.fixture.registered.tags["artifact_sha256"]
        self.fixture.registered.tags = tags
        self.plan = self.fixture.build()
        self.plan_path = self.root / "data/evaluations/capital-bikeshare/operational-plans/test.json"
        self.ledger_path = self.root / "data/evaluations/capital-bikeshare/attempts.json"
        self.lock_dir = self.root / "data/evaluations/capital-bikeshare/locks"
        with patch.object(plans, "PROJECT_ROOT", self.root):
            plans.save_operational_plan(self.plan, self.plan_path)
        self.frame = pd.DataFrame({"timestamp": pd.to_datetime(["2024-01-01"]), "cnt": [3]})
        self.client = Mock()
        self.client.get_model_version.return_value = self.fixture.registered
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for p in (
            patch.object(ledger, "PROJECT_ROOT", self.root),
            patch.object(preflight, "DATA_PROFILE_ID", "capital-bikeshare"),
            patch.object(preflight, "MODEL_NAME", "bike-demand-capital"),
            patch.object(preflight, "DEFAULT_DATASET", self.csv),
            patch.object(preflight, "DATASET_MANIFEST", self.manifest),
            patch.object(preflight, "check_candidate_training_window", return_value=self.window),
            patch.object(preflight, "verify_candidate_for_staging", side_effect=lambda *_: copy.deepcopy(self.evidence)),
            patch.object(preflight, "_load_profile_hourly", return_value=self.frame),
            patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
            patch("mlflow.tracking.MlflowClient", return_value=self.client),
        ):
            self.stack.enter_context(p)
        self.download = self.stack.enter_context(patch("mlflow.artifacts.download_artifacts", return_value=str(self.fixture.artifact)))

    def attempt(self):
        return ledger.measurement_attempt(self.plan_path, ledger_path=self.ledger_path, lock_dir=self.lock_dir)

    def verify(self, attempt):
        return preflight.verify_measurement_inputs(attempt, self.plan_path, self.root / "selection.json", self.root / "final.json")

    def test_binds_started_attempt_frame_and_registry_without_predictions(self):
        with self.attempt() as attempt:
            result = self.verify(attempt)
            self.assertEqual(result.plan["plan_sha256"], attempt.plan_sha256)
            self.assertEqual(result.plan_file_sha256, attempt.plan_file_sha256)
            pd.testing.assert_frame_equal(result.frame, self.frame)
            self.assertEqual(self.client.get_model_version.call_count, 2)
            self.download.assert_called_once()
            self.assertEqual(self.download.call_args.kwargs["registry_uri"], "http://127.0.0.1:5001")
            self.assertEqual(self.download.call_args.kwargs["tracking_uri"], "http://127.0.0.1:5001")
            self.assertIsNone(ledger.read_operational_ledger(self.ledger_path)["records"][0]["attempts"][0]["requests_started_at_utc"])
        with self.assertRaisesRegex(ValueError, "active unfinished"):
            self.verify(attempt)

    def test_rejects_changed_plan_or_dataset_before_registry_access(self):
        with self.attempt() as attempt:
            self.csv.write_text("different")
            with self.assertRaisesRegex(ValueError, "CSV or manifest"):
                self.verify(attempt)
            self.client.get_model_version.assert_not_called()
            self.download.assert_not_called()
        self.csv.write_text("synthetic dataset")
        # The same candidate cannot be silently retried after the interruption.
        with self.assertRaisesRegex(ValueError, "already has"):
            with self.attempt():
                pass

    def test_rejects_registry_change_during_download(self):
        changed = copy.deepcopy(self.fixture.registered)
        changed.tags["candidate_id"] = "0" * 64
        self.client.get_model_version.side_effect = [self.fixture.registered, changed]
        with self.attempt() as attempt:
            with self.assertRaisesRegex(ValueError, "candidate_id"):
                self.verify(attempt)
            self.assertIsNone(ledger.read_operational_ledger(self.ledger_path)["records"][0]["attempts"][0]["requests_started_at_utc"])

    def test_rejects_plan_or_csv_changed_during_load(self):
        def alter_plan(_):
            self.plan_path.write_text(self.plan_path.read_text() + " ")
            return self.frame
        with patch.object(preflight, "_load_profile_hourly", side_effect=alter_plan):
            with self.attempt() as attempt:
                with self.assertRaisesRegex(ValueError, "plan changed during preflight"):
                    self.verify(attempt)

    def test_rejects_dataset_modified_while_loading(self):
        def alter_csv(_):
            self.csv.write_text("changed during loading")
            return self.frame
        with patch.object(preflight, "_load_profile_hourly", side_effect=alter_csv):
            with self.attempt() as attempt:
                with self.assertRaisesRegex(ValueError, "CSV or manifest"):
                    self.verify(attempt)
                self.client.get_model_version.assert_not_called()

    def test_rejects_changed_candidate_evidence_after_download(self):
        changed = copy.deepcopy(self.evidence)
        changed["final_report_sha256"] = "0" * 64
        with patch.object(preflight, "verify_candidate_for_staging", side_effect=[self.evidence, changed]):
            with self.attempt() as attempt:
                with self.assertRaisesRegex(ValueError, "evidence changed during preflight"):
                    self.verify(attempt)

    def test_rejects_modified_downloaded_model(self):
        (self.fixture.artifact / "model.skops").write_bytes(b"tampered")
        with self.attempt() as attempt:
            with self.assertRaisesRegex(ValueError, "artifact"):
                self.verify(attempt)


if __name__ == "__main__":
    unittest.main()
