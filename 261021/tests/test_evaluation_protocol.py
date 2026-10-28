from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from bike_mlops.evaluation_protocol import (
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
