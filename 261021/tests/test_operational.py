"""Contract and regression tests: test_operational."""
from __future__ import annotations


# ========================================================================
# test_operational_benchmark
# ========================================================================

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from bike_mlops import operational as benchmark
from bike_mlops.operational_validation import CapitalBenchmarkObserver


class OperationalBenchmarkTests(unittest.TestCase):
    def test_resource_capture_targets_the_requested_isolated_compose_project(self):
        outputs = [
            Mock(stdout="isolated-container\n"),
            Mock(stdout="/api-staging|bike-api|0|0\n"),
            Mock(stdout="/api-staging|0.10%|10MiB / 1GiB|1.00%\n"),
            Mock(stdout="29.9.1\n"),
        ]
        with (patch.dict("os.environ", {"CAPITAL_COMPOSE_PROJECT_NAME": "bikemlopstest0123456789abcdef"}),
              patch.object(benchmark.subprocess, "run", side_effect=outputs) as run):
            result = benchmark._machine_and_container_resources()
        self.assertEqual(result["capital_stack_containers"]["status"], "captured")
        self.assertIn("--project-name", run.call_args_list[0].args[0])
        self.assertIn("bikemlopstest0123456789abcdef", run.call_args_list[0].args[0])

    def test_resource_capture_uses_restricted_compose_env_file(self):
        outputs = [Mock(stdout="isolated-container\n"), Mock(stdout="container|image|0|0\n"),
                   Mock(stdout="container|0.10%|10MiB / 1GiB|1.00%\n"), Mock(stdout="29.9.1\n")]
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "harness.env"
            env_file.write_text("POSTGRES_ADMIN_PASSWORD=test-only\n", encoding="utf-8")
            env_file.chmod(0o600)
            with (patch.dict("os.environ", {"CAPITAL_COMPOSE_PROJECT_NAME": "bikemlopstest0123456789abcdef",
                                             "CAPITAL_COMPOSE_ENV_FILE": str(env_file)}),
                  patch.object(benchmark.subprocess, "run", side_effect=outputs) as run):
                result = benchmark._machine_and_container_resources()
        self.assertEqual(result["capital_stack_containers"]["status"], "captured")
        command = run.call_args_list[0].args[0]
        self.assertIn("--env-file", command)
        self.assertIn(str(env_file), command)

    def test_resource_capture_rejects_unsafe_compose_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "harness.env"
            env_file.write_text("secret\n", encoding="utf-8")
            env_file.chmod(0o644)
            with (patch.dict("os.environ", {"CAPITAL_COMPOSE_PROJECT_NAME": "bikemlopstest0123456789abcdef",
                                             "CAPITAL_COMPOSE_ENV_FILE": str(env_file)}),
                  patch.object(benchmark.subprocess, "run") as run):
                result = benchmark._machine_and_container_resources()
        self.assertEqual(result["capital_stack_containers"]["status"], "unavailable")
        run.assert_not_called()

    def test_resource_capture_rejects_invalid_project_name_without_docker(self):
        with (patch.dict("os.environ", {"CAPITAL_COMPOSE_PROJECT_NAME": "../bike-mlops-capital"}),
              patch.object(benchmark.subprocess, "run") as run):
            result = benchmark._machine_and_container_resources()
        self.assertEqual(result["capital_stack_containers"]["status"], "unavailable")
        self.assertFalse(any(call.args and call.args[0] and call.args[0][0] == "docker"
                             for call in run.call_args_list))

    def test_nearest_rank_p95_and_failed_metrics_are_explicit(self):
        self.assertEqual(benchmark.percentile_nearest_rank([4, 1, 3, 2], 50), 2.0)
        self.assertEqual(benchmark.percentile_nearest_rank(list(range(1, 101)), 95), 95.0)
        self.assertIsNone(benchmark.percentile_nearest_rank([], 95))
        result = benchmark.assess_operational_measurement(
            warmup_requests=50,
            warmup_errors=0,
            measured_requests=1000,
            measured_errors=1,
            latencies_ms=[100.0] * 949 + [251.0] * 51,
            unique_request_ids=1050,
            retry_count=0,
        )
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["checks"]["measured_errors"])
        self.assertFalse(result["checks"]["p95_latency"])
        self.assertEqual(result["observed"]["p95_ms"], 251.0)

    def test_only_local_root_urls_are_accepted(self):
        self.assertEqual(benchmark.validate_local_api_url("http://127.0.0.1:8180/"), "http://127.0.0.1:8180")
        for url in (
            "https://127.0.0.1:8180",
            "http://example.com",
            "http://user:secret@127.0.0.1:8180",
            "http://127.0.0.1:8180/path",
            "http://127.0.0.1:8180/?token=secret",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                benchmark.validate_local_api_url(url)

    def test_fixed_sequential_workload_pins_identity_and_never_retries(self):
        profile = {
            "model_name": "bike-demand-capital",
            "feature_contract_version": "bike-demand-features-v1",
            "target": {"timezone_name": "America/New_York"},
        }
        artifact_hash = "a" * 64
        candidate_id = "c" * 64
        calls: list[tuple[str, dict | None]] = []
        events = []

        class Observer:
            def before_request(self, payload):
                events.append(("before", payload["request_id"]))

            def after_response(self, payload, response):
                events.append(("after", response["request_id"]))

        def request_fn(url, payload=None, timeout=10):
            calls.append((url, payload))
            if url.endswith("/ready"):
                return 200, {
                    "status": "ready",
                    "data_profile_id": "capital-bikeshare",
                    "target_contract_id": "capital-trip-starts-hourly-v1",
                    "model_name": profile["model_name"],
                    "model_version": "7",
                    "candidate_id": candidate_id,
                    "deployment_permission": "blocked",
                }
            if url.endswith("/version"):
                return 200, {
                    "data_profile_id": "capital-bikeshare",
                    "target_contract_id": "capital-trip-starts-hourly-v1",
                    "model_name": profile["model_name"],
                    "model_version": "7",
                    "candidate_id": candidate_id,
                    "deployment_permission": "blocked",
                    "artifact_sha256": artifact_hash,
                    "quality_gate_status": "passed",
                    "feature_contract_version": profile["feature_contract_version"],
                }
            return 200, {
                "request_id": payload["request_id"],
                "target_hour": payload["target_hour"],
                "prediction": 12.5,
                "data_profile_id": "capital-bikeshare",
                "target_contract_id": "capital-trip-starts-hourly-v1",
                "model_name": profile["model_name"],
                "model_version": "7",
                "candidate_id": candidate_id,
                "deployment_permission": "blocked",
                "artifact_sha256": artifact_hash,
                "data_version_sha256": "f" * 64,
                "feature_contract_version": profile["feature_contract_version"],
            }

        with (
            patch.object(benchmark, "DATA_PROFILE_ID", "capital-bikeshare"),
            patch.object(benchmark, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
            patch.object(benchmark, "DATA_PROFILE", profile),
        ):
            result = benchmark.run_capital_operational_benchmark(
                api_url="http://127.0.0.1:8110",
                model_version="7",
                candidate_id=candidate_id,
                start="2026-04-01 00:00:00",
                end="2026-04-08 00:00:00",
                request_fn=request_fn,
                resource_fn=lambda: {"host": {"logical_cpu_count": 4}},
                attempt_id="3ae0909d-7ae1-4a09-a756-96a7ed582edf",
                expected_artifact_sha256=artifact_hash,
                request_observer=Observer(),
            )
        self.assertEqual(result["operational_gate"]["status"], "not_evaluated")
        self.assertEqual(result["operational_gate"]["measurement_status"], "passed")
        self.assertEqual(result["operational_gate"]["input_provenance_status"], "not_evaluated")
        self.assertEqual(result["operational_gate"]["observed"]["measured_requests"], 1000)
        self.assertEqual(result["operational_gate"]["observed"]["retry_count"], 0)
        self.assertEqual(result["deployment_permission"]["status"], "blocked")
        self.assertEqual(result["model_identity"]["candidate_id"], candidate_id)
        prediction_calls = [(url, payload) for url, payload in calls if payload is not None]
        self.assertEqual(len(prediction_calls), 1050)
        self.assertEqual(len(events), 2100)
        self.assertEqual(events[0], ("before", prediction_calls[0][1]["request_id"]))
        self.assertEqual(events[1], ("after", prediction_calls[0][1]["request_id"]))
        self.assertTrue(all(payload["request_id"].startswith("capital-op:3ae0909d-7ae1-4a09-a756-96a7ed582edf:") for _, payload in prediction_calls))
        self.assertEqual(len({payload["request_id"] for _, payload in prediction_calls}), 1050)
        self.assertEqual(prediction_calls[50][1]["target_hour"], prediction_calls[218][1]["target_hour"])
        self.assertEqual(len(result["measurements"]), 1050)
        self.assertTrue(all(sample["latency_ms"] is not None for sample in result["measurements"]))
        self.assertEqual(result["resource_context"]["host"]["logical_cpu_count"], 4)

    def test_observer_stops_requests_when_guard_or_provenance_fails(self):
        profile = {"model_name": "bike-demand-capital", "feature_contract_version": "bike-demand-features-v1",
                   "target": {"timezone_name": "America/New_York"}}
        candidate_id, artifact = "c" * 64, "a" * 64
        identity = {"data_profile_id": "capital-bikeshare", "target_contract_id": "capital-trip-starts-hourly-v1",
                    "model_name": profile["model_name"], "model_version": "7", "candidate_id": candidate_id,
                    "deployment_permission": "blocked"}
        calls = []

        def request_fn(url, payload=None):
            if url.endswith("/ready"):
                return 200, {**identity, "status": "ready"}
            if url.endswith("/version"):
                return 200, {**identity, "artifact_sha256": artifact, "quality_gate_status": "passed",
                             "feature_contract_version": profile["feature_contract_version"]}
            calls.append(payload)
            return 200, {**identity, "request_id": payload["request_id"], "target_hour": payload["target_hour"],
                         "prediction": 12.5, "artifact_sha256": artifact, "data_version_sha256": "f" * 64,
                         "feature_contract_version": profile["feature_contract_version"]}

        class Observer:
            def __init__(self, fail_stage):
                self.fail_stage = fail_stage
                self.before_count = 0

            def before_request(self, payload):
                self.before_count += 1
                if self.fail_stage == "before" and self.before_count == 2:
                    raise RuntimeError("snapshot lost")

            def after_response(self, payload, response):
                if self.fail_stage == "after":
                    raise ValueError("wrong provenance")

        with (patch.object(benchmark, "DATA_PROFILE_ID", "capital-bikeshare"),
              patch.object(benchmark, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
              patch.object(benchmark, "DATA_PROFILE", profile)):
            args = dict(api_url="http://127.0.0.1:8110", model_version="7", candidate_id=candidate_id,
                        start="2026-04-01 00:00:00", end="2026-04-08 00:00:00", request_fn=request_fn,
                        resource_fn=lambda: {}, attempt_id="3ae0909d-7ae1-4a09-a756-96a7ed582edf",
                        expected_artifact_sha256=artifact)
            before_report = benchmark.run_capital_operational_benchmark(**args, request_observer=Observer("before"))
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(before_report["measurements"]), 1)
            self.assertEqual(before_report["execution_error"], "before_request: RuntimeError")
            self.assertFalse(before_report["completed"])
            calls.clear()
            after_report = benchmark.run_capital_operational_benchmark(**args, request_observer=Observer("after"))
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(after_report["measurements"]), 1)
            self.assertFalse(after_report["completed"])
            self.assertEqual(after_report["operational_gate"]["status"], "failed")
            calls.clear()
            args["expected_artifact_sha256"] = "b" * 64
            with self.assertRaisesRegex(RuntimeError, "identity/readiness"):
                benchmark.run_capital_operational_benchmark(**args, request_observer=Observer("before"))
            self.assertEqual(calls, [])
            args["expected_artifact_sha256"] = artifact
            attempt = Mock(request_prefix="capital-op:3ae0909d-7ae1-4a09-a756-96a7ed582edf:")
            snapshot, sources = Mock(unsafe=True), Mock()
            sources.validate_response.side_effect = [None, ValueError("incorrect provenance")]
            observer = CapitalBenchmarkObserver(attempt, snapshot, sources, pinned_identity={})
            result = benchmark.run_capital_operational_benchmark(**args, request_observer=observer)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(observer.exchanges), 1)
            self.assertFalse(result["completed"])
            self.assertEqual(result["operational_gate"]["status"], "failed")
            attempt.mark_requests_started.assert_called_once_with()
            calls.clear()

            def timeout_request(url, payload=None):
                if payload is not None:
                    calls.append(payload)
                    raise TimeoutError("simulated timeout")
                return request_fn(url)

            args["request_fn"] = timeout_request
            result = benchmark.run_capital_operational_benchmark(**args, request_observer=Observer("none"))
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(result["measurements"]), 1)
            self.assertFalse(result["completed"])
            self.assertEqual(result["operational_gate"]["status"], "failed")

    def test_preflight_rejects_a_different_model_before_sending_predictions(self):
        profile = {
            "model_name": "bike-demand-capital",
            "feature_contract_version": "bike-demand-features-v1",
            "target": {"timezone_name": "America/New_York"},
        }
        candidate_id = "c" * 64
        calls = []

        def request_fn(url, payload=None, timeout=10):
            calls.append((url, payload))
            if url.endswith("/ready"):
                return 200, {
                    "status": "ready",
                    "data_profile_id": "capital-bikeshare",
                    "target_contract_id": "capital-trip-starts-hourly-v1",
                    "model_name": profile["model_name"],
                    "model_version": "7",
                    "candidate_id": candidate_id,
                    "deployment_permission": "blocked",
                }
            return 200, {
                "data_profile_id": "capital-bikeshare",
                "target_contract_id": "capital-trip-starts-hourly-v1",
                "model_name": profile["model_name"],
                "model_version": "8",
                "candidate_id": candidate_id,
                "artifact_sha256": "a" * 64,
                "quality_gate_status": "passed",
            }

        with (
            patch.object(benchmark, "DATA_PROFILE_ID", "capital-bikeshare"),
            patch.object(benchmark, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
            patch.object(benchmark, "DATA_PROFILE", profile),
        ):
            with self.assertRaisesRegex(RuntimeError, "identity/readiness"):
                benchmark.run_capital_operational_benchmark(
                    api_url="http://127.0.0.1:8110",
                    model_version="7",
                    candidate_id=candidate_id,
                    start="2026-04-01 00:00:00",
                    end="2026-04-08 00:00:00",
                    request_fn=request_fn,
                    resource_fn=lambda: {},
                )
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(payload is None for _, payload in calls))

    def test_preflight_keyboard_interrupt_closes_http_client(self):
        profile = {"model_name": "bike-demand-capital", "feature_contract_version": "bike-demand-features-v1",
                   "target": {"timezone_name": "America/New_York"}}
        client = Mock(side_effect=KeyboardInterrupt)
        with (patch.object(benchmark, "DATA_PROFILE_ID", "capital-bikeshare"),
              patch.object(benchmark, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
              patch.object(benchmark, "DATA_PROFILE", profile),
              patch.object(benchmark, "_PersistentJsonClient", return_value=client)):
            with self.assertRaises(KeyboardInterrupt):
                benchmark.run_capital_operational_benchmark(
                    api_url="http://127.0.0.1:8110", model_version="7", candidate_id="c" * 64,
                    start="2026-04-01 00:00:00", end="2026-04-08 00:00:00")
        client.close.assert_called_once_with()

    def test_keyboard_interrupt_is_saved_as_an_incomplete_failed_report(self):
        profile = {
            "model_name": "bike-demand-capital",
            "feature_contract_version": "bike-demand-features-v1",
            "target": {"timezone_name": "America/New_York"},
        }
        candidate_id = "c" * 64

        def request_fn(url, payload=None, timeout=10):
            if url.endswith("/ready"):
                return 200, {
                    "status": "ready",
                    "data_profile_id": "capital-bikeshare",
                    "target_contract_id": "capital-trip-starts-hourly-v1",
                    "model_name": profile["model_name"],
                    "model_version": "7",
                    "candidate_id": candidate_id,
                    "deployment_permission": "blocked",
                }
            if url.endswith("/version"):
                return 200, {
                    "data_profile_id": "capital-bikeshare",
                    "target_contract_id": "capital-trip-starts-hourly-v1",
                    "model_name": profile["model_name"],
                    "model_version": "7",
                    "candidate_id": candidate_id,
                    "artifact_sha256": "a" * 64,
                    "quality_gate_status": "passed",
                    "deployment_permission": "blocked",
                    "feature_contract_version": profile["feature_contract_version"],
                }
            if ":measured:" in payload["request_id"]:
                raise KeyboardInterrupt
            return 200, {
                "request_id": payload["request_id"],
                "target_hour": payload["target_hour"],
                "prediction": 12.5,
                "data_profile_id": "capital-bikeshare",
                "target_contract_id": "capital-trip-starts-hourly-v1",
                "model_name": profile["model_name"],
                "model_version": "7",
                "candidate_id": candidate_id,
                "deployment_permission": "blocked",
                "artifact_sha256": "a" * 64,
                "data_version_sha256": "f" * 64,
                "feature_contract_version": profile["feature_contract_version"],
            }

        with (
            patch.object(benchmark, "DATA_PROFILE_ID", "capital-bikeshare"),
            patch.object(benchmark, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
            patch.object(benchmark, "DATA_PROFILE", profile),
        ):
            report = benchmark.run_capital_operational_benchmark(
                api_url="http://127.0.0.1:8110",
                model_version="7",
                candidate_id=candidate_id,
                start="2026-04-01 00:00:00",
                end="2026-04-08 00:00:00",
                request_fn=request_fn,
                resource_fn=lambda: {},
            )
        self.assertFalse(report["completed"])
        self.assertEqual(report["operational_gate"]["status"], "failed")
        self.assertEqual(report["operational_gate"]["observed"]["measured_requests"], 1)
        self.assertEqual(report["operational_gate"]["observed"]["measured_errors"], 1)
        self.assertIn("KeyboardInterrupt", report["operational_gate"]["observed"]["errors"][-1]["reason"])

    def test_window_must_be_exactly_168_hours(self):
        with patch.object(benchmark, "DATA_PROFILE_ID", "capital-bikeshare"):
            with self.assertRaisesRegex(ValueError, "exactly 168 hours"):
                benchmark.run_capital_operational_benchmark(
                    api_url="http://127.0.0.1:8110",
                    model_version="7",
                    candidate_id="c" * 64,
                    start="2026-04-01 00:00:00",
                    end="2026-04-07 23:00:00",
                )

    def test_input_week_must_not_include_missing_or_ambiguous_wall_hours(self):
        profile = {"target": {"timezone_name": "America/New_York"}}
        with patch.object(benchmark, "DATA_PROFILE", profile):
            with self.assertRaisesRegex(ValueError, "nonexistent local hour"):
                benchmark._validate_timezone_grid(pd.Timestamp("2026-03-08 00:00:00"))
            with self.assertRaisesRegex(ValueError, "ambiguous repeated local hour"):
                benchmark._validate_timezone_grid(pd.Timestamp("2026-11-01 00:00:00"))

    def test_prediction_schema_requires_data_hash_and_finite_nonnegative_value(self):
        profile = {"feature_contract_version": "bike-demand-features-v1"}
        response = {
            "request_id": "benchmark:test:measured:0000",
            "target_hour": "2026-04-01 00:00:00",
            "prediction": 5.0,
            "data_profile_id": "capital-bikeshare",
            "target_contract_id": "capital-trip-starts-hourly-v1",
            "model_name": "bike-demand-capital",
            "model_version": "7",
            "candidate_id": "c" * 64,
            "deployment_permission": "blocked",
            "artifact_sha256": "a" * 64,
            "data_version_sha256": "f" * 64,
            "feature_contract_version": "bike-demand-features-v1",
        }
        with (
            patch.object(benchmark, "DATA_PROFILE_ID", "capital-bikeshare"),
            patch.object(benchmark, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
            patch.object(benchmark, "DATA_PROFILE", profile),
        ):
            self.assertIsNone(benchmark._prediction_is_valid(
                200,
                response,
                request_id=response["request_id"],
                target_hour=response["target_hour"],
                model_name="bike-demand-capital",
                model_version="7",
                artifact_sha256="a" * 64,
                candidate_id="c" * 64,
            ))
            self.assertIn("candidate_id", benchmark._prediction_is_valid(
                200,
                response,
                request_id=response["request_id"],
                target_hour=response["target_hour"],
                model_name="bike-demand-capital",
                model_version="7",
                artifact_sha256="a" * 64,
                candidate_id="0" * 64,
            ))
            self.assertIn("deployment_permission", benchmark._prediction_is_valid(
                200,
                dict(response, deployment_permission="eligible"),
                request_id=response["request_id"],
                target_hour=response["target_hour"],
                model_name="bike-demand-capital",
                model_version="7",
                artifact_sha256="a" * 64,
                candidate_id="c" * 64,
            ))
            self.assertIn("data-version SHA", benchmark._prediction_is_valid(
                200,
                {key: value for key, value in response.items() if key != "data_version_sha256"},
                request_id=response["request_id"],
                target_hour=response["target_hour"],
                model_name="bike-demand-capital",
                model_version="7",
                artifact_sha256="a" * 64,
                candidate_id="c" * 64,
            ))
            self.assertIn("finite and non-negative", benchmark._prediction_is_valid(
                200,
                dict(response, prediction=float("nan")),
                request_id=response["request_id"],
                target_hour=response["target_hour"],
                model_name="bike-demand-capital",
                model_version="7",
                artifact_sha256="a" * 64,
                candidate_id="c" * 64,
            ))

    def test_report_writer_refuses_overwrite_and_outside_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / "report.json"
            with self.assertRaisesRegex(ValueError, "under data/reports"):
                benchmark.write_benchmark_report(outside, {"schema_version": 1})
            root = Path(directory) / "project"
            output = root / "data/reports/capital-bikeshare/report.json"
            with patch.object(benchmark, "PROJECT_ROOT", root):
                benchmark.write_benchmark_report(output, {"schema_version": 1})
                self.assertEqual(output.read_text(encoding="utf-8"), '{\n  "schema_version": 1\n}\n')
                with self.assertRaises(FileExistsError):
                    benchmark.write_benchmark_report(output, {"schema_version": 1})


# ========================================================================
# test_capital_operational_plan
# ========================================================================

import copy
import importlib.util
import io
import os
import sys
import tempfile as _capital_operational_plan_tempfile
import unittest as _capital_operational_plan_unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path as _capital_operational_plan_Path
from types import SimpleNamespace
from unittest.mock import patch as _capital_operational_plan_patch

from bike_mlops import operational as plans, staging as capital_staging
from bike_mlops.evaluation import BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
from bike_mlops.model import directory_sha256


class OperationalPlanTests(_capital_operational_plan_unittest.TestCase):
    def setUp(self):
        self.temp = _capital_operational_plan_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = _capital_operational_plan_Path(self.temp.name)
        self.artifact = self.root / "model"
        self.artifact.mkdir()
        (self.artifact / "MLmodel").write_text("synthetic metadata\n")
        (self.artifact / "model.skops").write_bytes(b"synthetic model")
        self.evidence = {
            "candidate_id": "a" * 64, "selection_report_sha256": "b" * 64,
            "final_report_sha256": "c" * 64, "evaluation_protocol_sha256": "d" * 64,
            "candidate_artifact_sha256": "e" * 64,
            "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
            "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
            "selection": {"data_sha256": "f" * 64, "data_source": {"source_manifest_sha256": "1" * 64},
                          "data_profile_id": "capital-bikeshare", "target_contract_id": "capital-trip-starts-hourly-v1",
                          "feature_contract_version": "bike-demand-features-v1", "training_cutoff": "2024-01-15 00:00:00",
                          "training_row_keys_sha256": "2" * 64, "split_counts": {"train": 300}},
        }
        self.window = {
            "candidate_id": "a" * 64, "candidate_report_sha256": "b" * 64, "final_report_sha256": "c" * 64,
            "evaluation_protocol_sha256": "d" * 64, "data_sha256": "f" * 64, "manifest_sha256": "1" * 64,
            "start_inclusive": "2024-01-08 00:00:00", "end_exclusive": "2024-01-14 22:00:00",
            "hours": 168, "training_cutoff": "2024-01-15 00:00:00", "label_delay_hours": 3,
            "training_rows": 300, "training_row_keys_sha256": "2" * 64,
            "latest_replay_label_available_at": "2024-01-15 00:00:00",
        }
        # The final target is end-exclusive minus one hour; choose a valid contiguous 168h window.
        self.window["start_inclusive"] = "2024-01-07 22:00:00"
        with _capital_operational_plan_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
            tags = capital_staging._registration_tags(self.evidence)
        tags["artifact_sha256"] = directory_sha256(self.artifact)
        self.registered = SimpleNamespace(name="bike-demand-capital", version="7", status="READY", tags=tags)

    def build(self):
        with _capital_operational_plan_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
            return plans.build_operational_plan(evidence=self.evidence, window=self.window,
                registered=self.registered, artifact_dir=self.artifact, api_url="http://127.0.0.1:8110",
                registry_uri="http://127.0.0.1:5001",
                resource_context={"host": {"logical_cpu_count": 4}, "host_load_average": [0.1, 0.2, 0.3]})

    def test_plan_is_bound_to_candidate_and_written_once(self):
        plan = self.build()
        self.assertEqual(plan["identity"]["candidate_id"], "a" * 64)
        self.assertEqual(plan["policy"]["measured_requests"], 1000)
        self.assertEqual(plan["deployment_permission"], "blocked")
        path = self.root / "data/evaluations/capital-bikeshare/operational-plans/plan.json"
        with _capital_operational_plan_patch.object(plans, '_operational_plan_PROJECT_ROOT', self.root):
            plans.save_operational_plan(plan, path)
            self.assertEqual(plans.read_operational_plan(path), plan)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                plans.save_operational_plan(plan, path)
            self.assertEqual(before, path.read_bytes())
            with self.assertRaisesRegex(ValueError, "inside the operational-plans"):
                plans.save_operational_plan(plan, self.root / "outside.json")

    def test_returned_nested_policy_cannot_change_the_fixed_measurement_rules(self):
        plan = self.build()
        original = copy.deepcopy(plans.PLAN_POLICY)
        plan["policy"]["request_journal"]["timeout_outcome"] = "unsafe_retry"
        plan["plan_sha256"] = plans._digest(plan)
        self.assertEqual(plans.PLAN_POLICY, original)
        self.assertEqual(self.build()["policy"], original)
        with self.assertRaisesRegex(ValueError, "policy"):
            plans.validate_operational_plan(plan)

    def test_mismatched_registry_artifact_and_source_are_rejected(self):
        self.registered.tags["candidate_id"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "candidate_id"):
            self.build()

        self.registered.tags["candidate_id"] = "a" * 64
        (self.artifact / "model.skops").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "artifact"):
            self.build()
        (self.artifact / "model.skops").write_bytes(b"synthetic model")
        self.window["data_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "data_sha256"):
            self.build()

    def test_concurrent_plan_writes_never_overwrite_and_symlink_escape_is_rejected(self):
        plan = self.build()
        directory = self.root / "data/evaluations/capital-bikeshare/operational-plans"
        directory.mkdir(parents=True)
        path = directory / "concurrent.json"
        outside = self.root / "outside"
        outside.mkdir()
        (directory / "escape").symlink_to(outside, target_is_directory=True)

        def save():
            try:
                plans.save_operational_plan(plan, path)
                return "saved"
            except FileExistsError:
                return "exists"

        with _capital_operational_plan_patch.object(plans, '_operational_plan_PROJECT_ROOT', self.root):
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertCountEqual(list(pool.map(lambda _: save(), range(2))), ["saved", "exists"])
            self.assertEqual(plans.read_operational_plan(path), plan)
            with self.assertRaisesRegex(ValueError, "inside the operational-plans"):
                plans.save_operational_plan(plan, directory / "escape/plan.json")
            self.assertFalse((outside / "plan.json").exists())

    def test_modified_plan_and_rehashed_policy_are_rejected(self):
        plan = self.build()
        changed = copy.deepcopy(plan)
        changed["input_window"]["start_inclusive"] = "2024-01-01 00:00:00"
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            plans.validate_operational_plan(changed)
        changed = copy.deepcopy(plan)
        changed["policy"]["p95_limit_ms"] = 999
        changed["plan_sha256"] = plans._digest(changed)
        with self.assertRaisesRegex(ValueError, "policy"):
            plans.validate_operational_plan(changed)
        changed = copy.deepcopy(plan)
        changed["schema_version"] = 1
        changed["plan_sha256"] = plans._digest(changed)
        with self.assertRaisesRegex(ValueError, "Unsupported operational plan"):
            plans.validate_operational_plan(changed)
        changed = copy.deepcopy(plan)
        changed["policy"]["concurrency"] = True
        changed["plan_sha256"] = plans._digest(changed)
        with self.assertRaisesRegex(ValueError, "policy"):
            plans.validate_operational_plan(changed)

    def test_unavailable_labels_and_wrong_port_are_rejected(self):
        self.window["start_inclusive"] = "2024-01-08 00:00:00"
        self.window["end_exclusive"] = "2024-01-15 00:00:00"
        self.window["latest_replay_label_available_at"] = "2024-01-15 02:00:00"
        with self.assertRaisesRegex(ValueError, "labels are not available"):
            self.build()
        with self.assertRaisesRegex(ValueError, "local staging API"):
            plans._staging_url("http://127.0.0.1:8080")

    def test_plan_binding_rechecks_current_evidence_and_registry(self):
        plan = self.build()
        with _capital_operational_plan_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
            plans.verify_operational_plan_binding(plan, evidence=self.evidence, window=self.window,
                                                  registered=self.registered, artifact_dir=self.artifact,
                                                  registry_uri="http://127.0.0.1:5001")
            altered = copy.deepcopy(plan)
            altered["identity"]["model_version"] = "8"
            altered["plan_sha256"] = plans._digest(altered)
            with self.assertRaisesRegex(ValueError, "identity differs"):
                plans.verify_operational_plan_binding(altered, evidence=self.evidence, window=self.window,
                                                      registered=self.registered, artifact_dir=self.artifact,
                                                      registry_uri="http://127.0.0.1:5001")
            with self.assertRaisesRegex(ValueError, "registry_uri differs"):
                plans.verify_operational_plan_binding(plan, evidence=self.evidence, window=self.window,
                                                      registered=self.registered, artifact_dir=self.artifact,
                                                      registry_uri="http://localhost:5001")
            self.registered.tags["deployment_permission"] = "eligible"
            with self.assertRaisesRegex(ValueError, "deployment_permission"):
                plans.verify_operational_plan_binding(plan, evidence=self.evidence, window=self.window,
                                                      registered=self.registered, artifact_dir=self.artifact,
                                                      registry_uri="http://127.0.0.1:5001")

    def test_cli_writes_plan_without_sending_prediction_requests(self):
        script = _capital_operational_plan_Path(__file__).resolve().parents[1] / 'scripts/runtime_tools.py'
        spec = importlib.util.spec_from_file_location("capital_plan_cli_for_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = self.root / "data/evaluations/capital-bikeshare/operational-plans/cli.json"
        argv = ["prepare", "--candidate-id", "a" * 64, "--model-version", "7",
                "--selection-report", "candidate.json", "--final-report", "final.json",
                "--start", self.window["start_inclusive"], "--end", self.window["end_exclusive"],
                "--output", str(output)]
        client = SimpleNamespace(get_model_version=lambda name, version: self.registered)
        with (_capital_operational_plan_patch.object(module, '_prepare_capital_operational_plan_DATA_PROFILE_ID', "capital-bikeshare"),
              _capital_operational_plan_patch.object(module, '_prepare_capital_operational_plan_MODEL_NAME', "bike-demand-capital"),
              _capital_operational_plan_patch.object(module, "check_candidate_training_window", return_value=self.window),
              _capital_operational_plan_patch.object(module, '_prepare_capital_operational_plan_verify_candidate_for_staging', return_value=self.evidence),
              _capital_operational_plan_patch.object(module, "_machine_and_container_resources", return_value={"host": {}}),
              _capital_operational_plan_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
              _capital_operational_plan_patch.object(plans, '_operational_plan_PROJECT_ROOT', self.root),
              _capital_operational_plan_patch("mlflow.set_tracking_uri"), _capital_operational_plan_patch("mlflow.set_registry_uri") as registry_setter,
              _capital_operational_plan_patch("mlflow.tracking.MlflowClient", return_value=client) as client_factory,
              _capital_operational_plan_patch("mlflow.artifacts.download_artifacts", return_value=str(self.artifact)) as download,
              _capital_operational_plan_patch('bike_mlops.operational._PersistentJsonClient') as http_client,
              _capital_operational_plan_patch.dict("os.environ", {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5001"}),
              _capital_operational_plan_patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()) as stdout):
            module._prepare_capital_operational_plan_main()
            registry_setter.assert_called_once_with("http://127.0.0.1:5001")
            client_factory.assert_called_once_with(tracking_uri="http://127.0.0.1:5001", registry_uri="http://127.0.0.1:5001")
            http_client.assert_not_called()
            self.assertIn("plan_frozen_no_predictions_sent", stdout.getvalue())
            self.assertEqual(plans.read_operational_plan(output)["identity"]["model_version"], "7")
            download.reset_mock()
            self.registered.tags["candidate_id"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "candidate_id"):
                module._prepare_capital_operational_plan_main()
            download.assert_not_called()

    def test_isolated_mlflow_registry_download_and_changed_tag(self):
        import mlflow
        import mlflow.artifacts
        import mlflow.tracking._model_registry.utils as registry_utils
        from mlflow.tracking import MlflowClient

        old_uri = mlflow.get_tracking_uri()
        old_registry_uri = registry_utils._registry_uri
        old_registry_env = os.environ.get("MLFLOW_REGISTRY_URI")
        uri = f"sqlite:///{self.root / 'synthetic-registry.sqlite'}"
        try:
            mlflow.set_tracking_uri(uri)
            mlflow.set_registry_uri(uri)
            client = MlflowClient(tracking_uri=uri, registry_uri=uri)
            client.create_registered_model("bike-demand-capital")
            created = client.create_model_version(name="bike-demand-capital", source=self.artifact.as_uri(),
                                                  tags=self.registered.tags)
            with _capital_operational_plan_tempfile.TemporaryDirectory() as download:
                artifact_dir = _capital_operational_plan_Path(mlflow.artifacts.download_artifacts(
                    artifact_uri=f"models:/bike-demand-capital/{created.version}", dst_path=download))
                # MLflow can add registered_model_meta to downloads; registration pins the downloaded directory.
                client.set_model_version_tag("bike-demand-capital", created.version, "artifact_sha256",
                                             directory_sha256(artifact_dir))
                registered = client.get_model_version("bike-demand-capital", created.version)
                with _capital_operational_plan_tempfile.TemporaryDirectory() as second_download:
                    second = _capital_operational_plan_Path(mlflow.artifacts.download_artifacts(
                        artifact_uri=f"models:/bike-demand-capital/{created.version}", dst_path=second_download))
                    self.assertEqual(directory_sha256(second), directory_sha256(artifact_dir))
                with _capital_operational_plan_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
                    plan = plans.build_operational_plan(evidence=self.evidence, window=self.window,
                        registered=registered, artifact_dir=artifact_dir, api_url="http://127.0.0.1:8110",
                        registry_uri="http://127.0.0.1:5001",
                        resource_context={"host": {"kind": "synthetic"}})
                    plans.verify_operational_plan_binding(plan, evidence=self.evidence, window=self.window,
                                                          registered=registered, artifact_dir=artifact_dir,
                                                          registry_uri="http://127.0.0.1:5001")
                    client.set_model_version_tag("bike-demand-capital", created.version, "candidate_id", "0" * 64)
                    changed = client.get_model_version("bike-demand-capital", created.version)
                    with self.assertRaisesRegex(ValueError, "candidate_id"):
                        plans.verify_operational_plan_binding(plan, evidence=self.evidence, window=self.window,
                                                              registered=changed, artifact_dir=artifact_dir,
                                                              registry_uri="http://127.0.0.1:5001")
        finally:
            mlflow.set_tracking_uri(old_uri)
            registry_utils._registry_uri = old_registry_uri
            if old_registry_env is None:
                os.environ.pop("MLFLOW_REGISTRY_URI", None)
            else:
                os.environ["MLFLOW_REGISTRY_URI"] = old_registry_env


# ========================================================================
# test_capital_operational_ledger
# ========================================================================

import json
import select
import subprocess
import sys as _capital_operational_ledger_sys
import tempfile as _capital_operational_ledger_tempfile
import unittest as _capital_operational_ledger_unittest
from pathlib import Path as _capital_operational_ledger_Path
from unittest.mock import patch as _capital_operational_ledger_patch

from bike_mlops import operational as ledger
from bike_mlops import operational as _capital_operational_ledger_plans


class OperationalLedgerTests(_capital_operational_ledger_unittest.TestCase):
    def setUp(self):
        self.temp = _capital_operational_ledger_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = _capital_operational_ledger_Path(self.temp.name)
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
                     "registry_uri": "http://127.0.0.1:5001", "policy": dict(_capital_operational_ledger_plans.PLAN_POLICY),
                     "resource_context_before_requests": {}, "deployment_permission": "blocked"}
        self.plan["plan_sha256"] = _capital_operational_ledger_plans._digest(self.plan)
        self.root_patch = _capital_operational_ledger_patch.object(ledger, '_operational_ledger_PROJECT_ROOT', self.root)
        self.plan_root_patch = _capital_operational_ledger_patch.object(_capital_operational_ledger_plans, '_operational_plan_PROJECT_ROOT', self.root)
        self.root_patch.start()
        self.plan_root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.plan_root_patch.stop)
        _capital_operational_ledger_plans.save_operational_plan(self.plan, self.plan_path)

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
        with _capital_operational_ledger_patch.object(ledger.MeasurementAttempt, "_interrupt_without_report", side_effect=OSError("write failed")):
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
        with _capital_operational_ledger_patch.object(ledger, "_write_ledger", side_effect=OSError("simulated write failure")):
            with self.assertRaisesRegex(OSError, "simulated"):
                with self.attempt():
                    self.fail("failed write yielded")
        self.assertFalse(self.ledger_path.exists())

    def test_killed_process_leaves_started_attempt_and_review_can_reopen_it(self):
        source = (
            "import sys,signal; from pathlib import Path; from bike_mlops import operational as L; L._operational_ledger_PROJECT_ROOT=Path(sys.argv[1]); ctx=L.measurement_attempt(Path(sys.argv[2]),ledger_path=Path(sys.argv[3]),lock_dir=Path(sys.argv[4])); lease=ctx.__enter__(); lease.mark_requests_started(); print('READY',flush=True); signal.pause()"
        )
        child = subprocess.Popen([_capital_operational_ledger_sys.executable, "-c", source, str(self.root), str(self.plan_path),
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


# ========================================================================
# test_capital_request_journal
# ========================================================================

import hashlib
import json as _capital_request_journal_json
import os as _capital_request_journal_os
import select as _capital_request_journal_select
import signal
import subprocess as _capital_request_journal_subprocess
import sys as _capital_request_journal_sys
import unittest as _capital_request_journal_unittest
from pathlib import Path as _capital_request_journal_Path
from unittest.mock import patch as _capital_request_journal_patch

from support import FailureTrace, trace_directory

from bike_mlops.operational import RequestJournal, inspect_request_journal
from bike_mlops import operational_validation as recovery
from bike_mlops import operational as _capital_request_journal_plans
from bike_mlops.operational_validation import CapitalBenchmarkObserver as _capital_request_journal_CapitalBenchmarkObserver
import test_operational as ledger_tests


class RequestJournalTests(_capital_request_journal_unittest.TestCase):
    def setUp(self):
        self.fixture = ledger_tests.OperationalLedgerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = self.fixture.plan
        self.root_patch = _capital_request_journal_patch.object(recovery, "PROJECT_ROOT", self.fixture.root)
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
            observer = _capital_request_journal_CapitalBenchmarkObserver(attempt, Mock(unsafe=True), Mock(), pinned_identity={}, journal=journal)
            attempt.mark_requests_started()
            observer._marked = True
            with _capital_request_journal_patch('bike_mlops.operational.os.fsync', side_effect=OSError("disk")):
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
        legacy = _capital_request_journal_json.loads(_capital_request_journal_json.dumps(self.plan))
        legacy["policy"].pop("request_journal")
        legacy["plan_sha256"] = _capital_request_journal_plans._digest(legacy)
        _capital_request_journal_plans.validate_operational_plan(legacy, allow_legacy=True)
        self.fixture.plan_path.write_text(_capital_request_journal_json.dumps(legacy))
        with self.assertRaisesRegex(ValueError, "policy"):
            with self.fixture.attempt(): pass
        self.assertFalse(self.fixture.ledger_path.exists())

    def test_http_timer_excludes_slow_validation_even_when_validation_fails(self):
        from bike_mlops import operational as benchmark
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
        with (_capital_request_journal_patch.object(benchmark, "DATA_PROFILE_ID", identity["data_profile_id"]),
              _capital_request_journal_patch.object(benchmark, "TARGET_CONTRACT_ID", identity["target_contract_id"]),
              _capital_request_journal_patch.object(benchmark, "DATA_PROFILE", {**identity, "target": {"timezone_name": "America/New_York"}}),
              _capital_request_journal_patch.object(benchmark._operational_benchmark_time, "perf_counter", side_effect=lambda: clock[0])):
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
        code = _capital_request_journal_Path(__file__).with_name("request_interruption_child.py")
        process = None
        failed = True
        try:
            trace.emit("child_launch")
            process = _capital_request_journal_subprocess.Popen([_capital_request_journal_sys.executable, str(code), str(self.fixture.root),
                self.fixture.plan_path.relative_to(self.fixture.root).as_posix(), str(ledger_path),
                f"http://127.0.0.1:{server.server_port}/predict", str(directory),
                "stall_ledger" if stalled_ledger else "normal"], stdout=_capital_request_journal_subprocess.PIPE,
                stderr=_capital_request_journal_subprocess.PIPE, text=True)
            self.assertTrue(_capital_request_journal_select.select([process.stdout], [], [], 15)[0], "child did not reach send intent")
            attempt_id = process.stdout.readline().strip()
            self.assertTrue(attempt_id, process.stderr.read() if process.poll() is not None else "child stopped")
            self.assertTrue(received.wait(10), "request did not reach HTTP server")
            trace.emit("parent_signal_send", signal=int(sig))
            process.send_signal(sig)
            if stalled_ledger:
                with self.assertRaises(_capital_request_journal_subprocess.TimeoutExpired):
                    process.communicate(timeout=1)
                trace.emit("expected_wait_timeout", deadline_seconds=1)
                process.kill()
            else:
                # Keep the original ten-second acceptance deadline.
                try:
                    process.communicate(timeout=10)
                except _capital_request_journal_subprocess.TimeoutExpired:
                    trace.emit("unexpected_wait_timeout", deadline_seconds=10)
                    raise
            process.communicate(timeout=10)
            trace.emit("child_wait_returned", returncode=process.returncode)
            self.assertEqual(process.returncode, -signal.SIGKILL if sig == signal.SIGKILL or stalled_ledger else 130)
            state = _capital_request_journal_json.loads(ledger_path.read_text())["records"][0]
            self.assertEqual(state["status"], "interrupted" if sig == signal.SIGTERM and not stalled_ledger else "started")
            attempt = state["attempts"][0]
            view = inspect_request_journal(self.fixture.root / (attempt_id + ".jsonl"),
                attempt_id=attempt_id, candidate_id=self.fixture.candidate, plan=self.plan,
                plan_file_sha256=attempt["plan_file_sha256"])
            self.assertEqual(view["counts"]["outcome_unknown_may_have_been_sent"], 1)
            # A lost result cannot authorize an automatic retry, even when SIGTERM was received.
            from bike_mlops import operational as ledger
            with self.assertRaisesRegex(ValueError, "already has an operational attempt"):
                with ledger.measurement_attempt(self.fixture.plan_path, ledger_path=ledger_path,
                                                lock_dir=self.fixture.root / "child-locks"):
                    pass
            rows = [_capital_request_journal_json.loads(line) for line in (directory / "child.jsonl").read_text().splitlines()]
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
                        print("TEST_DIAGNOSTICS " + name + "\n" + path.read_text(), file=_capital_request_journal_sys.stderr)

    def test_sigterm_and_sigkill_leave_durable_intent_and_block_automatic_retry(self):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=sig):
                self._signal_case(sig)

    def test_stalled_interrupt_write_is_diagnosable_and_remains_unknown(self):
        # Reproduce the timeout symptom deliberately; this is not the historical cause.
        self._signal_case(signal.SIGTERM, stalled_ledger=True)


if __name__ == "__main__":
    import unittest as _unittest_entry
    _unittest_entry.main()
