from __future__ import annotations

import hashlib
import io
import os
import shutil
import uuid
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from unittest.mock import patch
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import test_capital_measurement_recheck as recheck_tests
from bike_mlops.capital_measurement_evidence import (
    MEMBERS, _workspace_marker, export_synthetic_evidence, verify_archive_process,
)
from bike_mlops.capital_request_journal import JOURNAL_POLICY, _hash, request_schedule


class CapitalMeasurementEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = recheck_tests.MeasurementRecheckTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = self.fixture.plan
        self.report = self.fixture.report
        self.candidate_id = self.plan["identity"]["candidate_id"]
        self.attempt_id = self.fixture.attempt_id
        self.plan_raw = json.dumps(self.plan, ensure_ascii=False, allow_nan=False).encode()
        self.plan_sha = hashlib.sha256(self.plan_raw).hexdigest()
        self.report["operational_plan_file_sha256"] = self.plan_sha
        rows = request_schedule(self.attempt_id, self.plan)
        events = []

        def append(value):
            body = {**value, "sequence": len(events), "previous_sha256": events[-1]["event_sha256"] if events else None,
                    "recorded_at_utc": "2026-11-02T00:00:00+00:00"}
            events.append({**body, "event_sha256": _hash(body)})

        append({"event": "schedule", "schema_version": 1, "attempt_id": self.attempt_id,
                "candidate_id": self.candidate_id, "plan_sha256": self.plan["plan_sha256"],
                "plan_file_sha256": self.plan_sha, "policy": JOURNAL_POLICY, "requests": rows})
        for row, report_row in zip(rows, self.report["measurements"], strict=True):
            append({"event": "sending", "request_id": row["request_id"]})
            append({"event": "response_verified", "request_id": row["request_id"], "http_status": 200,
                    "latency_ms": report_row["latency_ms"], "error_type": None})
        self.journal_raw = b"".join((json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
                                    for event in events)
        self.report["request_journal"] = {"file_name": f"operational-{self.attempt_id}.requests.jsonl",
                                           "sha256": hashlib.sha256(self.journal_raw).hexdigest(),
                                           "events": len(events), "write_and_fsync_ms": 0,
                                           "policy_id": JOURNAL_POLICY["policy_id"]}
        self.report_raw = json.dumps(self.report, ensure_ascii=False, allow_nan=False).encode()
        self.ledger_raw = json.dumps({"schema_version": 1, "records": [{
            "candidate_id": self.candidate_id, "status": "completed", "reviews": [],
            "attempts": [{"attempt_id": self.attempt_id, "status": "completed",
                "plan_path": "data/evaluations/capital-bikeshare/operational-plans/synthetic-system-plan.json",
                "plan_sha256": self.plan["plan_sha256"], "plan_file_sha256": self.plan_sha,
                "report": {"path": f"data/reports/capital-bikeshare/operational-{self.attempt_id}.json",
                           "sha256": hashlib.sha256(self.report_raw).hexdigest(), "gate_status": "not_evaluated"}}]}]},
            sort_keys=True, ensure_ascii=False).encode()
        self.payloads = {
            "payload/plan.json": self.plan_raw,
            "payload/report.json": self.report_raw,
            "payload/operational-ledger.json": self.ledger_raw,
            "payload/request-journal.jsonl": self.journal_raw,
        }
        self.paths = {
            "payload/plan.json": "data/evaluations/capital-bikeshare/operational-plans/synthetic-system-plan.json",
            "payload/report.json": f"data/reports/capital-bikeshare/operational-{self.attempt_id}.json",
            "payload/operational-ledger.json": "data/evaluations/capital-bikeshare/operational-attempts.json",
            "payload/request-journal.jsonl": f"data/reports/capital-bikeshare/operational-{self.attempt_id}.requests.jsonl",
        }
        self.temp = tempfile.TemporaryDirectory(prefix="t16-test-")
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name) / "synthetic-measurement-evidence.zip"
        self._write_archive()

    def _write_archive(self, payloads=None, *, extras=(), duplicate=None, manifest_changes=None):
        payloads = dict(self.payloads if payloads is None else payloads)
        members = {name: {"source_relative_path": self.paths[name], "sha256": hashlib.sha256(raw).hexdigest(),
                          "bytes": len(raw)} for name, raw in payloads.items()}
        manifest = {"schema_version": 1, "synthetic_only": True, "candidate_id": self.candidate_id,
                    "attempt_id": self.attempt_id, "source_revision": {"commit": "a" * 40, "dirty": True},
                    "member_map": {name: MEMBERS[name] for name in MEMBERS}, "members": members,
                    "verification_scope": "test", "limitations": ["synthetic only"]}
        if manifest_changes:
            manifest_changes(manifest)
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest).encode())
            for name, raw in payloads.items():
                bundle.writestr(name, raw)
            for name in extras:
                bundle.writestr(name, b"extra")
            if duplicate:
                bundle.writestr(duplicate, payloads.get(duplicate, b"duplicate"))

    def test_archive_only_process_recalculates_1000_after_warmup_and_keeps_blocked(self):
        result = verify_archive_process(self.archive)
        self.assertEqual(result["measured_requests_recalculated"], 1000)
        self.assertEqual(result["warmup_requests_excluded"], 50)
        self.assertEqual(result["journal_verified_responses"], 1050)
        self.assertEqual(result["p95_ms"], 95.0)
        self.assertEqual(result["operational_gate"], "not_evaluated")
        self.assertEqual(result["deployment_permission"], "blocked")

    def test_separate_cli_process_receives_only_archive_argument(self):
        proc = subprocess.run([sys.executable, "-m", "bike_mlops.capital_measurement_evidence",
                              "--verify-archive", str(self.archive)], capture_output=True, text=True,
                             env={"PATH": sys.executable.rsplit("/", 1)[0], "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
                             timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["p95_ms"], 95.0)

    def test_rejects_missing_extra_duplicate_and_tampered_members(self):
        with self.assertRaises(ValueError):
            self._write_archive({k: v for k, v in self.payloads.items() if k != "payload/report.json"})
            verify_archive_process(self.archive)
        with self.assertRaises(ValueError):
            self._write_archive(extras=("payload/unexpected.txt",))
            verify_archive_process(self.archive)
        with self.assertRaises(ValueError):
            self._write_archive(duplicate="payload/plan.json")
            verify_archive_process(self.archive)
        changed = dict(self.payloads)
        changed["payload/report.json"] += b" "
        with self.assertRaises(ValueError):
            self._write_archive(changed)
            verify_archive_process(self.archive)

    def test_rejects_latency_tamper_even_when_manifest_hash_is_updated(self):
        changed = json.loads(self.report_raw)
        changed["measurements"][60]["latency_ms"] += 5
        raw = json.dumps(changed, ensure_ascii=False).encode()
        payloads = dict(self.payloads)
        payloads["payload/report.json"] = raw
        with self.assertRaises(ValueError):
            self._write_archive(payloads)
            verify_archive_process(self.archive)

    def test_rejects_torn_journal_even_when_descriptors_match_the_truncated_bytes(self):
        payloads = dict(self.payloads)
        payloads["payload/request-journal.jsonl"] = self.journal_raw[:-1]
        report = json.loads(self.report_raw)
        report["request_journal"]["sha256"] = hashlib.sha256(payloads["payload/request-journal.jsonl"]).hexdigest()
        payloads["payload/report.json"] = json.dumps(report, ensure_ascii=False).encode()
        ledger = json.loads(self.ledger_raw)
        ledger["records"][0]["attempts"][0]["report"]["sha256"] = hashlib.sha256(payloads["payload/report.json"]).hexdigest()
        payloads["payload/operational-ledger.json"] = json.dumps(ledger, sort_keys=True).encode()
        with self.assertRaises(ValueError):
            self._write_archive(payloads)
            verify_archive_process(self.archive)

    def test_rejects_sensitive_fields_absolute_paths_and_external_urls(self):
        for key, value in (("database_password", "redacted"), ("log_path", "/tmp/private.log"),
                           ("reference_url", "https://example.com/private")):
            changed = json.loads(self.plan_raw)
            changed[key] = value
            payloads = dict(self.payloads)
            payloads["payload/plan.json"] = json.dumps(changed).encode()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self._write_archive(payloads)
                verify_archive_process(self.archive)

    def test_rejects_different_attempt_and_path_traversal_member(self):
        with self.assertRaises(ValueError):
            self._write_archive(manifest_changes=lambda manifest: manifest.update(attempt_id="different"))
            verify_archive_process(self.archive)
        with self.assertRaises(ValueError):
            self._write_archive(manifest_changes=lambda manifest: manifest.update(harness_token="private"))
            verify_archive_process(self.archive)
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("manifest.json", b"{}")
            bundle.writestr("../outside.json", b"no")
        with self.assertRaises(ValueError):
            verify_archive_process(self.archive)

    def test_rejects_symlink_member(self):
        payloads = dict(self.payloads)
        manifest = {"schema_version": 1, "synthetic_only": True, "candidate_id": self.candidate_id,
                    "attempt_id": self.attempt_id, "source_revision": {"commit": "a" * 40, "dirty": True},
                    "member_map": {name: MEMBERS[name] for name in MEMBERS},
                    "members": {name: {"source_relative_path": self.paths[name],
                        "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for name, raw in payloads.items()},
                    "verification_scope": "test", "limitations": ["synthetic only"]}
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest).encode())
            for name, raw in payloads.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (0o120777 << 16)
                bundle.writestr(info, raw)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            verify_archive_process(self.archive)

    def test_export_rejects_external_workspace_and_cleans_partial_output(self):
        base = Path(__file__).resolve().parents[1] / "data" / "reports"
        output = base / f"t16-unit-failure-{self.attempt_id}"
        self.assertFalse(output.exists())
        with tempfile.TemporaryDirectory(prefix="t16-not-harness-") as directory:
            outside = Path(directory)
            (outside / ".t16-workspace-identity.json").write_text(
                json.dumps({"project": "p", "token": "t"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                export_synthetic_evidence(root=outside, project="p", token="t", candidate_id=self.candidate_id,
                    attempt_id=self.attempt_id, plan_relative="missing.json", report_path=outside / "missing.json",
                    evidence_dir=output, source_commit="a" * 40, source_dirty=False)
        with tempfile.TemporaryDirectory(prefix="capital-system-harness-") as directory:
            workspace = Path(directory)
            _workspace_marker(workspace, "bikemlopstest0000000000000000", "b" * 32)
            with self.assertRaises(FileNotFoundError):
                export_synthetic_evidence(root=workspace, project="bikemlopstest0000000000000000",
                    token="b" * 32, candidate_id=self.candidate_id, attempt_id=self.attempt_id,
                    plan_relative="missing.json", report_path=workspace / "missing.json", evidence_dir=output,
                    source_commit="a" * 40, source_dirty=False)
        self.assertFalse(output.exists())

    def test_existing_output_is_never_overwritten(self):
        output = Path(__file__).resolve().parents[1] / "data" / "reports" / f"t16-unit-existing-{self.attempt_id}"
        output.mkdir(parents=True, exist_ok=False)
        sentinel = output / "keep.txt"
        sentinel.write_text("existing", encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(output, ignore_errors=True))
        with tempfile.TemporaryDirectory(prefix="capital-system-harness-") as directory:
            workspace = Path(directory)
            project, token = "bikemlopstest0000000000000000", "b" * 32
            _workspace_marker(workspace, project, token)
            with self.assertRaises(FileExistsError):
                export_synthetic_evidence(root=workspace, project=project, token=token,
                    candidate_id=self.candidate_id, attempt_id=self.attempt_id, plan_relative="missing.json",
                    report_path=workspace / "missing.json", evidence_dir=output,
                    source_commit="a" * 40, source_dirty=False)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "existing")


    def _rebind_report(self, report, payloads=None):
        payloads = dict(self.payloads if payloads is None else payloads)
        payloads["payload/report.json"] = json.dumps(report).encode()
        ledger = json.loads(payloads["payload/operational-ledger.json"])
        ledger["records"][0]["attempts"][0]["report"]["sha256"] = hashlib.sha256(payloads["payload/report.json"]).hexdigest()
        payloads["payload/operational-ledger.json"] = json.dumps(ledger).encode()
        return payloads

    def _workspace(self):
        temp = tempfile.TemporaryDirectory(prefix="capital-system-harness-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name, raw in self.payloads.items():
            path = root / self.paths[name]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        project, token = "bikemlopstest" + "a" * 16, "b" * 32
        _workspace_marker(root, project, token)
        output = Path(__file__).resolve().parents[1] / "data/reports" / ("t16-sol-test-" + uuid.uuid4().hex)
        self.addCleanup(lambda: shutil.rmtree(output, ignore_errors=True))
        return dict(root=root, project=project, token=token, candidate_id=self.candidate_id,
                    attempt_id=self.attempt_id, plan_relative=self.paths["payload/plan.json"],
                    report_path=root / self.paths["payload/report.json"], evidence_dir=output,
                    source_commit="a" * 40, source_dirty=True)

    def test_verifier_rejects_secrets_in_ledger_and_rehashed_journal(self):
        payloads = dict(self.payloads)
        ledger = json.loads(self.ledger_raw)
        ledger["database_password"] = "synthetic-secret"
        payloads["payload/operational-ledger.json"] = json.dumps(ledger).encode()
        self._write_archive(payloads)
        with self.assertRaisesRegex(ValueError, "sensitive"):
            verify_archive_process(self.archive)
        events = [json.loads(line) for line in self.journal_raw.splitlines()]
        previous = None
        for index, event in enumerate(events):
            event.pop("event_sha256")
            event["previous_sha256"] = previous
            if index == 1:
                event["access_token"] = "synthetic-secret"
            previous = event["event_sha256"] = _hash(event)
        raw = b"".join((json.dumps(event)+"\n").encode() for event in events)
        payloads = dict(self.payloads)
        payloads["payload/request-journal.jsonl"] = raw
        report = json.loads(self.report_raw)
        report["request_journal"]["sha256"] = hashlib.sha256(raw).hexdigest()
        self._write_archive(self._rebind_report(report, payloads))
        with self.assertRaisesRegex(ValueError, "sensitive"):
            verify_archive_process(self.archive)

    def test_latency_mismatch_reaches_journal_comparison_after_rebinding(self):
        report = json.loads(self.report_raw)
        report["measurements"][60]["latency_ms"] += 5
        self._write_archive(self._rebind_report(report))
        with self.assertRaisesRegex(ValueError, "journal latency"):
            verify_archive_process(self.archive)

    def test_warmup_cannot_be_counted_as_measured(self):
        report = json.loads(self.report_raw)
        report["measurements"][0]["phase"] = "measured"
        self._write_archive(self._rebind_report(report))
        with self.assertRaisesRegex(ValueError, "order or target"):
            verify_archive_process(self.archive)

    def test_journal_source_descriptor_must_match_report_filename(self):
        self._write_archive(manifest_changes=lambda m: m["members"]["payload/request-journal.jsonl"].update(
            source_relative_path="data/reports/capital-bikeshare/unrelated.requests.jsonl"))
        with self.assertRaisesRegex(ValueError, "journal.*path"):
            verify_archive_process(self.archive)

    def test_export_rejects_root_and_parent_directory_symlinks(self):
        kwargs = self._workspace()
        alias = Path(self.temp.name) / "workspace-link"
        alias.symlink_to(kwargs["root"], target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            export_synthetic_evidence(**{**kwargs, "root": alias})
        plan_dir = kwargs["root"] / "plan-link"
        plan_dir.symlink_to((kwargs["root"] / kwargs["plan_relative"]).parent, target_is_directory=True)
        relative = "plan-link/" + Path(kwargs["plan_relative"]).name
        with self.assertRaisesRegex(ValueError, "symlink"):
            export_synthetic_evidence(**{**kwargs, "plan_relative": relative})

    def test_export_subprocess_failure_timeout_and_interruption_remove_output(self):
        for error in (RuntimeError("verifier failed"), subprocess.TimeoutExpired("verifier", 60), KeyboardInterrupt()):
            kwargs = self._workspace()
            with self.subTest(error=type(error).__name__), patch(
                    "bike_mlops.capital_measurement_evidence.subprocess.run", side_effect=error):
                with self.assertRaises(type(error)):
                    export_synthetic_evidence(**kwargs)
                self.assertFalse(kwargs["evidence_dir"].exists())

    def test_bounded_reader_detects_replacement_and_oversized_input(self):
        from bike_mlops.capital_measurement_evidence import _read_regular
        source = Path(self.temp.name) / "file"
        source.write_bytes(b"original")
        original_fstat = os.fstat
        calls = 0
        def changed(fd):
            nonlocal calls
            calls += 1
            if calls == 2:  # _read_regular has read the bytes and now checks its open inode.
                replacement = source.with_name("replacement")
                replacement.write_bytes(b"changed-after-read")
                os.replace(replacement, source)
            return original_fstat(fd)
        with patch("bike_mlops.capital_measurement_evidence.os.fstat", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed"):
                _read_regular(source)
        with self.assertRaisesRegex(ValueError, "bounded"):
            _read_regular(source, max_bytes=1)

    def test_manifest_descriptor_cannot_hide_extra_fields_or_unsafe_paths(self):
        for update in ({"private_note": "not-allowed"}, {"source_relative_path": ""},
                       {"source_relative_path": "data/./report.json"}):
            self._write_archive(manifest_changes=lambda m: m["members"]["payload/report.json"].update(update))
            with self.subTest(update=update), self.assertRaises(ValueError):
                verify_archive_process(self.archive)


    def test_harness_export_failure_and_term_still_attempt_resource_cleanup(self):
        import test_capital_system_harness as harness_tests
        harness = harness_tests.harness
        for failure in (ValueError("export failed"), SystemExit(143)):
            roots = []
            with self.subTest(failure=type(failure).__name__), ExitStack() as stack:
                stack.enter_context(redirect_stdout(io.StringIO()))
                stack.enter_context(redirect_stderr(io.StringIO()))
                stack.enter_context(patch("sys.argv", ["harness", "--run-synthetic-system", "--evidence-dir", "data/reports/t16-mock"]))
                for name in ("make_override", "write_synthetic_credentials", "check_local_daemon", "check_ports_free", "wait_ready"):
                    stack.enter_context(patch.object(harness, name))
                stack.enter_context(patch.object(harness, "copy_workspace", side_effect=lambda source, root: roots.append(root)))
                stack.enter_context(patch.object(harness, "prepare_synthetic_workspace", return_value={}))
                stack.enter_context(patch.object(harness, "docker", return_value=""))
                for name in ("assert_empty", "verify_ports", "verify_profiles"):
                    stack.enter_context(patch.object(harness.Harness, name))
                cleanup = stack.enter_context(patch.object(harness.Harness, "cleanup"))
                result = {"candidate_id": self.candidate_id, "measurement_attempt": self.attempt_id,
                          "plan_relative_path": self.paths["payload/plan.json"],
                          "report_relative_path": self.paths["payload/report.json"]}
                stack.enter_context(patch.object(harness, "run_synthetic_system", return_value=result))
                stack.enter_context(patch("bike_mlops.capital_measurement_evidence.export_synthetic_evidence", side_effect=failure))
                with self.assertRaises(type(failure)) as caught:
                    harness.main()
                if isinstance(failure, SystemExit):
                    self.assertEqual(caught.exception.code, 143)
                cleanup.assert_called_once_with()
                self.assertFalse(roots[0].exists())

    def test_partial_export_cleanup_failure_retains_both_errors(self):
        kwargs = self._workspace()
        original = shutil.rmtree
        def denied(path, *args, **kw):
            if Path(path) == kwargs["evidence_dir"]:
                raise PermissionError("partial-output removal denied")
            return original(path, *args, **kw)
        with patch("bike_mlops.capital_measurement_evidence.subprocess.run", side_effect=RuntimeError("verifier failure")), \
             patch("bike_mlops.capital_measurement_evidence.shutil.rmtree", side_effect=denied):
            with self.assertRaises(BaseExceptionGroup) as caught:
                export_synthetic_evidence(**kwargs)
        self.assertEqual([type(e) for e in caught.exception.exceptions], [RuntimeError, PermissionError])

    def test_export_raw_environment_and_inventory_path_escape_are_refused(self):
        from bike_mlops.capital_measurement_evidence import _assert_no_sensitive_or_external
        for value in ({"env": {"CUSTOM": "private"}}, {"containers": "/tmp/private|x"},
                      {"environment": []}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _assert_no_sensitive_or_external(value)


    def test_export_success_is_byte_exact_and_subprocess_environment_is_reduced(self):
        kwargs = self._workspace()
        original_run = subprocess.run
        with patch("bike_mlops.capital_measurement_evidence.subprocess.run", wraps=original_run) as process:
            result = export_synthetic_evidence(**kwargs)
        self.assertEqual(result["recalculation"]["p95_ms"], 95.0)
        self.assertEqual(set(process.call_args.kwargs["env"]) - {"PATH", "VIRTUAL_ENV", "MLFLOW_DISABLE_AGENT_HINT"}, set())
        with zipfile.ZipFile(kwargs["evidence_dir"] / "synthetic-measurement-evidence.zip") as archive:
            for name, raw in self.payloads.items():
                self.assertEqual(archive.read(name), raw)

    def test_harness_resource_cleanup_failure_removes_success_archive(self):
        import test_capital_system_harness as harness_tests
        harness = harness_tests.harness
        kwargs = self._workspace()
        output = kwargs["evidence_dir"]
        roots = []
        def exported(**arguments):
            output.mkdir()
            (output / "synthetic-measurement-evidence.zip").write_bytes(b"synthetic placeholder")
            return {"status": "synthetic"}
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(io.StringIO()))
            stack.enter_context(redirect_stderr(io.StringIO()))
            stack.enter_context(patch("sys.argv", ["harness", "--run-synthetic-system", "--evidence-dir", str(output)]))
            for name in ("make_override", "write_synthetic_credentials", "check_local_daemon", "check_ports_free", "wait_ready"):
                stack.enter_context(patch.object(harness, name))
            stack.enter_context(patch.object(harness, "copy_workspace", side_effect=lambda source, root: roots.append(root)))
            stack.enter_context(patch.object(harness, "prepare_synthetic_workspace", return_value={}))
            stack.enter_context(patch.object(harness, "docker", return_value=""))
            for name in ("assert_empty", "verify_ports", "verify_profiles"):
                stack.enter_context(patch.object(harness.Harness, name))
            stack.enter_context(patch.object(harness.Harness, "cleanup", side_effect=harness.HarnessError("resource cleanup failed")))
            stack.enter_context(patch.object(harness, "run_synthetic_system", return_value={
                "candidate_id": self.candidate_id, "measurement_attempt": self.attempt_id,
                "plan_relative_path": self.paths["payload/plan.json"], "report_relative_path": self.paths["payload/report.json"]}))
            stack.enter_context(patch("bike_mlops.capital_measurement_evidence.export_synthetic_evidence", side_effect=exported))
            with self.assertRaisesRegex(harness.HarnessError, "resource cleanup failed"):
                harness.main()
        self.addCleanup(lambda: shutil.rmtree(roots[0], ignore_errors=True))
        self.assertTrue(roots[0].exists())
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
