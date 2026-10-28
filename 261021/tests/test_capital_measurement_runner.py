from __future__ import annotations

import hashlib
import json
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from bike_mlops import capital_measurement_runner as runner
from bike_mlops import capital_operational_ledger as ledger
from bike_mlops import operational_benchmark as benchmark
import test_capital_operational_ledger as ledger_tests


class MeasurementRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ledger_tests.OperationalLedgerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = self.fixture.plan
        self.db = SimpleNamespace(url="postgresql://bike_app:unused@127.0.0.1:55434/bikeops", sqlite=False,
                                  data_profile_id="capital-bikeshare", target_contract_id="capital-trip-starts-hourly-v1")
        self.snapshot = Mock(unsafe=True)
        self.snapshot.row_count = 350
        self.sources = Mock()
        self.audit_fn = Mock(side_effect=self.audit)
        self.frame = pd.DataFrame({"timestamp": pd.to_datetime(["2024-01-01"]), "cnt": [1]})
        self.verified = SimpleNamespace(plan=self.plan, frame=self.frame)
        self.events = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(runner, "PROJECT_ROOT", self.fixture.root))
        self.stack.enter_context(patch.object(benchmark, "PROJECT_ROOT", self.fixture.root))
        self.stack.enter_context(patch.object(runner, "measurement_attempt", side_effect=lambda p: self.fixture.attempt()))
        self.preflight = self.stack.enter_context(patch.object(runner, "verify_measurement_inputs", return_value=self.verified))
        self.stack.enter_context(patch.object(runner, "hold_observation_snapshot", side_effect=self.hold))
        self.stack.enter_context(patch.object(runner, "FeatureSourceExpectations", return_value=self.sources))
        self.stack.enter_context(patch("bike_mlops.capital_benchmark_observer.verify_prediction_audit_batch", self.audit_fn))
        self.run = self.stack.enter_context(patch.object(runner, "run_capital_operational_benchmark", side_effect=self.measure))

    @contextmanager
    def hold(self, *_):
        self.events.append("lock")
        try:
            yield self.snapshot
        finally:
            self.events.append("release")

    def audit(self, guard, exchanges, sources, **kwargs):
        self.events.append("audit")
        ids = [entry[0]["request_id"] for entry in exchanges]
        return {"verified_rows": len(ids), "request_id_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest()}

    def measure(self, *, request_observer, attempt_id, **kwargs):
        self.events.append("measure")
        ids = []
        for index in range(1050):
            payload = {"request_id": f"capital-op:{attempt_id}:{index}", "target_hour": "2024-01-01 00:00:00"}
            response = {"data_version_sha256": "b" * 64}
            request_observer.before_request(payload)
            request_observer.after_response(payload, response)
            ids.append(payload["request_id"])
        identity = self.plan["identity"]
        return {"benchmark_id": attempt_id, "completed": True, "measurements": [],
                "model_identity": {"candidate_id": identity["candidate_id"], "model_name": identity["model_name"],
                    "model_version": identity["model_version"], "artifact_sha256": identity["registered_artifact_sha256"],
                    "feature_contract_version": identity["feature_contract_version"]},
                "operational_gate": {"status": "not_evaluated", "measurement_status": "passed", "observed": {
                    "request_id_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest()}}}

    def execute(self):
        return runner.run_staging_measurement(self.fixture.plan_path, self.fixture.root / "selection.json",
                                              self.fixture.root / "final.json", self.db)

    def read_record(self):
        return ledger.read_operational_ledger(self.fixture.ledger_path)["records"][0]

    def test_complete_measurement_audit_and_report_stay_unevaluated(self):
        result = self.execute()
        self.assertEqual(self.events, ["lock", "measure", "audit", "release"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["operational_gate"], "not_evaluated")
        self.assertEqual(self.read_record()["status"], "completed")
        report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(report["operational_gate"]["status"], "not_evaluated")
        self.assertEqual(report["audit"]["verified_rows"], 1050)
        self.assertEqual(report["snapshot"]["observation_count"], 350)
        self.assertEqual(self.read_record()["attempts"][0]["report"]["gate_status"], "not_evaluated")

    def test_real_benchmark_sequence_with_synthetic_transport(self):
        identity = self.plan["identity"]
        profile = {"model_name": identity["model_name"], "feature_contract_version": identity["feature_contract_version"],
                   "target": {"timezone_name": "America/New_York"}}
        base = {"data_profile_id": identity["data_profile_id"], "target_contract_id": identity["target_contract_id"],
                "model_name": identity["model_name"], "model_version": identity["model_version"],
                "candidate_id": identity["candidate_id"], "deployment_permission": "blocked"}
        calls = []

        def request(url, payload=None):
            if url.endswith("/ready"):
                return 200, {**base, "status": "ready"}
            if url.endswith("/version"):
                return 200, {**base, "artifact_sha256": identity["registered_artifact_sha256"],
                             "quality_gate_status": "passed", "feature_contract_version": profile["feature_contract_version"]}
            calls.append(payload)
            return 200, {**base, "request_id": payload["request_id"], "target_hour": payload["target_hour"],
                         "prediction": 1.0, "artifact_sha256": identity["registered_artifact_sha256"],
                         "data_version_sha256": "f" * 64, "feature_contract_version": profile["feature_contract_version"]}

        def actual_benchmark(**kwargs):
            return benchmark.run_capital_operational_benchmark(**kwargs, request_fn=request, resource_fn=lambda: {})

        self.run.side_effect = actual_benchmark
        with (patch.object(benchmark, "DATA_PROFILE_ID", "capital-bikeshare"),
              patch.object(benchmark, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
              patch.object(benchmark, "DATA_PROFILE", profile)):
            result = self.execute()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(calls), 1050)
        report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(report["measurements"]), 1050)
        self.assertEqual(report["operational_gate"]["status"], "not_evaluated")
        self.assertEqual(report["audit"]["verified_rows"], 1050)

    def test_preflight_failure_records_failed_report_without_request(self):
        self.preflight.side_effect = ValueError("hidden-secret")
        result = self.execute()
        self.run.assert_not_called()
        self.assertEqual(result["failure_stage"], "preflight")
        self.assertEqual(self.read_record()["status"], "failed")
        report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertFalse(report["completed"])
        self.assertNotIn("hidden-secret", json.dumps(report))
        self.assertIsNone(self.read_record()["attempts"][0]["requests_started_at_utc"])

    def test_audit_mismatch_keeps_attempt_failed(self):
        self.audit_fn.side_effect = ValueError("mismatch")
        result = self.execute()
        self.assertEqual(result["failure_stage"], "audit")
        self.assertEqual(self.read_record()["attempts"][0]["failure_code"], "audit_mismatch")
        report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertFalse(report["completed"])
        self.assertTrue(report["request_sequence_completed"])
        self.assertEqual(report["operational_gate"]["status"], "failed")
        self.assertEqual(report["audit"]["status"], "not_evaluated")

    def test_snapshot_release_failure_does_not_commit_success(self):
        @contextmanager
        def failed_exit(*_):
            yield self.snapshot
            raise RuntimeError("connection lost")
        with patch.object(runner, "hold_observation_snapshot", side_effect=failed_exit):
            result = self.execute()
        self.assertEqual(self.read_record()["status"], "failed")
        self.assertEqual(result["failure_stage"], "snapshot_release")
        self.assertTrue((self.fixture.report_dir / ("operational-" + result["attempt_id"] + ".json")).exists())
        self.assertTrue(result["report_path"].endswith("-failure.json"))

    def test_interrupt_preserves_pending_request_and_failed_attempt(self):
        def stop(*, request_observer, attempt_id, **_):
            request_observer.before_request({"request_id": f"capital-op:{attempt_id}:warmup:0000", "target_hour": "2024-01-01 00:00:00"})
            raise KeyboardInterrupt
        self.run.side_effect = stop
        with self.assertRaises(KeyboardInterrupt):
            self.execute()
        record = self.read_record()
        self.assertEqual(record["status"], "failed")
        report_path = self.fixture.root / record["attempts"][0]["report"]["path"]
        report = json.loads(report_path.read_text())
        self.assertTrue(report["pending_request_id"].endswith(":warmup:0000"))
        self.assertEqual(report["operational_gate"]["status"], "failed")

    def test_foreign_database_rejected_before_attempt(self):
        self.db.url = "postgresql://bike_app:unused@127.0.0.1:55432/bikeops"
        with self.assertRaisesRegex(ValueError, "isolated local"):
            self.execute()
        self.assertFalse(self.fixture.ledger_path.exists())
        self.preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()
