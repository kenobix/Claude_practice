"""Virtual counters and disposable child readers only; no real qualification."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from bike_mlops import clock_campaign_capture as raw
from bike_mlops import clock_campaign_schedule as scheduling
from test_clock_campaign_capture import rewrite

PROJECT = Path(__file__).resolve().parents[1]


def rows(root):
    return [raw.decode(line)["data"] for line in (root / "journal.jsonl").read_bytes().splitlines()]


class FixedSlotTests(unittest.TestCase):
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


class ScheduledCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        cls.original = Path(cls.shared.name) / "raw"
        cls.plan_hash = raw.capture_fake(cls.original, schedule="punctual")

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.raw = self.root / "raw"
        shutil.copytree(self.original, self.raw)

    def replay(self):
        return raw.derive_raw(self.raw, expected_plan_sha256=self.plan_hash)

    def test_normal_four_phases_exact_deadline_source_binding_not_qualification(self):
        value, report = self.replay()
        self.assertEqual(report["offline_checks_status"], "passed")
        self.assertEqual(report["clock_qualification_status"], "incomplete")
        saved = rows(self.raw)
        self.assertEqual(len(saved), 14415)
        self.assertEqual(sum(r["type"] == "request" for r in saved), 7202)
        self.assertIn("clock_campaign_schedule.py", saved[0]["manifest"]["files"])
        self.assertEqual(value["plan"]["workload_sha256"], raw.canonical_sha256(saved[0]["recipe"]))
        for ci, c in enumerate(value["campaigns"]):
            for pi, p in enumerate(c["phases"]):
                data = p["data"]
                self.assertEqual(data["lost_samples"], 0)
                self.assertEqual([s["qpc_tick"] for s in data["samples"]],
                                 [(ci * 4000 + pi * 1800 + i) * 1000 for i in range(1801)])

    def test_raw_copy_separate_process_through_existing_offline_cli(self):
        copied = self.root / "detached"
        shutil.copytree(self.raw, copied)
        output, report = self.root / "campaign.json", self.root / "report.json"
        derived = subprocess.run([sys.executable, str(PROJECT / "scripts/derive_clock_campaign.py"),
            "--input-dir", str(copied), "--output", str(output), "--expected-plan-sha256", self.plan_hash],
            cwd=self.root, capture_output=True, timeout=30)
        self.assertEqual(derived.returncode, 2, derived.stderr)
        reviewed = subprocess.run([sys.executable, str(PROJECT / "scripts/review_clock_campaign.py"),
            "--input", str(output), "--output", str(report), "--expected-plan-sha256", self.plan_hash],
            cwd=self.root, capture_output=True, timeout=30)
        self.assertEqual(reviewed.returncode, 2, reviewed.stderr)
        self.assertEqual(json.loads(report.read_bytes())["offline_checks_status"], "passed")

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
                shutil.copyfile(self.original / "journal.jsonl", self.raw / "journal.jsonl")
                shutil.copyfile(self.original / "complete.json", self.raw / "complete.json")
                rewrite(self.raw, mutate)
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
        plan_hash = raw.canonical_sha256(raw.decode((target / "plan.json").read_bytes()))
        def mutate(saved):
            next(r for r in saved if r["type"] == "phase-end")["future_probes"][1]["decision"] = "allow"
        rewrite(target, mutate)
        _, report = raw.derive_raw(target, expected_plan_sha256=plan_hash)
        self.assertIn("strict_future_probe_failed", {i["code"] for i in report["issues"]})

    def test_rehashed_missing_slot_cannot_be_removed_or_converted_to_request(self):
        target, _, _ = self.capture_replay("wake-late")
        plan_hash = raw.canonical_sha256(raw.decode((target / "plan.json").read_bytes()))
        rewrite(target, lambda saved: saved.remove(next(r for r in saved if r["type"] == "missed")))
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
            with patch.object(raw, "Reader", side_effect=factory):
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
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(sig=sig):
                target = self.root / f"signal-{sig}"
                process = subprocess.Popen([sys.executable, str(PROJECT / "scripts/capture_fake_clock_campaign.py"),
                    "--output-dir", str(target), "--schedule", "punctual", "--fault", "delay"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 5
                    children = []
                    while time.monotonic() < deadline:
                        path = target / "journal.jsonl"
                        if path.exists() and path.read_bytes().count(b"\n") >= 43:
                            children = Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()
                            if children:
                                break
                        time.sleep(0.02)
                    self.assertTrue(children)
                    self.assertEqual(rows(target)[-1]["type"], "request")
                    process.send_signal(sig)
                    out, err = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 2, (out, err))
                    self.assertFalse((target / "complete.json").exists())
                    self.assertTrue(rows(target)[-1]["reader_reaped"])
                    for pid in children:
                        self.assertFalse(Path(f"/proc/{pid}").exists())
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
            with patch.object(raw, "Reader", side_effect=factory):
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
        original = subprocess.Popen
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        def interrupted(signum, frame):
            raise InterruptedError("Fixture startup interruption")
        for case in ("signal", "register"):
            children = []
            def spawn(*args, **kwargs):
                child = original(*args, **kwargs)
                children.append(child)
                if case == "signal":
                    os.kill(os.getpid(), signal.SIGTERM)
                return child
            old_handler = signal.signal(signal.SIGTERM, interrupted)
            try:
                target = self.root / case
                with patch.object(subprocess, "Popen", side_effect=spawn):
                    if case == "register":
                        with patch("selectors.EpollSelector.register", side_effect=OSError("fixture register")):
                            with self.assertRaises(OSError):
                                raw.capture_fake(target, schedule="punctual")
                    else:
                        with self.assertRaises(InterruptedError):
                            raw.capture_fake(target, schedule="punctual")
                self.assertTrue(children)
                self.assertIsNotNone(children[0].poll())
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), mask)
                self.assertFalse((target / "complete.json").exists())
            finally:
                signal.signal(signal.SIGTERM, old_handler)
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
        plan_hash = raw.canonical_sha256(raw.decode((target / "plan.json").read_bytes()))
        rewrite(target, lambda saved: next(r for r in saved if r["type"] == "sample" and r["late"]).update(late=False))
        with self.assertRaises(ValueError):
            raw.derive_raw(target, expected_plan_sha256=plan_hash)


if __name__ == "__main__":
    unittest.main()
