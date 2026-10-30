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

    def test_sigterm_and_sigkill_leave_durable_intent_and_block_automatic_retry(self):
        # Kill while a real local HTTP handler has received the request but has not replied.
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        received, release = threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                received.set()
                release.wait(20)
                self.send_response(200)
                self.end_headers()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(release.set)
        code = r'''
import json, sys, time
from pathlib import Path
from unittest.mock import patch
from bike_mlops import capital_operational_ledger as ledger
from bike_mlops.capital_request_journal import RequestJournal
from bike_mlops.capital_measurement_runner import _termination_as_interrupt
root = Path(sys.argv[1])
with patch.object(ledger, "PROJECT_ROOT", root), _termination_as_interrupt():
    with ledger.measurement_attempt(root / sys.argv[2], ledger_path=root / "child-ledger.json", lock_dir=root / "child-locks") as attempt:
        plan = json.loads((root / sys.argv[2]).read_text())
        journal = RequestJournal(root / (attempt.attempt_id + ".jsonl"), attempt, plan)
        attempt.mark_requests_started()
        journal.sending(journal.schedule[0])
        print(attempt.attempt_id, flush=True)
        import httpx
        with httpx.Client(timeout=30, trust_env=False) as client:
            client.post(sys.argv[3], json=journal.schedule[0])
'''
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=sig):
                received.clear()
                ledger_path = self.fixture.root / "child-ledger.json"
                ledger_path.unlink(missing_ok=True)
                process = subprocess.Popen([sys.executable, "-c", code, str(self.fixture.root),
                    self.fixture.plan_path.relative_to(self.fixture.root).as_posix(),
                    f"http://127.0.0.1:{server.server_port}/predict"], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                try:
                    self.assertTrue(select.select([process.stdout], [], [], 15)[0], "child did not reach send intent")
                    attempt_id = process.stdout.readline().strip()
                    self.assertTrue(attempt_id, process.stderr.read() if process.poll() is not None else "child stopped")
                    self.assertTrue(received.wait(10), "request did not reach HTTP server")
                    process.send_signal(sig)
                    process.communicate(timeout=10)
                    state = json.loads(ledger_path.read_text())["records"][0]
                    self.assertEqual(state["status"], "interrupted" if sig == signal.SIGTERM else "started")
                    attempt = state["attempts"][0]
                    view = inspect_request_journal(self.fixture.root / (attempt_id + ".jsonl"),
                        attempt_id=attempt_id, candidate_id=self.fixture.candidate, plan=self.plan,
                        plan_file_sha256=attempt["plan_file_sha256"])
                    self.assertEqual(view["counts"]["outcome_unknown_may_have_been_sent"], 1)
                finally:
                    if process.poll() is None: process.kill()
                    process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
