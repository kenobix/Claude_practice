from __future__ import annotations

import copy
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bike_mlops import capital_operational_plan as plans, capital_staging
from bike_mlops.evaluation_protocol import BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
from bike_mlops.model import directory_sha256


class OperationalPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact = self.root / "model"
        self.artifact.mkdir()
        (self.artifact / "MLmodel").write_text("synthetic metadata\n")
        (self.artifact / "model.skops").write_bytes(b"synthetic model")
        self.evidence = {
            "candidate_id": "a" * 64, "selection_report_sha256": "b" * 64,
            "final_report_sha256": "c" * 64, "evaluation_protocol_sha256": "d" * 64,
            "candidate_artifact_sha256": "e" * 64,
            "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
            "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
            "selection": {"data_sha256": "f" * 64, "data_source": {"source_manifest_sha256": "1" * 64},
                          "data_profile_id": "capital-bikeshare", "target_contract_id": "capital-trip-starts-hourly-v1",
                          "feature_contract_version": "bike-demand-features-v1", "training_cutoff": "2024-01-15 00:00:00",
                          "training_row_keys_sha256": "2" * 64, "split_counts": {"train": 300}},
        }
        self.window = {
            "candidate_id": "a" * 64, "candidate_report_sha256": "b" * 64, "final_report_sha256": "c" * 64,
            "evaluation_protocol_sha256": "d" * 64, "data_sha256": "f" * 64, "manifest_sha256": "1" * 64,
            "start_inclusive": "2024-01-08 00:00:00", "end_exclusive": "2024-01-14 22:00:00",
            "hours": 168, "training_cutoff": "2024-01-15 00:00:00", "label_delay_hours": 3,
            "training_rows": 300, "training_row_keys_sha256": "2" * 64,
            "latest_replay_label_available_at": "2024-01-15 00:00:00",
        }
        # The final target is end-exclusive minus one hour; choose a valid contiguous 168h window.
        self.window["start_inclusive"] = "2024-01-07 22:00:00"
        with patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
            tags = capital_staging._registration_tags(self.evidence)
        tags["artifact_sha256"] = directory_sha256(self.artifact)
        self.registered = SimpleNamespace(name="bike-demand-capital", version="7", status="READY", tags=tags)

    def build(self):
        with patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
            return plans.build_operational_plan(evidence=self.evidence, window=self.window,
                registered=self.registered, artifact_dir=self.artifact, api_url="http://127.0.0.1:8110",
                resource_context={"host": {"logical_cpu_count": 4}, "host_load_average": [0.1, 0.2, 0.3]})

    def test_plan_is_bound_to_candidate_and_written_once(self):
        plan = self.build()
        self.assertEqual(plan["identity"]["candidate_id"], "a" * 64)
        self.assertEqual(plan["policy"]["measured_requests"], 1000)
        self.assertEqual(plan["deployment_permission"], "blocked")
        path = self.root / "data/evaluations/capital-bikeshare/operational-plans/plan.json"
        with patch.object(plans, "PROJECT_ROOT", self.root):
            plans.save_operational_plan(plan, path)
            self.assertEqual(plans.read_operational_plan(path), plan)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                plans.save_operational_plan(plan, path)
            self.assertEqual(before, path.read_bytes())
            with self.assertRaisesRegex(ValueError, "inside the operational-plans"):
                plans.save_operational_plan(plan, self.root / "outside.json")

    def test_mismatched_registry_artifact_and_source_are_rejected(self):
        self.registered.tags["candidate_id"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "candidate_id"):
            self.build()
        self.registered.tags["candidate_id"] = "a" * 64
        (self.artifact / "model.skops").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "artifact"):
            self.build()
        (self.artifact / "model.skops").write_bytes(b"synthetic model")
        self.window["data_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "data_sha256"):
            self.build()

    def test_concurrent_plan_writes_never_overwrite_and_symlink_escape_is_rejected(self):
        plan = self.build()
        directory = self.root / "data/evaluations/capital-bikeshare/operational-plans"
        directory.mkdir(parents=True)
        path = directory / "concurrent.json"
        outside = self.root / "outside"
        outside.mkdir()
        (directory / "escape").symlink_to(outside, target_is_directory=True)

        def save():
            try:
                plans.save_operational_plan(plan, path)
                return "saved"
            except FileExistsError:
                return "exists"

        with patch.object(plans, "PROJECT_ROOT", self.root):
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertCountEqual(list(pool.map(lambda _: save(), range(2))), ["saved", "exists"])
            self.assertEqual(plans.read_operational_plan(path), plan)
            with self.assertRaisesRegex(ValueError, "inside the operational-plans"):
                plans.save_operational_plan(plan, directory / "escape/plan.json")
            self.assertFalse((outside / "plan.json").exists())

    def test_modified_plan_and_rehashed_policy_are_rejected(self):
        plan = self.build()
        changed = copy.deepcopy(plan)
        changed["input_window"]["start_inclusive"] = "2024-01-01 00:00:00"
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            plans.validate_operational_plan(changed)
        changed = copy.deepcopy(plan)
        changed["policy"]["p95_limit_ms"] = 999
        changed["plan_sha256"] = plans._digest(changed)
        with self.assertRaisesRegex(ValueError, "policy"):
            plans.validate_operational_plan(changed)
        changed = copy.deepcopy(plan)
        changed["policy"]["concurrency"] = True
        changed["plan_sha256"] = plans._digest(changed)
        with self.assertRaisesRegex(ValueError, "policy"):
            plans.validate_operational_plan(changed)

    def test_unavailable_labels_and_wrong_port_are_rejected(self):
        self.window["start_inclusive"] = "2024-01-08 00:00:00"
        self.window["end_exclusive"] = "2024-01-15 00:00:00"
        self.window["latest_replay_label_available_at"] = "2024-01-15 02:00:00"
        with self.assertRaisesRegex(ValueError, "labels are not available"):
            self.build()
        with self.assertRaisesRegex(ValueError, "local staging API"):
            plans._staging_url("http://127.0.0.1:8080")

    def test_plan_binding_rechecks_current_evidence_and_registry(self):
        plan = self.build()
        with patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
            plans.verify_operational_plan_binding(plan, evidence=self.evidence, window=self.window,
                                                  registered=self.registered, artifact_dir=self.artifact)
            altered = copy.deepcopy(plan)
            altered["identity"]["model_version"] = "8"
            altered["plan_sha256"] = plans._digest(altered)
            with self.assertRaisesRegex(ValueError, "identity differs"):
                plans.verify_operational_plan_binding(altered, evidence=self.evidence, window=self.window,
                                                      registered=self.registered, artifact_dir=self.artifact)
            self.registered.tags["deployment_permission"] = "eligible"
            with self.assertRaisesRegex(ValueError, "deployment_permission"):
                plans.verify_operational_plan_binding(plan, evidence=self.evidence, window=self.window,
                                                      registered=self.registered, artifact_dir=self.artifact)

    def test_cli_writes_plan_without_sending_prediction_requests(self):
        script = Path(__file__).resolve().parents[1] / "scripts/prepare_capital_operational_plan.py"
        spec = importlib.util.spec_from_file_location("capital_plan_cli_for_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = self.root / "data/evaluations/capital-bikeshare/operational-plans/cli.json"
        argv = ["prepare", "--candidate-id", "a" * 64, "--model-version", "7",
                "--selection-report", "candidate.json", "--final-report", "final.json",
                "--start", self.window["start_inclusive"], "--end", self.window["end_exclusive"],
                "--output", str(output)]
        client = SimpleNamespace(get_model_version=lambda name, version: self.registered)
        with (patch.object(module, "DATA_PROFILE_ID", "capital-bikeshare"),
              patch.object(module, "MODEL_NAME", "bike-demand-capital"),
              patch.object(module, "check_candidate_training_window", return_value=self.window),
              patch.object(module, "verify_candidate_for_staging", return_value=self.evidence),
              patch.object(module, "_machine_and_container_resources", return_value={"host": {}}),
              patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
              patch.object(plans, "PROJECT_ROOT", self.root),
              patch("mlflow.set_tracking_uri"), patch("mlflow.set_registry_uri") as registry_setter,
              patch("mlflow.tracking.MlflowClient", return_value=client) as client_factory,
              patch("mlflow.artifacts.download_artifacts", return_value=str(self.artifact)) as download,
              patch("bike_mlops.operational_benchmark._PersistentJsonClient") as http_client,
              patch.dict("os.environ", {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5001"}),
              patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()) as stdout):
            module.main()
            registry_setter.assert_called_once_with("http://127.0.0.1:5001")
            client_factory.assert_called_once_with(tracking_uri="http://127.0.0.1:5001", registry_uri="http://127.0.0.1:5001")
            http_client.assert_not_called()
            self.assertIn("plan_frozen_no_predictions_sent", stdout.getvalue())
            self.assertEqual(plans.read_operational_plan(output)["identity"]["model_version"], "7")
            download.reset_mock()
            self.registered.tags["candidate_id"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "candidate_id"):
                module.main()
            download.assert_not_called()

    def test_isolated_mlflow_registry_download_and_changed_tag(self):
        import mlflow
        import mlflow.artifacts
        import mlflow.tracking._model_registry.utils as registry_utils
        from mlflow.tracking import MlflowClient

        old_uri = mlflow.get_tracking_uri()
        old_registry_uri = registry_utils._registry_uri
        old_registry_env = os.environ.get("MLFLOW_REGISTRY_URI")
        uri = f"sqlite:///{self.root / 'synthetic-registry.sqlite'}"
        try:
            mlflow.set_tracking_uri(uri)
            mlflow.set_registry_uri(uri)
            client = MlflowClient(tracking_uri=uri, registry_uri=uri)
            client.create_registered_model("bike-demand-capital")
            created = client.create_model_version(name="bike-demand-capital", source=self.artifact.as_uri(),
                                                  tags=self.registered.tags)
            with tempfile.TemporaryDirectory() as download:
                artifact_dir = Path(mlflow.artifacts.download_artifacts(
                    artifact_uri=f"models:/bike-demand-capital/{created.version}", dst_path=download))
                # MLflow can add registered_model_meta to downloads; registration pins the downloaded directory.
                client.set_model_version_tag("bike-demand-capital", created.version, "artifact_sha256",
                                             directory_sha256(artifact_dir))
                registered = client.get_model_version("bike-demand-capital", created.version)
                with tempfile.TemporaryDirectory() as second_download:
                    second = Path(mlflow.artifacts.download_artifacts(
                        artifact_uri=f"models:/bike-demand-capital/{created.version}", dst_path=second_download))
                    self.assertEqual(directory_sha256(second), directory_sha256(artifact_dir))
                with patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
                    plan = plans.build_operational_plan(evidence=self.evidence, window=self.window,
                        registered=registered, artifact_dir=artifact_dir, api_url="http://127.0.0.1:8110",
                        resource_context={"host": {"kind": "synthetic"}})
                    plans.verify_operational_plan_binding(plan, evidence=self.evidence, window=self.window,
                                                          registered=registered, artifact_dir=artifact_dir)
                    client.set_model_version_tag("bike-demand-capital", created.version, "candidate_id", "0" * 64)
                    changed = client.get_model_version("bike-demand-capital", created.version)
                    with self.assertRaisesRegex(ValueError, "candidate_id"):
                        plans.verify_operational_plan_binding(plan, evidence=self.evidence, window=self.window,
                                                              registered=changed, artifact_dir=artifact_dir)
        finally:
            mlflow.set_tracking_uri(old_uri)
            registry_utils._registry_uri = old_registry_uri
            if old_registry_env is None:
                os.environ.pop("MLFLOW_REGISTRY_URI", None)
            else:
                os.environ["MLFLOW_REGISTRY_URI"] = old_registry_env


if __name__ == "__main__":
    unittest.main()
