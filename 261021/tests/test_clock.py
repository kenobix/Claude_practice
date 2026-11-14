"""Contract and regression tests: test_clock."""
from __future__ import annotations


# ========================================================================
# test_clock_rate_review
# ========================================================================

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

from scripts import clock_tools as cli

from bike_mlops.clock import review_clock_rate_phase


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
        script = Path(__file__).resolve().parents[1] / 'scripts/clock_tools.py'
        return subprocess.run([sys.executable, str(script), "rate-review", "--input", str(input_path), "--output", str(output_path)],
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
                with patch.object(sys, "argv", ['clock_tools', "--input", str(source), "--output", str(root / "result.json")]), \
                     patch(f'scripts.clock_tools.{failure_target}', side_effect=OSError("private failure do-not-echo")), \
                     redirect_stderr(io.StringIO()) as stderr, redirect_stdout(io.StringIO()):
                    self.assertEqual(cli._review_clock_rate_phase_main(), 2)
                self.assertEqual([p.name for p in root.iterdir()], ["input.json"])
                self.assertEqual(source.read_bytes(), original)
                self.assertNotIn("do-not-echo", stderr.getvalue())

    def test_cli_rejects_visible_input_change_before_producing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            source.write_text(json.dumps(phase(seconds=2)))
            old_stat = source.stat()
            with patch.object(sys, "argv", ['clock_tools', "--input", str(source), "--output", str(root / "result.json")]), \
                 patch('scripts.clock_tools._review_clock_rate_phase_os.fstat', side_effect=[old_stat, source.parent.stat()]), \
                 redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(cli._review_clock_rate_phase_main(), 2)
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


# ========================================================================
# test_clock_campaign_review
# ========================================================================

"""Synthetic evidence only: no OS clocks, DBs, registry or suspend operations."""


import copy as _clock_campaign_review_copy
from contextlib import redirect_stderr as _clock_campaign_review_redirect_stderr, redirect_stdout as _clock_campaign_review_redirect_stdout
from fractions import Fraction as _clock_campaign_review_Fraction
import hashlib as _clock_campaign_review_hashlib
import io as _clock_campaign_review_io
import json as _clock_campaign_review_json
import os as _clock_campaign_review_os
from pathlib import Path as _clock_campaign_review_Path
import subprocess as _clock_campaign_review_subprocess
import sys as _clock_campaign_review_sys
import tempfile as _clock_campaign_review_tempfile
import unittest as _clock_campaign_review_unittest
from unittest.mock import patch as _clock_campaign_review_patch

from bike_mlops.clock import IDENTITY_FIELDS, _clock_campaign_review_POLICY as POLICY, _clock_campaign_review_POLICY_SHA256 as POLICY_SHA256, canonical_sha256, review_clock_campaign
from scripts import clock_tools as _clock_campaign_review_cli

H = "a" * 64
NS = 10**9


def seal(value):
    for campaign in value["campaigns"]:
        for phase in campaign["phases"]:
            phase["sha256"] = canonical_sha256(phase["data"])
    value["plan_sha256"] = canonical_sha256(value["plan"])
    return value


def fixture():
    identity = {key: H for key in IDENTITY_FIELDS}
    identity_hash = canonical_sha256(identity)
    plan = {key: H for key in ("collector_sha256", "workload_sha256", "utc_reader_sha256",
                               "utc_source_sha256", "calibration_source_sha256")}
    plan.update(policy_sha256=POLICY_SHA256, qpc_frequency_hz=1000)
    value = {"schema_version": 1, "kind": "clock-campaign-v1", "policy": _clock_campaign_review_copy.deepcopy(POLICY),
             "policy_sha256": POLICY_SHA256, "plan": plan, "plan_sha256": H, "campaigns": [], "resume": None}
    for ci, role in enumerate(("normal", "post-resume")):
        campaign = {"role": role, "trial_sha256": str(ci + 1) * 64, "phases": []}
        for pi, phase in enumerate(("idle", "fixture-load")):
            data = {"phase": phase, "identity_start": _clock_campaign_review_copy.deepcopy(identity), "identity_end": _clock_campaign_review_copy.deepcopy(identity),
                    "samples": [], "external_utc": [], "qpc_calibration": [], "future_probes": [],
                    "lost_samples": 0, "interrupted": False}
            origin = ci * 4000 + pi * 1800
            for index in range(1801):
                t = origin + index
                row = {"index": index, "qpc_tick": t * 1000, "identity_sha256": identity_hash}
                for clock in ("monotonic", "raw", "boottime"):
                    row[f"{clock}_before_ns"] = t * NS
                    row[f"{clock}_after_ns"] = t * NS + 1000
                for clock in ("windows_utc", "realtime"):
                    row[f"{clock}_before_ns"] = (1800000000 + t) * NS
                    row[f"{clock}_after_ns"] = (1800000000 + t) * NS + 1000
                data["samples"].append(row)
            for second in range(0, 1801, 60):
                utc = (1800000000 + origin + second) * NS
                data["external_utc"].append({"boundary_seconds": second, "sample_index": second,
                                             "lower_ns": utc - 1000, "upper_ns": utc + 1000,
                                             "source_sha256": H})
            for first, last in [(s, s + 60) for s in range(0, 1800, 60)] + [(0, 1800)]:
                data["qpc_calibration"].append({"first_index": first, "last_index": last,
                    "reference_lower_ns": (last - first) * NS - 1000,
                    "reference_upper_ns": (last - first) * NS + 1000, "source_sha256": H})
            for case, delta in (("equal", 0), ("future-1us", 1000), ("live", -1000)):
                data["future_probes"].append({"case": case, "generated_utc_ns": 2000 + delta,
                    "validated_utc_ns": 2000, "generated_monotonic_ns": 1000, "validated_monotonic_ns": 2000,
                    "sample_index": None, "decision": "reject" if delta > 0 else "allow", "blocked_before_sha256": H,
                    "blocked_after_sha256": H, "tags_before_sha256": H, "tags_after_sha256": H})
            live = data["future_probes"][-1]
            live.update(sample_index=10, generated_utc_ns=(1800000000 + origin + 10) * NS,
                        validated_utc_ns=(1800000000 + origin + 10) * NS + 1000,
                        generated_monotonic_ns=(origin + 10) * NS,
                        validated_monotonic_ns=(origin + 10) * NS + 1000)
            campaign["phases"].append({"sha256": H, "data": data})
        value["campaigns"].append(campaign)
    value["resume"] = {"before_identity_sha256": identity_hash, "after_identity_sha256": identity_hash,
        "windows_before_ns": (1800000000 + 3601) * NS, "windows_after_ns": (1800000000 + 3999) * NS,
        "guest_before_ns": (1800000000 + 3601) * NS, "guest_after_ns": (1800000000 + 3999) * NS}
    return seal(value)


class ClockCampaignTests(_clock_campaign_review_unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = fixture()

    def setUp(self):
        self.value = _clock_campaign_review_copy.deepcopy(self.original)
        self.phase = self.value["campaigns"][0]["phases"][0]["data"]

    def review(self):
        seal(self.value)
        return review_clock_campaign(self.value, expected_plan_sha256=self.value["plan_sha256"])

    def assert_issue(self, code, status=None):
        report = self.review()
        self.assertIn(code, {i["code"] for i in report["issues"]})
        if status:
            self.assertEqual(report["offline_checks_status"], status)
        self.assertNotEqual(report["clock_qualification_status"], "passed")
        return report

    def test_complete_four_phases_recomputed_but_whole_qualification_incomplete(self):
        report = self.review()
        self.assertEqual(report["offline_checks_status"], "passed")
        self.assertEqual(report["clock_qualification_status"], "incomplete")
        self.assertEqual(report["issues"], [])
        self.assertEqual(len(report["phases"]), 4)
        self.assertEqual(len(report["calculations"]), 4 * (62 + 31))
        self.assertEqual(sum(len(p["rate"]["intervals"]) for p in report["phases"]), 248)
        self.assertEqual(len(report["qualification_prerequisites_not_established"]), 6)

    def test_policy_and_expected_plan_hash_cannot_be_replaced_by_self_rehash(self):
        self.value["policy"]["maximum_utc_offset_ns"] *= 100
        self.value["policy_sha256"] = canonical_sha256(self.value["policy"])
        with self.assertRaises(ValueError):
            self.review()
        self.value = _clock_campaign_review_copy.deepcopy(self.original)
        self.value["plan"]["collector_sha256"] = "b" * 64
        seal(self.value)
        with self.assertRaises(ValueError):
            review_clock_campaign(self.value, expected_plan_sha256=self.original["plan_sha256"])

    def test_policy_integer_bool_substitution_is_refused(self):
        self.value["policy"]["automatic_qualification"] = 0
        with self.assertRaises(ValueError):
            self.review()

    def test_phase_hash_and_plan_hash_binding(self):
        self.phase["lost_samples"] = 1
        with self.assertRaises(ValueError):
            review_clock_campaign(self.value, expected_plan_sha256=self.value["plan_sha256"])
        self.value = _clock_campaign_review_copy.deepcopy(self.original)
        self.value["plan_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            review_clock_campaign(self.value, expected_plan_sha256=self.value["plan_sha256"])

    def test_partial_campaign_or_phase_never_substitutes_for_whole(self):
        self.value["campaigns"].pop()
        self.value["resume"] = None
        self.assert_issue("campaign_missing", "incomplete")
        self.value["campaigns"][0]["phases"].pop()
        self.assert_issue("phase_missing", "incomplete")

    def test_missing_identity_and_nullable_sample_mark_incomplete(self):
        self.phase["identity_start"] = None
        self.phase["samples"][10]["monotonic_before_ns"] = None
        self.assert_issue("identity_missing", "incomplete")
        self.assert_issue("rate_samples_missing", "incomplete")

    def test_missing_identity_does_not_disable_sample_digest_schema(self):
        self.phase["identity_start"] = None
        self.phase["samples"][40]["identity_sha256"] = "/private/invalid"
        with self.assertRaises(ValueError):
            self.review()

    def test_sample_and_end_identity_changes_fail(self):
        self.phase["samples"][40]["identity_sha256"] = "b" * 64
        self.phase["identity_end"]["process_set_sha256"] = "c" * 64
        self.assert_issue("sample_identity_changed", "failed")
        self.assert_issue("identity_changed", "failed")

    def test_sample_gap_truncation_and_instrumentation_loss(self):
        self.phase["samples"] = self.phase["samples"][::3]
        for index, row in enumerate(self.phase["samples"]):
            row["index"] = index
        self.phase["external_utc"] = []
        self.phase["qpc_calibration"] = []
        self.phase["future_probes"] = []  # Sparse sampling removed the live probe bracket too.
        self.phase["lost_samples"] = 1
        self.phase["interrupted"] = True
        self.assert_issue("rate_sample_gap", "incomplete")
        self.assert_issue("instrumentation_loss", "incomplete")
        self.assert_issue("collector_interrupted", "incomplete")

    def test_missing_identity_at_transition_is_not_a_confirmed_change(self):
        self.phase["identity_end"] = None
        self.assert_issue("phase_identity_missing", "incomplete")

    def test_missing_shared_endpoint_value_is_not_a_confirmed_change(self):
        self.phase["samples"][-1]["raw_before_ns"] = None
        self.assert_issue("phase_boundary_missing", "incomplete")

    def test_empty_samples_and_evidence_are_incomplete_without_exception(self):
        for key in ("samples", "external_utc", "qpc_calibration", "future_probes"):
            self.phase[key] = []
        self.assert_issue("coverage_missing", "incomplete")

    def test_unknown_fields_types_and_unsafe_identity_are_refused(self):
        mutations = (("lost_samples", True), ("interrupted", 1), ("phase", []), ("unknown", "secret"))
        for key, value in mutations:
            with self.subTest(key=key):
                data = _clock_campaign_review_copy.deepcopy(self.original)
                data["campaigns"][0]["phases"][0]["data"][key] = value
                seal(data)
                with self.assertRaises(ValueError):
                    review_clock_campaign(data, expected_plan_sha256=data["plan_sha256"])
        self.phase["identity_start"]["process_set_sha256"] = "/private/path"
        with self.assertRaises(ValueError):
            self.review()

    def test_utc_offsets_include_transport_uncertainty_and_exact_boundaries(self):
        item = self.phase["external_utc"][1]
        b = self.phase["samples"][60]["windows_utc_before_ns"]
        item.update(lower_ns=b - NS + 1000, upper_ns=b - NS + 2000)
        self.assertEqual(self.review()["offline_checks_status"], "passed")
        item["lower_ns"] -= 1
        self.assert_issue("external_utc_offset", "incomplete")
        item["upper_ns"] -= 3000
        item["lower_ns"] = item["upper_ns"] - 1000
        self.assert_issue("external_utc_offset", "failed")

    def test_utc_missing_duplicate_wrong_index_source_and_reversed_interval(self):
        self.phase["external_utc"].pop()
        self.assert_issue("utc_boundaries_missing", "incomplete")
        self.phase["external_utc"][1] = _clock_campaign_review_copy.deepcopy(self.phase["external_utc"][0])
        self.assert_issue("utc_boundary_duplicate_or_extra", "failed")
        self.phase["external_utc"][2]["sample_index"] = 119
        self.assert_issue("utc_wrong_sample", "failed")
        self.phase["external_utc"][3]["source_sha256"] = "b" * 64
        self.assert_issue("utc_source_changed", "failed")
        self.phase["external_utc"][4]["upper_ns"] = 1
        self.assert_issue("utc_reference_reversed", "failed")

    def test_windows_guest_and_boottime_backwards_are_detected(self):
        for clock in ("windows_utc", "realtime", "boottime"):
            with self.subTest(clock=clock):
                self.phase["samples"][30][f"{clock}_before_ns"] = 0
                self.assert_issue("clock_backwards", "failed")

    def test_rate_failure_dominates_missing_external_and_resume(self):
        for row in self.phase["samples"]:
            for edge in ("before", "after"):
                row[f"monotonic_{edge}_ns"] = row[f"monotonic_{edge}_ns"] * 11 // 10
        self.phase["external_utc"] = []
        self.value["resume"] = None
        report = self.assert_issue("rate_rate_outside_bounds", "failed")
        self.assertEqual({i["status"] for i in report["issues"]}, {"failed", "incomplete"})

    def test_qpc_calibration_exact_100ppm_limit_and_one_unit_crossing(self):
        item = self.phase["qpc_calibration"][0]
        # q=60,000,000,000ns; exact upper ratio 10001/10000.
        # Scale QPC frequency for integer reference at this boundary.
        self.value["plan"]["qpc_frequency_hz"] = 10001
        for campaign in self.value["campaigns"]:
            for wrapped in campaign["phases"]:
                for row in wrapped["data"]["samples"]:
                    row["qpc_tick"] = row["qpc_tick"] * 10001 // 1000
        item["reference_lower_ns"] = 60 * NS * 10000 // 10001
        # integer rounding down crosses by less than 1 ns: incomplete, not pass.
        report = self.assert_issue("qpc_calibration", "incomplete")
        check = next(c for c in report["calculations"] if c["check"] == "qpc_calibration")
        self.assertGreater(_clock_campaign_review_Fraction(*check["upper"]), _clock_campaign_review_Fraction(10001, 10000))
        item["reference_lower_ns"] += 1
        self.assertEqual(self.review()["offline_checks_status"], "passed")

    def test_qpc_calibration_observed_drift_missing_duplicate_source_and_reversal(self):
        self.phase["qpc_calibration"].pop()
        self.assert_issue("calibration_windows_missing", "incomplete")
        self.phase["qpc_calibration"][0]["reference_upper_ns"] //= 2
        self.phase["qpc_calibration"][0]["reference_lower_ns"] //= 2
        self.assert_issue("qpc_calibration", "failed")
        self.phase["qpc_calibration"][1] = _clock_campaign_review_copy.deepcopy(self.phase["qpc_calibration"][0])
        self.assert_issue("calibration_pair_duplicate_or_extra", "failed")
        self.phase["qpc_calibration"][2]["source_sha256"] = "b" * 64
        self.assert_issue("calibration_source_changed", "failed")
        self.phase["qpc_calibration"][3]["reference_lower_ns"] *= 2
        self.assert_issue("calibration_reference_reversed", "failed")

    def test_reused_trial_or_wrong_phase_order_refused(self):
        self.value["campaigns"][1]["trial_sha256"] = self.value["campaigns"][0]["trial_sha256"]
        self.assert_issue("reused_trial", "failed")
        self.value["campaigns"][0]["phases"].reverse()
        with self.assertRaises(ValueError):
            self.review()

    def test_phase_transition_must_share_sample_and_identity(self):
        next_phase = self.value["campaigns"][0]["phases"][1]["data"]
        next_phase["samples"][0]["qpc_tick"] += 1
        self.assert_issue("phase_boundary_not_shared", "failed")
        next_phase["identity_start"]["versions_sha256"] = "b" * 64
        self.assert_issue("phase_identity_changed", "failed")

    def test_resume_missing_reversed_and_identity_mismatch(self):
        self.value["resume"]["windows_after_ns"] = None
        self.assert_issue("resume_utc_missing", "incomplete")
        self.value["resume"]["guest_after_ns"] = 1
        self.assert_issue("resume_utc_backwards", "failed")
        self.value["resume"]["before_identity_sha256"] = "b" * 64
        self.assert_issue("resume_identity_mismatch", "failed")

    def test_resume_cannot_be_reordered_after_post_resume_campaign(self):
        self.value["resume"]["windows_after_ns"] += 10 * NS
        self.assert_issue("resume_outside_campaign_order", "failed")

    def test_strict_future_probe_cannot_be_relaxed_or_mutate_blocked_tags(self):
        probe = self.phase["future_probes"][1]
        probe["decision"] = "allow"
        self.assert_issue("strict_future_probe_failed", "failed")
        probe["tags_after_sha256"] = "b" * 64
        self.assert_issue("future_rejection_mutated_state", "failed")
        probe["generated_utc_ns"] -= 999  # Even 1ns future is rejected; fixture must still be exactly 1us.
        self.assert_issue("strict_future_probe_failed", "failed")

    def test_live_natural_future_is_failed_without_retry_or_clamp(self):
        probe = self.phase["future_probes"][2]
        probe.update(generated_utc_ns=probe["validated_utc_ns"] + 1000, decision="reject")
        self.assert_issue("strict_future_probe_failed", "failed")
        probe["generated_monotonic_ns"] = probe["validated_monotonic_ns"] + 1000
        self.assert_issue("future_probe_monotonic_backwards", "failed")

    def test_missing_or_duplicate_future_case_not_passed(self):
        self.phase["future_probes"].pop()
        self.assert_issue("future_probes_missing", "incomplete")
        self.phase["future_probes"][1] = _clock_campaign_review_copy.deepcopy(self.phase["future_probes"][0])
        self.assert_issue("future_probe_duplicate", "failed")

    def test_missing_rate_value_does_not_hide_other_observed_rate_failure(self):
        self.phase["samples"][10]["monotonic_before_ns"] = None
        for row in self.phase["samples"]:
            for edge in ("before", "after"):
                row[f"raw_{edge}_ns"] = row[f"raw_{edge}_ns"] * 11 // 10
        self.assert_issue("partial_elapsed_rate", "failed")

    def test_missing_qpc_does_not_hide_known_clock_reversal(self):
        self.phase["samples"][10]["qpc_tick"] = None
        self.phase["samples"][20]["raw_before_ns"] = 0
        self.assert_issue("clock_backwards", "failed")

    def test_live_probe_must_be_bound_to_corresponding_sample(self):
        live = self.phase["future_probes"][-1]
        live["sample_index"] = None
        self.assert_issue("live_probe_sample_missing", "incomplete")
        live["sample_index"] = 11
        self.assert_issue("live_probe_outside_sample", "failed")

    def test_inputs_and_returned_policy_are_not_mutable_global_state(self):
        before = _clock_campaign_review_copy.deepcopy(self.value)
        report = self.review()
        self.assertEqual(self.value, before)
        report["policy"]["qpc_minimum_ratio"][0] = 0
        self.assertEqual(self.review()["policy"]["qpc_minimum_ratio"], [9999, 10000])

    def run_cli(self, source, output, plan_hash=None):
        return _clock_campaign_review_subprocess.run([_clock_campaign_review_sys.executable, str(_clock_campaign_review_Path(__file__).resolve().parents[1] / 'scripts/clock_tools.py'), 'review',
            "--input", str(source), "--output", str(output), "--expected-plan-sha256", plan_hash or self.value["plan_sha256"]],
            capture_output=True, text=True, timeout=20)

    def test_cli_archive_only_recomputes_and_never_exits_zero(self):
        with _clock_campaign_review_tempfile.TemporaryDirectory() as directory:
            root = _clock_campaign_review_Path(directory)
            for name, failed in (("complete", False), ("failed", True)):
                self.phase["interrupted"] = False
                if failed:
                    self.phase["samples"][20]["windows_utc_before_ns"] = 0
                seal(self.value)
                source, output = root / "input.json", root / f"{name}.json"
                source.write_text(_clock_campaign_review_json.dumps(self.value))
                payload = source.read_bytes()
                result = self.run_cli(source, output)
                self.assertEqual(result.returncode, 1 if failed else 2, result.stderr)
                report = _clock_campaign_review_json.loads(output.read_bytes())
                self.assertEqual(report["input_sha256"], _clock_campaign_review_hashlib.sha256(payload).hexdigest())
                self.assertEqual(report["input_bytes"], len(payload))
                self.assertEqual(report["clock_qualification_status"], "failed" if failed else "incomplete")
                saved = output.read_bytes()
                self.assertEqual(self.run_cli(source, output).returncode, 2)
                self.assertEqual(output.read_bytes(), saved)
                self.assertEqual(source.read_bytes(), payload)

    def test_cli_untrusted_plan_json_and_private_data_are_not_echoed(self):
        with _clock_campaign_review_tempfile.TemporaryDirectory() as directory:
            root = _clock_campaign_review_Path(directory)
            source, output = root / "private.json", root / "output.json"
            for payload in ('{"secret":"do-not-echo","secret":1}', '{"x":NaN}', '{', '[' * 2000 + '0' + ']' * 2000, _clock_campaign_review_json.dumps(self.value)):
                source.write_text(payload)
                result = self.run_cli(source, output, "b" * 64)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(output.exists())
                self.assertNotIn("do-not-echo", result.stderr)
                self.assertNotIn(directory, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_cli_nonregular_oversized_and_symlink_rejected(self):
        with _clock_campaign_review_tempfile.TemporaryDirectory() as directory:
            root = _clock_campaign_review_Path(directory)
            source = root / "input.json"
            source.write_text(_clock_campaign_review_json.dumps(self.value))
            link, fifo, large = root / "link", root / "fifo", root / "large"
            link.symlink_to(source)
            _clock_campaign_review_os.mkfifo(fifo)
            with large.open("wb") as stream:
                stream.truncate(32 * 1024 * 1024 + 1)
            for path in (link, fifo, large, root):
                self.assertEqual(self.run_cli(path, root / "output.json").returncode, 2)
                self.assertFalse((root / "output.json").exists())

    def test_cli_rejects_visible_stat_change_and_cleans_output(self):
        with _clock_campaign_review_tempfile.TemporaryDirectory() as directory:
            root = _clock_campaign_review_Path(directory)
            source, output = root / "input.json", root / "output.json"
            source.write_text(_clock_campaign_review_json.dumps(self.value))
            before = source.read_bytes()
            with _clock_campaign_review_patch.object(_clock_campaign_review_sys, "argv", ['clock_tools', "--input", str(source), "--output", str(output),
                                           "--expected-plan-sha256", self.value["plan_sha256"]]), \
                 _clock_campaign_review_patch('scripts.clock_tools.os.fstat', side_effect=[source.stat(), root.stat()]), \
                 _clock_campaign_review_redirect_stderr(_clock_campaign_review_io.StringIO()), _clock_campaign_review_redirect_stdout(_clock_campaign_review_io.StringIO()):
                self.assertEqual(_clock_campaign_review_cli._review_clock_campaign_main(), 2)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse(output.exists())

    def test_cli_read_race_fsync_and_publish_failure_cleanup(self):
        for target in ("os.fsync", "os.link", "os.fstat"):
            with self.subTest(target=target), _clock_campaign_review_tempfile.TemporaryDirectory() as directory:
                root = _clock_campaign_review_Path(directory)
                source, output = root / "input.json", root / "output.json"
                source.write_text(_clock_campaign_review_json.dumps(self.value))
                before = source.read_bytes()
                with _clock_campaign_review_patch.object(_clock_campaign_review_sys, "argv", ['clock_tools', "--input", str(source), "--output", str(output),
                                               "--expected-plan-sha256", self.value["plan_sha256"]]), \
                     _clock_campaign_review_patch(f'scripts.clock_tools.{target}', side_effect=OSError("private failure")), \
                     _clock_campaign_review_redirect_stderr(_clock_campaign_review_io.StringIO()) as stderr, _clock_campaign_review_redirect_stdout(_clock_campaign_review_io.StringIO()):
                    self.assertEqual(_clock_campaign_review_cli._review_clock_campaign_main(), 2)
                self.assertEqual(source.read_bytes(), before)
                self.assertEqual([p.name for p in root.iterdir()], ["input.json"])
                self.assertNotIn("private failure", stderr.getvalue())

# Additional review regressions: missing evidence must not hide a known violation.
class ClockCampaignMissingEvidenceRegressionTests(_clock_campaign_review_unittest.TestCase):
    def test_missing_qpc_does_not_erase_exact_known_endpoint_rate_violation(self):
        value = fixture()
        value['campaigns'] = value['campaigns'][:1]
        value['campaigns'][0]['phases'] = value['campaigns'][0]['phases'][:1]
        value['resume'] = None
        data = value['campaigns'][0]['phases'][0]['data']
        data['samples'][10]['qpc_tick'] = None
        for sample in data['samples']:
            for edge in ('before', 'after'):
                sample[f'raw_{edge}_ns'] = sample[f'raw_{edge}_ns'] * 11 // 10
        seal(value)
        report = review_clock_campaign(value, expected_plan_sha256=value['plan_sha256'])
        self.assertIn('observed_exact_boundary_rate', {i['code'] for i in report['issues']})
        self.assertEqual(report['clock_qualification_status'], 'failed')

    def test_missing_qpc_does_not_erase_reversed_external_utc_interval(self):
        value = fixture()
        data = value['campaigns'][0]['phases'][0]['data']
        data['samples'][10]['qpc_tick'] = None
        data['external_utc'][1]['upper_ns'] = 1
        seal(value)
        report = review_clock_campaign(value, expected_plan_sha256=value['plan_sha256'])
        self.assertIn('utc_reference_reversed', {i['code'] for i in report['issues']})
        self.assertEqual(report['clock_qualification_status'], 'failed')


# ========================================================================
# test_clock_campaign_capture
# ========================================================================

"""Fake readers and disposable files only; never actual clock qualification."""


from contextlib import redirect_stderr as _clock_campaign_capture_redirect_stderr, redirect_stdout as _clock_campaign_capture_redirect_stdout
import copy as _clock_campaign_capture_copy
import hashlib as _clock_campaign_capture_hashlib
import io as _clock_campaign_capture_io
import json as _clock_campaign_capture_json
import os as _clock_campaign_capture_os
from pathlib import Path as _clock_campaign_capture_Path
import shutil
import signal
import subprocess as _clock_campaign_capture_subprocess
import sys as _clock_campaign_capture_sys
import tempfile as _clock_campaign_capture_tempfile
import time
import unittest as _clock_campaign_capture_unittest
from unittest.mock import patch as _clock_campaign_capture_patch

from bike_mlops import clock as capture
from bike_mlops.clock import canonical_sha256 as _clock_campaign_capture_canonical_sha256
from scripts import clock_tools as derive_cli

PROJECT = _clock_campaign_capture_Path(__file__).resolve().parents[1]


def samples(rows):
    return [row for row in rows if row["type"] == "sample"]


def rewrite(root, mutate):
    """Rehash attacker-edited records: semantic checks must survive valid hashes."""
    rows = [capture.decode(line)["data"] for line in (root / "journal.jsonl").read_bytes().splitlines()]
    mutate(rows)
    previous, output = "0" * 64, []
    for index, data in enumerate(rows):
        row = {"sequence": index, "previous_sha256": previous, "data": data}
        previous = _clock_campaign_capture_canonical_sha256(row)
        output.append(capture.encode({**row, "sha256": previous}))
    payload = b"".join(output)
    (root / "journal.jsonl").write_bytes(payload)
    marker = capture.decode((root / "complete.json").read_bytes())
    marker.update(journal_sha256=_clock_campaign_capture_hashlib.sha256(payload).hexdigest(), journal_bytes=len(payload),
                  records=len(rows), last_sha256=previous)
    (root / "complete.json").write_bytes(capture.encode(marker))


class ClockCaptureTests(_clock_campaign_capture_unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = _clock_campaign_capture_tempfile.TemporaryDirectory()
        cls.original = _clock_campaign_capture_Path(cls.shared.name) / "raw"
        cls.plan_hash = capture.capture_fake(cls.original)

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temporary = _clock_campaign_capture_tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = _clock_campaign_capture_Path(self.temporary.name)
        self.raw = self.root / "raw"
        shutil.copytree(self.original, self.raw)

    def replay(self):
        return capture.derive_raw(self.raw, expected_plan_sha256=self.plan_hash)

    def test_four_phases_share_original_boundary_and_do_not_qualify(self):
        value, report = self.replay()
        self.assertEqual(report["offline_checks_status"], "passed")
        self.assertEqual(report["clock_qualification_status"], "incomplete")
        self.assertEqual(len(report["qualification_prerequisites_not_established"]), 6)
        for campaign in value["campaigns"]:
            a, b = [p["data"] for p in campaign["phases"]]
            self.assertEqual(len(a["samples"]), 1801)
            self.assertEqual({**a["samples"][-1], "index": 0}, b["samples"][0])
            self.assertEqual(len(a["qpc_calibration"]), 31)
            self.assertEqual(a["qpc_calibration"][0]["reference_lower_ns"], 60 * 10**9 - 2000)
        identity = capture.identity_evidence()
        self.assertEqual(value["campaigns"][0]["phases"][0]["data"]["identity_start"], {
            name + "_sha256": _clock_campaign_capture_canonical_sha256(original) for name, original in identity.items()})

    def test_preconsolidation_source_layout_replays_without_executing_saved_code(self):
        # Old evidence uses six source member names rather than the current two.
        # Source payloads are checked as bytes, never imported or executed.
        names = {"clock_campaign_capture.py", "clock_campaign_fake_reader.py",
                 "clock_campaign_review.py", "clock_rate_review.py",
                 "capture_fake_clock_campaign.py", "derive_clock_campaign.py"}
        sources = {name: b"raise RuntimeError('must not execute archived code')\n".hex() for name in names}
        rows = [capture.decode(line)["data"] for line in (self.raw / "journal.jsonl").read_bytes().splitlines()]
        manifest = rows[0]["manifest"]
        manifest["files"] = {name: _clock_campaign_capture_hashlib.sha256(bytes.fromhex(value)).hexdigest()
                             for name, value in sources.items()}
        plan = capture.decode((self.raw / "plan.json").read_bytes())
        plan["collector_sha256"] = plan["utc_reader_sha256"] = _clock_campaign_capture_canonical_sha256(manifest)
        expected = _clock_campaign_capture_canonical_sha256(plan)
        (self.raw / "sources.json").write_bytes(capture.encode(sources))
        (self.raw / "plan.json").write_bytes(capture.encode(plan))
        rewrite(self.raw, lambda original: original[0].update(manifest=manifest, plan_sha256=expected))
        marker = capture.decode((self.raw / "complete.json").read_bytes())
        marker["plan_sha256"] = expected
        (self.raw / "complete.json").write_bytes(capture.encode(marker))
        _, report = capture.derive_raw(self.raw, expected_plan_sha256=expected)
        self.assertEqual(report["offline_checks_status"], "passed")
        self.assertEqual(report["clock_qualification_status"], "incomplete")
        # Publication stays restricted to the installed current source bytes.
        with self.assertRaises(ValueError):
            capture.export_bundle(self.raw, self.root / "legacy-export.zip", expected_plan_sha256=expected)

    def test_archive_only_copy_in_other_process_then_existing_offline_cli(self):
        detached = self.root / "detached"
        shutil.copytree(self.raw, detached)
        output, report = self.root / "campaign.json", self.root / "report.json"
        command = [_clock_campaign_capture_sys.executable, str(PROJECT / 'scripts/clock_tools.py'), 'derive',
                   "--input-dir", str(detached), "--output", str(output),
                   "--expected-plan-sha256", self.plan_hash]
        result = _clock_campaign_capture_subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        offline = _clock_campaign_capture_subprocess.run([_clock_campaign_capture_sys.executable, str(PROJECT / 'scripts/clock_tools.py'), 'review',
            "--input", str(output), "--output", str(report), "--expected-plan-sha256", self.plan_hash],
            cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(offline.returncode, 2, offline.stderr)
        actual = _clock_campaign_capture_json.loads(report.read_bytes())
        self.assertEqual(actual["offline_checks_status"], "passed")
        self.assertEqual(actual["clock_qualification_status"], "incomplete")
        self.assertEqual(actual["input_sha256"], _clock_campaign_capture_hashlib.sha256(output.read_bytes()).hexdigest())
        before = output.read_bytes()
        self.assertEqual(_clock_campaign_capture_subprocess.run(command, cwd=self.root, capture_output=True, timeout=30).returncode, 2)
        self.assertEqual(output.read_bytes(), before)

    def test_challenge_changes_rejected_even_after_valid_rehash(self):
        for key, replacement in (("nonce", "0" * 32), ("sequence", 1), ("trial", "0" * 32),
                                 ("phase", "fixture-load"), ("role", "post-resume")):
            with self.subTest(key=key):
                shutil.copyfile(self.original / "journal.jsonl", self.raw / "journal.jsonl")
                shutil.copyfile(self.original / "complete.json", self.raw / "complete.json")
                rewrite(self.raw, lambda rows: samples(rows)[0]["response"]["echo"].update({key: replacement}))
                with self.assertRaises(ValueError):
                    self.replay()

    def test_reused_nonce_missing_duplicate_reordered_and_extra_response(self):
        mutations = (
            lambda rows: (samples(rows)[1]["request"].update(nonce=samples(rows)[0]["request"]["nonce"]),
                          rows[4]["request"].update(nonce=samples(rows)[0]["request"]["nonce"]),
                          samples(rows)[1]["response"]["echo"].update(nonce=samples(rows)[0]["request"]["nonce"])),
            lambda rows: rows.pop(rows.index(samples(rows)[1])),
            lambda rows: rows.insert(rows.index(samples(rows)[1]), _clock_campaign_capture_copy.deepcopy(samples(rows)[0])),
            lambda rows: rows.__setitem__(slice(2, 6), list(reversed(rows[2:6]))),
            lambda rows: rows.append(_clock_campaign_capture_copy.deepcopy(samples(rows)[0])),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                shutil.copyfile(self.original / "journal.jsonl", self.raw / "journal.jsonl")
                shutil.copyfile(self.original / "complete.json", self.raw / "complete.json")
                rewrite(self.raw, mutate)
                with self.assertRaises((ValueError, StopIteration)):
                    self.replay()

    def test_saved_request_bracket_cannot_be_replaced_at_response(self):
        rewrite(self.raw, lambda rows: rows[2]["before"].update(monotonic=0))
        # First MONOTONIC is already zero. Change the saved request instead.
        rewrite(self.raw, lambda rows: rows[2]["before"].update(monotonic=1))
        with self.assertRaises(ValueError):
            self.replay()

    def test_frequency_and_read_order_changes_are_protocol_failures(self):
        for key, item in (("qpc_frequency_hz", 1001), ("read_order", ["qpc", "utc-before", "utc-after"])):
            shutil.copyfile(self.original / "journal.jsonl", self.raw / "journal.jsonl")
            shutil.copyfile(self.original / "complete.json", self.raw / "complete.json")
            rewrite(self.raw, lambda rows: samples(rows)[0]["response"].update({key: item}))
            with self.assertRaises(ValueError):
                self.replay()

    def test_process_reuse_namespace_config_changes_remain_known_violations(self):
        def pid(rows):
            samples(rows)[20]["identity"]["process_set"][0]["start_tick"] += 1
        def namespace(rows):
            samples(rows)[20]["identity"]["process_set"][0]["time_namespace"] += 1
        def config(rows):
            samples(rows)[20]["identity"]["sync_configuration"]["active_bytes_hex"] = "00"
        for mutate in (pid, namespace, config):
            shutil.copyfile(self.original / "journal.jsonl", self.raw / "journal.jsonl")
            shutil.copyfile(self.original / "complete.json", self.raw / "complete.json")
            rewrite(self.raw, mutate)
            _, report = self.replay()
            self.assertEqual(report["clock_qualification_status"], "failed")
            self.assertIn("sample_identity_changed", {i["code"] for i in report["issues"]})

    def test_external_reference_and_uncertainty_are_bound_not_self_certified(self):
        rewrite(self.raw, lambda rows: samples(rows)[0]["response"]["reference"].update(uncertainty_ns=0))
        with self.assertRaises(ValueError):
            self.replay()

    def test_future_probe_is_saved_raw_and_rejection_cannot_be_replaced(self):
        def mutate(rows):
            end = next(r for r in rows if r["type"] == "phase-end")
            end["future_probes"][1]["decision"] = "allow"
        rewrite(self.raw, mutate)
        _, report = self.replay()
        self.assertEqual(report["clock_qualification_status"], "failed")
        self.assertIn("strict_future_probe_failed", {i["code"] for i in report["issues"]})

    def test_utc_backwards_is_failed_without_clamp_or_retry(self):
        rewrite(self.raw, lambda rows: samples(rows)[20]["response"].update(utc_before_ns=0))
        _, report = self.replay()
        self.assertEqual(report["clock_qualification_status"], "failed")
        self.assertIn("clock_backwards", {i["code"] for i in report["issues"]})

    def test_missing_qpc_and_known_reversal_do_not_create_success(self):
        def mutate(rows):
            samples(rows)[10]["response"].pop("qpc_tick")
            samples(rows)[20]["response"]["utc_before_ns"] = 0
        rewrite(self.raw, mutate)
        # Acquisition contract rejects missing response fields before numeric review.
        # The original known reversal stays in raw; it is not replaced with a success.
        with self.assertRaises(ValueError):
            self.replay()

    def test_plan_manifest_and_source_bytes_are_bound(self):
        with self.assertRaises(ValueError):
            capture.derive_raw(self.raw, expected_plan_sha256="b" * 64)
        source = self.raw / "sources.json"
        data = _clock_campaign_capture_json.loads(source.read_bytes())
        data['clock.py'] = "00"
        source.write_bytes(capture.encode(data))
        with self.assertRaises(ValueError):
            self.replay()

    def test_marker_chain_framing_and_member_fail_closed(self):
        original = (self.raw / "complete.json").read_bytes()
        for key, item in (("journal_sha256", "b" * 64), ("last_sha256", "b" * 64),
                          ("reader_reaped", False), ("records", True), ("journal_bytes", 1)):
            data = _clock_campaign_capture_json.loads(original)
            data[key] = item
            (self.raw / "complete.json").write_bytes(capture.encode(data))
            with self.assertRaises(ValueError):
                self.replay()
        (self.raw / "complete.json").write_bytes(original)
        (self.raw / "unexpected").touch()
        with self.assertRaises(ValueError):
            self.replay()
        (self.raw / "unexpected").unlink()
        (self.raw / "complete.json").unlink()
        with self.assertRaises(ValueError):
            self.replay()

    def test_hash_chain_detects_modified_original_without_rehash(self):
        path = self.raw / "journal.jsonl"
        raw = path.read_bytes().replace(b'"sequence":20', b'"sequence":21', 1)
        path.write_bytes(raw)
        marker = _clock_campaign_capture_json.loads((self.raw / "complete.json").read_bytes())
        marker.update(journal_sha256=_clock_campaign_capture_hashlib.sha256(raw).hexdigest(), journal_bytes=len(raw))
        (self.raw / "complete.json").write_bytes(capture.encode(marker))
        with self.assertRaises(ValueError):
            self.replay()

    def test_stable_read_rejects_symlink_fifo_oversize_and_visible_change(self):
        source = self.root / "small"
        source.write_bytes(b"{}")
        link, fifo, large = (self.root / x for x in ("link", "fifo", "large"))
        link.symlink_to(source)
        _clock_campaign_capture_os.mkfifo(fifo)
        with large.open("wb") as stream:
            stream.truncate(capture.MAX_BYTES + 1)
        for path in (link, fifo, large, self.root):
            with self.assertRaises((OSError, ValueError)):
                capture.read_stable(path)
        with _clock_campaign_capture_patch.object(capture.os, "fstat", side_effect=[source.stat(), self.root.stat()]):
            with self.assertRaises(ValueError):
                capture.read_stable(source)

    def test_change_after_replay_is_detected(self):
        original = capture._clock_campaign_capture_review_clock_campaign
        def change(value, **kwargs):
            report = original(value, **kwargs)
            (self.raw / "sources.json").write_bytes(b"{}")
            return report
        with _clock_campaign_capture_patch.object(capture, '_clock_campaign_capture_review_clock_campaign', side_effect=change):
            with self.assertRaises(ValueError):
                self.replay()

    def test_same_byte_replacement_during_replay_is_detected(self):
        original = capture._clock_campaign_capture_review_clock_campaign
        def change(value, **kwargs):
            report = original(value, **kwargs)
            path = self.raw / "sources.json"
            payload = path.read_bytes()
            replacement = self.root / "replacement"
            replacement.write_bytes(payload)
            _clock_campaign_capture_os.replace(replacement, path)
            return report
        with _clock_campaign_capture_patch.object(capture, '_clock_campaign_capture_review_clock_campaign', side_effect=change):
            with self.assertRaises(ValueError):
                self.replay()

    def test_derived_output_cannot_mutate_raw_directory(self):
        original = {p.name: p.read_bytes() for p in self.raw.iterdir()}
        with _clock_campaign_capture_patch.object(_clock_campaign_capture_sys, "argv", ["derive", "--input-dir", str(self.raw),
                          "--output", str(self.raw / "derived.json"),
                          "--expected-plan-sha256", self.plan_hash]), _clock_campaign_capture_redirect_stderr(_clock_campaign_capture_io.StringIO()):
            self.assertEqual(derive_cli._derive_clock_campaign_main(), 2)
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.raw.iterdir()})

    def test_raw_identity_unknown_or_private_fields_are_not_accepted(self):
        rewrite(self.raw, lambda rows: samples(rows)[0]["identity"]["process_set"][0].update(argv="private-value"))
        with self.assertRaises(ValueError):
            self.replay()

    def test_unexpected_policy_plan_cannot_be_rebound_by_caller_hash(self):
        plan = capture.decode((self.raw / "plan.json").read_bytes())
        plan["qpc_frequency_hz"] = True
        (self.raw / "plan.json").write_bytes(capture.encode(plan))
        with self.assertRaises(ValueError):
            capture.derive_raw(self.raw, expected_plan_sha256=_clock_campaign_capture_canonical_sha256(plan))

    def test_safe_invalid_cli_does_not_echo_values_or_paths_or_publish(self):
        output = self.root / "output.json"
        (self.raw / "complete.json").write_text('{"private":"do-not-echo","private":1}')
        with _clock_campaign_capture_patch.object(_clock_campaign_capture_sys, "argv", ["derive", "--input-dir", str(self.raw), "--output", str(output),
                          "--expected-plan-sha256", self.plan_hash]), _clock_campaign_capture_redirect_stderr(_clock_campaign_capture_io.StringIO()) as stderr:
            self.assertEqual(derive_cli._derive_clock_campaign_main(), 2)
        self.assertFalse(output.exists())
        self.assertNotIn("do-not-echo", stderr.getvalue())
        self.assertNotIn(str(self.root), stderr.getvalue())

    def test_publish_failure_cleans_staging_without_overwriting(self):
        for target in ("fsync", "link"):
            with _clock_campaign_capture_patch.object(capture.os, target, side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    capture.publish_json(self.root / "out.json", {"synthetic": True})
            self.assertEqual({p.name for p in self.root.iterdir()}, {"raw"})
        output = self.root / "out.json"
        output.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            capture.publish_json(output, {})
        self.assertEqual(output.read_bytes(), b"original")


class ClockCaptureFailureTests(_clock_campaign_capture_unittest.TestCase):
    def test_constructor_failure_reaps_already_spawned_reader(self):
        processes, original = [], _clock_campaign_capture_subprocess.Popen
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        def factory(*args, **kwargs):
            child = original(*args, **kwargs)
            processes.append(child)
            return child
        with _clock_campaign_capture_tempfile.TemporaryDirectory() as directory:
            try:
                with _clock_campaign_capture_patch.object(capture.subprocess, "Popen", side_effect=factory), \
                     _clock_campaign_capture_patch("selectors.EpollSelector.register", side_effect=OSError("synthetic registration failure")):
                    with self.assertRaises(OSError):
                        capture.capture_fake(_clock_campaign_capture_Path(directory) / "attempt")
                self.assertTrue(processes)
                self.assertIsNotNone(processes[0].poll(), "Child survived failed Reader construction")
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), previous_mask)
            finally:
                for child in processes:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=5)

    def test_signal_during_spawn_reaps_reader_before_capture_returns(self):
        processes, original = [], _clock_campaign_capture_subprocess.Popen
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        def interrupted(signum, frame):
            raise InterruptedError("Synthetic startup signal")
        def factory(*args, **kwargs):
            child = original(*args, **kwargs)
            processes.append(child)
            _clock_campaign_capture_os.kill(_clock_campaign_capture_os.getpid(), signal.SIGTERM)
            return child
        previous = signal.signal(signal.SIGTERM, interrupted)
        with _clock_campaign_capture_tempfile.TemporaryDirectory() as directory:
            root = _clock_campaign_capture_Path(directory) / "attempt"
            try:
                with _clock_campaign_capture_patch.object(capture.subprocess, "Popen", side_effect=factory):
                    with self.assertRaises(InterruptedError):
                        capture.capture_fake(root)
                self.assertTrue(processes)
                self.assertIsNotNone(processes[0].poll(), "Startup signal lost ownership of the child")
                self.assertFalse((root / "complete.json").exists())
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), previous_mask)
            finally:
                signal.signal(signal.SIGTERM, previous)
                for child in processes:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=5)

    def test_inflight_request_is_preserved_before_timeout(self):
        with _clock_campaign_capture_tempfile.TemporaryDirectory() as directory:
            root = _clock_campaign_capture_Path(directory) / "attempt"
            with self.assertRaises(TimeoutError):
                capture.capture_fake(root, fault="delay", reader_timeout=1.0)
            rows = [capture.decode(line)["data"] for line in (root / "journal.jsonl").read_bytes().splitlines()]
            pending = [row for row in rows if row["type"] == "request"]
            self.assertTrue(pending, "Request was not saved before the blocked response")
            self.assertEqual(pending[-1]["request"]["sequence"], 20)
            self.assertFalse((root / "complete.json").exists())

    def test_bad_reader_responses_timeouts_and_exit_preserve_partial_and_reap(self):
        original = capture.Reader
        for fault in ("nonce", "sequence", "trial", "duplicate", "frequency", "missing", "delay", "exit", "trailing"):
            with self.subTest(fault=fault), _clock_campaign_capture_tempfile.TemporaryDirectory() as directory:
                readers = []
                def factory(**kwargs):
                    reader = original(**kwargs)
                    readers.append(reader)
                    return reader
                root = _clock_campaign_capture_Path(directory) / "attempt"
                with _clock_campaign_capture_patch.object(capture, "Reader", side_effect=factory):
                    with self.assertRaises((ValueError, OSError)):
                        capture.capture_fake(root, fault=fault, reader_timeout=3.0)
                self.assertFalse((root / "complete.json").exists())
                self.assertGreater((root / "journal.jsonl").stat().st_size, 0)
                self.assertIsNotNone(readers[0].process.poll())
                failure = capture.decode((root / "journal.jsonl").read_bytes().splitlines()[-1])["data"]
                self.assertEqual(failure["type"], "failure")
                self.assertTrue(failure["reader_reaped"])

    def test_journal_disk_failure_reaps_child_and_never_completes(self):
        readers, original = [], capture.Reader
        def factory(**kwargs):
            reader = original(**kwargs)
            readers.append(reader)
            return reader
        def fail(record):
            if record["type"] == "sample":
                raise OSError("synthetic disk full")
        with _clock_campaign_capture_tempfile.TemporaryDirectory() as directory, _clock_campaign_capture_patch.object(capture, "Reader", side_effect=factory):
            root = _clock_campaign_capture_Path(directory) / "attempt"
            with self.assertRaises(OSError):
                capture.capture_fake(root, on_record=fail)
            self.assertFalse((root / "complete.json").exists())
            self.assertIsNotNone(readers[0].process.poll())
            self.assertTrue((root / "journal.jsonl").exists())
            rows = [capture.decode(line) for line in (root / "journal.jsonl").read_bytes().splitlines()]
            self.assertEqual([row["sequence"] for row in rows], list(range(len(rows))))

    def test_journal_fsync_failure_prevents_complete_marker(self):
        original = capture.os.fsync
        calls = 0
        def fail_third(fd):
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise OSError("synthetic final journal disk failure")
            return original(fd)
        with _clock_campaign_capture_tempfile.TemporaryDirectory() as directory, _clock_campaign_capture_patch.object(capture.os, "fsync", side_effect=fail_third):
            root = _clock_campaign_capture_Path(directory) / "attempt"
            with self.assertRaises(OSError):
                capture.capture_fake(root)
            self.assertFalse((root / "complete.json").exists())
            self.assertTrue((root / "journal.jsonl").exists())

    def test_sigterm_and_sigint_real_subprocess_reap_reader_no_marker(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=sig), _clock_campaign_capture_tempfile.TemporaryDirectory() as directory:
                root = _clock_campaign_capture_Path(directory) / "attempt"
                process = _clock_campaign_capture_subprocess.Popen([_clock_campaign_capture_sys.executable, str(PROJECT / 'scripts/clock_tools.py'), 'capture-fake',
                    "--output-dir", str(root), "--fault", "delay"], stdout=_clock_campaign_capture_subprocess.PIPE, stderr=_clock_campaign_capture_subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 5
                    children = []
                    while time.monotonic() < deadline:
                        path = root / "journal.jsonl"
                        if path.exists() and path.read_bytes().count(b"\n") >= 43:
                            children = _clock_campaign_capture_Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()
                            if children:
                                break
                        time.sleep(0.02)
                    self.assertTrue(children)
                    process.send_signal(sig)
                    out, err = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 2, (out, err))
                    self.assertFalse((root / "complete.json").exists())
                    for pid in children:
                        self.assertFalse(_clock_campaign_capture_Path(f"/proc/{pid}").exists(), "Fixture child survived interruption")
                    rows = (root / "journal.jsonl").read_bytes().splitlines()
                    self.assertTrue(capture.decode(rows[-1])["data"]["reader_reaped"])
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

    def test_existing_attempt_never_deleted_or_reused(self):
        with _clock_campaign_capture_tempfile.TemporaryDirectory() as directory:
            root = _clock_campaign_capture_Path(directory)
            sentinel = root / "operator-file"
            sentinel.write_bytes(b"protected")
            with self.assertRaises(FileExistsError):
                capture.capture_fake(root)
            self.assertEqual(sentinel.read_bytes(), b"protected")
            self.assertEqual([p.name for p in root.iterdir()], ["operator-file"])


# ========================================================================
# test_clock_campaign_schedule
# ========================================================================

"""Virtual counters and disposable child readers only; no real qualification."""


import json as _clock_campaign_schedule_json
import os as _clock_campaign_schedule_os
from pathlib import Path as _clock_campaign_schedule_Path
import shutil as _clock_campaign_schedule_shutil
import signal as _clock_campaign_schedule_signal
import subprocess as _clock_campaign_schedule_subprocess
import sys as _clock_campaign_schedule_sys
import tempfile as _clock_campaign_schedule_tempfile
import time as _clock_campaign_schedule_time
import unittest as _clock_campaign_schedule_unittest
from unittest.mock import patch as _clock_campaign_schedule_patch

from bike_mlops import clock as raw
from bike_mlops import clock as scheduling
_clock_campaign_schedule_rewrite = rewrite

_clock_campaign_schedule_PROJECT = _clock_campaign_schedule_Path(__file__).resolve().parents[1]


def rows(root):
    return [raw.decode(line)["data"] for line in (root / "journal.jsonl").read_bytes().splitlines()]


class FixedSlotTests(_clock_campaign_schedule_unittest.TestCase):
    def test_exact_lattice_no_catchup_or_phase_extension(self):
        for origin in (0, 1800000, 4000000):
            for index in (0, 1, 20, 1799, 1800):
                due = origin + index * 1000
                self.assertEqual(scheduling.slot_decision(origin, index, due)["action"], "dispatch")
                self.assertEqual(scheduling.slot_decision(origin, index, due + 1)["action"], "missed")
                self.assertEqual(scheduling.slot_decision(origin, index, due + 1)["deadline_tick"], origin + 1800000)
        for args in ((0, True, 1000), (0, 1801, 1801000), (0, 1, 999), (-1, 0, 0)):
            with self.assertRaises(ValueError):
                scheduling.slot_decision(*args)

    def test_fake_wait_overshoot_skips_without_rebasing(self):
        clock = scheduling.FakeQPC("wake-late")
        observed = [clock.wake(0, 0, i) for i in range(20, 25)]
        self.assertEqual(observed, [22500, 22500, 22500, 23000, 24000])
        self.assertEqual([scheduling.slot_decision(0, i, t)["action"] for i, t in zip(range(20, 25), observed)],
                         ["missed", "missed", "missed", "dispatch", "dispatch"])

    def test_frequency_policy_bool_and_unknown_source_flags_rejected(self):
        for mutate in (lambda r: r["scheduler"].update(period_ticks=999),
                       lambda r: r["scheduler"].update(extend_phase=True),
                       lambda r: r["scheduler"].update(catch_up=0),
                       lambda r: r.update(independent=True),
                       lambda r: r.update(scenario="live")):
            recipe = scheduling.recipe_for("punctual")
            mutate(recipe)
            with self.assertRaises(ValueError):
                scheduling.validate_recipe(recipe)


class ScheduledCaptureTests(_clock_campaign_schedule_unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = _clock_campaign_schedule_tempfile.TemporaryDirectory()
        cls.original = _clock_campaign_schedule_Path(cls.shared.name) / "raw"
        cls.plan_hash = raw.capture_fake(cls.original, schedule="punctual")

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temporary = _clock_campaign_schedule_tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = _clock_campaign_schedule_Path(self.temporary.name)
        self.raw = self.root / "raw"
        _clock_campaign_schedule_shutil.copytree(self.original, self.raw)

    def replay(self):
        return raw.derive_raw(self.raw, expected_plan_sha256=self.plan_hash)

    def test_normal_four_phases_exact_deadline_source_binding_not_qualification(self):
        value, report = self.replay()
        self.assertEqual(report["offline_checks_status"], "passed")
        self.assertEqual(report["clock_qualification_status"], "incomplete")
        saved = rows(self.raw)
        self.assertEqual(len(saved), 14415)
        self.assertEqual(sum(r["type"] == "request" for r in saved), 7202)
        self.assertIn('clock.py', saved[0]["manifest"]["files"])
        self.assertEqual(value["plan"]["workload_sha256"], raw._clock_campaign_capture_canonical_sha256(saved[0]["recipe"]))
        for ci, c in enumerate(value["campaigns"]):
            for pi, p in enumerate(c["phases"]):
                data = p["data"]
                self.assertEqual(data["lost_samples"], 0)
                self.assertEqual([s["qpc_tick"] for s in data["samples"]],
                                 [(ci * 4000 + pi * 1800 + i) * 1000 for i in range(1801)])

    def test_raw_copy_separate_process_through_existing_offline_cli(self):
        copied = self.root / "detached"
        _clock_campaign_schedule_shutil.copytree(self.raw, copied)
        output, report = self.root / "campaign.json", self.root / "report.json"
        derived = _clock_campaign_schedule_subprocess.run([_clock_campaign_schedule_sys.executable, str(_clock_campaign_schedule_PROJECT / 'scripts/clock_tools.py'), 'derive',
            "--input-dir", str(copied), "--output", str(output), "--expected-plan-sha256", self.plan_hash],
            cwd=self.root, capture_output=True, timeout=30)
        self.assertEqual(derived.returncode, 2, derived.stderr)
        reviewed = _clock_campaign_schedule_subprocess.run([_clock_campaign_schedule_sys.executable, str(_clock_campaign_schedule_PROJECT / 'scripts/clock_tools.py'), 'review',
            "--input", str(output), "--output", str(report), "--expected-plan-sha256", self.plan_hash],
            cwd=self.root, capture_output=True, timeout=30)
        self.assertEqual(reviewed.returncode, 2, reviewed.stderr)
        self.assertEqual(_clock_campaign_schedule_json.loads(report.read_bytes())["offline_checks_status"], "passed")

    def test_rehashed_schedule_dispatch_deadline_or_completion_lies_refused(self):
        for mutate in (
            lambda r: next(x for x in r if x["type"] == "phase-start").update(deadline_tick=1801000),
            lambda r: next(x for x in r if x["type"] == "request")["schedule"].update(observed_tick=1),
            lambda r: next(x for x in r if x["type"] == "request")["schedule"].update(due_tick=1),
            lambda r: next(x for x in r if x["type"] == "sample").update(completed_tick=1),
            lambda r: next(x for x in r if x["type"] == "sample").update(late=1),
            lambda r: next(x for x in r if x["type"] == "phase-end").update(lost_samples=1),
        ):
            with self.subTest(mutate=mutate):
                _clock_campaign_schedule_shutil.copyfile(self.original / "journal.jsonl", self.raw / "journal.jsonl")
                _clock_campaign_schedule_shutil.copyfile(self.original / "complete.json", self.raw / "complete.json")
                _clock_campaign_schedule_rewrite(self.raw, mutate)
                with self.assertRaises(ValueError):
                    self.replay()

    def capture_replay(self, scenario, fault="none"):
        target = self.root / scenario
        plan_hash = raw.capture_fake(target, schedule=scenario, fault=fault)
        value, report = raw.derive_raw(target, expected_plan_sha256=plan_hash)
        return target, value, report

    def test_wake_late_saves_exact_gaps_and_fixed_end_without_catchup(self):
        target, value, report = self.capture_replay("wake-late")
        first = value["campaigns"][0]["phases"][0]["data"]
        self.assertEqual([s["index"] for s in first["samples"] if s["qpc_tick"] is None], [20, 21, 22])
        self.assertEqual(first["lost_samples"], 3)
        self.assertEqual(first["samples"][-1]["qpc_tick"], 1800000)
        self.assertEqual(first["samples"][23]["qpc_tick"], 23000)
        self.assertEqual(report["offline_checks_status"], "incomplete")
        requests = [r["request"]["sequence"] for r in rows(target) if r["type"] == "request"
                    and r["request"]["role"] == "normal" and r["request"]["phase"] == "idle"]
        self.assertEqual(requests, [i for i in range(1801) if i not in (20, 21, 22)])

    def test_response_late_retains_observed_bracket_and_known_violation(self):
        target, value, report = self.capture_replay("response-late", fault="reverse")
        first = value["campaigns"][0]["phases"][0]["data"]
        self.assertEqual(first["lost_samples"], 3)  # late20 plus missing21/22
        self.assertIsNone(first["samples"][21]["qpc_tick"])
        self.assertEqual(first["samples"][20]["realtime_after_ns"] - first["samples"][20]["realtime_before_ns"], 2500000000)
        self.assertEqual(first["samples"][20]["windows_utc_before_ns"], 0)
        self.assertEqual(report["clock_qualification_status"], "failed")
        self.assertIn("clock_backwards", {i["code"] for i in report["issues"]})
        sample = [r for r in rows(target) if r["type"] == "sample"][20]
        self.assertTrue(sample["late"])

    def test_phase_deadline_never_sends_after_cutoff_or_shifts_next_origin(self):
        target, value, report = self.capture_replay("deadline")
        first, second = [p["data"] for p in value["campaigns"][0]["phases"]]
        self.assertEqual(first["lost_samples"], 1781)
        self.assertEqual(second["lost_samples"], 1)  # missing shared boundary
        self.assertIsNone(first["samples"][-1]["qpc_tick"])
        self.assertEqual(second["samples"][1]["qpc_tick"], 1801000)
        self.assertEqual(len(first["samples"]), 1801)
        self.assertEqual(report["offline_checks_status"], "incomplete")
        saved = rows(target)
        starts = [r for r in saved if r["type"] == "phase-start"]
        self.assertEqual(starts[1]["origin_tick"], 1800000)
        self.assertEqual(starts[1]["observed_tick"], 1801000)
        self.assertFalse(any(r["type"] == "request" and r["request"]["phase"] == "idle"
                             and r["request"]["role"] == "normal" and r["request"]["sequence"] >= 20 for r in saved))

    def test_endpoint_late_is_retained_and_shared_loss_propagated(self):
        target, value, report = self.capture_replay("endpoint-late")
        a, b = [p["data"] for p in value["campaigns"][0]["phases"]]
        self.assertEqual(a["lost_samples"], 1)
        self.assertEqual(b["lost_samples"], 1)
        self.assertEqual({**a["samples"][-1], "index": 0}, b["samples"][0])
        self.assertEqual(a["samples"][-1]["qpc_tick"], 1800000)
        self.assertEqual(report["offline_checks_status"], "incomplete")
        self.assertTrue(next(r for r in rows(target) if r["type"] == "sample" and r["late"])["completed_tick"] > 1800000)

    def test_gap_does_not_hide_identity_or_future_rejection_violation(self):
        target, _, report = self.capture_replay("wake-late", fault="identity")
        self.assertEqual(report["clock_qualification_status"], "failed")
        self.assertTrue(any(i["code"] == "sample_identity_changed" and i.get("campaign") == 0
                            and i.get("phase") == 0 for i in report["issues"]),
                        "Identity violation must coexist with the gap in the same phase")
        plan_hash = raw._clock_campaign_capture_canonical_sha256(raw.decode((target / "plan.json").read_bytes()))
        def mutate(saved):
            next(r for r in saved if r["type"] == "phase-end")["future_probes"][1]["decision"] = "allow"
        _clock_campaign_schedule_rewrite(target, mutate)
        _, report = raw.derive_raw(target, expected_plan_sha256=plan_hash)
        self.assertIn("strict_future_probe_failed", {i["code"] for i in report["issues"]})

    def test_rehashed_missing_slot_cannot_be_removed_or_converted_to_request(self):
        target, _, _ = self.capture_replay("wake-late")
        plan_hash = raw._clock_campaign_capture_canonical_sha256(raw.decode((target / "plan.json").read_bytes()))
        _clock_campaign_schedule_rewrite(target, lambda saved: saved.remove(next(r for r in saved if r["type"] == "missed")))
        with self.assertRaises(ValueError):
            raw.derive_raw(target, expected_plan_sha256=plan_hash)

    def test_qpc_backwards_and_interruption_save_error_and_reap_child(self):
        original = raw.Reader
        for scenario in ("qpc-backwards", "interrupt"):
            readers = []
            def factory(**kwargs):
                reader = original(**kwargs)
                readers.append(reader)
                return reader
            target = self.root / scenario
            with _clock_campaign_schedule_patch.object(raw, "Reader", side_effect=factory):
                with self.assertRaises((ValueError, InterruptedError)):
                    raw.capture_fake(target, schedule=scenario)
            self.assertIsNotNone(readers[0].process.poll())
            self.assertFalse((target / "complete.json").exists())
            saved = rows(target)
            self.assertEqual(saved[-2]["type"], "scheduler-error")
            self.assertEqual(saved[-2]["sequence"], 20)
            self.assertTrue(saved[-1]["reader_reaped"])
            if scenario == "qpc-backwards":
                self.assertLess(saved[-2]["observed_tick"], saved[-2]["previous_tick"])

    def test_sigint_sigterm_while_request_pending_preserve_intent_and_reap(self):
        for sig in (_clock_campaign_schedule_signal.SIGINT, _clock_campaign_schedule_signal.SIGTERM):
            with self.subTest(sig=sig):
                target = self.root / f"signal-{sig}"
                process = _clock_campaign_schedule_subprocess.Popen([_clock_campaign_schedule_sys.executable, str(_clock_campaign_schedule_PROJECT / 'scripts/clock_tools.py'), 'capture-fake',
                    "--output-dir", str(target), "--schedule", "punctual", "--fault", "delay"],
                    stdout=_clock_campaign_schedule_subprocess.PIPE, stderr=_clock_campaign_schedule_subprocess.PIPE)
                try:
                    deadline = _clock_campaign_schedule_time.monotonic() + 5
                    children = []
                    while _clock_campaign_schedule_time.monotonic() < deadline:
                        path = target / "journal.jsonl"
                        if path.exists() and path.read_bytes().count(b"\n") >= 43:
                            children = _clock_campaign_schedule_Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()
                            if children:
                                break
                        _clock_campaign_schedule_time.sleep(0.02)
                    self.assertTrue(children)
                    self.assertEqual(rows(target)[-1]["type"], "request")
                    process.send_signal(sig)
                    out, err = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 2, (out, err))
                    self.assertFalse((target / "complete.json").exists())
                    self.assertTrue(rows(target)[-1]["reader_reaped"])
                    for pid in children:
                        self.assertFalse(_clock_campaign_schedule_Path(f"/proc/{pid}").exists())
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

    def test_io_timeout_and_disk_failure_reap_without_marker(self):
        original = raw.Reader
        for fault in ("delay", "disk"):
            readers = []
            def factory(**kwargs):
                reader = original(**kwargs)
                readers.append(reader)
                return reader
            def fail(record):
                if fault == "disk" and record["type"] == "sample":
                    raise OSError("fixture disk failure")
            target = self.root / fault
            with _clock_campaign_schedule_patch.object(raw, "Reader", side_effect=factory):
                with self.assertRaises(OSError):
                    raw.capture_fake(target, schedule="punctual", fault="delay" if fault == "delay" else "none",
                                     reader_timeout=3, on_record=fail)
            self.assertIsNotNone(readers[0].process.poll())
            self.assertFalse((target / "complete.json").exists())
            self.assertTrue(rows(target)[-1]["reader_reaped"])

    def test_unknown_scenario_does_not_create_attempt(self):
        target = self.root / "unknown"
        with self.assertRaises(ValueError):
            raw.capture_fake(target, schedule="live")
        self.assertFalse(target.exists())

    def test_startup_signal_and_registration_failure_reap_restore_mask(self):
        original = _clock_campaign_schedule_subprocess.Popen
        mask = _clock_campaign_schedule_signal.pthread_sigmask(_clock_campaign_schedule_signal.SIG_BLOCK, set())
        def interrupted(signum, frame):
            raise InterruptedError("Fixture startup interruption")
        for case in ("signal", "register"):
            children = []
            def spawn(*args, **kwargs):
                child = original(*args, **kwargs)
                children.append(child)
                if case == "signal":
                    _clock_campaign_schedule_os.kill(_clock_campaign_schedule_os.getpid(), _clock_campaign_schedule_signal.SIGTERM)
                return child
            old_handler = _clock_campaign_schedule_signal.signal(_clock_campaign_schedule_signal.SIGTERM, interrupted)
            try:
                target = self.root / case
                with _clock_campaign_schedule_patch.object(_clock_campaign_schedule_subprocess, "Popen", side_effect=spawn):
                    if case == "register":
                        with _clock_campaign_schedule_patch("selectors.EpollSelector.register", side_effect=OSError("fixture register")):
                            with self.assertRaises(OSError):
                                raw.capture_fake(target, schedule="punctual")
                    else:
                        with self.assertRaises(InterruptedError):
                            raw.capture_fake(target, schedule="punctual")
                self.assertTrue(children)
                self.assertIsNotNone(children[0].poll())
                self.assertEqual(_clock_campaign_schedule_signal.pthread_sigmask(_clock_campaign_schedule_signal.SIG_BLOCK, set()), mask)
                self.assertFalse((target / "complete.json").exists())
            finally:
                _clock_campaign_schedule_signal.signal(_clock_campaign_schedule_signal.SIGTERM, old_handler)
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=5)

    def test_interruption_after_gap_keeps_original_known_utc_violation(self):
        def stop(record):
            if record["type"] == "sample" and record["request"]["sequence"] == 23:
                raise InterruptedError("fixture interrupted after known bad response")
        target = self.root / "partial-known"
        with self.assertRaises(InterruptedError):
            raw.capture_fake(target, schedule="wake-late", fault="reverse", on_record=stop)
        saved = rows(target)
        self.assertEqual([r["sequence"] for r in saved if r["type"] == "missed"], [20, 21, 22])
        self.assertEqual(saved[-2]["response"]["utc_before_ns"], 0)
        self.assertTrue(saved[-1]["reader_reaped"])
        self.assertFalse((target / "complete.json").exists())

    def test_late_flag_cannot_be_cleared_even_after_rehash(self):
        target, _, _ = self.capture_replay("endpoint-late")
        plan_hash = raw._clock_campaign_capture_canonical_sha256(raw.decode((target / "plan.json").read_bytes()))
        _clock_campaign_schedule_rewrite(target, lambda saved: next(r for r in saved if r["type"] == "sample" and r["late"]).update(late=False))
        with self.assertRaises(ValueError):
            raw.derive_raw(target, expected_plan_sha256=plan_hash)


# ========================================================================
# test_clock_raw_bundle
# ========================================================================

"""Disposable fake raw only; bundle hashes are not source authentication."""


import contextlib
import hashlib as _clock_raw_bundle_hashlib
import io as _clock_raw_bundle_io
import json as _clock_raw_bundle_json
import os as _clock_raw_bundle_os
from pathlib import Path as _clock_raw_bundle_Path
import shutil as _clock_raw_bundle_shutil
import signal as _clock_raw_bundle_signal
import stat
import struct
import subprocess as _clock_raw_bundle_subprocess
import sys as _clock_raw_bundle_sys
import tempfile as _clock_raw_bundle_tempfile
import unittest as _clock_raw_bundle_unittest
import zipfile
from unittest.mock import patch as _clock_raw_bundle_patch

from bike_mlops import clock as _clock_raw_bundle_raw
from bike_mlops import clock as bundle
from scripts import clock_tools as _clock_raw_bundle_cli
_clock_raw_bundle_rewrite = rewrite
_clock_raw_bundle_samples = samples

_clock_raw_bundle_PROJECT = _clock_raw_bundle_Path(__file__).resolve().parents[1]


class ClockBundleMalformedInputTests(_clock_raw_bundle_unittest.TestCase):
    """Parser failures must refuse input before replay or report publication."""

    def setUp(self):
        self.temp = _clock_raw_bundle_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = _clock_raw_bundle_Path(self.temp.name)
        self.archive = self.root / "PRIVATE_ARCHIVE_CANARY.zip"
        # ZipFile parses the required version while reading the directory,
        # before our member allowlist or deterministic encoding check runs.
        original = bundle._zip_bytes({name: b"PRIVATE_PAYLOAD_CANARY" for name in bundle.NAMES})
        central = original.index(b"PK\x01\x02")
        self.variants = {}
        for name, local_flags, central_flags in (
                ("version-99", 0, 0), ("patched-data", 32, 32),
                ("strong-encryption", 64, 64),
                ("local-patched-data", 32, 0), ("local-strong-encryption", 64, 0)):
            payload = bytearray(original)
            if name == "version-99":
                struct.pack_into("<H", payload, 4, 99)
                struct.pack_into("<H", payload, central + 6, 99)
            struct.pack_into("<H", payload, 6, local_flags)
            struct.pack_into("<H", payload, central + 8, central_flags)
            self.variants[name] = bytes(payload)

    def test_unsupported_zip_features_refused_as_invalid_input_before_replay(self):
        for name, payload in self.variants.items():
            with self.subTest(feature=name):
                self.archive.write_bytes(payload)
                with _clock_raw_bundle_patch.object(bundle, "_replay") as replay:
                    with self.assertRaises(ValueError):
                        bundle.review_bundle(self.archive, expected_plan_sha256="a" * 64)
                    replay.assert_not_called()
                self.assertEqual(self.archive.read_bytes(), payload)

    def test_cli_unsupported_zip_features_are_sanitized_and_write_no_report(self):
        output = self.root / "report.json"
        for name, payload in self.variants.items():
            with self.subTest(feature=name):
                self.archive.write_bytes(payload)
                result = _clock_raw_bundle_subprocess.run(
                    [_clock_raw_bundle_sys.executable, str(_clock_raw_bundle_PROJECT / 'scripts/clock_tools.py'), 'bundle',
                     "--review-archive", str(self.archive), "--output", str(output),
                     "--expected-plan-sha256", "a" * 64],
                    cwd=self.root, env={"PATH": _clock_raw_bundle_os.environ["PATH"]},
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr,
                                 "Synthetic bundle invalid or unavailable; no qualification\n")
                self.assertFalse(output.exists())
                self.assertEqual(list(self.root.iterdir()), [self.archive])
                for private in ("Traceback", str(self.root), "PRIVATE_", str(_clock_raw_bundle_PROJECT)):
                    self.assertNotIn(private, result.stdout + result.stderr)


class ClockBundleTests(_clock_raw_bundle_unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = _clock_raw_bundle_tempfile.TemporaryDirectory()
        cls.original = _clock_raw_bundle_Path(cls.shared.name) / "original"
        cls.plan = _clock_raw_bundle_raw.capture_fake(cls.original, schedule="punctual")
        cls.reference_campaign, cls.reference_report = _clock_raw_bundle_raw.derive_raw(
            cls.original, expected_plan_sha256=cls.plan)
        cls.archive = _clock_raw_bundle_Path(cls.shared.name) / "original.zip"
        bundle.export_bundle(cls.original, cls.archive, expected_plan_sha256=cls.plan)
        cls.zip_bytes = cls.archive.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temp = _clock_raw_bundle_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = _clock_raw_bundle_Path(self.temp.name)
        self.raw = self.root / "raw"
        _clock_raw_bundle_shutil.copytree(self.original, self.raw)
        self.archive = self.root / "bundle.zip"
        self.archive.write_bytes(self.zip_bytes)

    def review(self):
        return bundle.review_bundle(self.archive, expected_plan_sha256=self.plan)

    def payloads(self):
        with zipfile.ZipFile(self.archive) as stream:
            return {name: stream.read(name) for name in stream.namelist()}

    def replace_payloads(self, payloads, *, rehash=False):
        if rehash:
            value = _clock_raw_bundle_raw.decode(payloads["manifest.json"])
            value["members"] = {name: {"bytes": len(data), "sha256": _clock_raw_bundle_hashlib.sha256(data).hexdigest()}
                                for name, data in payloads.items() if name != "manifest.json"}
            payloads["manifest.json"] = _clock_raw_bundle_raw.encode(value)
        self.archive.write_bytes(bundle._zip_bytes(payloads))

    def test_exact_raw_and_report_round_trip_no_qualification(self):
        value = self.review()
        self.assertEqual(value["review"], self.reference_report)
        self.assertEqual(value["campaign_sha256"], _clock_raw_bundle_raw._clock_campaign_capture_canonical_sha256(self.reference_campaign))
        self.assertEqual(value["archive_sha256"], _clock_raw_bundle_hashlib.sha256(self.zip_bytes).hexdigest())
        self.assertEqual(set(value["members"]), bundle.NAMES)
        self.assertEqual(value["review"]["offline_checks_status"], "passed")
        self.assertEqual(value["review"]["clock_qualification_status"], "incomplete")
        for name in _clock_raw_bundle_raw.MEMBERS:
            self.assertEqual(self.payloads()["raw/" + name], (self.raw / name).read_bytes())

    def test_archive_only_child_without_originals_environment_or_cwd(self):
        # Delete this attempt's original directory. Child receives only the ZIP,
        # installed verifier, expected plan and a separate exclusive output name.
        _clock_raw_bundle_shutil.rmtree(self.raw)
        detached = self.root / "detached"
        detached.mkdir()
        output = detached / "report.json"
        command = [_clock_raw_bundle_sys.executable, str(_clock_raw_bundle_PROJECT / 'scripts/clock_tools.py'), 'bundle',
                   "--review-archive", str(self.archive), "--output", str(output),
                   "--expected-plan-sha256", self.plan]
        result = _clock_raw_bundle_subprocess.run(command, cwd=detached, env={"PATH": _clock_raw_bundle_os.environ["PATH"]},
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        value = _clock_raw_bundle_json.loads(output.read_bytes())
        self.assertEqual(value["review"], self.reference_report)
        self.assertEqual(value["campaign_sha256"], _clock_raw_bundle_raw._clock_campaign_capture_canonical_sha256(self.reference_campaign))
        self.assertEqual(list(detached.iterdir()), [output])

    def test_legacy_unscheduled_raw_is_supported_without_fake_scheduler(self):
        directory = self.root / "legacy"
        plan = _clock_raw_bundle_raw.capture_fake(directory)
        target = self.root / "legacy.zip"
        bundle.export_bundle(directory, target, expected_plan_sha256=plan)
        report = bundle.review_bundle(target, expected_plan_sha256=plan)
        self.assertEqual(report["review"]["offline_checks_status"], "passed")

    def test_wrong_independent_plan_or_corrupted_zip_refused(self):
        with self.assertRaises(ValueError):
            bundle.review_bundle(self.archive, expected_plan_sha256="b" * 64)
        self.archive.write_bytes(b"not a zip")
        with self.assertRaises(zipfile.BadZipFile):
            self.review()

    def test_missing_extra_traversal_absolute_and_duplicate_member_refused(self):
        original = self.payloads()
        variants = []
        missing = dict(original)
        missing.pop("raw/complete.json")
        variants.append(missing)
        for name in ("extra.txt", "../outside", "/absolute", "raw/../plan.json", "raw\\plan.json"):
            variants.append({**original, name: b"private text"})
        for data in variants:
            self.replace_payloads(data)
            with self.assertRaises(ValueError):
                self.review()
        self.replace_payloads(original)
        with zipfile.ZipFile(self.archive, "a") as stream:
            with contextlib.redirect_stderr(_clock_raw_bundle_io.StringIO()):
                stream.writestr("raw/plan.json", original["raw/plan.json"])
        with self.assertRaises(ValueError):
            self.review()
        self.assertFalse((self.root / "outside").exists())

    def test_symlink_directory_and_compression_refused_without_extraction(self):
        original = self.payloads()
        for mode, compression in ((stat.S_IFLNK, zipfile.ZIP_STORED),
                                  (stat.S_IFDIR, zipfile.ZIP_STORED),
                                  (stat.S_IFREG, zipfile.ZIP_DEFLATED)):
            with zipfile.ZipFile(self.archive, "w", compression=compression) as stream:
                for name, payload in original.items():
                    item = zipfile.ZipInfo(name)
                    item.create_system = 3
                    item.external_attr = (mode | 0o600) << 16
                    item.compress_type = compression
                    stream.writestr(item, payload)
            with self.assertRaises(ValueError):
                self.review()

    def test_member_digest_and_rehashed_marker_corruption_refused(self):
        original = self.payloads()
        changed = dict(original)
        changed["raw/journal.jsonl"] += b"\n"
        self.replace_payloads(changed)
        with self.assertRaises(ValueError):
            self.review()
        self.replace_payloads(changed, rehash=True)
        with self.assertRaises(ValueError):
            self.review()
        changed = dict(original)
        marker = _clock_raw_bundle_raw.decode(changed["raw/complete.json"])
        marker["reader_reaped"] = False
        changed["raw/complete.json"] = _clock_raw_bundle_raw.encode(marker)
        self.replace_payloads(changed, rehash=True)
        with self.assertRaises(ValueError):
            self.review()

    def test_rehashed_journal_lies_and_source_substitution_do_not_pass(self):
        _clock_raw_bundle_rewrite(self.raw, lambda rows: _clock_raw_bundle_samples(rows)[0]["response"]["echo"].update(nonce="0" * 32))
        payloads = {"raw/" + name: (self.raw / name).read_bytes() for name in _clock_raw_bundle_raw.MEMBERS}
        payloads["manifest.json"] = self.payloads()["manifest.json"]
        self.replace_payloads(payloads, rehash=True)
        with self.assertRaises(ValueError):
            self.review()
        data = self.payloads()
        sources = _clock_raw_bundle_raw.decode(data["raw/sources.json"])
        sources['clock.py'] = b"raise RuntimeError('DO NOT EXECUTE')".hex()
        data["raw/sources.json"] = _clock_raw_bundle_raw.encode(sources)
        self.replace_payloads(data, rehash=True)
        with self.assertRaises(ValueError):
            self.review()

    def test_export_refuses_arbitrary_source_or_extra_private_file(self):
        target = self.root / "new.zip"
        (self.raw / ".env").write_text("PRIVATE_CANARY=fixture")
        with self.assertRaises(ValueError):
            bundle.export_bundle(self.raw, target, expected_plan_sha256=self.plan)
        (self.raw / ".env").unlink()
        sources = _clock_raw_bundle_raw.decode((self.raw / "sources.json").read_bytes())
        sources['clock.py'] = b"PRIVATE_CANARY".hex()
        (self.raw / "sources.json").write_bytes(_clock_raw_bundle_raw.encode(sources))
        with self.assertRaises(ValueError):
            bundle.export_bundle(self.raw, target, expected_plan_sha256=self.plan)
        self.assertFalse(target.exists())

    def test_rehashed_known_violation_and_gap_preserved_through_bundle(self):
        _clock_raw_bundle_rewrite(self.raw, lambda rows: _clock_raw_bundle_samples(rows)[20]["response"].update(utc_before_ns=0))
        target = self.root / "failed.zip"
        bundle.export_bundle(self.raw, target, expected_plan_sha256=self.plan)
        value = bundle.review_bundle(target, expected_plan_sha256=self.plan)
        self.assertEqual(value["review"]["clock_qualification_status"], "failed")
        self.assertIn("clock_backwards", {i["code"] for i in value["review"]["issues"]})
        directory = self.root / "gap"
        plan = _clock_raw_bundle_raw.capture_fake(directory, schedule="wake-late")
        target = self.root / "gap.zip"
        bundle.export_bundle(directory, target, expected_plan_sha256=plan)
        value = bundle.review_bundle(target, expected_plan_sha256=plan)
        self.assertEqual(value["review"]["offline_checks_status"], "incomplete")
        self.assertEqual(value["review"]["clock_qualification_status"], "incomplete")

    def test_exclusive_output_and_input_symlinks_refused(self):
        before = self.archive.read_bytes()
        with self.assertRaises(FileExistsError):
            bundle.export_bundle(self.raw, self.archive, expected_plan_sha256=self.plan)
        self.assertEqual(self.archive.read_bytes(), before)
        alias = self.root / "alias.zip"
        alias.symlink_to(self.archive)
        with self.assertRaises(OSError):
            bundle.review_bundle(alias, expected_plan_sha256=self.plan)
        target = self.root / "new.zip"
        original = self.raw / "plan.json"
        data = original.read_bytes()
        original.unlink()
        saved = self.root / "saved.json"
        saved.write_bytes(data)
        original.symlink_to(saved)
        with self.assertRaises(OSError):
            bundle.export_bundle(self.raw, target, expected_plan_sha256=self.plan)
        self.assertFalse(target.exists())

    def test_export_rechecks_original_changes_after_replay(self):
        real = bundle._validate
        def change(*args):
            result = real(*args)
            (self.raw / "plan.json").write_bytes((self.raw / "plan.json").read_bytes() + b" ")
            return result
        target = self.root / "new.zip"
        with _clock_raw_bundle_patch.object(bundle, "_validate", side_effect=change):
            with self.assertRaises(ValueError):
                bundle.export_bundle(self.raw, target, expected_plan_sha256=self.plan)
        self.assertFalse(target.exists())

    def test_size_limits_manifest_duplicate_keys_and_nan_refused(self):
        with _clock_raw_bundle_patch.object(bundle, "ZIP_LIMIT", 100):
            with self.assertRaises(ValueError):
                self.review()
        # Distinguish the total raw budget from the separate manifest allowance.
        raw_size = sum(len(data) for name, data in self.payloads().items()
                       if name != "manifest.json")
        with _clock_raw_bundle_patch.object(bundle, "LIMIT", raw_size - 1):
            with self.assertRaises(ValueError):
                self.review()
        payloads = self.payloads()
        for data in (b'{"kind":"x","kind":"y"}', b'{"kind":NaN}', b"x" * 16385):
            payloads["manifest.json"] = data
            self.replace_payloads(payloads)
            with self.assertRaises(ValueError):
                self.review()

    def test_noncanonical_zip_metadata_cannot_smuggle_extra_content(self):
        self.archive.write_bytes(self.zip_bytes + b"PRIVATE_TRAILING_CANARY")
        with self.assertRaises(ValueError):
            self.review()
        self.archive.write_bytes(self.zip_bytes)
        with zipfile.ZipFile(self.archive, "a") as stream:
            stream.comment = b"PRIVATE_COMMENT_CANARY"
        with self.assertRaises(ValueError):
            self.review()

    def test_partial_raw_cannot_gain_complete_bundle(self):
        (self.raw / "complete.json").unlink()
        with self.assertRaises(ValueError):
            bundle.export_bundle(self.raw, self.root / "partial.zip", expected_plan_sha256=self.plan)
        self.assertFalse((self.root / "partial.zip").exists())

    def test_failed_publication_and_interruption_remove_only_private_staging(self):
        before = set(self.root.iterdir())
        for error in (OSError("fixture disk failure"), KeyboardInterrupt()):
            with _clock_raw_bundle_patch.object(bundle._clock_raw_bundle_os, "link", side_effect=error):
                with self.assertRaises(type(error)):
                    bundle._publish(self.root / "unpublished.zip", b"fixture")
            self.assertEqual(set(self.root.iterdir()), before)
        with _clock_raw_bundle_patch.object(bundle._clock_raw_bundle_os, "fsync", side_effect=OSError("fixture fsync failure")):
            with self.assertRaises(OSError):
                bundle._publish(self.root / "unpublished.zip", b"fixture")
        self.assertEqual(set(self.root.iterdir()), before)

    def test_cli_sigterm_runs_cleanup_and_restores_handler(self):
        before = set(self.root.iterdir())
        previous = _clock_raw_bundle_signal.getsignal(_clock_raw_bundle_signal.SIGTERM)
        output = self.root / "interrupted.zip"
        arguments = ['clock_tools', "--export-dir", str(self.raw),
                     "--output", str(output), "--expected-plan-sha256", self.plan]
        def terminate(*args):
            _clock_raw_bundle_os.kill(_clock_raw_bundle_os.getpid(), _clock_raw_bundle_signal.SIGTERM)
        stderr = _clock_raw_bundle_io.StringIO()
        with _clock_raw_bundle_patch.object(_clock_raw_bundle_sys, "argv", arguments), _clock_raw_bundle_patch.object(bundle._clock_raw_bundle_os, "link", side_effect=terminate), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(_clock_raw_bundle_cli._clock_raw_bundle_main(), 2)
        self.assertEqual(_clock_raw_bundle_signal.getsignal(_clock_raw_bundle_signal.SIGTERM), previous)
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertNotIn(str(self.root), stderr.getvalue())

    def test_cli_exit_values_and_sanitized_error_do_not_hide_failure(self):
        def call(arguments):
            output, error = _clock_raw_bundle_io.StringIO(), _clock_raw_bundle_io.StringIO()
            with _clock_raw_bundle_patch.object(_clock_raw_bundle_sys, "argv", ['clock_tools', *arguments]), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                code = _clock_raw_bundle_cli._clock_raw_bundle_main()
            return code, output.getvalue(), error.getvalue()
        common = ["--expected-plan-sha256", self.plan]
        target = self.root / "new.zip"
        code, _, _ = call(["--export-dir", str(self.raw), "--output", str(target), *common])
        self.assertEqual(code, 0)
        report = self.root / "report.json"
        code, _, _ = call(["--review-archive", str(target), "--output", str(report), *common])
        self.assertEqual(code, 2)
        before = report.read_bytes()
        code, _, error = call(["--review-archive", str(target), "--output", str(report), *common])
        self.assertEqual(code, 2)
        self.assertEqual(report.read_bytes(), before)
        self.assertNotIn(str(self.root), error)
        _clock_raw_bundle_rewrite(self.raw, lambda rows: _clock_raw_bundle_samples(rows)[20]["response"].update(utc_before_ns=0))
        failed = self.root / "failed.zip"
        bundle.export_bundle(self.raw, failed, expected_plan_sha256=self.plan)
        code, _, _ = call(["--review-archive", str(failed), "--output", str(self.root / "failed.json"), *common])
        self.assertEqual(code, 1)
        self.archive.write_bytes(b"PRIVATE_CANARY")
        bad_output = self.root / "bad.json"
        code, stdout, stderr = call(["--review-archive", str(self.archive), "--output", str(bad_output), *common])
        self.assertEqual(code, 2)
        self.assertFalse(bad_output.exists())
        self.assertNotIn("PRIVATE_CANARY", stdout + stderr)
        self.assertNotIn(str(self.root), stdout + stderr)


# ========================================================================
# test_clock_resource_reader
# ========================================================================

"""Run the actual shell reader against copied /proc fixtures; no OS changes.

Only the two literal filesystem roots are replaced in a temporary script copy.
The distributed reader never accepts caller-supplied /proc roots or root bypasses.
"""
from pathlib import Path as _clock_resource_reader_Path
import os as _clock_resource_reader_os
import subprocess as _clock_resource_reader_subprocess
import tempfile as _clock_resource_reader_tempfile
import unittest as _clock_resource_reader_unittest


SOURCE = _clock_resource_reader_Path(__file__).resolve().parents[1] / "scripts/read_chrony_resources.sh"


class ClockResourceReaderTests(_clock_resource_reader_unittest.TestCase):
    def setUp(self):
        self.temp = _clock_resource_reader_tempfile.TemporaryDirectory(prefix="clock-resource-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = _clock_resource_reader_Path(self.temp.name)
        self.proc = self.root / "proc"
        self.sys = self.root / "sys"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.shim("id", "printf '0\\n'\n")
        self.script = self.root / "reader.sh"
        self.script.write_text(SOURCE.read_text().replace(
            "task_proc_root=/proc", f"task_proc_root='{self.proc}'").replace(
            "task_sys_root=/sys", f"task_sys_root='{self.sys}'"))
        self.write(self.proc / "sys/kernel/random/boot_id", "fixture-boot\n")
        self.link(self.proc / "self/ns/pid", "pid:[1]")
        self.process()

    def shim(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o700)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def link(self, path, target):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)

    def process(self, pid=213):
        p = self.proc / str(pid)
        self.write(p / "comm", "chronyd\n")
        # Rest fields 3..21 (19 fields), then starttime, then trailing fields.
        self.write(p / "stat", f"{pid} (chronyd test) " + " ".join(["S"] + ["0"] * 18 + ["319", "0"]) + "\n")
        self.write(p / "status", f"PPid:\t1\nNSpid:\t{pid}\n")
        self.write(p / "binary", "fixture-executable\n")
        self.link(p / "exe", str(p / "binary"))
        self.write(p / "cmdline", "/sbin/chronyd\0")
        self.write(p / "root/etc/chrony.conf", "refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0\n")
        self.link(p / "root/dev/ptp0", "/dev/null")
        self.link(p / "fd/3", "/dev/null")
        self.link(p / "fd/4", "socket:[3582]")
        self.link(p / "fd/5", "/private/unrelated-secret-file")
        for n in ("pid", "mnt", "time", "net"):
            self.link(p / "ns" / n, f"{n}:[1]")
        self.write(p / "net/udp", "  sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
                   " 1: 0100007F:0143 00000000:0000 07 00000000:00000000 00:00000000 00000000 0 0 3582 2 pointer 0\n"
                   " 2: 0100007F:9999 00000000:0000 07 00000000:00000000 00:00000000 00000000 0 0 9999 2 pointer 0\n")
        self.write(p / "net/udp6", "header\n")
        return p

    def run_reader(self):
        return _clock_resource_reader_subprocess.run(["/bin/sh", str(self.script)], capture_output=True, text=True,
                              env={"PATH": str(self.bin) + _clock_resource_reader_os.pathsep + "/usr/bin:/bin"}, timeout=10)

    def test_phc_device_and_exact_process_socket_inode_are_matched(self):
        result = self.run_reader()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("START_TICKS=319\n", result.stdout)
        identity = (self.proc / "213/exe").stat()
        self.assertIn(f"EXE_FILE_ID={identity.st_dev}:{identity.st_ino}:{identity.st_size}\n", result.stdout)
        self.assertIn("PHC_FD=3 RDEV=1:3\n", result.stdout)
        self.assertIn("UDP_FD=4 TABLE=udp LOCAL_REMOTE_INODE=0100007F:0143 00000000:0000 3582", result.stdout)
        self.assertNotIn("9999", result.stdout)
        self.assertNotIn("unrelated-secret-file", result.stdout)
        self.assertIn("READ_COMPLETE_NEEDS_TRACE_CORRELATION", result.stdout)

    def test_root_is_required(self):
        self.shim("id", "printf '1000\\n'\n")
        result = self.run_reader()
        self.assertEqual(result.returncode, 77)
        self.assertNotIn("PROCESS_BEGIN", result.stdout)

    def test_missing_process_is_incomplete_not_success(self):
        (self.proc / "213/comm").write_text("other\n")
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("candidates=0", result.stdout)

    def test_multiple_chronyds_require_review(self):
        self.process(214)
        self.assertEqual(self.run_reader().returncode, 42)

    def test_missing_phc_descriptor_cannot_succeed(self):
        (self.proc / "213/fd/3").unlink()
        self.assertEqual(self.run_reader().returncode, 42)

    def test_another_inode_in_the_reader_network_does_not_count(self):
        # The reader's table has the inode, the candidate's table does not.
        self.write(self.proc / "net/udp", (self.proc / "213/net/udp").read_text())
        self.write(self.proc / "213/net/udp", "header\n")
        self.assertEqual(self.run_reader().returncode, 42)

    def test_ipv6_process_socket_is_matched(self):
        table = (self.proc / "213/net/udp").read_text().replace("0100007F:0143", "00000000000000000000000001000000:0143")
        self.write(self.proc / "213/net/udp", "header\n")
        self.write(self.proc / "213/net/udp6", table)
        result = self.run_reader()
        self.assertEqual(result.returncode, 0)
        self.assertIn("TABLE=udp6", result.stdout)

    def test_matching_inode_on_an_unrelated_port_does_not_count(self):
        table = (self.proc / "213/net/udp").read_text().replace("0100007F:0143", "0100007F:9999")
        self.write(self.proc / "213/net/udp", table)
        self.assertEqual(self.run_reader().returncode, 42)

    def test_include_graph_is_not_followed_or_marked_complete(self):
        self.write(self.proc / "213/root/etc/chrony.conf", "include /private/secret\nrefclock PHC /dev/ptp0\n")
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("INCLUDE_GRAPH_REQUIRES_REVIEW", result.stdout)
        self.assertNotIn("/private/secret", result.stdout)

    def test_alternate_config_argument_is_not_followed(self):
        self.write(self.proc / "213/cmdline", "/sbin/chronyd\0-f/private/secret\0")
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("UNSUPPORTED_ARGV_REQUIRES_REVIEW", result.stdout)
        self.assertNotIn("/private/secret", result.stdout)

    def test_uppercase_include_directive_also_requires_review(self):
        self.write(self.proc / "213/root/etc/chrony.conf", "INCLUDE /private/secret\nrefclock PHC /dev/ptp0\n")
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("INCLUDE_GRAPH_REQUIRES_REVIEW", result.stdout)

    def test_changed_configuration_hash_is_detected(self):
        self.shim("sha256sum", f'''if [ "$1" = '{self.proc}/213/root/etc/chrony.conf' ]; then
 if [ -f '{self.root}/seen-config' ]; then printf 'changed  %s\\n' "$1"; exit 0; fi
 : > '{self.root}/seen-config'
fi
exec /usr/bin/sha256sum "$@"
''')
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("IDENTITY_OR_CONFIG_CHANGED_DURING_READ", result.stdout)

    def test_unknown_phc_configuration_is_incomplete(self):
        self.write(self.proc / "213/root/etc/chrony.conf", "refclock PHC /dev/ptp1\n")
        self.assertEqual(self.run_reader().returncode, 42)

    def test_changed_descriptor_is_detected(self):
        self.shim("readlink", f'''if [ "$1" = '{self.proc}/213/fd/4' ]; then
 if [ -f '{self.root}/seen-fd' ]; then printf 'socket:[9999]\\n'; exit 0; fi
 : > '{self.root}/seen-fd'
fi
exec /usr/bin/readlink "$@"
''')
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("FD_CHANGED_DURING_READ", result.stdout)

    def test_changed_executable_identity_is_detected(self):
        self.shim("sha256sum", f'''if [ "$1" = '{self.proc}/213/exe' ]; then
 if [ -f '{self.root}/seen-exe' ]; then printf 'changed  %s\\n' "$1"; exit 0; fi
 : > '{self.root}/seen-exe'
fi
exec /usr/bin/sha256sum "$@"
''')
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("IDENTITY_OR_CONFIG_CHANGED_DURING_READ", result.stdout)

    def test_descriptor_bound_is_enforced(self):
        for n in range(6, 262):
            self.link(self.proc / f"213/fd/{n}", "socket:[123]")
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("FD_BOUND_EXCEEDED", result.stdout)

    def test_same_content_executable_replacement_is_incomplete(self):
        self.shim("stat", f'''if [ "$3" = '{self.proc}/213/exe' ]; then
 if [ -f '{self.root}/seen-file-id' ]; then printf '1:999999:19\\n'; exit 0; fi
 : > '{self.root}/seen-file-id'
fi
exec /usr/bin/stat "$@"
''')
        result = self.run_reader()
        self.assertEqual(result.returncode, 42)
        self.assertIn("IDENTITY_OR_CONFIG_CHANGED_DURING_READ", result.stdout)


if __name__ == "__main__":
    import unittest as _unittest_entry
    _unittest_entry.main()
