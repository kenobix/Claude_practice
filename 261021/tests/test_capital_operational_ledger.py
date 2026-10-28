from __future__ import annotations

import json
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bike_mlops import capital_operational_ledger as ledger
from bike_mlops import capital_operational_plan as plans


class OperationalLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger_path = self.root / "data/evaluations/capital-bikeshare/attempts.json"
        self.lock_dir = self.root / "data/evaluations/capital-bikeshare/locks"
        self.plan_path = self.root / "data/evaluations/capital-bikeshare/operational-plans/test.json"
        self.report_dir = self.root / "data/reports/capital-bikeshare"
        self.report_dir.mkdir(parents=True)
        self.candidate = "a" * 64
        identity = {"candidate_id": self.candidate, "candidate_report_sha256": "b" * 64,
                    "final_report_sha256": "c" * 64, "evaluation_protocol_sha256": "d" * 64,
                    "data_sha256": "e" * 64, "manifest_sha256": "f" * 64,
                    "candidate_artifact_sha256": "1" * 64, "registered_artifact_sha256": "2" * 64,
                    "model_name": "bike-demand-capital", "model_version": "7",
                    "data_profile_id": "capital-bikeshare", "target_contract_id": "capital-trip-starts-hourly-v1",
                    "feature_contract_version": "bike-demand-features-v1"}
        window = {key: identity[key] for key in ("candidate_id", "candidate_report_sha256", "final_report_sha256",
                  "evaluation_protocol_sha256", "data_sha256", "manifest_sha256")}
        window.update({"start_inclusive": "2024-01-07 22:00:00", "end_exclusive": "2024-01-14 22:00:00",
                       "hours": 168, "training_cutoff": "2024-01-15 00:00:00", "label_delay_hours": 3,
                       "training_rows": 300, "training_row_keys_sha256": "3" * 64,
                       "latest_replay_label_available_at": "2024-01-15 00:00:00"})
        self.plan = {"schema_version": 2, "kind": "capital-operational-plan", "identity": identity,
                     "input_window": window, "api_url": "http://127.0.0.1:8110",
                     "registry_uri": "http://127.0.0.1:5001", "policy": dict(plans.PLAN_POLICY),
                     "resource_context_before_requests": {}, "deployment_permission": "blocked"}
        self.plan["plan_sha256"] = plans._digest(self.plan)
        self.root_patch = patch.object(ledger, "PROJECT_ROOT", self.root)
        self.plan_root_patch = patch.object(plans, "PROJECT_ROOT", self.root)
        self.root_patch.start()
        self.plan_root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.plan_root_patch.stop)
        plans.save_operational_plan(self.plan, self.plan_path)

    def attempt(self):
        return ledger.measurement_attempt(self.plan_path, ledger_path=self.ledger_path, lock_dir=self.lock_dir)

    def review(self, operator="tester", reason="investigate interruption"):
        ledger.review_operational_retry(self.candidate, operator, reason,
                                        next_plan_path=self.plan_path, ledger_path=self.ledger_path, lock_dir=self.lock_dir)

    def report(self, lease, *, passed=True):
        path = self.report_dir / (lease.attempt_id + ".json")
        value = {"operational_attempt_id": lease.attempt_id, "operational_plan_sha256": lease.plan_sha256,
                 "operational_plan_file_sha256": lease.plan_file_sha256, "completed": passed,
                 "operational_gate": {"status": "passed" if passed else "failed"},
                 "model_identity": {"candidate_id": self.candidate, "model_name": "bike-demand-capital",
                                    "model_version": "7", "artifact_sha256": "2" * 64}}
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_started_is_durable_before_yield_and_completed_attempt_cannot_be_retried(self):
        with self.attempt() as lease:
            record = ledger.read_operational_ledger(self.ledger_path)["records"][0]
            self.assertEqual(record["status"], "started")
            self.assertEqual(record["attempts"][0]["plan_sha256"], self.plan["plan_sha256"])
            self.assertEqual(record["attempts"][0]["plan_file_sha256"], lease.plan_file_sha256)
            self.assertEqual(lease.request_prefix, f"capital-op:{lease.attempt_id}:")
            lease.mark_requests_started()
            self.assertIsNotNone(ledger.read_operational_ledger(self.ledger_path)["records"][0]["attempts"][0]["requests_started_at_utc"])
            lease.finish(status="completed", report_path=self.report(lease))
            with self.assertRaisesRegex(ValueError, "no longer active"):
                lease.finish(status="failed", failure_code="other")
        record = ledger.read_operational_ledger(self.ledger_path)["records"][0]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(len(record["attempts"][0]["report"]["sha256"]), 64)
        with self.assertRaisesRegex(ValueError, "Only a failed or interrupted"):
            self.review()
        with self.assertRaisesRegex(ValueError, "already has an operational attempt"):
            with self.attempt():
                pass

    def test_failed_attempt_requires_reasoned_review_and_history_is_retained(self):
        with self.attempt() as first:
            first.mark_requests_started()
            first.finish(status="failed", report_path=self.report(first, passed=False), failure_code="request_failed")
        with self.assertRaisesRegex(ValueError, "already has"):
            with self.attempt():
                pass
        with self.assertRaisesRegex(ValueError, "operator and a reason"):
            self.review(reason=" ")
        self.review()
        with self.attempt() as second:
            self.assertNotEqual(first.attempt_id, second.attempt_id)
            second.finish(status="failed", failure_code="preflight_failed")
        record = ledger.read_operational_ledger(self.ledger_path)["records"][0]
        self.assertEqual([item["status"] for item in record["attempts"]], ["failed", "failed"])
        self.assertEqual(record["reviews"][0]["attempt_id"], first.attempt_id)
        self.assertEqual(record["reviews"][0]["reason"], "investigate interruption")

    def test_live_attempt_excludes_concurrent_start_and_retry_review(self):
        with self.attempt():
            with self.assertRaisesRegex(RuntimeError, "locked"):
                with self.attempt():
                    pass
            with self.assertRaisesRegex(RuntimeError, "locked"):
                self.review()
        record = ledger.read_operational_ledger(self.ledger_path)["records"][0]
        self.assertEqual(record["status"], "interrupted")
        self.assertEqual(record["attempts"][0]["failure_code"], "interrupted")

    def test_reviewed_retry_cannot_switch_to_a_changed_plan(self):
        with self.attempt() as first:
            first.finish(status="failed", failure_code="preflight_failed")
        self.review()
        original = self.plan_path.read_bytes()
        self.plan_path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "differs from the reviewed plan"):
            with self.attempt():
                self.fail("changed retry plan was accepted")
        self.assertEqual(ledger.read_operational_ledger(self.ledger_path)["records"][0]["status"], "reviewed_reopen")
        self.plan_path.write_bytes(original)
        with self.attempt() as second:
            second.finish(status="failed", failure_code="preflight_failed")

    def test_wrong_or_modified_report_and_plan_do_not_complete(self):
        with self.attempt() as lease:
            lease.mark_requests_started()
            report = self.report(lease)
            body = json.loads(report.read_text())
            body["operational_plan_sha256"] = "0" * 64
            report.write_text(json.dumps(body))
            with self.assertRaisesRegex(ValueError, "does not match"):
                lease.finish(status="completed", report_path=report)
            report = self.report(lease)
            self.plan_path.write_text(self.plan_path.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "plan or report model identity changed"):
                lease.finish(status="completed", report_path=report)
        self.assertEqual(ledger.read_operational_ledger(self.ledger_path)["records"][0]["status"], "interrupted")

    def test_started_requests_require_partial_report_or_explicit_missing_evidence(self):
        with self.attempt() as lease:
            lease.mark_requests_started()
            with self.assertRaisesRegex(ValueError, "already marked"):
                lease.mark_requests_started()
            with self.assertRaisesRegex(ValueError, "partial report"):
                lease.finish(status="failed", failure_code="request_failed")
        record = ledger.read_operational_ledger(self.ledger_path)["records"][0]
        self.assertEqual(record["status"], "interrupted")
        self.assertTrue(record["attempts"][0]["partial_evidence_missing"])

    def test_exception_after_finish_does_not_record_success(self):
        with self.assertRaisesRegex(RuntimeError, "after finish"):
            with self.attempt() as lease:
                lease.mark_requests_started()
                lease.finish(status="completed", report_path=self.report(lease))
                self.assertEqual(ledger.read_operational_ledger(self.ledger_path)["records"][0]["status"], "started")
                raise RuntimeError("after finish")
        record = ledger.read_operational_ledger(self.ledger_path)["records"][0]
        self.assertEqual(record["status"], "interrupted")
        self.assertTrue(record["attempts"][0]["partial_evidence_missing"])

    def test_completed_means_finished_not_operational_gate_passed(self):
        with self.attempt() as lease:
            lease.mark_requests_started()
            report = self.report(lease)
            body = json.loads(report.read_text())
            body["operational_gate"]["status"] = "not_evaluated"
            report.write_text(json.dumps(body))
            lease.finish(status="completed", report_path=report)
        record = ledger.read_operational_ledger(self.ledger_path)["records"][0]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["attempts"][0]["report"]["gate_status"], "not_evaluated")

    def test_failed_gate_cannot_be_recorded_as_completed(self):
        with self.attempt() as lease:
            lease.mark_requests_started()
            report = self.report(lease)
            body = json.loads(report.read_text())
            body["operational_gate"]["status"] = "failed"
            report.write_text(json.dumps(body))
            with self.assertRaisesRegex(ValueError, "does not match"):
                lease.finish(status="completed", report_path=report)
        self.assertEqual(ledger.read_operational_ledger(self.ledger_path)["records"][0]["status"], "interrupted")

    def test_report_changed_after_finish_cannot_be_committed(self):
        with self.assertRaisesRegex(ValueError, "report changed before ledger commit"):
            with self.attempt() as lease:
                lease.mark_requests_started()
                report = self.report(lease)
                lease.finish(status="completed", report_path=report)
                report.write_text(report.read_text() + " ")
        record = ledger.read_operational_ledger(self.ledger_path)["records"][0]
        self.assertEqual(record["status"], "interrupted")

    def test_original_exception_survives_interruption_write_failure(self):
        with patch.object(ledger.MeasurementAttempt, "_interrupt_without_report", side_effect=OSError("write failed")):
            with self.assertRaisesRegex(RuntimeError, "measurement error") as caught:
                with self.attempt():
                    raise RuntimeError("measurement error")
        self.assertIn("Could not persist interrupted attempt: OSError", caught.exception.__notes__)
        self.assertEqual(ledger.read_operational_ledger(self.ledger_path)["records"][0]["status"], "started")

    def test_corrupt_ledger_and_failed_write_never_yield_an_attempt(self):
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text('{"schema_version":1,"records":[{"candidate_id":"bad"}]}')
        before = self.ledger_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "invalid candidate"):
            with self.attempt():
                self.fail("corrupt ledger yielded")
        self.assertEqual(before, self.ledger_path.read_bytes())
        self.ledger_path.unlink()
        with patch.object(ledger, "_write_ledger", side_effect=OSError("simulated write failure")):
            with self.assertRaisesRegex(OSError, "simulated"):
                with self.attempt():
                    self.fail("failed write yielded")
        self.assertFalse(self.ledger_path.exists())

    def test_killed_process_leaves_started_attempt_and_review_can_reopen_it(self):
        source = (
            "import sys,signal; from pathlib import Path; "
            "from bike_mlops import capital_operational_ledger as L; "
            "L.PROJECT_ROOT=Path(sys.argv[1]); "
            "ctx=L.measurement_attempt(Path(sys.argv[2]),ledger_path=Path(sys.argv[3]),lock_dir=Path(sys.argv[4])); "
            "lease=ctx.__enter__(); lease.mark_requests_started(); print('READY',flush=True); signal.pause()"
        )
        child = subprocess.Popen([sys.executable, "-c", source, str(self.root), str(self.plan_path),
                                  str(self.ledger_path), str(self.lock_dir)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([child.stdout], [], [], 15)
            self.assertTrue(ready, "child did not start the attempt")
            self.assertEqual(child.stdout.readline().strip(), "READY")
            with self.assertRaisesRegex(RuntimeError, "locked"):
                self.review()
            child.kill()
            child.wait(timeout=5)
            self.assertEqual(ledger.read_operational_ledger(self.ledger_path)["records"][0]["status"], "started")
            self.review(reason="verified runner terminated")
            record = ledger.read_operational_ledger(self.ledger_path)["records"][0]
            self.assertEqual(record["status"], "reviewed_reopen")
            self.assertEqual(record["attempts"][0]["status"], "interrupted")
            self.assertTrue(record["attempts"][0]["partial_evidence_missing"])
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()


if __name__ == "__main__":
    unittest.main()
