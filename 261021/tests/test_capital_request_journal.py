from __future__ import annotations

import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from failure_diagnostics import FailureTrace, trace_directory

from bike_mlops.capital_request_journal import RequestJournal, inspect_request_journal
from bike_mlops import capital_operational_recovery as recovery
from bike_mlops import capital_operational_plan as plans
from bike_mlops.capital_benchmark_observer import CapitalBenchmarkObserver
import test_capital_operational_ledger as ledger_tests


class RequestJournalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ledger_tests.OperationalLedgerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = self.fixture.plan
        self.root_patch = patch.object(recovery, "PROJECT_ROOT", self.fixture.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def journal(self, attempt):
        path = self.fixture.report_dir / f"operational-{attempt.attempt_id}.requests.jsonl"
        journal = RequestJournal(path, attempt, self.plan)
        self.addCleanup(journal.close)
        return journal

    def inspect(self, journal, attempt):
        return inspect_request_journal(journal.path, attempt_id=attempt.attempt_id,
            candidate_id=self.fixture.candidate, plan=self.plan, plan_file_sha256=attempt.plan_file_sha256)

    def test_fixed_schedule_unknown_and_verified_survive_without_failure_report(self):
        with self.fixture.attempt() as attempt:
            journal = self.journal(attempt)
            self.assertEqual(len(journal.schedule), 1050)
            first, second = journal.schedule[:2]
            journal.sending(first)
            journal.outcome(first, http_status=200, latency_ms=12.5, error=None)
            journal.sending(second)
        original = journal.path.read_bytes()
        view = self.inspect(journal, attempt)
        self.assertEqual(view["counts"], {"response_verified": 1,
            "outcome_unknown_may_have_been_sent": 1, "scheduled_only_no_durable_send_intent": 1048})
        self.assertEqual(view["verified_latencies_ms"], {first["request_id"]: 12.5})
        result = recovery.inspect_operational_reports(self.fixture.candidate,
            ledger_path=self.fixture.ledger_path, lock_dir=self.fixture.lock_dir)
        self.assertEqual(result["attempts"][0]["request_journal"]["sha256"], hashlib.sha256(original).hexdigest())
        self.assertTrue(result["attempts"][0]["unresolved"])
        self.assertEqual(original, journal.path.read_bytes())

    def test_unknown_outcome_cannot_advance_or_retry_and_reasoned_new_attempt_keeps_history(self):
        with self.fixture.attempt() as attempt:
            journal = self.journal(attempt)
            payload = journal.schedule[0]
            journal.sending(payload)
            journal.outcome(payload, http_status=None, latency_ms=10, error="timeout")
            with self.assertRaises(ValueError): journal.sending(journal.schedule[1])
            with self.assertRaises(ValueError): journal.sending(payload)
            with self.assertRaises(ValueError): journal.assert_complete()
        previous = journal.path.read_bytes()
        with self.assertRaises(ValueError):
            with self.fixture.attempt(): pass
        self.fixture.review(reason="Investigated unknown outcome; preserve failed attempt, retry separately")
        with self.fixture.attempt() as next_attempt:
            self.assertNotEqual(next_attempt.attempt_id, attempt.attempt_id)
        self.assertEqual(previous, journal.path.read_bytes())

    def test_torn_tail_is_reported_and_complete_corruption_is_rejected_without_repair(self):
        with self.fixture.attempt() as attempt:
            journal = self.journal(attempt)
            journal.sending(journal.schedule[0])
        original = journal.path.read_bytes()
        journal.path.write_bytes(original + b'{"event":')
        self.assertEqual(self.inspect(journal, attempt)["status"], "torn_tail")
        self.assertEqual(self.inspect(journal, attempt)["counts"]["outcome_unknown_may_have_been_sent"], 1)
        journal.path.write_bytes(original.replace(b'"sending"', b'"changed"'))
        with self.assertRaisesRegex(ValueError, "hash"): self.inspect(journal, attempt)
        journal.path.write_bytes(original + original.splitlines(keepends=True)[1])
        with self.assertRaises(ValueError): self.inspect(journal, attempt)

    def test_fsync_failure_stops_before_network_and_cannot_be_reused(self):
        from unittest.mock import Mock
        with self.fixture.attempt() as attempt:
            journal = self.journal(attempt)
            observer = CapitalBenchmarkObserver(attempt, Mock(unsafe=True), Mock(), pinned_identity={}, journal=journal)
            attempt.mark_requests_started()
            observer._marked = True
            with patch("bike_mlops.capital_request_journal.os.fsync", side_effect=OSError("disk")):
                with self.assertRaises(OSError): observer.before_request(journal.schedule[0])
            with self.assertRaises(RuntimeError): observer.before_request(journal.schedule[0])
            self.assertEqual(observer.exchanges, [])
        self.assertTrue(journal.poisoned)

    def test_foreign_payload_symlink_identity_and_overwrite_are_refused(self):
        with self.fixture.attempt() as attempt:
            journal = self.journal(attempt)
            with self.assertRaises(ValueError): journal.sending({**journal.schedule[0], "as_of": "wrong"})
            with self.assertRaises(FileExistsError): RequestJournal(journal.path, attempt, self.plan)
            with self.assertRaises(ValueError): inspect_request_journal(journal.path, attempt_id=attempt.attempt_id,
                candidate_id="0"*64, plan=self.plan, plan_file_sha256=attempt.plan_file_sha256)
            link = journal.path.with_name("link.jsonl")
            link.symlink_to(journal.path)
            with self.assertRaises(OSError): inspect_request_journal(link, attempt_id=attempt.attempt_id,
                candidate_id=self.fixture.candidate, plan=self.plan, plan_file_sha256=attempt.plan_file_sha256)

    def test_old_plan_is_inspectable_but_rejected_before_new_attempt(self):
        legacy = json.loads(json.dumps(self.plan))
        legacy["policy"].pop("request_journal")
        legacy["plan_sha256"] = plans._digest(legacy)
        plans.validate_operational_plan(legacy, allow_legacy=True)
        self.fixture.plan_path.write_text(json.dumps(legacy))
        with self.assertRaisesRegex(ValueError, "policy"):
            with self.fixture.attempt(): pass
        self.assertFalse(self.fixture.ledger_path.exists())

    def test_http_timer_excludes_slow_validation_even_when_validation_fails(self):
        from bike_mlops import operational_benchmark as benchmark
        from types import SimpleNamespace
        identity = self.plan["identity"]
        clock = [0.0]
        base = {key: identity[key] for key in ("data_profile_id", "target_contract_id", "model_name",
                "model_version", "candidate_id", "feature_contract_version")}
        base.update(artifact_sha256=identity["registered_artifact_sha256"], deployment_permission="blocked",
                    quality_gate_status="passed")
        def request(url, payload=None):
            if url.endswith("/ready"): return 200, {**base, "status": "ready"}
            if url.endswith("/version"): return 200, base
            clock[0] += 0.002
            return 200, {**base, **payload, "prediction": 1.0, "data_version_sha256": "f"*64}
        def before(_): clock[0] += 0.1
        def after(*_):
            clock[0] += 0.5
            raise ValueError("synthetic source check failure")
        def result(*_, **__):
            clock[0] += 0.3
            raise KeyboardInterrupt("synthetic signal during result journal write")
        observer = SimpleNamespace(before_request=before, after_response=after, request_result=result)
        with (patch.object(benchmark, "DATA_PROFILE_ID", identity["data_profile_id"]),
              patch.object(benchmark, "TARGET_CONTRACT_ID", identity["target_contract_id"]),
              patch.object(benchmark, "DATA_PROFILE", {**identity, "target": {"timezone_name": "America/New_York"}}),
              patch.object(benchmark.time, "perf_counter", side_effect=lambda: clock[0])):
            report = benchmark.run_capital_operational_benchmark(api_url=self.plan["api_url"],
                model_version=identity["model_version"], candidate_id=self.fixture.candidate,
                start=self.plan["input_window"]["start_inclusive"], end=self.plan["input_window"]["end_exclusive"],
                attempt_id="11111111-1111-4111-8111-111111111111", expected_artifact_sha256=identity["registered_artifact_sha256"],
                request_fn=request, resource_fn=lambda: {}, request_observer=observer)
        self.assertAlmostEqual(report["measurements"][0]["latency_ms"], 2.0)
        self.assertAlmostEqual(report["timing"]["request_loop_elapsed_ms"], 902.0)
        self.assertFalse(report["completed"])
        self.assertTrue(report["interruption_requested"])

    def _signal_case(self, sig, *, stalled_ledger=False):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        directory = trace_directory(self.fixture.root, "signal-" + sig.name)
        trace = FailureTrace(directory, "parent")
        received, release = threading.Event(), threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                trace.emit("http_request_received")
                received.set()
                release.wait(20)
                try:
                    self.send_response(200)
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        ledger_path = self.fixture.root / ("child-ledger-" + sig.name + ".json")
        code = Path(__file__).with_name("request_interruption_child.py")
        process = None
        failed = True
        try:
            trace.emit("child_launch")
            process = subprocess.Popen([sys.executable, str(code), str(self.fixture.root),
                self.fixture.plan_path.relative_to(self.fixture.root).as_posix(), str(ledger_path),
                f"http://127.0.0.1:{server.server_port}/predict", str(directory),
                "stall_ledger" if stalled_ledger else "normal"], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
            self.assertTrue(select.select([process.stdout], [], [], 15)[0], "child did not reach send intent")
            attempt_id = process.stdout.readline().strip()
            self.assertTrue(attempt_id, process.stderr.read() if process.poll() is not None else "child stopped")
            self.assertTrue(received.wait(10), "request did not reach HTTP server")
            trace.emit("parent_signal_send", signal=int(sig))
            process.send_signal(sig)
            if stalled_ledger:
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.communicate(timeout=1)
                trace.emit("expected_wait_timeout", deadline_seconds=1)
                process.kill()
            else:
                # Keep the original ten-second acceptance deadline.
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    trace.emit("unexpected_wait_timeout", deadline_seconds=10)
                    raise
            process.communicate(timeout=10)
            trace.emit("child_wait_returned", returncode=process.returncode)
            self.assertEqual(process.returncode, -signal.SIGKILL if sig == signal.SIGKILL or stalled_ledger else 130)
            state = json.loads(ledger_path.read_text())["records"][0]
            self.assertEqual(state["status"], "interrupted" if sig == signal.SIGTERM and not stalled_ledger else "started")
            attempt = state["attempts"][0]
            view = inspect_request_journal(self.fixture.root / (attempt_id + ".jsonl"),
                attempt_id=attempt_id, candidate_id=self.fixture.candidate, plan=self.plan,
                plan_file_sha256=attempt["plan_file_sha256"])
            self.assertEqual(view["counts"]["outcome_unknown_may_have_been_sent"], 1)
            # A lost result cannot authorize an automatic retry, even when SIGTERM was received.
            from bike_mlops import capital_operational_ledger as ledger
            with self.assertRaisesRegex(ValueError, "already has an operational attempt"):
                with ledger.measurement_attempt(self.fixture.plan_path, ledger_path=ledger_path,
                                                lock_dir=self.fixture.root / "child-locks"):
                    pass
            rows = [json.loads(line) for line in (directory / "child.jsonl").read_text().splitlines()]
            phases = [row["phase"] for row in rows]
            if sig == signal.SIGTERM:
                self.assertIn("signal_received", phases)
                self.assertIn("signal_handler_raised", phases)
                self.assertIn("ledger_interrupt_enter", phases)
                if stalled_ledger:
                    self.assertNotIn("ledger_interrupt_returned", phases)
                    stacks = (directory / "child-stacks.txt").read_text()
                    self.assertIn("interrupt", stacks)
                    self.assertIn("request_interruption_child.py", stacks)
                else:
                    self.assertIn("ledger_interrupt_returned", phases)
                    self.assertIn("child_exit", phases)
                    self.assertLess(phases.index("signal_received"), phases.index("ledger_interrupt_enter"))
                    self.assertLess(phases.index("ledger_interrupt_enter"), phases.index("ledger_interrupt_returned"))
            else:
                self.assertNotIn("signal_received", phases)
                self.assertNotIn("child_exit", phases)
            trace.emit("state_and_journal_verified", state=state["status"])
            failed = False
        finally:
            if process is not None:
                if process.poll() is None:
                    trace.emit("cleanup_force_kill")
                    process.kill()
                stdout, stderr = process.communicate(timeout=10)
                (directory / "child-stdout.txt").write_text(stdout)
                (directory / "child-stderr.txt").write_text(stderr)
                trace.emit("cleanup_child_reaped", returncode=process.returncode)
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            trace.emit("server_cleanup_complete")
            trace.publish(failed)
            if failed:
                for name in ("child.jsonl", "child-stacks.txt", "child-stderr.txt"):
                    path = directory / name
                    if path.exists():
                        print("TEST_DIAGNOSTICS " + name + "\n" + path.read_text(), file=sys.stderr)

    def test_sigterm_and_sigkill_leave_durable_intent_and_block_automatic_retry(self):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=sig):
                self._signal_case(sig)

    def test_stalled_interrupt_write_is_diagnosable_and_remains_unknown(self):
        # Reproduce the timeout symptom deliberately; this is not the historical cause.
        self._signal_case(signal.SIGTERM, stalled_ledger=True)


if __name__ == "__main__":
    unittest.main()
