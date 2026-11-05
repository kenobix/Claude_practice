"""Fake readers and disposable files only; never actual clock qualification."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
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

from bike_mlops import clock_campaign_capture as capture
from bike_mlops.clock_campaign_review import canonical_sha256
from scripts import derive_clock_campaign as derive_cli

PROJECT = Path(__file__).resolve().parents[1]


def samples(rows):
    return [row for row in rows if row["type"] == "sample"]


def rewrite(root, mutate):
    """Rehash attacker-edited records: semantic checks must survive valid hashes."""
    rows = [capture.decode(line)["data"] for line in (root / "journal.jsonl").read_bytes().splitlines()]
    mutate(rows)
    previous, output = "0" * 64, []
    for index, data in enumerate(rows):
        row = {"sequence": index, "previous_sha256": previous, "data": data}
        previous = canonical_sha256(row)
        output.append(capture.encode({**row, "sha256": previous}))
    payload = b"".join(output)
    (root / "journal.jsonl").write_bytes(payload)
    marker = capture.decode((root / "complete.json").read_bytes())
    marker.update(journal_sha256=hashlib.sha256(payload).hexdigest(), journal_bytes=len(payload),
                  records=len(rows), last_sha256=previous)
    (root / "complete.json").write_bytes(capture.encode(marker))


class ClockCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        cls.original = Path(cls.shared.name) / "raw"
        cls.plan_hash = capture.capture_fake(cls.original)

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
            name + "_sha256": canonical_sha256(original) for name, original in identity.items()})

    def test_archive_only_copy_in_other_process_then_existing_offline_cli(self):
        detached = self.root / "detached"
        shutil.copytree(self.raw, detached)
        output, report = self.root / "campaign.json", self.root / "report.json"
        command = [sys.executable, str(PROJECT / "scripts/derive_clock_campaign.py"),
                   "--input-dir", str(detached), "--output", str(output),
                   "--expected-plan-sha256", self.plan_hash]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        offline = subprocess.run([sys.executable, str(PROJECT / "scripts/review_clock_campaign.py"),
            "--input", str(output), "--output", str(report), "--expected-plan-sha256", self.plan_hash],
            cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(offline.returncode, 2, offline.stderr)
        actual = json.loads(report.read_bytes())
        self.assertEqual(actual["offline_checks_status"], "passed")
        self.assertEqual(actual["clock_qualification_status"], "incomplete")
        self.assertEqual(actual["input_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        before = output.read_bytes()
        self.assertEqual(subprocess.run(command, cwd=self.root, capture_output=True, timeout=30).returncode, 2)
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
            lambda rows: rows.insert(rows.index(samples(rows)[1]), copy.deepcopy(samples(rows)[0])),
            lambda rows: rows.__setitem__(slice(2, 6), list(reversed(rows[2:6]))),
            lambda rows: rows.append(copy.deepcopy(samples(rows)[0])),
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
        data = json.loads(source.read_bytes())
        data["clock_campaign_capture.py"] = "00"
        source.write_bytes(capture.encode(data))
        with self.assertRaises(ValueError):
            self.replay()

    def test_marker_chain_framing_and_member_fail_closed(self):
        original = (self.raw / "complete.json").read_bytes()
        for key, item in (("journal_sha256", "b" * 64), ("last_sha256", "b" * 64),
                          ("reader_reaped", False), ("records", True), ("journal_bytes", 1)):
            data = json.loads(original)
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
        marker = json.loads((self.raw / "complete.json").read_bytes())
        marker.update(journal_sha256=hashlib.sha256(raw).hexdigest(), journal_bytes=len(raw))
        (self.raw / "complete.json").write_bytes(capture.encode(marker))
        with self.assertRaises(ValueError):
            self.replay()

    def test_stable_read_rejects_symlink_fifo_oversize_and_visible_change(self):
        source = self.root / "small"
        source.write_bytes(b"{}")
        link, fifo, large = (self.root / x for x in ("link", "fifo", "large"))
        link.symlink_to(source)
        os.mkfifo(fifo)
        with large.open("wb") as stream:
            stream.truncate(capture.MAX_BYTES + 1)
        for path in (link, fifo, large, self.root):
            with self.assertRaises((OSError, ValueError)):
                capture.read_stable(path)
        with patch.object(capture.os, "fstat", side_effect=[source.stat(), self.root.stat()]):
            with self.assertRaises(ValueError):
                capture.read_stable(source)

    def test_change_after_replay_is_detected(self):
        original = capture.review_clock_campaign
        def change(value, **kwargs):
            report = original(value, **kwargs)
            (self.raw / "sources.json").write_bytes(b"{}")
            return report
        with patch.object(capture, "review_clock_campaign", side_effect=change):
            with self.assertRaises(ValueError):
                self.replay()

    def test_same_byte_replacement_during_replay_is_detected(self):
        original = capture.review_clock_campaign
        def change(value, **kwargs):
            report = original(value, **kwargs)
            path = self.raw / "sources.json"
            payload = path.read_bytes()
            replacement = self.root / "replacement"
            replacement.write_bytes(payload)
            os.replace(replacement, path)
            return report
        with patch.object(capture, "review_clock_campaign", side_effect=change):
            with self.assertRaises(ValueError):
                self.replay()

    def test_derived_output_cannot_mutate_raw_directory(self):
        original = {p.name: p.read_bytes() for p in self.raw.iterdir()}
        with patch.object(sys, "argv", ["derive", "--input-dir", str(self.raw),
                          "--output", str(self.raw / "derived.json"),
                          "--expected-plan-sha256", self.plan_hash]), redirect_stderr(io.StringIO()):
            self.assertEqual(derive_cli.main(), 2)
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
            capture.derive_raw(self.raw, expected_plan_sha256=canonical_sha256(plan))

    def test_safe_invalid_cli_does_not_echo_values_or_paths_or_publish(self):
        output = self.root / "output.json"
        (self.raw / "complete.json").write_text('{"private":"do-not-echo","private":1}')
        with patch.object(sys, "argv", ["derive", "--input-dir", str(self.raw), "--output", str(output),
                          "--expected-plan-sha256", self.plan_hash]), redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(derive_cli.main(), 2)
        self.assertFalse(output.exists())
        self.assertNotIn("do-not-echo", stderr.getvalue())
        self.assertNotIn(str(self.root), stderr.getvalue())

    def test_publish_failure_cleans_staging_without_overwriting(self):
        for target in ("fsync", "link"):
            with patch.object(capture.os, target, side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    capture.publish_json(self.root / "out.json", {"synthetic": True})
            self.assertEqual({p.name for p in self.root.iterdir()}, {"raw"})
        output = self.root / "out.json"
        output.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            capture.publish_json(output, {})
        self.assertEqual(output.read_bytes(), b"original")


class ClockCaptureFailureTests(unittest.TestCase):
    def test_inflight_request_is_preserved_before_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "attempt"
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
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                readers = []
                def factory(**kwargs):
                    reader = original(**kwargs)
                    readers.append(reader)
                    return reader
                root = Path(directory) / "attempt"
                with patch.object(capture, "Reader", side_effect=factory):
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
        with tempfile.TemporaryDirectory() as directory, patch.object(capture, "Reader", side_effect=factory):
            root = Path(directory) / "attempt"
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
        with tempfile.TemporaryDirectory() as directory, patch.object(capture.os, "fsync", side_effect=fail_third):
            root = Path(directory) / "attempt"
            with self.assertRaises(OSError):
                capture.capture_fake(root)
            self.assertFalse((root / "complete.json").exists())
            self.assertTrue((root / "journal.jsonl").exists())

    def test_sigterm_and_sigint_real_subprocess_reap_reader_no_marker(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=sig), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "attempt"
                process = subprocess.Popen([sys.executable, str(PROJECT / "scripts/capture_fake_clock_campaign.py"),
                    "--output-dir", str(root), "--fault", "delay"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 5
                    children = []
                    while time.monotonic() < deadline:
                        path = root / "journal.jsonl"
                        if path.exists() and path.read_bytes().count(b"\n") >= 43:
                            children = Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()
                            if children:
                                break
                        time.sleep(0.02)
                    self.assertTrue(children)
                    process.send_signal(sig)
                    out, err = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 2, (out, err))
                    self.assertFalse((root / "complete.json").exists())
                    for pid in children:
                        self.assertFalse(Path(f"/proc/{pid}").exists(), "Fixture child survived interruption")
                    rows = (root / "journal.jsonl").read_bytes().splitlines()
                    self.assertTrue(capture.decode(rows[-1])["data"]["reader_reaped"])
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

    def test_existing_attempt_never_deleted_or_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "operator-file"
            sentinel.write_bytes(b"protected")
            with self.assertRaises(FileExistsError):
                capture.capture_fake(root)
            self.assertEqual(sentinel.read_bytes(), b"protected")
            self.assertEqual([p.name for p in root.iterdir()], ["operator-file"])


if __name__ == "__main__":
    unittest.main()
