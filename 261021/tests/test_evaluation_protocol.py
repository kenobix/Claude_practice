from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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
from scripts.prepare_capitalbikeshare import aggregate_archives
from bike_mlops.training import train_candidate


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

    def test_candidate_runner_refuses_bootstrap_before_consuming_window(self):
        protocol = self.build_bootstrap_protocol()
        protocol_path = self.root / "bootstrap-protocol.json"
        protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
        with patch("bike_mlops.training.begin_evaluation") as begin:
            with self.assertRaisesRegex(ValueError, "no evaluation window was consumed"):
                train_candidate(incumbent_version=None, register=False, evaluation_protocol_path=protocol_path)
        begin.assert_not_called()

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
