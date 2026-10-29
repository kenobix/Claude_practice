from __future__ import annotations

import unittest
from types import SimpleNamespace

from bike_mlops import capital_operational_registry as registry
from bike_mlops.capital_operational_registry import (
    _validate_completed_evidence,
    _write_operational_evidence_tags,
)


def resource_context():
    return {
        "host": {
            "os": "Linux synthetic",
            "architecture": "x86_64",
            "logical_cpu_count": 4,
            "memory_total_bytes": 8_000_000_000,
            "python": "3.12.0",
            "dependencies": {"mlflow": "3.7.2"},
        },
        "capital_stack_containers": {
            "status": "captured",
            "docker_engine_version": "29.0.0",
            "containers": ["synthetic-container"],
            "stats": ["synthetic-container|0%|0B / 1B|0%"],
        },
    }


def evidence():
    identity = {
        "candidate_id": "a" * 64,
        "model_name": "bike-demand-capital",
        "model_version": "1",
        "registered_artifact_sha256": "b" * 64,
        "candidate_artifact_sha256": "c" * 64,
        "candidate_report_sha256": "d" * 64,
        "final_report_sha256": "e" * 64,
        "evaluation_protocol_sha256": "f" * 64,
        "data_sha256": "1" * 64,
        "feature_contract_version": "capital-hourly-v1",
        "data_profile_id": "capital-bikeshare",
        "target_contract_id": "capital-trip-starts-hourly-v1",
    }
    plan = {
        "identity": identity,
        "plan_sha256": "2" * 64,
        "resource_context_before_requests": resource_context(),
    }
    attempt_id = "11111111-1111-4111-8111-111111111111"
    request_hash = "3" * 64
    report = {
        "completed": True,
        "operational_attempt_id": attempt_id,
        "benchmark_id": attempt_id,
        "operational_plan_sha256": plan["plan_sha256"],
        "operational_plan_file_sha256": "4" * 64,
        "model_identity": {
            "candidate_id": identity["candidate_id"],
            "model_name": identity["model_name"],
            "model_version": identity["model_version"],
            "artifact_sha256": identity["registered_artifact_sha256"],
            "feature_contract_version": identity["feature_contract_version"],
        },
        "data_profile_id": identity["data_profile_id"],
        "target_contract_id": identity["target_contract_id"],
        "operational_gate": {
            "status": "not_evaluated",
            "input_provenance_status": "passed",
            "measurement_status": "passed",
            "observed": {
                "warmup_requests": 50,
                "measured_requests": 1000,
                "request_id_sha256": request_hash,
                "p95_ms": 100.0,
            },
        },
        "audit": {"status": "passed", "verified_rows": 1050, "request_id_sha256": request_hash},
        "snapshot": {"status": "verified", "observation_count": 744},
        "deployment_permission": {"status": "blocked"},
        "resource_context": resource_context(),
    }
    return plan, report, attempt_id


class CapitalOperationalRegistryEvidenceTests(unittest.TestCase):
    def test_builds_report_hash_and_status_tags_without_approving_version(self):
        plan, report, attempt_id = evidence()
        tags = _validate_completed_evidence(plan, report, "5" * 64, attempt_id, "4" * 64)
        self.assertEqual(tags["capital_operational_report_sha256"], "5" * 64)
        self.assertEqual(tags["capital_operational_measurement_status"], "passed")
        self.assertEqual(tags["capital_operational_numeric_recheck_status"], "matched")
        self.assertNotIn("operational_gate_status", tags)
        self.assertNotIn("deployment_permission", tags)

    def test_numeric_failure_is_recordable_but_gate_stays_unevaluated(self):
        plan, report, attempt_id = evidence()
        report["operational_gate"]["measurement_status"] = "failed"
        tags = _validate_completed_evidence(plan, report, "5" * 64, attempt_id, "4" * 64)
        self.assertEqual(tags["capital_operational_measurement_status"], "failed")
        self.assertNotIn("operational_gate_status", tags)

    def test_rejects_missing_or_mismatched_audit_and_provenance(self):
        for path, value in (
            (("audit", "status"), "not_evaluated"),
            (("audit", "verified_rows"), 1049),
            (("audit", "request_id_sha256"), "9" * 64),
            (("operational_gate", "input_provenance_status"), "failed"),
        ):
            with self.subTest(path=path):
                plan, report, attempt_id = evidence()
                report[path[0]][path[1]] = value
                with self.assertRaises(ValueError):
                    _validate_completed_evidence(plan, report, "5" * 64, attempt_id, "4" * 64)

    def test_rejects_eligible_or_incomplete_identity_and_resource_context(self):
        plan, report, attempt_id = evidence()
        report["deployment_permission"]["status"] = "eligible"
        with self.assertRaisesRegex(ValueError, "identity or blocked gate"):
            _validate_completed_evidence(plan, report, "5" * 64, attempt_id, "4" * 64)

        plan, report, attempt_id = evidence()
        report["resource_context"]["capital_stack_containers"]["status"] = "unavailable"
        with self.assertRaisesRegex(ValueError, "resource context"):
            _validate_completed_evidence(plan, report, "5" * 64, attempt_id, "4" * 64)

    def test_version_tag_write_is_idempotent_and_keeps_gate_blocked(self):
        class Client:
            def __init__(self):
                self.tags = {"operational_gate_status": "not_evaluated", "deployment_permission": "blocked"}
                self.writes = []

            def get_model_version(self, _name, version):
                return SimpleNamespace(name=registry.MODEL_NAME, version=version, status="READY",
                                       tags=dict(self.tags))

            def set_model_version_tag(self, _name, version, key, value):
                self.writes.append((version, key, value))
                self.tags[key] = value

        client = Client()
        tags = {"capital_operational_report_sha256": "a" * 64,
                "capital_operational_measurement_status": "passed"}
        _write_operational_evidence_tags(client, "1", tags)
        write_count = len(client.writes)
        _write_operational_evidence_tags(client, "1", tags)
        self.assertEqual(len(client.writes), write_count)
        self.assertEqual(client.tags["operational_gate_status"], "not_evaluated")
        self.assertEqual(client.tags["deployment_permission"], "blocked")

    def test_version_tag_write_refuses_different_attempt_without_overwriting(self):
        class Client:
            def __init__(self):
                self.tags = {"operational_gate_status": "not_evaluated", "deployment_permission": "blocked",
                             "capital_operational_report_sha256": "a" * 64}
                self.writes = []

            def get_model_version(self, _name, version):
                return SimpleNamespace(name=registry.MODEL_NAME, version=version, status="READY",
                                       tags=dict(self.tags))

            def set_model_version_tag(self, *args):
                self.writes.append(args)

        client = Client()
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            _write_operational_evidence_tags(client, "1", {"capital_operational_report_sha256": "b" * 64})
        self.assertEqual(client.writes, [])

    def test_version_tag_write_rejects_changed_blocked_gate(self):
        class Client:
            def get_model_version(self, _name, version):
                return SimpleNamespace(name=registry.MODEL_NAME, version=version, status="READY",
                                       tags={"operational_gate_status": "passed",
                                             "deployment_permission": "eligible"})

            def set_model_version_tag(self, *args):
                raise AssertionError("must not write")

        with self.assertRaisesRegex(ValueError, "only be attached while deployment remains blocked"):
            _write_operational_evidence_tags(Client(), "1", {"capital_operational_report_sha256": "a" * 64})


if __name__ == "__main__":
    unittest.main()
