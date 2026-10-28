from __future__ import annotations

import json
import io
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

import pandas as pd

from bike_mlops.evaluation_protocol import (
    BOOTSTRAP_QUALITY_GATE_POLICY,
    BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
    begin_evaluation,
    build_capital_protocol,
    finish_evaluation,
    read_ledger,
    review_reopen_evaluation,
    save_protocol,
    protocol_sha256,
    validate_protocol,
)
from bike_mlops.profiles import load_data_profile
from bike_mlops.training import HYPERPARAMETERS, train_candidate
from scripts.prepare_capitalbikeshare import aggregate_archives


class EvaluationProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.csv = self.root / "hourly.csv"
        self.manifest = self.root / "manifest.json"
        self.write_archive()
        aggregate_archives(
            ["202401"],
            pd.Timestamp("2024-01-01"),
            pd.Timestamp("2024-01-06"),
            self.csv,
            self.manifest,
            raw_dir=self.raw,
        )
        self.ledger = self.root / "consumption-ledger.json"
        self.legacy = self.root / "legacy.json"
        self.legacy.write_text(json.dumps({"schema_version": 1, "windows": []}))

    def tearDown(self):
        self.temp.cleanup()

    def write_archive(self):
        import zipfile

        path = self.raw / "202401-capitalbikeshare-tripdata.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "trips.csv",
                "ride_id,started_at\nfirst,2024-01-01 00:00:00\nlast,2024-01-05 23:00:00\n",
            )

    def build_protocol(self, evaluation_start="2024-01-03 02:00:00"):
        return build_capital_protocol(
            self.csv,
            self.manifest,
            training_cutoff="2024-01-02 00:00:00",
            validation_start="2024-01-02 00:00:00",
            validation_end="2024-01-03 00:00:00",
            evaluation_start=evaluation_start,
            evaluation_end="2024-01-04 00:00:00",
            incumbent_version="3",
            incumbent_artifact_sha256="a" * 64,
            incumbent_quality_gate="passed",
            candidate_recipe={"model_family": "synthetic", "hyperparameters": {"seed": 17}},
            raw_dir=self.raw,
        )

    def build_bootstrap_protocol(self):
        return build_capital_protocol(
            self.csv,
            self.manifest,
            training_cutoff="2024-01-02 00:00:00",
            validation_start="2024-01-02 00:00:00",
            validation_end="2024-01-03 00:00:00",
            evaluation_start="2024-01-03 02:00:00",
            evaluation_end="2024-01-04 00:00:00",
            incumbent_version=None,
            incumbent_artifact_sha256=None,
            incumbent_quality_gate=None,
            candidate_recipe={"model_family": "synthetic", "hyperparameters": {"seed": 17}},
            raw_dir=self.raw,
        )

    def test_protocol_fixes_row_counts_and_waits_for_validation_labels(self):
        with self.assertRaisesRegex(ValueError, "precedes the time"):
            self.build_protocol("2024-01-03 00:00:00")
        protocol = self.build_protocol()
        self.assertEqual(protocol["selection_decision_time"], "2024-01-03 02:00:00")
        self.assertGreater(protocol["training"]["rows"], 0)
        self.assertEqual(protocol["validation"]["rows"], 24)
        self.assertEqual(protocol["evaluation"]["rows"], 22)
        path = self.root / "frozen.json"
        save_protocol(protocol, path)
        with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
            save_protocol(protocol, path)

    def test_bootstrap_protocol_freezes_baseline_only_mae_rmse_policy(self):
        protocol = self.build_bootstrap_protocol()
        self.assertEqual(protocol["schema_version"], 2)
        self.assertIsNone(protocol["incumbent"])
        self.assertEqual(protocol["selection_gate"]["policy"], BOOTSTRAP_QUALITY_GATE_POLICY)
        self.assertEqual(protocol["selection_gate"]["policy_sha256"], BOOTSTRAP_QUALITY_GATE_POLICY_SHA256)
        self.assertEqual(
            protocol["selection_gate"]["policy_id"], "capital-bootstrap-baseline-mae-rmse-v1"
        )
        self.assertEqual(protocol["selection_gate"]["final_evaluation_used_for_selection"], False)
        self.assertEqual(validate_protocol(protocol), protocol)

        tampered = dict(protocol)
        tampered["selection_gate"] = dict(protocol["selection_gate"])
        tampered["selection_gate"]["policy"] = dict(BOOTSTRAP_QUALITY_GATE_POLICY)
        tampered["selection_gate"]["policy"]["segments"] = "gate"
        tampered["protocol_sha256"] = protocol_sha256(tampered)
        with self.assertRaisesRegex(ValueError, "policy is missing, unsupported, or modified"):
            validate_protocol(tampered)

    def test_bootstrap_protocol_requires_all_incumbent_fields_or_none(self):
        with self.assertRaisesRegex(ValueError, "complete version/hash/passed-gate tuple"):
            build_capital_protocol(
                self.csv,
                self.manifest,
                training_cutoff="2024-01-02 00:00:00",
                validation_start="2024-01-02 00:00:00",
                validation_end="2024-01-03 00:00:00",
                evaluation_start="2024-01-03 02:00:00",
                evaluation_end="2024-01-04 00:00:00",
                incumbent_version=None,
                incumbent_artifact_sha256="a" * 64,
                incumbent_quality_gate=None,
                candidate_recipe={"model_family": "synthetic", "hyperparameters": {"seed": 17}},
                raw_dir=self.raw,
            )

    def test_new_protocol_rejects_legacy_timezone_unpinned_manifest(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["manifest_schema_version"] = 2
        manifest["processing"]["processing_version"] = "capital-hourly-aggregation-v2"
        manifest.pop("timezone_database")
        legacy_manifest = self.root / "legacy-manifest.json"
        legacy_manifest.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "require manifest schema 3"):
            build_capital_protocol(
                self.csv,
                legacy_manifest,
                training_cutoff="2024-01-02 00:00:00",
                validation_start="2024-01-02 00:00:00",
                validation_end="2024-01-03 00:00:00",
                evaluation_start="2024-01-03 02:00:00",
                evaluation_end="2024-01-04 00:00:00",
                incumbent_version="3",
                incumbent_artifact_sha256="a" * 64,
                incumbent_quality_gate="passed",
                candidate_recipe={"model_family": "synthetic", "hyperparameters": {"seed": 17}},
                raw_dir=self.raw,
            )

    def test_protocol_rejects_overlapping_declared_windows_even_when_rows_are_missing(self):
        protocol = self.build_protocol()
        protocol["validation"]["end_exclusive"] = "2024-01-04 00:00:00"
        protocol["protocol_sha256"] = protocol_sha256(protocol)
        with self.assertRaisesRegex(ValueError, "overlaps the validation window"):
            validate_protocol(protocol)

    def test_ledger_blocks_reuse_and_requires_human_review_after_failure(self):
        protocol = self.build_protocol()
        stale_lock = self.ledger.with_suffix(self.ledger.suffix + ".lock")
        stale_lock.write_text("pid=999999999\n")
        attempt = begin_evaluation(
            protocol, "validation_selection", None, self.ledger, self.legacy
        )
        ledger = read_ledger(self.ledger)
        key = ledger["records"][0]["consumption_key"]
        with self.assertRaisesRegex(ValueError, "overlaps an already consumed/started"):
            begin_evaluation(protocol, "validation_selection", None, self.ledger, self.legacy)
        changed_snapshot = dict(protocol)
        changed_snapshot["dataset"] = dict(protocol["dataset"], data_sha256="f" * 64)
        changed_snapshot["protocol_sha256"] = protocol_sha256(changed_snapshot)
        with self.assertRaisesRegex(ValueError, "overlaps an already consumed/started"):
            begin_evaluation(changed_snapshot, "validation_selection", None, self.ledger, self.legacy)
        finish_evaluation(attempt, "failed", self.ledger, detail="synthetic interruption")
        with self.assertRaisesRegex(ValueError, "overlaps an already consumed/started"):
            begin_evaluation(protocol, "validation_selection", None, self.ledger, self.legacy)
        review_reopen_evaluation(key, "tester", "checked synthetic outputs", self.ledger)
        retry = begin_evaluation(protocol, "validation_selection", "b" * 64, self.ledger, self.legacy)
        finish_evaluation(retry, "completed", self.ledger)
        record = read_ledger(self.ledger)["records"][0]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(len(record["attempts"]), 2)
        self.assertEqual(record["attempts"][0]["status"], "failed")
        self.assertEqual(record["attempts"][1]["status"], "completed")

    def test_bootstrap_ledger_records_absent_incumbent_as_null(self):
        protocol = self.build_bootstrap_protocol()
        attempt = begin_evaluation(protocol, "validation_selection", None, self.ledger, self.legacy)
        record = read_ledger(self.ledger)["records"][0]
        self.assertEqual(record["attempts"][0]["attempt_id"], attempt)
        self.assertIsNone(record["attempts"][0]["incumbent_artifact_sha256"])
        finish_evaluation(attempt, "failed", self.ledger, detail="synthetic policy test")

    def test_bootstrap_candidate_fit_and_final_evaluation_need_no_registry_model(self):
        from bike_mlops import training
        from bike_mlops.candidate_artifacts import persist_candidate_bundle, source_tree_inventory
        from scripts import evaluate_capitalbikeshare_holdout as final_evaluation

        dense_raw = self.root / "dense-raw"
        dense_raw.mkdir()
        dense_archive = dense_raw / "202401-capitalbikeshare-tripdata.zip"
        rows = ["ride_id,started_at"]
        ride_id = 0
        for timestamp in pd.date_range("2024-01-01", "2024-02-01", freq="h", inclusive="left"):
            hourly_count = 5 + timestamp.hour % 12 + (0 if timestamp.dayofweek >= 5 else 25)
            for _ in range(hourly_count):
                rows.append(f"ride-{ride_id},{timestamp:%Y-%m-%d %H:%M:%S}")
                ride_id += 1
        import zipfile

        with zipfile.ZipFile(dense_archive, "w") as archive:
            archive.writestr("trips.csv", "\n".join(rows) + "\n")
        dense_csv = self.root / "dense-hourly.csv"
        dense_manifest = self.root / "dense-manifest.json"
        aggregate_archives(
            ["202401"],
            pd.Timestamp("2024-01-01"),
            pd.Timestamp("2024-02-01"),
            dense_csv,
            dense_manifest,
            raw_dir=dense_raw,
        )
        protocol = build_capital_protocol(
            dense_csv,
            dense_manifest,
            training_cutoff="2024-01-20 00:00:00",
            validation_start="2024-01-20 00:00:00",
            validation_end="2024-01-21 00:00:00",
            evaluation_start="2024-01-21 03:00:00",
            evaluation_end="2024-01-22 00:00:00",
            incumbent_version=None,
            incumbent_artifact_sha256=None,
            incumbent_quality_gate=None,
            candidate_recipe={
                "model_family": "scikit-learn HistGradientBoostingRegressor in a Pipeline",
                "hyperparameters": HYPERPARAMETERS,
            },
            raw_dir=dense_raw,
        )
        protocol_path = self.root / "bootstrap-protocol.json"
        protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
        capital_profile = load_data_profile("capital-bikeshare")
        original_validator = training.validate_capital_manifest

        def validate_local_manifest(manifest, csv_path):
            return original_validator(manifest, csv_path, raw_dir=dense_raw)

        captured = {}

        def capture_candidate_bundle(estimator, candidate_report, validation_predictions, *, protocol_bytes=None):
            captured.update({
                "estimator": estimator,
                "report": candidate_report,
                "validation_predictions": validation_predictions,
                "protocol_bytes": protocol_bytes,
            })
            return {"candidate_artifact_sha256": "a" * 64}

        with patch.multiple(
            training,
            DATA_PROFILE_ID="capital-bikeshare",
            TARGET_CONTRACT_ID="capital-trip-starts-hourly-v1",
            DATA_PROFILE=capital_profile,
        ), patch(
            "bike_mlops.training.load_registered_model", side_effect=AssertionError("registry must not be used")
        ), patch(
            "bike_mlops.training.persist_candidate_bundle", side_effect=capture_candidate_bundle
        ), patch(
            "bike_mlops.training.validate_capital_manifest", side_effect=validate_local_manifest
        ), patch(
            "bike_mlops.training.begin_evaluation", return_value="synthetic-attempt"
        ) as begin, patch(
            "bike_mlops.training.finish_evaluation"
        ) as finish:
            report = train_candidate(
                data_path=dense_csv,
                incumbent_version=None,
                source_manifest_path=dense_manifest,
                register=False,
                evaluation_protocol_path=protocol_path,
            )
        begin.assert_called_once()
        finish.assert_called_once()
        self.assertEqual(finish.call_args.args[1], "completed")
        self.assertEqual(report["comparison"]["reference"], "historical_baseline_only")
        self.assertIsNone(report["comparison"]["incumbent"])
        self.assertEqual(report["quality_gate_policy_id"], "capital-bootstrap-baseline-mae-rmse-v1")
        self.assertIn(report["validation_gate_status"], {"passed", "failed", "not_evaluable"})
        self.assertTrue(report["quality_gate_passed"], report["comparison"]["validation_gate"])
        self.assertEqual(report["final_accuracy_gate_status"], "not_evaluated")
        self.assertEqual(report["operational_gate_status"], "not_evaluated")
        self.assertEqual(report["deployment_permission"]["status"], "blocked")

        (self.root / "README.md").write_text("synthetic source snapshot\n", encoding="utf-8")
        source_sha, source_file_count = source_tree_inventory(self.root)
        report["source_revision"] = {
            **report["source_revision"],
            "source_tree_sha256": source_sha,
            "source_file_count": source_file_count,
        }
        with patch("bike_mlops.candidate_artifacts.PROJECT_ROOT", self.root):
            bundle_result = persist_candidate_bundle(
                captured["estimator"],
                report,
                captured["validation_predictions"],
                protocol_bytes=captured["protocol_bytes"],
            )
        output_path = self.root / "data/reports/bootstrap-final-evaluation.json"
        original_final_validator = final_evaluation.validate_capital_manifest

        def validate_final_manifest(manifest, csv_path):
            return original_final_validator(manifest, csv_path, raw_dir=dense_raw)

        arguments = [
            "evaluate_capitalbikeshare_holdout.py",
            "--csv", str(dense_csv),
            "--source-manifest", str(dense_manifest),
            "--selection-report", str(self.root / bundle_result["candidate_report"]),
            "--output", str(output_path),
        ]
        with patch.multiple(
            final_evaluation,
            PROJECT_ROOT=self.root,
            DATA_PROFILE_ID="capital-bikeshare",
            TARGET_CONTRACT_ID="capital-trip-starts-hourly-v1",
        ), patch(
            "scripts.evaluate_capitalbikeshare_holdout.validate_capital_manifest",
            side_effect=validate_final_manifest,
        ), patch(
            "scripts.evaluate_capitalbikeshare_holdout.begin_evaluation",
            return_value="synthetic-final-attempt",
        ) as begin_final, patch(
            "scripts.evaluate_capitalbikeshare_holdout.finish_evaluation"
        ) as finish_final, patch(
            "scripts.evaluate_capitalbikeshare_holdout.load_registered_model",
            side_effect=AssertionError("baseline-only final evaluation must not load a registered model"),
        ), patch.object(sys, "argv", arguments), redirect_stdout(io.StringIO()):
            final_evaluation.main()

        begin_final.assert_called_once()
        finish_final.assert_called_once()
        self.assertEqual(finish_final.call_args.args[1], "completed")
        final_report = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertIsNone(final_report["incumbent"])
        self.assertFalse(final_report["model_fit_performed_during_evaluation"])
        self.assertEqual(
            final_report["final_accuracy_gate"]["policy_id"], "capital-bootstrap-baseline-mae-rmse-v1"
        )
        self.assertEqual(final_report["operational_gate_status"], "not_evaluated")
        self.assertEqual(final_report["deployment_permission"]["status"], "blocked")
        self.assertTrue(output_path.with_suffix(".predictions.csv").is_file())

    def test_legacy_overlap_blocks_before_any_ledger_write(self):
        protocol = self.build_protocol()
        legacy = {
            "schema_version": 1,
            "windows": [{
                "record_id": "old",
                "data_sha256": protocol["dataset"]["data_sha256"],
                "target_contract": protocol["dataset"]["target_contract"],
                "start_inclusive": "2024-01-02 12:00:00",
                "end_exclusive": "2024-01-03 12:00:00",
            }],
        }
        self.legacy.write_text(json.dumps(legacy))
        with self.assertRaisesRegex(ValueError, "overlaps a previously used"):
            begin_evaluation(protocol, "validation_selection", None, self.ledger, self.legacy)
        self.assertFalse(self.ledger.exists())

    def test_concurrent_start_allows_only_one_attempt_for_a_window(self):
        protocol = self.build_protocol()
        barrier = threading.Barrier(2)

        def start_once():
            barrier.wait()
            try:
                return begin_evaluation(protocol, "validation_selection", None, self.ledger, self.legacy)
            except (ValueError, RuntimeError) as error:
                return f"blocked:{type(error).__name__}"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: start_once(), range(2)))
        self.assertEqual(sum(isinstance(value, str) and not value.startswith("blocked:") for value in outcomes), 1)
        ledger = read_ledger(self.ledger)
        self.assertEqual(len(ledger["records"]), 1)
        self.assertEqual(len(ledger["records"][0]["attempts"]), 1)
        self.assertEqual(ledger["records"][0]["status"], "started")


if __name__ == "__main__":
    unittest.main()
