from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import review_clock_rate_phase as cli

from bike_mlops.clock_rate_review import review_clock_rate_phase


def phase(*, seconds=1800, mono_rate=Fraction(1), raw_rate=Fraction(1), width_ns=1000):
    samples = []
    for index in range(seconds + 1):
        row = {"index": index, "qpc_tick": 12345 + index * 1000}
        for clock, rate in (("monotonic", mono_rate), ("raw", raw_rate)):
            before = int(index * 10**9 * rate)
            row[f"{clock}_before_ns"] = before
            row[f"{clock}_after_ns"] = before + width_ns
        samples.append(row)
    return {"schema_version": 1, "kind": "clock-rate-phase-v1", "phase": "idle",
            "qpc_frequency_hz": 1000, "samples": samples}


class ClockRateReviewTests(unittest.TestCase):
    def test_complete_phase_checks_every_fixed_window_and_never_qualifies_clock(self):
        report = review_clock_rate_phase(phase())
        self.assertEqual(report["rate_component_status"], "passed")
        self.assertEqual(len(report["intervals"]), 62)
        self.assertEqual(report["clock_qualification_status"], "not_evaluated")
        self.assertIn("qpc_calibration", report["unevaluated"])
        first = report["intervals"][0]
        self.assertEqual(first["first_index"], 0)
        self.assertEqual(first["last_index"], 60)
        self.assertEqual(Fraction(**first["lower"]), Fraction(60 * 10**9 - 1000, 60 * 10**9))

    def test_ten_percent_monotonic_distortion_fails_even_if_raw_is_correct(self):
        report = review_clock_rate_phase(phase(mono_rate=Fraction(11, 10)))
        self.assertEqual(report["rate_component_status"], "failed")
        self.assertTrue(any(item["clock"] == "monotonic" and item["status"] == "failed" for item in report["intervals"]))

    def test_wide_brackets_are_incomplete_despite_central_estimate_of_one(self):
        report = review_clock_rate_phase(phase(width_ns=100_000_000))
        self.assertEqual(report["rate_component_status"], "incomplete")

    def test_exact_ratio_boundary_is_accepted_only_with_zero_width(self):
        for rate in (Fraction(999, 1000), Fraction(1001, 1000)):
            with self.subTest(rate=rate):
                self.assertEqual(review_clock_rate_phase(phase(mono_rate=rate, width_ns=0))["rate_component_status"], "passed")
                self.assertEqual(review_clock_rate_phase(phase(mono_rate=rate))["rate_component_status"], "incomplete")

    def test_missing_last_boundary_and_extra_samples_are_incomplete(self):
        for seconds in (1799, 1801):
            with self.subTest(seconds=seconds):
                report = review_clock_rate_phase(phase(seconds=seconds))
                self.assertEqual(report["rate_component_status"], "incomplete")

    def test_reference_gap_is_not_hidden_by_good_long_interval(self):
        value = phase()
        del value["samples"][10:13]
        for index, sample in enumerate(value["samples"]):
            sample["index"] = index
        report = review_clock_rate_phase(value)
        self.assertEqual(report["rate_component_status"], "incomplete")
        self.assertTrue(any(item["code"] == "sample_gap" for item in report["issues"]))

    def test_two_endpoints_cannot_establish_missing_intermediate_windows(self):
        value = phase()
        value["samples"] = [value["samples"][0], value["samples"][-1]]
        value["samples"][1]["index"] = 1
        report = review_clock_rate_phase(value)
        self.assertEqual(report["rate_component_status"], "incomplete")
        self.assertTrue(any(item["code"] == "unobserved_window" for item in report["issues"]))
        self.assertTrue(any(item["kind"] == "phase" for item in report["intervals"]))

    def test_failure_has_priority_over_incomplete_and_both_are_retained(self):
        report = review_clock_rate_phase(phase(seconds=1799, mono_rate=Fraction(11, 10)))
        self.assertEqual(report["rate_component_status"], "failed")
        self.assertEqual({item["status"] for item in report["issues"]}, {"failed", "incomplete"})

    def test_invalid_order_and_reversed_bracket_fail(self):
        changes = (("qpc_tick", 12345), ("monotonic_before_ns", 0), ("raw_before_ns", 2 * 10**9))
        for name, new_value in changes:
            with self.subTest(name=name):
                value = phase()
                value["samples"][1][name] = new_value
                self.assertEqual(review_clock_rate_phase(value)["rate_component_status"], "failed")

    def test_integer_schema_rejects_bool_float_negative_unknown_fields_and_bad_indexes(self):
        changes = [("qpc_frequency_hz", True), ("qpc_frequency_hz", 1000.0),
                   ("qpc_frequency_hz", 0), ("phase", {}), ("schema_version", True)]
        for key, new_value in changes:
            with self.subTest(key=key, new_value=new_value):
                value = phase(seconds=2)
                value[key] = new_value
                with self.assertRaises(ValueError):
                    review_clock_rate_phase(value)
        for key, new_value in (("index", 99), ("qpc_tick", -1), ("raw_after_ns", False), ("unknown", 0)):
            with self.subTest(key=key):
                value = phase(seconds=2)
                value["samples"][1][key] = new_value
                with self.assertRaises(ValueError):
                    review_clock_rate_phase(value)

    def test_boundary_selection_uses_first_reach_and_cannot_choose_a_later_sample(self):
        value = phase()
        # First 60s boundary lies between sample59 and sample60.
        for sample in value["samples"][1:]:
            sample["qpc_tick"] += 1
        report = review_clock_rate_phase(value)
        self.assertEqual(report["intervals"][0]["last_index"], 60)

    def test_input_and_returned_policy_cannot_mutate_subsequent_policy(self):
        value = phase(seconds=2)
        original = copy.deepcopy(value)
        report = review_clock_rate_phase(value)
        self.assertEqual(value, original)
        report["policy"]["minimum_ratio"][0] = 0
        self.assertEqual(review_clock_rate_phase(value)["policy"]["minimum_ratio"], [999, 1000])

    def run_cli(self, input_path, output_path):
        script = Path(__file__).resolve().parents[1] / "scripts/review_clock_rate_phase.py"
        return subprocess.run([sys.executable, str(script), "--input", str(input_path), "--output", str(output_path)],
                              capture_output=True, text=True, timeout=10)

    def test_cli_separate_process_hash_exit_codes_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            for name, value, code in (("good", phase(), 0), ("failed", phase(mono_rate=Fraction(11, 10)), 1),
                                      ("short", phase(seconds=2), 2)):
                source.write_text(json.dumps(value))
                payload = source.read_bytes()
                output = root / f"{name}.json"
                result = self.run_cli(source, output)
                self.assertEqual(result.returncode, code, result.stderr)
                report = json.loads(output.read_text())
                self.assertEqual(report["input_sha256"], hashlib.sha256(payload).hexdigest())
                self.assertEqual(report["clock_qualification_status"], "not_evaluated")
                before = output.read_bytes()
                self.assertEqual(self.run_cli(source, output).returncode, 2)
                self.assertEqual(output.read_bytes(), before)
                self.assertEqual(source.read_bytes(), payload)
            self.assertEqual({p.name for p in root.iterdir()}, {"input.json", "good.json", "failed.json", "short.json"})

    def test_cli_rejects_duplicate_nonfinite_and_malformed_json_without_echoing_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for payload in ('{"secret":"do-not-echo", "secret":1}', '{"v":NaN}', '{"v":Infinity}', '{'):
                source = root / "private-input.json"
                source.write_text(payload)
                output = root / "result.json"
                result = self.run_cli(source, output)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(output.exists())
                self.assertNotIn("do-not-echo", result.stderr)
                self.assertNotIn(directory, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_cli_output_failures_clean_staged_file_and_leave_source_unchanged(self):
        for failure_target in ("os.link", "os.fsync"):
            with self.subTest(target=failure_target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "input.json"
                source.write_text(json.dumps(phase(seconds=2)))
                original = source.read_bytes()
                with patch.object(sys, "argv", ["review_clock_rate_phase", "--input", str(source), "--output", str(root / "result.json")]), \
                     patch(f"scripts.review_clock_rate_phase.{failure_target}", side_effect=OSError("private failure do-not-echo")), \
                     redirect_stderr(io.StringIO()) as stderr, redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(), 2)
                self.assertEqual([p.name for p in root.iterdir()], ["input.json"])
                self.assertEqual(source.read_bytes(), original)
                self.assertNotIn("do-not-echo", stderr.getvalue())

    def test_cli_rejects_visible_input_change_before_producing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            source.write_text(json.dumps(phase(seconds=2)))
            old_stat = source.stat()
            with patch.object(sys, "argv", ["review_clock_rate_phase", "--input", str(source), "--output", str(root / "result.json")]), \
                 patch("scripts.review_clock_rate_phase.os.fstat", side_effect=[old_stat, source.parent.stat()]), \
                 redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 2)
            self.assertFalse((root / "result.json").exists())

    def test_cli_rejects_symlink_and_fifo_without_waiting_for_a_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular = root / "regular.json"
            regular.write_text(json.dumps(phase(seconds=2)))
            link = root / "link.json"
            link.symlink_to(regular)
            fifo = root / "input.fifo"
            os.mkfifo(fifo)
            for source in (link, fifo, root):
                with self.subTest(source=source.name):
                    output = root / "result.json"
                    self.assertEqual(self.run_cli(source, output).returncode, 2)
                    self.assertFalse(output.exists())

    def test_cli_refuses_oversized_input_before_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large.json"
            with source.open("wb") as stream:
                stream.truncate(16 * 1024 * 1024 + 1)
            output = root / "result.json"
            self.assertEqual(self.run_cli(source, output).returncode, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
