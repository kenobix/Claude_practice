from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from bike_mlops.capital_benchmark_inputs import check_candidate_training_window, validate_training_window


class BenchmarkTrainingWindowTests(unittest.TestCase):
    def setUp(self):
        times = pd.date_range("2024-01-01", "2024-01-11", freq="h", inclusive="left")
        self.frame = pd.DataFrame({"timestamp": times, "cnt": range(len(times))})
        self.protocol = {
            "training": {"cutoff": "2024-01-11 00:00:00", "rows": 238},
            "validation": {"start_inclusive": "2024-01-11 00:00:00"},
            "label_availability": {"delay_hours": 3},
        }
        self.selection = {"training_cutoff": "2024-01-11 00:00:00", "split_counts": {"train": 238}}
        self.freeze_rows()

    def freeze_rows(self):
        cutoff = pd.Timestamp(self.protocol["training"]["cutoff"])
        start = pd.Timestamp(self.protocol["validation"]["start_inclusive"])
        delay = self.protocol["label_availability"]["delay_hours"]
        train = self.frame.loc[(self.frame.timestamp < start) &
                               (self.frame.timestamp + pd.Timedelta(hours=delay) <= cutoff)]
        payload = "".join(f"{stamp.isoformat(sep=' ')}\t{int(count)}\n"
                          for stamp, count in zip(train.timestamp, train.cnt))
        self.selection["training_row_keys_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
        self.selection["split_counts"]["train"] = len(train)
        self.protocol["training"]["rows"] = len(train)

    def validate(self, start="2024-01-03 22:00:00", end="2024-01-10 22:00:00"):
        return validate_training_window(self.frame, self.protocol, self.selection, start=start, end=end)

    def test_last_label_at_cutoff_is_allowed(self):
        result = self.validate()
        self.assertEqual(result["hours"], 168)
        self.assertEqual(result["latest_replay_label_available_at"], "2024-01-11 00:00:00")

    def test_rejects_two_unavailable_labels_previously_used_in_synthetic_benchmark(self):
        with self.assertRaisesRegex(ValueError, "non-training hour: 2024-01-10 22:00:00"):
            self.validate("2024-01-04 00:00:00", "2024-01-11 00:00:00")

    def test_rejects_missing_hour_even_if_frozen_dataset_and_hash_are_consistent(self):
        self.frame = self.frame.loc[self.frame.timestamp != pd.Timestamp("2024-01-05 12:00:00")].copy()
        self.freeze_rows()
        with self.assertRaisesRegex(ValueError, "2024-01-05 12:00:00"):
            self.validate()

    def test_rejects_changed_training_values_count_and_delay(self):
        self.frame.loc[0, "cnt"] = 999
        with self.assertRaisesRegex(ValueError, "training rows differ"):
            self.validate()
        self.frame.loc[0, "cnt"] = 0
        self.selection["split_counts"]["train"] -= 1
        with self.assertRaisesRegex(ValueError, "row count"):
            self.validate()
        self.selection["split_counts"]["train"] += 1
        self.protocol["label_availability"]["delay_hours"] = 2
        with self.assertRaisesRegex(ValueError, "label delay"):
            self.validate()

    def test_rejects_non_hour_unsorted_duplicates_and_short_window(self):
        self.frame.loc[0, "timestamp"] += pd.Timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError, "hour-aligned"):
            self.validate()
        self.frame.loc[0, "timestamp"] -= pd.Timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError, "exactly 168 hours"):
            self.validate(end="2024-01-10 21:00:00")
        self.frame.loc[0, "timestamp"] = self.frame.loc[1, "timestamp"]
        with self.assertRaisesRegex(ValueError, "sorted and unique"):
            self.validate()

    def test_cli_only_checks_inputs_until_provenance_is_enforced(self):
        script = Path(__file__).resolve().parents[1] / "scripts/benchmark_capital_runtime.py"
        spec = importlib.util.spec_from_file_location("benchmark_cli_for_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        argv = ["benchmark", "--candidate-id", "c" * 64,
                "--selection-report", "candidate.json", "--final-report", "final.json"]
        with patch.object(module, "check_candidate_training_window", return_value={"hours": 168}) as check:
            with patch.object(sys, "argv", argv), self.assertRaisesRegex(SystemExit, "disabled"):
                module.main()
            with patch.object(sys, "argv", argv + ["--check-input-only"]), redirect_stdout(io.StringIO()) as output:
                module.main()
            self.assertIn('"status": "training_window_verified"', output.getvalue())
            self.assertEqual(check.call_count, 2)

    def test_candidate_evidence_and_source_are_checked_before_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv, manifest = root / "hourly.csv", root / "manifest.json"
            csv.write_text("synthetic csv", encoding="utf-8")
            manifest.write_text("synthetic manifest", encoding="utf-8")
            protocol_path = root / "evaluation-protocol.json"
            protocol_path.write_text("synthetic protocol", encoding="utf-8")
            self.protocol["protocol_sha256"] = "d" * 64
            self.selection.update({"data_sha256": hashlib.sha256(csv.read_bytes()).hexdigest(),
                                   "data_source": {"source_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()},
                                   "evaluation_protocol_artifact": {"sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest()}})
            evidence = {"candidate_id": "c" * 64, "selection": self.selection,
                        "selection_report_path": root / "candidate-report.json", "selection_report_sha256": "a" * 64,
                        "final_report_sha256": "b" * 64, "evaluation_protocol_sha256": "d" * 64}
            with (patch("bike_mlops.capital_staging.verify_candidate_for_staging", return_value=evidence),
                  patch("bike_mlops.cli._load_profile_hourly", return_value=self.frame),
                  patch("bike_mlops.config.DEFAULT_DATASET", csv), patch("bike_mlops.config.DATASET_MANIFEST", manifest),
                  patch("bike_mlops.evaluation_protocol.read_protocol", return_value=self.protocol)):
                run = lambda candidate: check_candidate_training_window(
                    root / "candidate-report.json", root / "final.json", candidate,
                    start="2024-01-03 22:00:00", end="2024-01-10 22:00:00")
                self.assertEqual(run("c" * 64)["hours"], 168)
                with self.assertRaisesRegex(ValueError, "candidate ID"):
                    run("e" * 64)
                csv.write_text("altered", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "CSV or manifest differs"):
                    run("c" * 64)
                csv.write_text("synthetic csv", encoding="utf-8")
                protocol_path.write_text("changed", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "protocol file differs"):
                    run("c" * 64)


if __name__ == "__main__":
    unittest.main()
