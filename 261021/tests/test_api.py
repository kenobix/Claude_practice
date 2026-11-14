"""Contract and regression tests: test_api."""
from __future__ import annotations


# ========================================================================
# test_api
# ========================================================================

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "FastAPI/httpx development dependencies are not installed")
class PredictionApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_url = f"sqlite:///{Path(self.tempdir.name) / 'api.sqlite'}"

        class FixedModel:
            def predict(self, features):
                return [float(features.iloc[0]["lag_24"] if features.iloc[0]["lag_24_missing"] == 0 else 42)]

        self.environment = patch.dict(
            os.environ,
            {"DATABASE_URL": self.database_url, "MODEL_VERSION": "7"},
        )
        self.environment.start()
        from bike_mlops import api

        self.api = api
        self.model_patch = patch.object(
            api, "_runtime_model", return_value=(FixedModel(), "7", "artifact-test-sha", "passed", None)
        )
        self.model_patch.start()
        self.client_context = TestClient(api.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.model_patch.stop()
        self.environment.stop()
        self.tempdir.cleanup()

    def test_request_retry_returns_same_persisted_version_and_changed_input_conflicts(self):
        observed_at = "2012-06-30T10:00:00"
        accepted = self.client.post(
            "/observations",
            json={"timestamp": observed_at, "cnt": 123, "available_at": "2012-06-30T13:00:00"},
        )
        self.assertEqual(accepted.status_code, 202)
        payload = {
            "request_id": "request-test-001",
            "target_hour": "2012-07-01T10:00:00",
            "as_of": "2012-07-01T10:00:00",
            "feature_contract_version": "bike-demand-features-v1",
        }
        first = self.client.post("/predict", json=payload)
        # A retry after a restart must return its original model version without reloading that model.
        self.client.app.state.model = None
        retried = self.client.post("/predict", json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(first.json(), retried.json())
        self.assertEqual(first.json()["prediction"], 123.0)
        changed = {**payload, "target_hour": "2012-07-01T11:00:00", "as_of": "2012-07-01T11:00:00"}
        self.assertEqual(self.client.post("/predict", json=changed).status_code, 409)

    def test_a_prediction_is_not_successful_when_audit_write_fails(self):
        def fail_record(_record):
            raise OSError("simulated database write failure")

        self.client.app.state.database.save_prediction = fail_record
        payload = {
            "request_id": "request-test-002",
            "target_hour": "2012-07-01T10:00:00",
            "as_of": "2012-07-01T10:00:00",
            "feature_contract_version": "bike-demand-features-v1",
        }
        response = self.client.post("/predict", json=payload)
        self.assertEqual(response.status_code, 503)

    def test_api_publishes_profile_and_rejects_a_foreign_profile(self):
        version = self.client.get("/version").json()
        self.assertEqual(version["data_profile_id"], "uci-bike-sharing")
        self.assertEqual(
            version["target_contract_id"], "uci-bike-sharing-cnt-dataset-local-hour"
        )
        response = self.client.post(
            "/predict",
            json={
                "request_id": "foreign-profile-001",
                "target_hour": "2012-07-01T10:00:00",
                "as_of": "2012-07-01T10:00:00",
                "feature_contract_version": "bike-demand-features-v1",
                "data_profile_id": "capital-bikeshare",
                "target_contract_id": "capital-trip-starts-hourly-v1",
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_old_uci_idempotency_hash_remains_valid_after_profile_migration(self):
        from bike_mlops.db import stable_hash

        payload = {
            "request_id": "legacy-request-001",
            "target_hour": "2012-07-01T10:00:00",
            "as_of": "2012-07-01T10:00:00",
            "feature_contract_version": "bike-demand-features-v1",
        }
        old_hash = stable_hash(
            {
                "target_hour": "2012-07-01 10:00:00",
                "as_of": "2012-07-01 10:00:00",
                "feature_contract_version": "bike-demand-features-v1",
            }
        )
        self.client.app.state.database.save_prediction(
            {
                "request_id": payload["request_id"],
                "input_hash": old_hash,
                "target_hour": "2012-07-01 10:00:00",
                "as_of": "2012-07-01 10:00:00",
                "predicted": 88.0,
                "model_name": "bike-demand",
                "model_version": "6",
                "model_quality_gate": "passed",
                "artifact_sha256": "old-artifact-sha",
                "data_version_sha256": "old-data-sha",
                "feature_contract_version": "bike-demand-features-v1",
                "features_json": "{}",
                "created_at": "2012-07-01T10:00:00+00:00",
            }
        )
        response = self.client.post("/predict", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["prediction"], 88.0)
        self.assertEqual(response.json()["data_profile_id"], "uci-bike-sharing")

    def test_capital_profile_rejects_nonexistent_and_ambiguous_wall_hours(self):
        from datetime import datetime

        from fastapi import HTTPException

        with patch.object(self.api, "DATA_PROFILE", {"target": {"timezone_name": "America/New_York"}}):
            with self.assertRaises(HTTPException) as spring:
                self.api._validate_wall_clock_timestamp(datetime(2024, 3, 10, 2))
            with self.assertRaises(HTTPException) as autumn:
                self.api._validate_wall_clock_timestamp(datetime(2024, 11, 3, 1))
            self.api._validate_wall_clock_timestamp(datetime(2024, 3, 10, 3))
        self.assertEqual(spring.exception.status_code, 422)
        self.assertEqual(autumn.exception.status_code, 422)


# ========================================================================
# test_capital_staging
# ========================================================================

import hashlib
import json
import os as _capital_staging_os
import tarfile
import tempfile as _capital_staging_tempfile
import unittest as _capital_staging_unittest
from pathlib import Path as _capital_staging_Path
from types import SimpleNamespace
from unittest.mock import patch as _capital_staging_patch

from bike_mlops import staging as capital_staging
from bike_mlops.evaluation import candidate_identity, sha256_file
from bike_mlops.evaluation import BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
from bike_mlops.evaluation import baseline_accuracy_gate


class CapitalStagingTests(_capital_staging_unittest.TestCase):
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
        with _capital_staging_tempfile.TemporaryDirectory() as directory:
            root = _capital_staging_Path(directory)
            selection_path, final_path, selection = self._fixture(root)
            fake_protocol = self._protocol(selection)
            with (
                _capital_staging_patch.object(capital_staging, "PROJECT_ROOT", root),
                _capital_staging_patch.object(capital_staging, "DATA_PROFILE_ID", "capital-bikeshare"),
                _capital_staging_patch.object(capital_staging, "MODEL_NAME", "bike-demand-capital"),
                _capital_staging_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                _capital_staging_patch.object(capital_staging, "read_protocol", return_value=fake_protocol),
                _capital_staging_patch.object(
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
        with _capital_staging_tempfile.TemporaryDirectory() as directory:
            root = _capital_staging_Path(directory)
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
                _capital_staging_patch.object(capital_staging, "PROJECT_ROOT", root),
                _capital_staging_patch.object(capital_staging, "DATA_PROFILE_ID", "capital-bikeshare"),
                _capital_staging_patch.object(capital_staging, "MODEL_NAME", "bike-demand-capital"),
                _capital_staging_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                _capital_staging_patch.object(capital_staging, "read_protocol", return_value=protocol),
                _capital_staging_patch.object(
                    capital_staging, "read_ledger",
                    return_value=self._ledger(selection, final_path, protocol),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "does not match its saved row-level evidence"):
                    capital_staging.verify_candidate_for_staging(selection_path, final_path)

    def test_staging_rejects_final_report_not_present_in_completed_ledger(self):
        with _capital_staging_tempfile.TemporaryDirectory() as directory:
            root = _capital_staging_Path(directory)
            selection_path, final_path, selection = self._fixture(root)
            protocol = self._protocol(selection)
            with (
                _capital_staging_patch.object(capital_staging, "PROJECT_ROOT", root),
                _capital_staging_patch.object(capital_staging, "DATA_PROFILE_ID", "capital-bikeshare"),
                _capital_staging_patch.object(capital_staging, "MODEL_NAME", "bike-demand-capital"),
                _capital_staging_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                _capital_staging_patch.object(capital_staging, "read_protocol", return_value=protocol),
                _capital_staging_patch.object(capital_staging, "read_ledger", return_value={"schema_version": 1, "records": []}),
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
        with _capital_staging_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
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
            with _capital_staging_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
                capital_staging.validate_staging_model_tags(
                    {**tags, "deployment_permission": "eligible"},
                    {"candidate_id": evidence["candidate_id"]},
                )
        with self.assertRaisesRegex(ValueError, "candidate_id"):
            with _capital_staging_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
                capital_staging.validate_staging_model_tags(tags, {"candidate_id": "0" * 64})

    def test_registration_pins_registry_uri_even_if_environment_selects_another(self):
        import mlflow
        import mlflow.artifacts
        import mlflow.tracking

        with _capital_staging_tempfile.TemporaryDirectory() as directory:
            root = _capital_staging_Path(directory)
            model_dir = root / "model"
            model_dir.mkdir()
            (model_dir / "MLmodel").write_text("synthetic metadata\n")
            evidence = {
                "candidate_id": "a" * 64, "candidate_artifact_sha256": "b" * 64,
                "selection_report_sha256": "c" * 64, "final_report_sha256": "d" * 64,
                "evaluation_protocol_sha256": "e" * 64,
                "candidate_artifact_path": root / "raw.skops",
                "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
                "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
                "selection": {"data_sha256": "f" * 64, "feature_contract_version": "bike-demand-features-v1"},
            }
            with (_capital_staging_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                  _capital_staging_patch.object(capital_staging, "PROJECT_ROOT", root)):
                tags = capital_staging._registration_tags(evidence)
                tags["artifact_sha256"] = capital_staging.directory_sha256(model_dir)
                registered = SimpleNamespace(tags=tags, status="READY", version="7")
                with (_capital_staging_patch.dict(_capital_staging_os.environ, {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5001",
                                              "MLFLOW_REGISTRY_URI": "http://other.invalid"}),
                      _capital_staging_patch.object(capital_staging, "verify_candidate_for_staging", return_value=evidence),
                      _capital_staging_patch.object(capital_staging, "load_candidate_model", return_value=(object(), "b" * 64)),
                      _capital_staging_patch.object(capital_staging, "_write_staging_environment", return_value=root / "staging.env"),
                      _capital_staging_patch.object(mlflow, "set_tracking_uri"), _capital_staging_patch.object(mlflow, "set_registry_uri") as registry_setter,
                      _capital_staging_patch.object(mlflow.tracking, "MlflowClient") as factory,
                      _capital_staging_patch.object(mlflow.artifacts, "download_artifacts", return_value=str(model_dir))):
                    factory.return_value.search_model_versions.return_value = [registered]
                    factory.return_value.get_model_version.return_value = registered
                    result = capital_staging.register_blocked_capital_candidate(root / "selection", root / "final")
                    self.assertEqual(result["model_version"], "7")
                    registry_setter.assert_called_once_with("http://127.0.0.1:5001")
                    factory.assert_called_once_with(tracking_uri="http://127.0.0.1:5001",
                                                   registry_uri="http://127.0.0.1:5001")

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
        with _capital_staging_tempfile.TemporaryDirectory() as directory:
            model_path = _capital_staging_Path(directory) / "registered-model"
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
                _capital_staging_patch.object(capital_staging, "DATA_PROFILE_ID", "capital-bikeshare"),
                _capital_staging_patch.object(capital_staging, "MODEL_NAME", "bike-demand-capital"),
                _capital_staging_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                _capital_staging_patch.dict(_capital_staging_os.environ, environment),
                _capital_staging_patch.object(mlflow, "set_tracking_uri"),
                _capital_staging_patch.object(mlflow, "set_registry_uri") as registry_setter,
                _capital_staging_patch.object(mlflow.tracking, "MlflowClient") as client_class,
                _capital_staging_patch.object(mlflow.artifacts, "download_artifacts", return_value=str(model_path)),
                _capital_staging_patch.object(mlflow.sklearn, "load_model", return_value=model),
            ):
                client_class.return_value.get_model_version.return_value = SimpleNamespace(
                    status="READY", tags=tags
                )
                result = capital_staging.load_staging_registered_model()
                registry_setter.assert_called_once_with("http://mlflow:5000")
                client_class.assert_called_once_with(tracking_uri="http://mlflow:5000", registry_uri="http://mlflow:5000")
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
        with _capital_staging_tempfile.TemporaryDirectory() as directory:
            model_path = _capital_staging_Path(directory) / "registered-model"
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
                _capital_staging_patch.object(capital_staging, "DATA_PROFILE_ID", "capital-bikeshare"),
                _capital_staging_patch.object(capital_staging, "MODEL_NAME", "bike-demand-capital"),
                _capital_staging_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                _capital_staging_patch.dict(_capital_staging_os.environ, environment),
                _capital_staging_patch.object(mlflow, "set_tracking_uri"),
                _capital_staging_patch.object(mlflow, "set_registry_uri"),
                _capital_staging_patch.object(mlflow.tracking, "MlflowClient") as client_class,
                _capital_staging_patch.object(mlflow.artifacts, "download_artifacts", return_value=str(model_path)),
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
        from bike_mlops import api as staging_api

        candidate_id = "c" * 64

        class FixedModel:
            def predict(self, _features):
                return [3.0]

        with _capital_staging_tempfile.TemporaryDirectory() as directory:
            with (
                _capital_staging_patch.dict(
                    _capital_staging_os.environ,
                    {
                        "DATABASE_URL": f"sqlite:///{_capital_staging_Path(directory) / 'staging.sqlite'}",
                        "CAPITAL_STAGING_CANDIDATE_ID": candidate_id,
                    },
                ),
                _capital_staging_patch.object(staging_api, '_staging_api_DATA_PROFILE_ID', "capital-bikeshare"),
                _capital_staging_patch.object(staging_api, '_staging_api_TARGET_CONTRACT_ID', "capital-trip-starts-hourly-v1"),
                _capital_staging_patch.object(staging_api, "_staging_runtime_model", return_value=(FixedModel(), "9", "a" * 64, "passed", None)),
                _capital_staging_patch.object(api, "DATA_PROFILE_ID", "capital-bikeshare"),
                _capital_staging_patch.object(api, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
                _capital_staging_patch.object(api, "MODEL_NAME", "bike-demand-capital"),
            ):
                with TestClient(staging_api._staging_api_app) as client:
                    version = client.get("/version").json()
                    ready = client.get("/ready").json()
                    payload = {
                        "request_id": "staging-candidate-test-001",
                        "target_hour": "2026-04-01T10:00:00",
                        "as_of": "2026-04-01T10:00:00",
                        "feature_contract_version": "bike-demand-features-v1",
                        "data_profile_id": "capital-bikeshare",
                        "target_contract_id": "capital-trip-starts-hourly-v1",
                    }
                    prediction = client.post("/predict", json=payload).json()
                    # A retry must retain the identity stored with the original prediction,
                    # even after the service has loaded a different candidate.
                    client.app.state.candidate_id = "d" * 64
                    client.app.state.model_version = "10"
                    client.app.state.artifact_hash = "b" * 64
                    retry = client.post("/predict", json=payload).json()
        self.assertEqual(version["candidate_id"], candidate_id)
        self.assertEqual(version["deployment_permission"], "blocked")
        self.assertEqual(ready["candidate_id"], candidate_id)
        self.assertEqual(ready["deployment_permission"], "blocked")
        self.assertEqual(prediction["candidate_id"], candidate_id)
        self.assertEqual(prediction["deployment_permission"], "blocked")
        self.assertEqual(retry, prediction)


# ========================================================================
# test_capital_staging_seed
# ========================================================================

import tempfile as _capital_staging_seed_tempfile
import importlib.util
import io
import os as _capital_staging_seed_os
import sys
import unittest as _capital_staging_seed_unittest
from contextlib import redirect_stdout
from pathlib import Path as _capital_staging_seed_Path
from types import SimpleNamespace as _capital_staging_seed_SimpleNamespace
from unittest.mock import patch as _capital_staging_seed_patch

import pandas as pd

from bike_mlops import staging as capital_staging_seed
from bike_mlops.evaluation import sha256_file as _capital_staging_seed_sha256_file
from bike_mlops.staging import BOOTSTRAP_QUALITY_GATE_POLICY as _capital_staging_seed_BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256 as _capital_staging_seed_BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
from bike_mlops.config import TARGET_CONTRACT_ID
from bike_mlops.db import Database
from bike_mlops.data import available_observation_frame


class StagingSeedTests(_capital_staging_seed_unittest.TestCase):
    def setUp(self):
        self.temp = _capital_staging_seed_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = _capital_staging_seed_Path(self.temp.name)
        self.csv = root / "source.csv"
        self.manifest = root / "manifest.json"
        self.csv.write_text("fixture csv", encoding="utf-8")
        self.manifest.write_text("fixture manifest", encoding="utf-8")
        self.data_hash = _capital_staging_seed_sha256_file(self.csv)
        self.frame = pd.DataFrame({"timestamp": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 01:00"]),
                                   "cnt": [3, 4]})
        self.evidence = {"candidate_id": "a" * 64, "selection_report_sha256": "b" * 64,
                         "selection": {"data_sha256": self.data_hash,
                                       "data_source": {"source_manifest_sha256": _capital_staging_seed_sha256_file(self.manifest)}}}
        self.tags = {"data_profile_id": "capital-bikeshare", "target_contract_id": TARGET_CONTRACT_ID,
                     "quality_gate": "passed", "validation_gate_status": "passed",
                     "final_accuracy_gate_status": "passed", "operational_gate_status": "not_evaluated",
                     "deployment_permission": "blocked", "quality_gate_policy_id": _capital_staging_seed_BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
                     "quality_gate_policy_sha256": _capital_staging_seed_BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
                     "candidate_id": self.evidence["candidate_id"], "data_sha256": self.data_hash}
        self.registered = _capital_staging_seed_SimpleNamespace(status="READY", tags=self.tags, version="7")
        self.db = Database(f"sqlite:///{root / 'staging.sqlite'}")
        self.db.initialize()

    def seed(self):
        expected = {"candidate_id": self.evidence["candidate_id"], "data_sha256": self.data_hash}
        with _capital_staging_seed_patch.object(capital_staging_seed, '_staging_seed_registration_tags', return_value=expected):
            return capital_staging_seed.seed_verified_candidate(
                self.db, self.frame, self.csv, self.manifest, self.evidence, self.registered)

    def rows(self):
        with self.db.session() as connection:
            return [dict(row) for row in self.db.execute(connection, "SELECT * FROM observations ORDER BY timestamp").fetchall()]

    def test_seed_is_idempotent_and_identity_is_reported(self):
        result = self.seed()
        self.assertEqual(result["new_observations_inserted"], 2)
        self.assertEqual(result["model_version"], "7")
        self.assertEqual(self.seed()["new_observations_inserted"], 0)
        self.assertEqual(len(self.rows()), 2)

    def test_registry_and_source_mismatch_do_not_write(self):
        for change in ("registry", "csv", "manifest", "status"):
            with self.subTest(change=change):
                original_tags, original_status = self.tags.copy(), self.registered.status
                if change == "registry": self.tags["data_sha256"] = "c" * 64
                if change == "csv": self.csv.write_text("changed", encoding="utf-8")
                if change == "manifest": self.manifest.write_text("changed", encoding="utf-8")
                if change == "status": self.registered.status = "FAILED_REGISTRATION"
                with self.assertRaises(ValueError): self.seed()
                self.assertEqual(self.rows(), [])
                self.tags.clear(); self.tags.update(original_tags)
                self.registered.status = original_status
                self.csv.write_text("fixture csv", encoding="utf-8")
                self.manifest.write_text("fixture manifest", encoding="utf-8")

    def test_existing_extra_or_changed_rows_are_rejected_without_partial_writes(self):
        for timestamp, count, source_hash in [("2023-01-01 00:00:00", 9, self.data_hash),
                                               ("2024-01-01 00:00:00", 9, self.data_hash),
                                               ("2024-01-01 00:00:00", 3, "c" * 64)]:
            with self.subTest(timestamp=timestamp, count=count, source_hash=source_hash):
                with self.db.transaction() as connection:
                    self.db.execute(connection, "DELETE FROM observations")
                self.db.add_observation(timestamp, count, pd.Timestamp(timestamp) + pd.Timedelta(hours=3), source_hash)
                before = self.rows()
                with self.assertRaisesRegex(ValueError, "differs from candidate dataset"): self.seed()
                self.assertEqual(self.rows(), before)

    def test_verify_requires_exact_complete_snapshot_without_writes(self):
        observations = available_observation_frame(self.frame)
        with self.assertRaisesRegex(ValueError, "missing rows"):
            self.db.verify_staging_observations(observations, self.data_hash)
        self.seed()
        self.assertEqual(self.db.verify_staging_observations(observations, self.data_hash), 2)
        expected = {"candidate_id": self.evidence["candidate_id"], "data_sha256": self.data_hash}
        with _capital_staging_seed_patch.object(capital_staging_seed, '_staging_seed_registration_tags', return_value=expected):
            result = capital_staging_seed.verify_seeded_candidate(
                self.db, self.frame, self.csv, self.manifest, self.evidence, self.registered)
            self.assertEqual(result["verified_rows"], 2)
            self.tags["candidate_id"] = "d" * 64
            with self.assertRaises(ValueError):
                capital_staging_seed.verify_seeded_candidate(
                    self.db, self.frame, self.csv, self.manifest, self.evidence, self.registered)
            self.tags["candidate_id"] = self.evidence["candidate_id"]
        for statement in (
            "DELETE FROM observations WHERE timestamp='2024-01-01 00:00:00'",
            "UPDATE observations SET cnt=cnt+1 WHERE timestamp='2024-01-01 00:00:00'",
            "UPDATE observations SET data_hash='wrong' WHERE timestamp='2024-01-01 00:00:00'",
            "UPDATE observations SET available_at='2024-01-02 00:00:00' WHERE timestamp='2024-01-01 00:00:00'",
            "INSERT INTO observations VALUES ('2023-01-01 00:00:00',1,'2023-01-01 03:00:00','extra')",
        ):
            with self.subTest(statement=statement):
                with self.db.transaction() as connection:
                    self.db.execute(connection, statement)
                before = self.rows()
                with self.assertRaises(ValueError):
                    self.db.verify_staging_observations(observations, self.data_hash)
                self.assertEqual(before, self.rows())
                with self.db.transaction() as connection:
                    self.db.execute(connection, "DELETE FROM observations")
                self.seed()

    def test_seed_cli_pins_registry_uri_independently_of_environment(self):
        script = _capital_staging_seed_Path(__file__).resolve().parents[1] / 'scripts/runtime_tools.py'
        spec = importlib.util.spec_from_file_location("capital_seed_cli_for_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        evidence = {"selection": {"data_sha256": "a" * 64,
                                  "data_source": {"source_manifest_sha256": "b" * 64}}}
        argv = ["seed", "--selection-report", "candidate.json", "--final-report", "final.json",
                "--model-version", "7"]
        with (_capital_staging_seed_patch.object(module, "DATA_PROFILE_ID", "capital-bikeshare"),
              _capital_staging_seed_patch.object(module, "verify_candidate_for_staging", return_value=evidence),
              _capital_staging_seed_patch.object(module, "sha256_file", side_effect=["a" * 64, "b" * 64]),
              _capital_staging_seed_patch.object(module, "_load_profile_hourly", return_value=self.frame),
              _capital_staging_seed_patch.object(module, "Database"), _capital_staging_seed_patch.object(module, "seed_verified_candidate", return_value={}),
              _capital_staging_seed_patch("mlflow.tracking.MlflowClient") as factory,
              _capital_staging_seed_patch.dict(_capital_staging_seed_os.environ, {"APP_DB_PASSWORD": "synthetic", "MLFLOW_TRACKING_URI": "http://127.0.0.1:5001",
                                      "MLFLOW_REGISTRY_URI": "http://other.invalid"}),
              _capital_staging_seed_patch.object(sys, "argv", argv), redirect_stdout(io.StringIO())):
            module._seed_capital_staging_main()
            factory.assert_called_once_with(tracking_uri="http://127.0.0.1:5001", registry_uri="http://127.0.0.1:5001")


if __name__ == "__main__":
    import unittest as _unittest_entry
    _unittest_entry.main()
