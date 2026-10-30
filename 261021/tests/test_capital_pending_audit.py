from __future__ import annotations

import json
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from bike_mlops import capital_operational_ledger as ledger
from bike_mlops import capital_operational_plan as plans
from bike_mlops import capital_operational_recovery as recovery
from bike_mlops.capital_pending_audit import inspect_pending_audit
import test_capital_operational_ledger as ledger_tests


class PendingAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ledger_tests.OperationalLedgerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.patch = patch.object(recovery, "PROJECT_ROOT", self.fixture.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        with self.fixture.attempt() as lease:
            lease.mark_requests_started()
            self.attempt = lease.attempt_id
            self.pending = f"capital-op:{self.attempt}:warmup:0000"
            path = self.fixture.report_dir / f"operational-{self.attempt}-failure.json"
            report = {"operational_attempt_id": self.attempt, "operational_plan_sha256": lease.plan_sha256,
                      "operational_plan_file_sha256": lease.plan_file_sha256, "completed": False,
                      "operational_gate": {"status": "failed"},
                      "partial_evidence": {"pending_request_id": self.pending,
                        "pending_request_status": "outcome_unknown_may_have_been_sent"}}
            path.write_text(json.dumps(report))
            lease.finish(status="failed", report_path=path, failure_code="request_failed")
        # Emulate a historical plan and linked report without per-request journal.
        self.fixture.plan["policy"].pop("request_journal")
        self.fixture.plan["plan_sha256"] = plans._digest(self.fixture.plan)
        self.fixture.plan_path.write_text(json.dumps(self.fixture.plan))
        state = json.loads(self.fixture.ledger_path.read_text())
        import hashlib
        attempt = state["records"][0]["attempts"][0]
        attempt["plan_sha256"] = self.fixture.plan["plan_sha256"]
        attempt["plan_file_sha256"] = hashlib.sha256(self.fixture.plan_path.read_bytes()).hexdigest()
        report["operational_plan_sha256"] = attempt["plan_sha256"]
        report["operational_plan_file_sha256"] = attempt["plan_file_sha256"]
        path.write_text(json.dumps(report))
        attempt["report"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.fixture.ledger_path.write_text(json.dumps(state))
        self.path = path
        self.db = Mock()
        self.db.url = "postgresql://bike_app:unused@127.0.0.1:55434/bikeops"
        self.db.sqlite = False
        self.db.data_profile_id = "capital-bikeshare"
        self.db.target_contract_id = "capital-trip-starts-hourly-v1"
        self.db.session.return_value = nullcontext(object())
        self.db.execute.return_value.fetchall.return_value = []

    def inspect(self):
        return inspect_pending_audit(self.fixture.candidate, self.attempt, self.db,
                                     ledger_path=self.fixture.ledger_path, lock_dir=self.fixture.lock_dir)

    def test_absent_row_stays_ambiguous_and_does_not_modify_evidence(self):
        before = (self.path.read_bytes(), self.fixture.ledger_path.read_bytes())
        result = self.inspect()
        self.assertEqual(result["audit_observation"], "not_seen_at_query_time")
        self.assertTrue(result["later_commit_possible"])
        self.assertEqual(result["decision"], "inspection_only_no_retry_or_approval")
        self.assertEqual(self.db.execute.call_args_list[0].args[1], "SET TRANSACTION READ ONLY")
        self.assertEqual(self.db.execute.call_args_list[1].args[2], (self.pending,))
        self.assertEqual(before, (self.path.read_bytes(), self.fixture.ledger_path.read_bytes()))

    def test_identity_match_does_not_claim_full_audit_success(self):
        identity = self.fixture.plan["identity"]
        row = {"request_id": self.pending, "candidate_id": self.fixture.candidate, "model_name": identity["model_name"],
               "model_version": identity["model_version"], "artifact_sha256": identity["registered_artifact_sha256"],
               "data_profile_id": identity["data_profile_id"], "target_contract_id": identity["target_contract_id"]}
        self.db.execute.return_value.fetchall.return_value = [row]
        match = self.inspect()
        self.assertEqual(match["audit_observation"], "identity_matches_unverified")
        self.assertFalse(match["later_commit_possible"])
        row["artifact_sha256"] = "0" * 64
        self.assertEqual(self.inspect()["audit_observation"], "identity_mismatch")

    def test_report_change_and_held_lock_are_rejected_before_database_query(self):
        with ledger._exclusive_lock(self.fixture.lock_dir / f"{self.fixture.candidate}.lock"):
            with self.assertRaisesRegex(RuntimeError, "locked"):
                self.inspect()
        self.path.write_text(self.path.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "different SHA-256"):
            self.inspect()
        self.db.session.assert_not_called()

    def test_foreign_database_and_query_errors_are_safe(self):
        self.db.url = "postgresql://bike_app:unused@127.0.0.1:55432/bikeops"
        with self.assertRaisesRegex(ValueError, "isolated"):
            self.inspect()
        self.db.url = "postgresql://bike_app:unused@127.0.0.1:55434/bikeops"
        self.db.execute.side_effect = RuntimeError("hidden secret")
        with self.assertRaisesRegex(RuntimeError, "read-only") as caught:
            self.inspect()
        self.assertNotIn("hidden secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
