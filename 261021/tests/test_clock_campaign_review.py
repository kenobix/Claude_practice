"""Synthetic evidence only: no OS clocks, DBs, registry or suspend operations."""
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

from bike_mlops.clock_campaign_review import (
    IDENTITY_FIELDS, POLICY, POLICY_SHA256, canonical_sha256, review_clock_campaign,
)
from scripts import review_clock_campaign as cli

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
    value = {"schema_version": 1, "kind": "clock-campaign-v1", "policy": copy.deepcopy(POLICY),
             "policy_sha256": POLICY_SHA256, "plan": plan, "plan_sha256": H, "campaigns": [], "resume": None}
    for ci, role in enumerate(("normal", "post-resume")):
        campaign = {"role": role, "trial_sha256": str(ci + 1) * 64, "phases": []}
        for pi, phase in enumerate(("idle", "fixture-load")):
            data = {"phase": phase, "identity_start": copy.deepcopy(identity), "identity_end": copy.deepcopy(identity),
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


class ClockCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = fixture()

    def setUp(self):
        self.value = copy.deepcopy(self.original)
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
        self.value = copy.deepcopy(self.original)
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
        self.value = copy.deepcopy(self.original)
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
                data = copy.deepcopy(self.original)
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
        self.phase["external_utc"][1] = copy.deepcopy(self.phase["external_utc"][0])
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
        self.assertGreater(Fraction(*check["upper"]), Fraction(10001, 10000))
        item["reference_lower_ns"] += 1
        self.assertEqual(self.review()["offline_checks_status"], "passed")

    def test_qpc_calibration_observed_drift_missing_duplicate_source_and_reversal(self):
        self.phase["qpc_calibration"].pop()
        self.assert_issue("calibration_windows_missing", "incomplete")
        self.phase["qpc_calibration"][0]["reference_upper_ns"] //= 2
        self.phase["qpc_calibration"][0]["reference_lower_ns"] //= 2
        self.assert_issue("qpc_calibration", "failed")
        self.phase["qpc_calibration"][1] = copy.deepcopy(self.phase["qpc_calibration"][0])
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
        self.phase["future_probes"][1] = copy.deepcopy(self.phase["future_probes"][0])
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
        before = copy.deepcopy(self.value)
        report = self.review()
        self.assertEqual(self.value, before)
        report["policy"]["qpc_minimum_ratio"][0] = 0
        self.assertEqual(self.review()["policy"]["qpc_minimum_ratio"], [9999, 10000])

    def run_cli(self, source, output, plan_hash=None):
        return subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/review_clock_campaign.py"),
            "--input", str(source), "--output", str(output), "--expected-plan-sha256", plan_hash or self.value["plan_sha256"]],
            capture_output=True, text=True, timeout=20)

    def test_cli_archive_only_recomputes_and_never_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, failed in (("complete", False), ("failed", True)):
                self.phase["interrupted"] = False
                if failed:
                    self.phase["samples"][20]["windows_utc_before_ns"] = 0
                seal(self.value)
                source, output = root / "input.json", root / f"{name}.json"
                source.write_text(json.dumps(self.value))
                payload = source.read_bytes()
                result = self.run_cli(source, output)
                self.assertEqual(result.returncode, 1 if failed else 2, result.stderr)
                report = json.loads(output.read_bytes())
                self.assertEqual(report["input_sha256"], hashlib.sha256(payload).hexdigest())
                self.assertEqual(report["input_bytes"], len(payload))
                self.assertEqual(report["clock_qualification_status"], "failed" if failed else "incomplete")
                saved = output.read_bytes()
                self.assertEqual(self.run_cli(source, output).returncode, 2)
                self.assertEqual(output.read_bytes(), saved)
                self.assertEqual(source.read_bytes(), payload)

    def test_cli_untrusted_plan_json_and_private_data_are_not_echoed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "private.json", root / "output.json"
            for payload in ('{"secret":"do-not-echo","secret":1}', '{"x":NaN}', '{', '[' * 2000 + '0' + ']' * 2000, json.dumps(self.value)):
                source.write_text(payload)
                result = self.run_cli(source, output, "b" * 64)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(output.exists())
                self.assertNotIn("do-not-echo", result.stderr)
                self.assertNotIn(directory, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_cli_nonregular_oversized_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            source.write_text(json.dumps(self.value))
            link, fifo, large = root / "link", root / "fifo", root / "large"
            link.symlink_to(source)
            os.mkfifo(fifo)
            with large.open("wb") as stream:
                stream.truncate(32 * 1024 * 1024 + 1)
            for path in (link, fifo, large, root):
                self.assertEqual(self.run_cli(path, root / "output.json").returncode, 2)
                self.assertFalse((root / "output.json").exists())

    def test_cli_rejects_visible_stat_change_and_cleans_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "input.json", root / "output.json"
            source.write_text(json.dumps(self.value))
            before = source.read_bytes()
            with patch.object(sys, "argv", ["review_clock_campaign", "--input", str(source), "--output", str(output),
                                           "--expected-plan-sha256", self.value["plan_sha256"]]), \
                 patch("scripts.review_clock_campaign.os.fstat", side_effect=[source.stat(), root.stat()]), \
                 redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 2)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse(output.exists())

    def test_cli_read_race_fsync_and_publish_failure_cleanup(self):
        for target in ("os.fsync", "os.link", "os.fstat"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, output = root / "input.json", root / "output.json"
                source.write_text(json.dumps(self.value))
                before = source.read_bytes()
                with patch.object(sys, "argv", ["review_clock_campaign", "--input", str(source), "--output", str(output),
                                               "--expected-plan-sha256", self.value["plan_sha256"]]), \
                     patch(f"scripts.review_clock_campaign.{target}", side_effect=OSError("private failure")), \
                     redirect_stderr(io.StringIO()) as stderr, redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(), 2)
                self.assertEqual(source.read_bytes(), before)
                self.assertEqual([p.name for p in root.iterdir()], ["input.json"])
                self.assertNotIn("private failure", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
