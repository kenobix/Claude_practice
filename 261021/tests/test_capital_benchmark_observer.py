from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from bike_mlops.capital_benchmark_observer import CapitalBenchmarkObserver


class BenchmarkObserverTests(unittest.TestCase):
    def setUp(self):
        self.attempt = Mock(request_prefix="capital-op:attempt:")
        self.snapshot = Mock(unsafe=True)
        self.sources = Mock()
        self.observer = CapitalBenchmarkObserver(self.attempt, self.snapshot, self.sources,
                                                 pinned_identity={"candidate_id": "a" * 64})
        self.payload = {"request_id": "capital-op:attempt:warmup:0000", "target_hour": "2024-01-01 00:00:00"}
        self.response = {"data_version_sha256": "b" * 64}

    def test_checks_lock_marks_before_request_and_copies_verified_exchange(self):
        self.observer.before_request(self.payload)
        self.attempt.mark_requests_started.assert_called_once_with()
        self.assertEqual(self.snapshot.assert_held.call_count, 2)
        self.observer.after_response(self.payload, self.response)
        self.sources.validate_response.assert_called_once_with(self.payload["target_hour"], self.response)
        self.payload["target_hour"] = "changed"
        self.assertEqual(self.observer.exchanges[0][0]["target_hour"], "2024-01-01 00:00:00")
        with self.assertRaisesRegex(ValueError, "complete fixed-size"):
            self.observer.verify_audits()

    def test_snapshot_loss_and_bad_source_fail_closed(self):
        self.snapshot.assert_held.side_effect = RuntimeError("lost")
        with self.assertRaisesRegex(RuntimeError, "lost"):
            self.observer.before_request(self.payload)
        self.attempt.mark_requests_started.assert_not_called()
        self.snapshot.assert_held.side_effect = None
        self.observer.before_request(self.payload)
        self.sources.validate_response.side_effect = ValueError("wrong source")
        with self.assertRaisesRegex(ValueError, "wrong source"):
            self.observer.after_response(self.payload, self.response)
        self.assertEqual(self.observer.exchanges, [])
        with self.assertRaisesRegex(ValueError, "complete fixed-size"):
            self.observer.verify_audits()

    def test_duplicate_pending_and_foreign_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.observer.before_request({**self.payload, "request_id": "foreign"})
        self.observer.before_request(self.payload)
        with self.assertRaisesRegex(ValueError, "pending"):
            self.observer.before_request({**self.payload, "request_id": "capital-op:attempt:other"})
        self.observer.after_response(self.payload, self.response)
        with self.assertRaisesRegex(ValueError, "duplicated"):
            self.observer.before_request(self.payload)

    def test_complete_batch_is_checked_against_audit_and_pinned_identity(self):
        for index in range(1050):
            payload = {**self.payload, "request_id": f"capital-op:attempt:{index}"}
            self.observer.before_request(payload)
            self.observer.after_response(payload, self.response)
        self.attempt.mark_requests_started.assert_called_once_with()
        with patch("bike_mlops.capital_benchmark_observer.verify_prediction_audit_batch", return_value={"verified_rows": 1050}) as verify:
            self.assertEqual(self.observer.verify_audits(), {"verified_rows": 1050})
            self.assertEqual(len(verify.call_args.args[1]), 1050)
            self.assertEqual(verify.call_args.kwargs["candidate_id"], "a" * 64)
            self.assertEqual(verify.call_args.kwargs["request_prefix"], self.attempt.request_prefix)


if __name__ == "__main__":
    unittest.main()
