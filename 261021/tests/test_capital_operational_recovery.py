from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from bike_mlops import capital_operational_ledger as ledger
from bike_mlops import capital_operational_recovery as recovery
import test_capital_operational_ledger as ledger_tests


class OperationalRecoveryInspectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ledger_tests.OperationalLedgerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root_patch = patch.object(recovery, "PROJECT_ROOT", self.fixture.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def inspect(self):
        return recovery.inspect_operational_reports(self.fixture.candidate, ledger_path=self.fixture.ledger_path,
                                                    lock_dir=self.fixture.lock_dir)

    def make_report(self, lease, *, failure=False):
        original = self.fixture.report(lease, passed=not failure)
        body = json.loads(original.read_text())
        if not failure:
            body["operational_gate"]["status"] = "not_evaluated"
        destination = self.fixture.report_dir / f"operational-{lease.attempt_id}{'-failure' if failure else ''}.json"
        destination.write_text(json.dumps(body))
        original.unlink()
        return destination

    def completed(self):
        with self.fixture.attempt() as lease:
            lease.mark_requests_started()
            report = self.make_report(lease)
            lease.finish(status="completed", report_path=report)
        return lease, report

    def test_linked_and_orphan_reports_are_distinguished_without_writes(self):
        lease, report = self.completed()
        orphan = self.fixture.report_dir / f"operational-{lease.attempt_id}-failure.json"
        body = json.loads(report.read_text())
        body["completed"] = False
        body["operational_gate"]["status"] = "failed"
        orphan.write_text(json.dumps(body))
        before = {p: p.read_bytes() for p in [report, orphan, self.fixture.ledger_path]}
        result = self.inspect()
        item = result["attempts"][0]
        self.assertEqual(item["linked_report"]["status"], "matched")
        self.assertEqual(item["orphans"][0]["status"], "matches_attempt_not_adopted")
        self.assertEqual(item["plan_status"], "matched")
        self.assertEqual(item["evidence_state"], "linked_with_orphans")
        self.assertTrue(item["unresolved"])
        self.assertEqual(result["decision"], "inspection_only_no_approval")
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_missing_and_hash_modified_report_are_reported(self):
        lease, report = self.completed()
        original = report.read_bytes()
        report.write_bytes(original + b" ")
        self.assertEqual(self.inspect()["attempts"][0]["linked_report"]["reason"], "file_sha256_mismatch")
        report.unlink()
        self.assertEqual(self.inspect()["attempts"][0]["linked_report"]["status"], "missing")

    def test_matching_hash_does_not_hide_report_identity_mismatch(self):
        lease, report = self.completed()
        body = json.loads(report.read_text())
        body["model_identity"]["candidate_id"] = "0" * 64
        report.write_text(json.dumps(body))
        # Simulate a damaged ledger with a matching file hash; identity is still checked.
        import hashlib
        state = json.loads(self.fixture.ledger_path.read_text())
        state["records"][0]["attempts"][0]["report"]["sha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
        self.fixture.ledger_path.write_text(json.dumps(state))
        item = self.inspect()["attempts"][0]
        self.assertEqual(item["linked_report"]["reason"], "candidate_mismatch")
        self.assertTrue(item["unresolved"])

    def test_top_level_candidate_and_profile_claims_must_match(self):
        import hashlib
        _, report = self.completed()
        original = json.loads(report.read_text())
        state = json.loads(self.fixture.ledger_path.read_text())
        for changes, reason in (({"candidate_id": "0" * 64}, "candidate_mismatch"),
                                ({"data_profile_id": "uci-bike-sharing"}, "profile_or_target_mismatch")):
            body = {**original, **changes}
            report.write_text(json.dumps(body))
            state["records"][0]["attempts"][0]["report"]["sha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
            self.fixture.ledger_path.write_text(json.dumps(state))
            self.assertEqual(self.inspect()["attempts"][0]["linked_report"]["reason"], reason)

    def test_interrupted_attempt_and_lock_contention_are_not_recovered(self):
        with self.fixture.attempt() as lease:
            lease.mark_requests_started()
            with self.assertRaisesRegex(RuntimeError, "locked"):
                self.inspect()
        result = self.inspect()
        item = result["attempts"][0]
        self.assertEqual(item["status"], "interrupted")
        self.assertEqual(item["evidence_state"], "unfinished")
        self.assertTrue(item["requests_started"])
        self.assertEqual(item["linked_report"]["status"], "not_recorded")
        self.assertTrue(item["unresolved"])
        self.assertEqual(ledger.read_operational_ledger(self.fixture.ledger_path)["records"][0]["status"], "interrupted")

    def test_live_ledger_lock_refuses_inspection(self):
        self.completed()
        lock = self.fixture.ledger_path.with_suffix(self.fixture.ledger_path.suffix + ".lock")
        with ledger._exclusive_lock(lock):
            with self.assertRaisesRegex(RuntimeError, "locked"):
                self.inspect()

    def test_failed_preflight_report_can_identify_candidate_via_plan_and_ledger(self):
        with self.fixture.attempt() as lease:
            report = self.make_report(lease, failure=True)
            body = json.loads(report.read_text())
            body.pop("model_identity")
            report.write_text(json.dumps(body))
            lease.finish(status="failed", report_path=report, failure_code="preflight_failed")
        item = self.inspect()["attempts"][0]
        self.assertEqual(item["linked_report"]["status"], "matched")
        self.assertEqual(item["linked_report"]["candidate_identity_source"], "ledger_and_plan")
        self.assertEqual(item["evidence_state"], "linked")

    def test_missing_lock_refuses_without_creating_it(self):
        self.completed()
        lock = self.fixture.ledger_path.with_suffix(self.fixture.ledger_path.suffix + ".lock")
        lock.unlink()
        with self.assertRaisesRegex(RuntimeError, "existing inspection lock"):
            self.inspect()
        self.assertFalse(lock.exists())

    def test_changed_plan_invalidates_linked_report(self):
        self.completed()
        self.fixture.plan_path.write_text(self.fixture.plan_path.read_text() + " ")
        item = self.inspect()["attempts"][0]
        self.assertEqual(item["plan_status"], "changed_or_mismatched")
        self.assertEqual(item["linked_report"]["reason"], "plan_not_matched")

    def test_symlink_report_is_not_followed(self):
        _, report = self.completed()
        target = self.fixture.root / "unrelated.json"
        target.write_text("target unchanged")
        report.unlink()
        report.symlink_to(target)
        item = self.inspect()["attempts"][0]
        self.assertEqual(item["linked_report"]["reason"], "unreadable_or_symlink")
        self.assertEqual(target.read_text(), "target unchanged")

    def test_unassociated_report_is_not_claimed_for_candidate(self):
        self.completed()
        other = self.fixture.report_dir / "operational-00000000-0000-0000-0000-000000000000.json"
        other.write_text("{}")
        result = self.inspect()
        self.assertEqual(result["unassociated_report_files"], [{"name": other.name, "reason": "unknown_attempt_id"}])
        self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
