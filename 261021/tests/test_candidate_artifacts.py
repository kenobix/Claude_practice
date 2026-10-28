from __future__ import annotations

import json
import ast
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from bike_mlops.candidate_artifacts import (
    _write_source_snapshot,
    candidate_identity,
    load_candidate_model,
    persist_candidate_bundle,
    sha256_file,
    source_snapshot_inventory,
    source_tree_inventory,
    source_tree_sha256,
)
from bike_mlops.training import build_estimator
from bike_mlops.features import FEATURE_COLUMNS
from bike_mlops.features import feature_frame_for_dataset
from bike_mlops.quality_gates import baseline_accuracy_gate
from bike_mlops.training import HistoricalBaseline, regression_metrics, segment_comparison, segment_mae
from scripts.evaluate_capitalbikeshare_holdout import _verify_validation_evidence


def _test_row_hash(frame: pd.DataFrame) -> str:
    payload = "".join(
        f"{timestamp.isoformat(sep=' ')}\t{int(target)}\n"
        for timestamp, target in zip(frame["timestamp"], frame["cnt"])
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CandidateArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _fitted_model(self):
        x = pd.DataFrame(
            {
                "hour": np.arange(48) % 24,
                "weekday": np.arange(48) % 7,
                "month": np.ones(48),
                "day_of_year": np.arange(48) + 1,
                "is_weekend": (np.arange(48) % 7 >= 5).astype(float),
                "lag_24": np.arange(48, dtype=float),
                "lag_24_missing": np.zeros(48),
                "lag_168": np.arange(48, dtype=float),
                "lag_168_missing": np.zeros(48),
            },
            columns=FEATURE_COLUMNS,
        )
        model = build_estimator().fit(x, np.arange(48, dtype=float) * 2)
        return model, x.iloc[-5:].copy()

    def test_skops_artifact_is_hash_checked_and_predictions_round_trip(self):
        model, x = self._fitted_model()
        import skops.io as sio

        path = self.root / "model.skops"
        sio.dump(model, path)
        expected_hash = sha256_file(path)
        restored, actual_hash = load_candidate_model(path, expected_hash)
        np.testing.assert_allclose(model.predict(x), restored.predict(x), rtol=0, atol=0)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            load_candidate_model(path, "0" * 64)

    def test_two_fits_on_the_same_development_data_reproduce_predictions(self):
        first, x = self._fitted_model()
        second, _ = self._fitted_model()
        np.testing.assert_allclose(first.predict(x), second.predict(x), rtol=0, atol=0)

    def test_source_snapshot_is_deterministic_and_excludes_local_data_and_env(self):
        source = self.root / "source"
        (source / "src").mkdir(parents=True)
        (source / "data").mkdir()
        (source / "src" / "app.py").write_text("value = 1\n")
        (source / ".env").write_text("SECRET=not-for-snapshot\n")
        (source / ".env.production").write_text("SECRET=also-not-for-snapshot\n")
        (source / "data" / "raw.csv").write_text("private,local,data\n")
        first = self.root / "first.tar.gz"
        second = self.root / "second.tar.gz"
        _write_source_snapshot(first, source)
        _write_source_snapshot(second, source)
        self.assertEqual(sha256_file(first), sha256_file(second))
        import tarfile

        with tarfile.open(first, "r:gz") as archive:
            names = archive.getnames()
        self.assertIn("src/app.py", names)
        self.assertNotIn(".env", names)
        self.assertNotIn(".env.production", names)
        self.assertFalse(any(name.startswith("data/") for name in names))
        self.assertEqual(source_snapshot_inventory(first), source_tree_inventory(source))

    def test_reserved_evaluation_script_never_fits_the_candidate(self):
        script = Path(__file__).resolve().parents[1] / "scripts/evaluate_capitalbikeshare_holdout.py"
        tree = ast.parse(script.read_text(encoding="utf-8"))
        candidate_fit_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fit"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "candidate"
        ]
        self.assertEqual(candidate_fit_calls, [])

    def test_final_evaluation_recomputes_validation_predictions_and_gate(self):
        timestamps = pd.date_range("2024-01-01", periods=24 * 10, freq="h")
        frame = pd.DataFrame({"timestamp": timestamps, "cnt": np.arange(len(timestamps), dtype=int)})
        validation_start = pd.Timestamp("2024-01-07 00:00:00")
        validation_end = pd.Timestamp("2024-01-08 00:00:00")
        cutoff = pd.Timestamp("2024-01-07 03:00:00")
        train_mask = (frame["timestamp"] < validation_start) & (
            frame["timestamp"] + pd.Timedelta(hours=3) <= cutoff
        )
        validation_mask = (frame["timestamp"] >= validation_start) & (frame["timestamp"] < validation_end)
        validation = frame.loc[validation_mask].reset_index(drop=True)
        features = feature_frame_for_dataset(frame)

        class FixedPredictions:
            def __init__(self, values):
                self.values = np.asarray(values, dtype=float)

            def predict(self, rows):
                if len(rows) != len(self.values):
                    raise AssertionError("unexpected prediction row count")
                return self.values

        candidate = FixedPredictions(validation["cnt"].to_numpy())
        incumbent = FixedPredictions(np.full(len(validation), 500.0))
        candidate_predictions = candidate.predict(features.loc[validation_mask, FEATURE_COLUMNS])
        incumbent_predictions = incumbent.predict(features.loc[validation_mask, FEATURE_COLUMNS])
        baseline_model = HistoricalBaseline().fit(frame.loc[train_mask].reset_index(drop=True), frame)
        baseline_predictions = baseline_model.predict(validation["timestamp"])
        evidence = pd.DataFrame({
            "timestamp": validation["timestamp"].map(lambda stamp: stamp.isoformat(sep=" ")),
            "label_available_at": (validation["timestamp"] + pd.Timedelta(hours=3)).map(
                lambda stamp: stamp.isoformat(sep=" ")
            ),
            "actual": validation["cnt"].astype(int),
            "candidate_prediction": candidate_predictions,
            "incumbent_prediction": incumbent_predictions,
            "baseline_prediction": baseline_predictions,
        })
        path = self.root / "validation-predictions.csv"
        evidence.to_csv(path, index=False, float_format="%.17g")
        validation_metrics = {
            "model": regression_metrics(validation["cnt"], candidate_predictions),
            "baseline": regression_metrics(validation["cnt"], baseline_predictions),
            "model_by_segment": segment_mae(validation, candidate_predictions),
            "baseline_by_segment": segment_mae(validation, baseline_predictions),
        }
        incumbent_metrics = regression_metrics(validation["cnt"], incumbent_predictions)
        protocol = {
            "incumbent": {
                "model_version": "1",
                "artifact_sha256": "a" * 64,
                "quality_gate": "passed",
            },
            "training": {"cutoff": cutoff.isoformat(sep=" "), "rows": int(train_mask.sum())},
            "validation": {
                "start_inclusive": validation_start.isoformat(sep=" "),
                "end_exclusive": validation_end.isoformat(sep=" "),
                "rows": len(validation),
            },
            "label_availability": {"delay_hours": 3},
        }
        selection = {
            "validation_predictions": {
                "path": "validation-predictions.csv",
                "sha256": sha256_file(path),
                "rows": len(evidence),
            },
            "training_row_keys_sha256": _test_row_hash(frame.loc[train_mask].reset_index(drop=True)),
            "validation_row_keys_sha256": _test_row_hash(validation),
            "validation": validation_metrics,
            "comparison": {
                "incumbent": incumbent_metrics,
                "incumbent_quality_gate": "passed",
            },
            "quality_gate_passed": True,
        }
        _verify_validation_evidence(selection, path, frame, protocol, candidate, incumbent)
        with self.assertRaisesRegex(ValueError, "quality gate"):
            _verify_validation_evidence(
                dict(selection, quality_gate_passed=False), path, frame, protocol, candidate, incumbent
            )

    def test_bootstrap_validation_evidence_uses_only_historical_baseline_gate(self):
        timestamps = pd.date_range("2024-01-01", periods=24 * 10, freq="h")
        frame = pd.DataFrame({"timestamp": timestamps, "cnt": np.arange(len(timestamps), dtype=int)})
        validation_start = pd.Timestamp("2024-01-07 00:00:00")
        validation_end = pd.Timestamp("2024-01-08 00:00:00")
        cutoff = pd.Timestamp("2024-01-07 03:00:00")
        train_mask = (frame["timestamp"] < validation_start) & (
            frame["timestamp"] + pd.Timedelta(hours=3) <= cutoff
        )
        validation_mask = (frame["timestamp"] >= validation_start) & (frame["timestamp"] < validation_end)
        validation = frame.loc[validation_mask].reset_index(drop=True)
        features = feature_frame_for_dataset(frame)

        class FixedPredictions:
            def predict(self, rows):
                if len(rows) != len(validation):
                    raise AssertionError("unexpected prediction row count")
                return validation["cnt"].to_numpy(dtype=float)

        candidate = FixedPredictions()
        candidate_predictions = candidate.predict(features.loc[validation_mask, FEATURE_COLUMNS])
        baseline_model = HistoricalBaseline().fit(frame.loc[train_mask].reset_index(drop=True), frame)
        baseline_predictions = baseline_model.predict(validation["timestamp"])
        path = self.root / "bootstrap-validation-predictions.csv"
        evidence = pd.DataFrame({
            "timestamp": validation["timestamp"].map(lambda stamp: stamp.isoformat(sep=" ")),
            "label_available_at": (validation["timestamp"] + pd.Timedelta(hours=3)).map(
                lambda stamp: stamp.isoformat(sep=" ")
            ),
            "actual": validation["cnt"].astype(int),
            "candidate_prediction": candidate_predictions,
            "baseline_prediction": baseline_predictions,
        })
        evidence.to_csv(path, index=False, float_format="%.17g")
        candidate_metrics = regression_metrics(validation["cnt"], candidate_predictions)
        baseline_metrics = regression_metrics(validation["cnt"], baseline_predictions)
        gate = baseline_accuracy_gate(candidate_metrics, baseline_metrics, period="validation_selection")
        validation_report = {
            "model": candidate_metrics,
            "baseline": baseline_metrics,
            "model_by_segment": segment_mae(validation, candidate_predictions),
            "baseline_by_segment": segment_mae(validation, baseline_predictions),
            "segment_comparison": segment_comparison(validation, candidate_predictions, baseline_predictions),
        }
        selection = {
            "validation_predictions": {
                "path": "validation-predictions.csv",
                "sha256": sha256_file(path),
                "rows": len(evidence),
            },
            "training_row_keys_sha256": _test_row_hash(frame.loc[train_mask].reset_index(drop=True)),
            "validation_row_keys_sha256": _test_row_hash(validation),
            "validation": validation_report,
            "comparison": {
                "reference": "historical_baseline_only",
                "incumbent_model_version": None,
                "incumbent_artifact_sha256": None,
                "incumbent_quality_gate": None,
                "incumbent": None,
                "validation_gate": gate,
            },
            "quality_gate_policy_id": gate["policy_id"],
            "quality_gate_policy_sha256": gate["policy_sha256"],
            "quality_gate_passed": gate["passed"],
        }
        protocol = {
            "incumbent": None,
            "training": {"cutoff": cutoff.isoformat(sep=" "), "rows": int(train_mask.sum())},
            "validation": {
                "start_inclusive": validation_start.isoformat(sep=" "),
                "end_exclusive": validation_end.isoformat(sep=" "),
                "rows": len(validation),
            },
            "label_availability": {"delay_hours": 3},
        }
        _verify_validation_evidence(selection, path, frame, protocol, candidate, incumbent=None)
        with self.assertRaisesRegex(ValueError, "quality gate"):
            _verify_validation_evidence(
                dict(selection, quality_gate_passed=False), path, frame, protocol, candidate, incumbent=None
            )


    def test_candidate_bundle_is_atomic_immutable_and_contains_row_level_evidence(self):
        model, _ = self._fitted_model()
        report = {
            "data_sha256": "1" * 64,
            "data_source": {"source_manifest_sha256": "2" * 64},
            "source_revision": {"source_tree_sha256": "3" * 64, "source_file_count": 0},
            "environment": {"direct_dependencies": {"scikit-learn": "test"}},
            "feature_contract_version": "bike-demand-features-v1",
            "feature_columns": list(FEATURE_COLUMNS),
            "hyperparameters": {"random_state": 17},
            "training_cutoff": "2024-01-01 00:00:00",
            "validation_window": {"start": "2024-01-01 00:00:00", "end_exclusive": "2024-01-02 00:00:00"},
            "comparison": {"incumbent_artifact_sha256": "4" * 64},
            "training_row_keys_sha256": "5" * 64,
            "validation_row_keys_sha256": "6" * 64,
            "quality_gate_passed": True,
            "quality_gate": "candidate must pass synthetic test metrics",
            "validation": {
                "model": {"mae": 1.0, "rmse": 1.0},
                "baseline": {"mae": 2.0, "rmse": 2.0},
                "model_by_segment": {},
                "baseline_by_segment": {},
            },
        }
        validation = pd.DataFrame(
            {"timestamp": ["2024-01-01 00:00:00"], "actual": [1], "candidate_prediction": [1.0]}
        )
        with patch("bike_mlops.candidate_artifacts.PROJECT_ROOT", self.root):
            report["source_revision"]["source_tree_sha256"] = source_tree_sha256(self.root)
            result = persist_candidate_bundle(model, report, validation)
            bundle = self.root / result["candidate_bundle"]
            frozen = json.loads((bundle / "candidate-report.json").read_text())
            self.assertTrue((bundle / "model.skops").is_file())
            self.assertTrue((bundle / "validation-predictions.csv").is_file())
            self.assertEqual(sha256_file(bundle / "model.skops"), frozen["candidate_artifact"]["sha256"])
            self.assertEqual(candidate_identity(frozen), frozen["candidate_identity"])
            changed_report = dict(frozen, quality_gate_passed=False)
            self.assertNotEqual(candidate_identity(changed_report), frozen["candidate_identity"])
            with patch(
                "bike_mlops.candidate_artifacts.candidate_identity",
                return_value=frozen["candidate_identity"],
            ):
                with self.assertRaisesRegex(FileExistsError, "immutable"):
                    persist_candidate_bundle(model, report, validation)


if __name__ == "__main__":
    unittest.main()
