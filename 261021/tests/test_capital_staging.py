from __future__ import annotations

import hashlib
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bike_mlops import capital_staging
from bike_mlops.candidate_artifacts import candidate_identity, sha256_file
from bike_mlops.evaluation_protocol import (
    BOOTSTRAP_QUALITY_GATE_POLICY,
    BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
)
from bike_mlops.quality_gates import baseline_accuracy_gate


class CapitalStagingTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, dict]:
        data_dir = root / "data"
        data_dir.mkdir()
        bundle_dir = data_dir / "candidate-build"
        bundle_dir.mkdir()
        model_bytes = b"synthetic model artifact; not loaded by these evidence checks\n"
        (bundle_dir / "model.skops").write_bytes(model_bytes)
        predictions_bytes = b"timestamp,actual,candidate_prediction,baseline_prediction\n"
        (bundle_dir / "validation-predictions.csv").write_bytes(predictions_bytes)
        protocol_bytes = b'{"synthetic":"protocol fixture"}\n'
        (bundle_dir / "evaluation-protocol.json").write_bytes(protocol_bytes)
        snapshot_path = bundle_dir / "source-snapshot.tar.gz"
        with tarfile.open(snapshot_path, "w:gz"):
            pass

        report = {
            "data_sha256": "d" * 64,
            "data_source": {"source_manifest_sha256": "e" * 64},
            "source_revision": {"source_tree_sha256": hashlib.sha256(b"").hexdigest(), "source_file_count": 0},
            "environment": {"direct_dependencies": {"scikit-learn": "synthetic"}},
            "feature_contract_version": "bike-demand-features-v1",
            "feature_columns": ["hour"],
            "hyperparameters": {"max_iter": 1},
            "training_cutoff": "2026-06-01 00:00:00",
            "validation_window": {"start": "2026-06-01 00:00:00", "end_exclusive": "2026-08-01 00:00:00"},
            "comparison": {
                "reference": "historical_baseline_only",
                "incumbent_model_version": None,
                "incumbent_artifact_sha256": None,
                "validation_gate": baseline_accuracy_gate(
                    {"mae": 1.0, "rmse": 1.0}, {"mae": 2.0, "rmse": 2.0}, period="validation_selection"
                ),
            },
            "evaluation_protocol_sha256": "f" * 64,
            "training_row_keys_sha256": "a" * 64,
            "validation_row_keys_sha256": "b" * 64,
            "candidate_artifact": {"path": "model.skops", "sha256": hashlib.sha256(model_bytes).hexdigest()},
            "validation_predictions": {
                "path": "validation-predictions.csv",
                "sha256": hashlib.sha256(predictions_bytes).hexdigest(),
            },
            "source_snapshot": {"path": "source-snapshot.tar.gz", "sha256": sha256_file(snapshot_path)},
            "evaluation_protocol_artifact": {
                "path": "evaluation-protocol.json",
                "sha256": hashlib.sha256(protocol_bytes).hexdigest(),
            },
            "quality_gate_passed": True,
            "quality_gate": "synthetic accepted fixture",
            "validation_gate_status": "passed",
            "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
            "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
            "validation": {"model": {"mae": 1.0, "rmse": 1.0}, "baseline": {"mae": 2.0, "rmse": 2.0}},
            "data_profile_id": "capital-bikeshare",
            "target_contract_id": "capital-trip-starts-hourly-v1",
            "candidate_mode": "rolling_time_series",
        }
        report["candidate_identity"] = candidate_identity(report)
        candidate_id = hashlib.sha256(
            json.dumps(report["candidate_identity"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        report["candidate_id"] = candidate_id
        report["candidate_bundle"] = f"data/candidates/{candidate_id}"
        final_dir = data_dir / "candidates" / candidate_id
        final_dir.parent.mkdir()
        bundle_dir.rename(final_dir)
        selection_path = final_dir / "candidate-report.json"
        selection_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        reports_dir = root / "data/reports/capital-bikeshare"
        reports_dir.mkdir(parents=True)
        final_predictions_path = reports_dir / "final-report.predictions.csv"
        final_predictions_path.write_text(
            "timestamp,label_available_at,actual,candidate_prediction,baseline_prediction\n"
            "2026-08-01 03:00:00,2026-08-01 05:00:00,2,1,4\n",
            encoding="utf-8",
        )
        final_report = {
            "candidate_id": candidate_id,
            "candidate_artifact_sha256": report["candidate_artifact"]["sha256"],
            "evaluation_protocol_sha256": report["evaluation_protocol_sha256"],
            "data_profile_id": "capital-bikeshare",
            "target_contract_id": "capital-trip-starts-hourly-v1",
            "validation_gate_status": "passed",
            "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
            "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
            "selection_report": f"data/candidates/{candidate_id}/candidate-report.json",
            "candidate": {"model": {"mae": 1.0, "rmse": 1.0}},
            "historical_baseline": {"model": {"mae": 2.0, "rmse": 2.0}},
            "data_source": {"csv_sha256": report["data_sha256"], "source_manifest_sha256": report["data_source"]["source_manifest_sha256"]},
            "evaluation_window": {
                "start_inclusive": "2026-08-01 03:00:00",
                "end_exclusive": "2026-10-01 00:00:00",
                "rows": 1,
                "used_for_candidate_selection": False,
                "row_level_predictions": "data/reports/capital-bikeshare/final-report.predictions.csv",
                "row_level_predictions_sha256": sha256_file(final_predictions_path),
            },
            "final_accuracy_gate": baseline_accuracy_gate(
                {"mae": 1.0, "rmse": 1.0}, {"mae": 2.0, "rmse": 2.0}, period="final_evaluation"
            ),
            "operational_gate_status": "not_evaluated",
            "deployment_permission": {"status": "blocked"},
            "model_fit_performed_during_evaluation": False,
        }
        final_path = reports_dir / "final-report.json"
        final_path.write_text(json.dumps(final_report, indent=2) + "\n", encoding="utf-8")
        return selection_path, final_path, report

    def _protocol(self, selection: dict) -> dict:
        return {
            "schema_version": 2,
            "incumbent": None,
            "protocol_sha256": selection["evaluation_protocol_sha256"],
            "dataset": {
                "data_profile_id": "capital-bikeshare",
                "target_contract": "capital-trip-starts-hourly-v1",
                "data_sha256": selection["data_sha256"],
                "source_manifest_sha256": selection["data_source"]["source_manifest_sha256"],
            },
            "training": {"cutoff": selection["training_cutoff"]},
            "validation": {
                "start_inclusive": selection["validation_window"]["start"],
                "end_exclusive": selection["validation_window"]["end_exclusive"],
            },
            "evaluation": {
                "start_inclusive": "2026-08-01 03:00:00",
                "end_exclusive": "2026-10-01 00:00:00",
                "rows": 1,
            },
            "label_availability": {"delay_hours": 2},
            "selection_gate": {
                "policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
                "policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
            },
        }

    def _ledger(self, selection: dict, final_path: Path, protocol: dict) -> dict:
        return {
            "schema_version": 1,
            "records": [{
                "consumption_key": capital_staging.evaluation_window_key(
                    protocol, "reserved_final_evaluation"
                ),
                "data_sha256": selection["data_sha256"],
                "target_contract": "capital-trip-starts-hourly-v1",
                "start_inclusive": "2026-08-01 03:00:00",
                "end_exclusive": "2026-10-01 00:00:00",
                "purpose": "reserved_final_evaluation",
                "status": "completed",
                "attempts": [{
                    "status": "completed",
                    "protocol_sha256": selection["evaluation_protocol_sha256"],
                    "candidate_artifact_sha256": selection["candidate_artifact"]["sha256"],
                    "detail": sha256_file(final_path),
                }],
            }],
        }

    def test_staging_requires_matching_immutable_candidate_and_passed_final_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection_path, final_path, selection = self._fixture(root)
            fake_protocol = self._protocol(selection)
            with (
                patch.object(capital_staging, "PROJECT_ROOT", root),
                patch.object(capital_staging, "DATA_PROFILE_ID", "capital-bikeshare"),
                patch.object(capital_staging, "MODEL_NAME", "bike-demand-capital"),
                patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                patch.object(capital_staging, "read_protocol", return_value=fake_protocol),
                patch.object(
                    capital_staging, "read_ledger",
                    return_value=self._ledger(selection, final_path, fake_protocol),
                ),
            ):
                evidence = capital_staging.verify_candidate_for_staging(selection_path, final_path)
                self.assertEqual(evidence["candidate_id"], selection["candidate_id"])
                self.assertEqual(evidence["candidate_artifact_sha256"], selection["candidate_artifact"]["sha256"])

                final = json.loads(final_path.read_text(encoding="utf-8"))
                final["candidate_artifact_sha256"] = "0" * 64
                final_path.write_text(json.dumps(final), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Final report"):
                    capital_staging.verify_candidate_for_staging(selection_path, final_path)

    def test_staging_rejects_final_metrics_not_supported_by_saved_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection_path, final_path, selection = self._fixture(root)
            protocol = self._protocol(selection)
            final = json.loads(final_path.read_text(encoding="utf-8"))
            final["candidate"]["model"] = {"mae": 0.5, "rmse": 0.5}
            final["final_accuracy_gate"] = baseline_accuracy_gate(
                final["candidate"]["model"], final["historical_baseline"]["model"],
                period="final_evaluation",
            )
            final_path.write_text(json.dumps(final, indent=2) + "\n", encoding="utf-8")
            with (
                patch.object(capital_staging, "PROJECT_ROOT", root),
                patch.object(capital_staging, "DATA_PROFILE_ID", "capital-bikeshare"),
                patch.object(capital_staging, "MODEL_NAME", "bike-demand-capital"),
                patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                patch.object(capital_staging, "read_protocol", return_value=protocol),
                patch.object(
                    capital_staging, "read_ledger",
                    return_value=self._ledger(selection, final_path, protocol),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "does not match its saved row-level evidence"):
                    capital_staging.verify_candidate_for_staging(selection_path, final_path)

    def test_staging_rejects_final_report_not_present_in_completed_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection_path, final_path, selection = self._fixture(root)
            protocol = self._protocol(selection)
            with (
                patch.object(capital_staging, "PROJECT_ROOT", root),
                patch.object(capital_staging, "DATA_PROFILE_ID", "capital-bikeshare"),
                patch.object(capital_staging, "MODEL_NAME", "bike-demand-capital"),
                patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                patch.object(capital_staging, "read_protocol", return_value=protocol),
                patch.object(capital_staging, "read_ledger", return_value={"schema_version": 1, "records": []}),
            ):
                with self.assertRaisesRegex(ValueError, "no unique consumption-ledger record"):
                    capital_staging.verify_candidate_for_staging(selection_path, final_path)

    def test_staging_registry_requires_non_deployable_tags_and_exact_candidate(self):
        evidence = {
            "candidate_id": "a" * 64,
            "candidate_artifact_sha256": "b" * 64,
            "selection_report_sha256": "c" * 64,
            "final_report_sha256": "d" * 64,
            "evaluation_protocol_sha256": "e" * 64,
            "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
            "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
            "selection": {"data_sha256": "f" * 64, "feature_contract_version": "bike-demand-features-v1"},
        }
        with patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
            tags = capital_staging._registration_tags(evidence)
            capital_staging.validate_staging_model_tags(
                tags,
                {
                    "candidate_id": evidence["candidate_id"],
                    "candidate_model_sha256": evidence["candidate_artifact_sha256"],
                    "candidate_report_sha256": evidence["selection_report_sha256"],
                    "final_report_sha256": evidence["final_report_sha256"],
                },
            )
        self.assertEqual(tags["target_contract_id"], "capital-trip-starts-hourly-v1")
        self.assertEqual(tags["deployment_permission"], "blocked")
        self.assertEqual(tags["operational_gate_status"], "not_evaluated")
        with self.assertRaisesRegex(ValueError, "deployment_permission"):
            with patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
                capital_staging.validate_staging_model_tags(
                    {**tags, "deployment_permission": "eligible"},
                    {"candidate_id": evidence["candidate_id"]},
                )
        with self.assertRaisesRegex(ValueError, "candidate_id"):
            with patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
                capital_staging.validate_staging_model_tags(tags, {"candidate_id": "0" * 64})

    def test_staging_loader_loads_only_the_pinned_blocked_registry_artifact(self):
        import mlflow
        import mlflow.artifacts
        import mlflow.sklearn
        import mlflow.tracking

        candidate_id = "a" * 64
        candidate_sha = "b" * 64
        selection_sha = "c" * 64
        final_sha = "d" * 64
        protocol_sha = "e" * 64
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "registered-model"
            model_path.mkdir()
            (model_path / "MLmodel").write_text("synthetic MLflow model metadata\n", encoding="utf-8")
            artifact_sha = capital_staging.directory_sha256(model_path)
            evidence = {
                "candidate_id": candidate_id,
                "candidate_artifact_sha256": candidate_sha,
                "selection_report_sha256": selection_sha,
                "final_report_sha256": final_sha,
                "evaluation_protocol_sha256": protocol_sha,
                "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
                "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
                "selection": {"data_sha256": "f" * 64, "feature_contract_version": "bike-demand-features-v1"},
            }
            tags = capital_staging._registration_tags(evidence)
            tags["target_contract_id"] = "capital-trip-starts-hourly-v1"
            tags["artifact_sha256"] = artifact_sha
            environment = {
                "DATA_PROFILE_ID": "capital-bikeshare",
                "MLFLOW_TRACKING_URI": "http://mlflow:5000",
                "MODEL_VERSION": "9",
                "CAPITAL_STAGING_MODEL_VERSION": "9",
                "CAPITAL_STAGING_CANDIDATE_ID": candidate_id,
                "CAPITAL_STAGING_CANDIDATE_ARTIFACT_SHA256": candidate_sha,
                "CAPITAL_STAGING_SELECTION_REPORT_SHA256": selection_sha,
                "CAPITAL_STAGING_FINAL_REPORT_SHA256": final_sha,
                "CAPITAL_STAGING_EVALUATION_PROTOCOL_SHA256": protocol_sha,
                "CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256": artifact_sha,
            }
            model = object()
            with (
                patch.object(capital_staging, "DATA_PROFILE_ID", "capital-bikeshare"),
                patch.object(capital_staging, "MODEL_NAME", "bike-demand-capital"),
                patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                patch.dict(os.environ, environment),
                patch.object(mlflow, "set_tracking_uri"),
                patch.object(mlflow.tracking, "MlflowClient") as client_class,
                patch.object(mlflow.artifacts, "download_artifacts", return_value=str(model_path)),
                patch.object(mlflow.sklearn, "load_model", return_value=model),
            ):
                client_class.return_value.get_model_version.return_value = SimpleNamespace(
                    status="READY", tags=tags
                )
                result = capital_staging.load_staging_registered_model()
        self.assertIs(result[0], model)
        self.assertEqual(result[1:4], ("9", artifact_sha, "passed"))
        self.assertIsNone(result[4])

    def test_staging_loader_rejects_tampered_registered_artifact(self):
        import mlflow
        import mlflow.artifacts
        import mlflow.tracking

        evidence = {
            "candidate_id": "a" * 64,
            "candidate_artifact_sha256": "b" * 64,
            "selection_report_sha256": "c" * 64,
            "final_report_sha256": "d" * 64,
            "evaluation_protocol_sha256": "e" * 64,
            "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
            "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
            "selection": {"data_sha256": "f" * 64, "feature_contract_version": "bike-demand-features-v1"},
        }
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "registered-model"
            model_path.mkdir()
            (model_path / "MLmodel").write_text("tampered artifact\n", encoding="utf-8")
            tags = capital_staging._registration_tags(evidence)
            tags["target_contract_id"] = "capital-trip-starts-hourly-v1"
            tags["artifact_sha256"] = "0" * 64
            environment = {
                "DATA_PROFILE_ID": "capital-bikeshare",
                "MLFLOW_TRACKING_URI": "http://mlflow:5000",
                "MODEL_VERSION": "9",
                "CAPITAL_STAGING_MODEL_VERSION": "9",
                "CAPITAL_STAGING_CANDIDATE_ID": evidence["candidate_id"],
                "CAPITAL_STAGING_CANDIDATE_ARTIFACT_SHA256": evidence["candidate_artifact_sha256"],
                "CAPITAL_STAGING_SELECTION_REPORT_SHA256": evidence["selection_report_sha256"],
                "CAPITAL_STAGING_FINAL_REPORT_SHA256": evidence["final_report_sha256"],
                "CAPITAL_STAGING_EVALUATION_PROTOCOL_SHA256": evidence["evaluation_protocol_sha256"],
                "CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256": tags["artifact_sha256"],
            }
            with (
                patch.object(capital_staging, "DATA_PROFILE_ID", "capital-bikeshare"),
                patch.object(capital_staging, "MODEL_NAME", "bike-demand-capital"),
                patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                patch.dict(os.environ, environment),
                patch.object(mlflow, "set_tracking_uri"),
                patch.object(mlflow.tracking, "MlflowClient") as client_class,
                patch.object(mlflow.artifacts, "download_artifacts", return_value=str(model_path)),
            ):
                client_class.return_value.get_model_version.return_value = SimpleNamespace(
                    status="READY", tags=tags
                )
                with self.assertRaisesRegex(ValueError, "artifact SHA-256"):
                    capital_staging.load_staging_registered_model()

    def test_staging_api_exposes_pinned_candidate_as_blocked(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI/httpx development dependencies are not installed")

        from bike_mlops import api
        from bike_mlops import staging_api

        candidate_id = "c" * 64

        class FixedModel:
            def predict(self, _features):
                return [3.0]

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(
                    os.environ,
                    {
                        "DATABASE_URL": f"sqlite:///{Path(directory) / 'staging.sqlite'}",
                        "CAPITAL_STAGING_CANDIDATE_ID": candidate_id,
                    },
                ),
                patch.object(staging_api, "DATA_PROFILE_ID", "capital-bikeshare"),
                patch.object(staging_api, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                patch.object(staging_api, "_staging_runtime_model", return_value=(FixedModel(), "9", "a" * 64, "passed", None)),
                patch.object(api, "DATA_PROFILE_ID", "capital-bikeshare"),
                patch.object(api, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                patch.object(api, "MODEL_NAME", "bike-demand-capital"),
            ):
                with TestClient(staging_api.app) as client:
                    version = client.get("/version").json()
                    ready = client.get("/ready").json()
                    prediction = client.post(
                        "/predict",
                        json={
                            "request_id": "staging-candidate-test-001",
                            "target_hour": "2026-04-01T10:00:00",
                            "as_of": "2026-04-01T10:00:00",
                            "feature_contract_version": "bike-demand-features-v1",
                            "data_profile_id": "capital-bikeshare",
                            "target_contract_id": "capital-trip-starts-hourly-v1",
                        },
                    ).json()
        self.assertEqual(version["candidate_id"], candidate_id)
        self.assertEqual(version["deployment_permission"], "blocked")
        self.assertEqual(ready["candidate_id"], candidate_id)
        self.assertEqual(ready["deployment_permission"], "blocked")
        self.assertEqual(prediction["candidate_id"], candidate_id)


if __name__ == "__main__":
    unittest.main()
