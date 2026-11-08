"""Disposable fake raw only; bundle hashes are not source authentication."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from bike_mlops import clock_campaign_capture as raw
from bike_mlops import clock_raw_bundle as bundle
from scripts import clock_raw_bundle as cli
from test_clock_campaign_capture import rewrite, samples

PROJECT = Path(__file__).resolve().parents[1]


class ClockBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        cls.original = Path(cls.shared.name) / "original"
        cls.plan = raw.capture_fake(cls.original, schedule="punctual")
        cls.reference_campaign, cls.reference_report = raw.derive_raw(
            cls.original, expected_plan_sha256=cls.plan)
        cls.archive = Path(cls.shared.name) / "original.zip"
        bundle.export_bundle(cls.original, cls.archive, expected_plan_sha256=cls.plan)
        cls.zip_bytes = cls.archive.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        shutil.copytree(self.original, self.raw)
        self.archive = self.root / "bundle.zip"
        self.archive.write_bytes(self.zip_bytes)

    def review(self):
        return bundle.review_bundle(self.archive, expected_plan_sha256=self.plan)

    def payloads(self):
        with zipfile.ZipFile(self.archive) as stream:
            return {name: stream.read(name) for name in stream.namelist()}

    def replace_payloads(self, payloads, *, rehash=False):
        if rehash:
            value = raw.decode(payloads["manifest.json"])
            value["members"] = {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                                for name, data in payloads.items() if name != "manifest.json"}
            payloads["manifest.json"] = raw.encode(value)
        self.archive.write_bytes(bundle._zip_bytes(payloads))

    def test_exact_raw_and_report_round_trip_no_qualification(self):
        value = self.review()
        self.assertEqual(value["review"], self.reference_report)
        self.assertEqual(value["campaign_sha256"], raw.canonical_sha256(self.reference_campaign))
        self.assertEqual(value["archive_sha256"], hashlib.sha256(self.zip_bytes).hexdigest())
        self.assertEqual(set(value["members"]), bundle.NAMES)
        self.assertEqual(value["review"]["offline_checks_status"], "passed")
        self.assertEqual(value["review"]["clock_qualification_status"], "incomplete")
        for name in raw.MEMBERS:
            self.assertEqual(self.payloads()["raw/" + name], (self.raw / name).read_bytes())

    def test_archive_only_child_without_originals_environment_or_cwd(self):
        # Delete this attempt's original directory. Child receives only the ZIP,
        # installed verifier, expected plan and a separate exclusive output name.
        shutil.rmtree(self.raw)
        detached = self.root / "detached"
        detached.mkdir()
        output = detached / "report.json"
        command = [sys.executable, str(PROJECT / "scripts/clock_raw_bundle.py"),
                   "--review-archive", str(self.archive), "--output", str(output),
                   "--expected-plan-sha256", self.plan]
        result = subprocess.run(command, cwd=detached, env={"PATH": os.environ["PATH"]},
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        value = json.loads(output.read_bytes())
        self.assertEqual(value["review"], self.reference_report)
        self.assertEqual(value["campaign_sha256"], raw.canonical_sha256(self.reference_campaign))
        self.assertEqual(list(detached.iterdir()), [output])

    def test_legacy_unscheduled_raw_is_supported_without_fake_scheduler(self):
        directory = self.root / "legacy"
        plan = raw.capture_fake(directory)
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
            with contextlib.redirect_stderr(io.StringIO()):
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
        marker = raw.decode(changed["raw/complete.json"])
        marker["reader_reaped"] = False
        changed["raw/complete.json"] = raw.encode(marker)
        self.replace_payloads(changed, rehash=True)
        with self.assertRaises(ValueError):
            self.review()

    def test_rehashed_journal_lies_and_source_substitution_do_not_pass(self):
        rewrite(self.raw, lambda rows: samples(rows)[0]["response"]["echo"].update(nonce="0" * 32))
        payloads = {"raw/" + name: (self.raw / name).read_bytes() for name in raw.MEMBERS}
        payloads["manifest.json"] = self.payloads()["manifest.json"]
        self.replace_payloads(payloads, rehash=True)
        with self.assertRaises(ValueError):
            self.review()
        data = self.payloads()
        sources = raw.decode(data["raw/sources.json"])
        sources["clock_campaign_capture.py"] = b"raise RuntimeError('DO NOT EXECUTE')".hex()
        data["raw/sources.json"] = raw.encode(sources)
        self.replace_payloads(data, rehash=True)
        with self.assertRaises(ValueError):
            self.review()

    def test_export_refuses_arbitrary_source_or_extra_private_file(self):
        target = self.root / "new.zip"
        (self.raw / ".env").write_text("PRIVATE_CANARY=fixture")
        with self.assertRaises(ValueError):
            bundle.export_bundle(self.raw, target, expected_plan_sha256=self.plan)
        (self.raw / ".env").unlink()
        sources = raw.decode((self.raw / "sources.json").read_bytes())
        sources["clock_campaign_capture.py"] = b"PRIVATE_CANARY".hex()
        (self.raw / "sources.json").write_bytes(raw.encode(sources))
        with self.assertRaises(ValueError):
            bundle.export_bundle(self.raw, target, expected_plan_sha256=self.plan)
        self.assertFalse(target.exists())

    def test_rehashed_known_violation_and_gap_preserved_through_bundle(self):
        rewrite(self.raw, lambda rows: samples(rows)[20]["response"].update(utc_before_ns=0))
        target = self.root / "failed.zip"
        bundle.export_bundle(self.raw, target, expected_plan_sha256=self.plan)
        value = bundle.review_bundle(target, expected_plan_sha256=self.plan)
        self.assertEqual(value["review"]["clock_qualification_status"], "failed")
        self.assertIn("clock_backwards", {i["code"] for i in value["review"]["issues"]})
        directory = self.root / "gap"
        plan = raw.capture_fake(directory, schedule="wake-late")
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
        with patch.object(bundle, "_validate", side_effect=change):
            with self.assertRaises(ValueError):
                bundle.export_bundle(self.raw, target, expected_plan_sha256=self.plan)
        self.assertFalse(target.exists())

    def test_size_limits_manifest_duplicate_keys_and_nan_refused(self):
        with patch.object(bundle, "ZIP_LIMIT", 100):
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
            with patch.object(bundle.os, "link", side_effect=error):
                with self.assertRaises(type(error)):
                    bundle._publish(self.root / "unpublished.zip", b"fixture")
            self.assertEqual(set(self.root.iterdir()), before)
        with patch.object(bundle.os, "fsync", side_effect=OSError("fixture fsync failure")):
            with self.assertRaises(OSError):
                bundle._publish(self.root / "unpublished.zip", b"fixture")
        self.assertEqual(set(self.root.iterdir()), before)

    def test_cli_sigterm_runs_cleanup_and_restores_handler(self):
        before = set(self.root.iterdir())
        previous = signal.getsignal(signal.SIGTERM)
        output = self.root / "interrupted.zip"
        arguments = ["clock_raw_bundle", "--export-dir", str(self.raw),
                     "--output", str(output), "--expected-plan-sha256", self.plan]
        def terminate(*args):
            os.kill(os.getpid(), signal.SIGTERM)
        stderr = io.StringIO()
        with patch.object(sys, "argv", arguments), patch.object(bundle.os, "link", side_effect=terminate), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(), 2)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertNotIn(str(self.root), stderr.getvalue())

    def test_cli_exit_values_and_sanitized_error_do_not_hide_failure(self):
        def call(arguments):
            output, error = io.StringIO(), io.StringIO()
            with patch.object(sys, "argv", ["clock_raw_bundle", *arguments]), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                code = cli.main()
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
        rewrite(self.raw, lambda rows: samples(rows)[20]["response"].update(utc_before_ns=0))
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


if __name__ == "__main__":
    unittest.main()
