from __future__ import annotations

import copy
import hashlib
import unittest
from unittest.mock import patch

import pandas as pd

from bike_mlops.capital_measurement_recheck import recheck_measurement_records
from bike_mlops import operational_benchmark as benchmark
import test_capital_operational_ledger as ledger_tests


class MeasurementRecheckTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ledger_tests.OperationalLedgerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = self.fixture.plan
        self.attempt_id = "3ae0909d-7ae1-4a09-a756-96a7ed582edf"
        self.file_sha = "9" * 64
        identity = self.plan["identity"]
        start = pd.Timestamp(self.plan["input_window"]["start_inclusive"])
        rows = []
        for pos in range(1050):
            phase = "warmup" if pos < 50 else "measured"
            index = pos if pos < 50 else pos - 50
            rows.append({"phase": phase, "index": index, "request_id": f"capital-op:{self.attempt_id}:{phase}:{index:04d}",
                         "target_hour": (start + pd.Timedelta(hours=index % 168)).isoformat(sep=" "),
                         "http_status": 200, "latency_ms": float(index + 1) / 10,
                         "prediction": 2.5, "data_version_sha256": "f" * 64, "error": None})
        digest = hashlib.sha256("\n".join(row["request_id"] for row in rows).encode()).hexdigest()
        self.report = {"operational_attempt_id": self.attempt_id, "benchmark_id": self.attempt_id,
                       "operational_plan_sha256": self.plan["plan_sha256"], "operational_plan_file_sha256": self.file_sha,
                       "completed": True, "data_profile_id": identity["data_profile_id"],
                       "target_contract_id": identity["target_contract_id"], "api_url": self.plan["api_url"],
                       "model_identity": {"candidate_id": identity["candidate_id"], "model_name": identity["model_name"],
                           "model_version": identity["model_version"], "artifact_sha256": identity["registered_artifact_sha256"],
                           "feature_contract_version": identity["feature_contract_version"]},
                       "deployment_permission": {"status": "blocked"},
                       "input_window": {key: self.plan["input_window"][key] for key in ("start_inclusive", "end_exclusive", "hours")},
                       "operational_gate": {"status": "not_evaluated", "measurement_status": "passed", "observed": {
                           "warmup_requests": 50, "warmup_errors": 0, "measured_requests": 1000, "measured_errors": 0,
                           "unique_request_ids": 1050, "retry_count": 0, "latency_count": 1000,
                           "request_id_sha256": digest, "p95_ms": 95.0}}, "measurements": rows}

    def check(self, report=None):
        return recheck_measurement_records(self.plan, self.report if report is None else report,
                                           attempt_id=self.attempt_id, plan_file_sha256=self.file_sha)

    def test_recomputes_nearest_rank_and_does_not_approve(self):
        result = self.check()
        self.assertEqual(result["p95_ms"], 95.0)
        self.assertEqual(result["numeric_status"], "passed")
        self.assertEqual(result["operational_gate"], "not_evaluated")
        self.assertEqual(result["deployment_permission"], "blocked")

    def test_rejects_changed_saved_rows_and_aggregate_claims(self):
        for change in ("id", "target", "prediction", "count", "p95", "artifact", "filehash"):
            with self.subTest(change=change):
                value = copy.deepcopy(self.report)
                if change == "id": value["measurements"][10]["request_id"] = value["measurements"][11]["request_id"]
                if change == "target": value["measurements"][50]["target_hour"] = "2024-01-01 00:00:00"
                if change == "prediction": value["measurements"][0]["prediction"] = float("nan")
                if change == "count": value["operational_gate"]["observed"]["unique_request_ids"] = True
                if change == "p95": value["operational_gate"]["observed"]["p95_ms"] = 0
                if change == "artifact": value["model_identity"]["artifact_sha256"] = "0" * 64
                if change == "filehash": value["operational_plan_file_sha256"] = "0" * 64
                with self.assertRaises(ValueError): self.check(value)

    def test_over_threshold_is_recomputed_without_approval(self):
        value = copy.deepcopy(self.report)
        for row in value["measurements"][50:]: row["latency_ms"] = 251.0
        value["operational_gate"]["observed"]["p95_ms"] = 251.0
        value["operational_gate"]["measurement_status"] = "failed"
        self.assertEqual(self.check(value)["numeric_status"], "failed")
        value["operational_gate"]["measurement_status"] = "passed"
        with self.assertRaisesRegex(ValueError, "numerical status"):
            self.check(value)

    def test_refuses_incomplete_or_preapproved_report(self):
        for key, value in (("completed", False), ("operational_gate", {"status": "passed", "observed": {}})):
            changed = copy.deepcopy(self.report)
            changed[key] = value
            with self.assertRaises(ValueError): self.check(changed)

    def test_rechecks_actual_benchmark_report_with_synthetic_transport(self):
        identity = self.plan["identity"]
        profile = {"model_name": identity["model_name"], "feature_contract_version": identity["feature_contract_version"],
                   "target": {"timezone_name": "America/New_York"}}
        base = {"data_profile_id": identity["data_profile_id"], "target_contract_id": identity["target_contract_id"],
                "model_name": identity["model_name"], "model_version": identity["model_version"],
                "candidate_id": identity["candidate_id"], "deployment_permission": "blocked"}
        def request(url, payload=None):
            if url.endswith("/ready"):
                return 200, {**base, "status": "ready"}
            if url.endswith("/version"):
                return 200, {**base, "artifact_sha256": identity["registered_artifact_sha256"],
                             "quality_gate_status": "passed", "feature_contract_version": identity["feature_contract_version"]}
            return 200, {**base, "request_id": payload["request_id"], "target_hour": payload["target_hour"],
                         "prediction": 1.0, "artifact_sha256": identity["registered_artifact_sha256"],
                         "data_version_sha256": "f" * 64, "feature_contract_version": identity["feature_contract_version"]}
        with (patch.object(benchmark, "DATA_PROFILE_ID", identity["data_profile_id"]),
              patch.object(benchmark, "TARGET_CONTRACT_ID", identity["target_contract_id"]),
              patch.object(benchmark, "DATA_PROFILE", profile)):
            report = benchmark.run_capital_operational_benchmark(
                api_url=self.plan["api_url"], model_version=identity["model_version"], candidate_id=identity["candidate_id"],
                start=self.plan["input_window"]["start_inclusive"], end=self.plan["input_window"]["end_exclusive"],
                attempt_id=self.attempt_id, request_fn=request, resource_fn=lambda: {})
        report.update({"operational_attempt_id": self.attempt_id, "operational_plan_sha256": self.plan["plan_sha256"],
                       "operational_plan_file_sha256": self.file_sha})
        self.assertEqual(self.check(report)["numeric_status"], "passed")


if __name__ == "__main__":
    unittest.main()
