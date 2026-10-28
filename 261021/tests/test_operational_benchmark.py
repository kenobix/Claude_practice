from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from bike_mlops import operational_benchmark as benchmark


class OperationalBenchmarkTests(unittest.TestCase):
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
        calls: list[tuple[str, dict | None]] = []

        def request_fn(url, payload=None, timeout=10):
            calls.append((url, payload))
            if url.endswith("/ready"):
                return 200, {
                    "status": "ready",
                    "data_profile_id": "capital-bikeshare",
                    "target_contract_id": "capital-trip-starts-hourly-v1",
                    "model_name": profile["model_name"],
                    "model_version": "7",
                }
            if url.endswith("/version"):
                return 200, {
                    "data_profile_id": "capital-bikeshare",
                    "target_contract_id": "capital-trip-starts-hourly-v1",
                    "model_name": profile["model_name"],
                    "model_version": "7",
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
                api_url="http://127.0.0.1:8180",
                model_version="7",
                start="2026-04-01 00:00:00",
                end="2026-04-08 00:00:00",
                request_fn=request_fn,
                resource_fn=lambda: {"host": {"logical_cpu_count": 4}},
            )
        self.assertEqual(result["operational_gate"]["status"], "passed")
        self.assertEqual(result["operational_gate"]["observed"]["measured_requests"], 1000)
        self.assertEqual(result["operational_gate"]["observed"]["retry_count"], 0)
        self.assertEqual(result["deployment_permission"]["status"], "blocked")
        prediction_calls = [(url, payload) for url, payload in calls if payload is not None]
        self.assertEqual(len(prediction_calls), 1050)
        self.assertEqual(len({payload["request_id"] for _, payload in prediction_calls}), 1050)
        self.assertEqual(prediction_calls[50][1]["target_hour"], prediction_calls[218][1]["target_hour"])
        self.assertEqual(len(result["measurements"]), 1050)
        self.assertTrue(all(sample["latency_ms"] is not None for sample in result["measurements"]))
        self.assertEqual(result["resource_context"]["host"]["logical_cpu_count"], 4)

    def test_preflight_rejects_a_different_model_before_sending_predictions(self):
        profile = {
            "model_name": "bike-demand-capital",
            "feature_contract_version": "bike-demand-features-v1",
            "target": {"timezone_name": "America/New_York"},
        }
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
                }
            return 200, {
                "data_profile_id": "capital-bikeshare",
                "target_contract_id": "capital-trip-starts-hourly-v1",
                "model_name": profile["model_name"],
                "model_version": "8",
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
                    api_url="http://127.0.0.1:8180",
                    model_version="7",
                    start="2026-04-01 00:00:00",
                    end="2026-04-08 00:00:00",
                    request_fn=request_fn,
                    resource_fn=lambda: {},
                )
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(payload is None for _, payload in calls))

    def test_keyboard_interrupt_is_saved_as_an_incomplete_failed_report(self):
        profile = {
            "model_name": "bike-demand-capital",
            "feature_contract_version": "bike-demand-features-v1",
            "target": {"timezone_name": "America/New_York"},
        }

        def request_fn(url, payload=None, timeout=10):
            if url.endswith("/ready"):
                return 200, {
                    "status": "ready",
                    "data_profile_id": "capital-bikeshare",
                    "target_contract_id": "capital-trip-starts-hourly-v1",
                    "model_name": profile["model_name"],
                    "model_version": "7",
                }
            if url.endswith("/version"):
                return 200, {
                    "data_profile_id": "capital-bikeshare",
                    "target_contract_id": "capital-trip-starts-hourly-v1",
                    "model_name": profile["model_name"],
                    "model_version": "7",
                    "artifact_sha256": "a" * 64,
                    "quality_gate_status": "passed",
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
                api_url="http://127.0.0.1:8180",
                model_version="7",
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
                    api_url="http://127.0.0.1:8180",
                    model_version="7",
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
            ))
            self.assertIn("data-version SHA", benchmark._prediction_is_valid(
                200,
                {key: value for key, value in response.items() if key != "data_version_sha256"},
                request_id=response["request_id"],
                target_hour=response["target_hour"],
                model_name="bike-demand-capital",
                model_version="7",
                artifact_sha256="a" * 64,
            ))
            self.assertIn("finite and non-negative", benchmark._prediction_is_valid(
                200,
                dict(response, prediction=float("nan")),
                request_id=response["request_id"],
                target_hour=response["target_hour"],
                model_name="bike-demand-capital",
                model_version="7",
                artifact_sha256="a" * 64,
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


if __name__ == "__main__":
    unittest.main()
