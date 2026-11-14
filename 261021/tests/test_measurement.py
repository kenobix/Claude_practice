"""Contract and regression tests: test_measurement."""
from __future__ import annotations


# ========================================================================
# test_capital_benchmark_inputs
# ========================================================================

import hashlib
import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from bike_mlops.operational_validation import check_candidate_training_window, validate_training_window


class BenchmarkTrainingWindowTests(unittest.TestCase):
    def setUp(self):
        times = pd.date_range("2024-01-01", "2024-01-11", freq="h", inclusive="left")
        self.frame = pd.DataFrame({"timestamp": times, "cnt": range(len(times))})
        self.protocol = {
            "training": {"cutoff": "2024-01-11 00:00:00", "rows": 238},
            "validation": {"start_inclusive": "2024-01-11 00:00:00"},
            "label_availability": {"delay_hours": 3},
        }
        self.selection = {"training_cutoff": "2024-01-11 00:00:00", "split_counts": {"train": 238}}
        self.freeze_rows()

    def freeze_rows(self):
        cutoff = pd.Timestamp(self.protocol["training"]["cutoff"])
        start = pd.Timestamp(self.protocol["validation"]["start_inclusive"])
        delay = self.protocol["label_availability"]["delay_hours"]
        train = self.frame.loc[(self.frame.timestamp < start) &
                               (self.frame.timestamp + pd.Timedelta(hours=delay) <= cutoff)]
        payload = "".join(f"{stamp.isoformat(sep=' ')}\t{int(count)}\n"
                          for stamp, count in zip(train.timestamp, train.cnt))
        self.selection["training_row_keys_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
        self.selection["split_counts"]["train"] = len(train)
        self.protocol["training"]["rows"] = len(train)

    def validate(self, start="2024-01-03 22:00:00", end="2024-01-10 22:00:00"):
        return validate_training_window(self.frame, self.protocol, self.selection, start=start, end=end)

    def test_last_label_at_cutoff_is_allowed(self):
        result = self.validate()
        self.assertEqual(result["hours"], 168)
        self.assertEqual(result["latest_replay_label_available_at"], "2024-01-11 00:00:00")

    def test_rejects_two_unavailable_labels_previously_used_in_synthetic_benchmark(self):
        with self.assertRaisesRegex(ValueError, "non-training hour: 2024-01-10 22:00:00"):
            self.validate("2024-01-04 00:00:00", "2024-01-11 00:00:00")

    def test_rejects_missing_hour_even_if_frozen_dataset_and_hash_are_consistent(self):
        self.frame = self.frame.loc[self.frame.timestamp != pd.Timestamp("2024-01-05 12:00:00")].copy()
        self.freeze_rows()
        with self.assertRaisesRegex(ValueError, "2024-01-05 12:00:00"):
            self.validate()

    def test_rejects_changed_training_values_count_and_delay(self):
        self.frame.loc[0, "cnt"] = 999
        with self.assertRaisesRegex(ValueError, "training rows differ"):
            self.validate()
        self.frame.loc[0, "cnt"] = 0
        self.selection["split_counts"]["train"] -= 1
        with self.assertRaisesRegex(ValueError, "row count"):
            self.validate()
        self.selection["split_counts"]["train"] += 1
        self.protocol["label_availability"]["delay_hours"] = 2
        with self.assertRaisesRegex(ValueError, "label delay"):
            self.validate()

    def test_rejects_non_hour_unsorted_duplicates_and_short_window(self):
        self.frame.loc[0, "timestamp"] += pd.Timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError, "hour-aligned"):
            self.validate()
        self.frame.loc[0, "timestamp"] -= pd.Timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError, "exactly 168 hours"):
            self.validate(end="2024-01-10 21:00:00")
        self.frame.loc[0, "timestamp"] = self.frame.loc[1, "timestamp"]
        with self.assertRaisesRegex(ValueError, "sorted and unique"):
            self.validate()

    def test_cli_keeps_input_check_read_only_and_requires_explicit_mode(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/runtime_tools.py'
        spec = importlib.util.spec_from_file_location("benchmark_cli_for_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        argv = ["benchmark", "--candidate-id", "c" * 64,
                "--selection-report", "candidate.json", "--final-report", "final.json"]
        with (patch.object(module, '_benchmark_capital_runtime_DATA_PROFILE_ID', "capital-bikeshare"),
              patch.object(module, '_benchmark_capital_runtime_TARGET_CONTRACT_ID', "capital-trip-starts-hourly-v1"),
              patch.object(module, '_benchmark_capital_runtime_check_candidate_training_window', return_value={"hours": 168}) as check):
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
                module._benchmark_capital_runtime_main()
            with patch.object(sys, "argv", argv + ["--check-input-only"]), redirect_stdout(io.StringIO()) as output:
                module._benchmark_capital_runtime_main()
            self.assertIn('"status": "training_window_verified"', output.getvalue())
            self.assertEqual(check.call_count, 1)

    def test_cli_run_mode_calls_the_guarded_runner_with_exact_evidence(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/runtime_tools.py'
        spec = importlib.util.spec_from_file_location("benchmark_cli_measurement_for_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        plan, selection, final = Path("plan.json"), Path("candidate.json"), Path("final.json")
        database = object()
        result = {"status": "completed", "measurement_status": "failed",
                  "operational_gate": "not_evaluated", "deployment_permission": "blocked"}
        import bike_mlops.operational_runner as runner_module

        argv = ["benchmark", "--run-measurement", "--plan", str(plan),
                "--candidate-id", "c" * 64, "--selection-report", str(selection), "--final-report", str(final)]
        with (patch.object(module, '_benchmark_capital_runtime_DATA_PROFILE_ID', "capital-bikeshare"),
              patch.object(module, '_benchmark_capital_runtime_TARGET_CONTRACT_ID', "capital-trip-starts-hourly-v1"),
              patch.object(module, "DEFAULT_DATABASE_URL", "postgresql://bike_app:synthetic@localhost:55434/bikeops"),
              patch.object(module, '_benchmark_capital_runtime_Database', return_value=database),
              patch.object(runner_module, "run_staging_measurement", return_value=result) as runner,
              patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()) as output):
            module._benchmark_capital_runtime_main()
        runner.assert_called_once_with(plan, selection, final, database)
        self.assertIn('"status": "completed"', output.getvalue())
        self.assertIn('"deployment_permission": "blocked"', output.getvalue())

    def test_candidate_evidence_and_source_are_checked_before_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv, manifest = root / "hourly.csv", root / "manifest.json"
            csv.write_text("synthetic csv", encoding="utf-8")
            manifest.write_text("synthetic manifest", encoding="utf-8")
            protocol_path = root / "evaluation-protocol.json"
            protocol_path.write_text("synthetic protocol", encoding="utf-8")
            self.protocol["protocol_sha256"] = "d" * 64
            self.selection.update({"data_sha256": hashlib.sha256(csv.read_bytes()).hexdigest(),
                                   "data_source": {"source_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()},
                                   "evaluation_protocol_artifact": {"sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest()}})
            evidence = {"candidate_id": "c" * 64, "selection": self.selection,
                        "selection_report_path": root / "candidate-report.json", "selection_report_sha256": "a" * 64,
                        "final_report_sha256": "b" * 64, "evaluation_protocol_sha256": "d" * 64}
            with (patch('bike_mlops.staging.verify_candidate_for_staging', return_value=evidence),
                  patch("bike_mlops.cli._load_profile_hourly", return_value=self.frame),
                  patch("bike_mlops.config.DEFAULT_DATASET", csv), patch("bike_mlops.config.DATASET_MANIFEST", manifest),
                  patch('bike_mlops.evaluation.read_protocol', return_value=self.protocol)):
                run = lambda candidate: check_candidate_training_window(
                    root / "candidate-report.json", root / "final.json", candidate,
                    start="2024-01-03 22:00:00", end="2024-01-10 22:00:00")
                self.assertEqual(run("c" * 64)["hours"], 168)
                with self.assertRaisesRegex(ValueError, "candidate ID"):
                    run("e" * 64)
                csv.write_text("altered", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "CSV or manifest differs"):
                    run("c" * 64)
                csv.write_text("synthetic csv", encoding="utf-8")
                protocol_path.write_text("changed", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "protocol file differs"):
                    run("c" * 64)


# ========================================================================
# test_capital_measurement_guard
# ========================================================================

import tempfile as _capital_measurement_guard_tempfile
import unittest as _capital_measurement_guard_unittest
from pathlib import Path as _capital_measurement_guard_Path

import pandas as _capital_measurement_guard_pd

from bike_mlops.operational_validation import FeatureSourceExpectations, hold_observation_snapshot
from bike_mlops.db import Database, stable_hash


class CapitalMeasurementGuardTests(_capital_measurement_guard_unittest.TestCase):
    def setUp(self):
        self.hash = "a" * 64
        self.frame = _capital_measurement_guard_pd.DataFrame({
            "timestamp": _capital_measurement_guard_pd.to_datetime(["2024-01-03 10:00", "2024-01-09 10:00", "2024-01-10 10:00"]),
            "cnt": [5, 0, 20],
        })

    def test_expected_hash_matches_exact_lags_including_zero(self):
        sources = FeatureSourceExpectations(self.frame, self.hash, label_delay_hours=3)
        expected = stable_hash({
            "lag_24": {"timestamp": "2024-01-09 10:00:00", "cnt": 0, "data_hash": self.hash},
            "lag_168": {"timestamp": "2024-01-03 10:00:00", "cnt": 5, "data_hash": self.hash},
        })
        self.assertEqual(sources.source_hash("2024-01-10 10:00:00"), expected)
        sources.validate_response("2024-01-10 10:00:00", {"data_version_sha256": expected})
        with self.assertRaisesRegex(ValueError, "provenance"):
            sources.validate_response("2024-01-10 10:00:00", {"data_version_sha256": "f" * 64})

    def test_missing_and_late_lags_are_not_treated_as_observed(self):
        missing = FeatureSourceExpectations(self.frame.iloc[1:], self.hash, label_delay_hours=3)
        self.assertEqual(missing.source_hash("2024-01-10 10:00:00"), stable_hash({
            "lag_24": {"timestamp": "2024-01-09 10:00:00", "cnt": 0, "data_hash": self.hash},
            "lag_168": None,
        }))
        late = FeatureSourceExpectations(self.frame, self.hash, label_delay_hours=169)
        self.assertEqual(late.source_hash("2024-01-10 10:00:00"), stable_hash({"lag_24": None, "lag_168": None}))

    def test_invalid_source_data_and_targets_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "lowercase SHA"):
            FeatureSourceExpectations(self.frame, "A" * 64, label_delay_hours=3)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            FeatureSourceExpectations(_capital_measurement_guard_pd.concat([self.frame, self.frame.iloc[:1]]), self.hash, label_delay_hours=3)
        bad = self.frame.copy()
        bad.loc[0, "cnt"] = -1
        with self.assertRaisesRegex(ValueError, "Invalid"):
            FeatureSourceExpectations(bad, self.hash, label_delay_hours=3)
        sources = FeatureSourceExpectations(self.frame, self.hash, label_delay_hours=3)
        with self.assertRaisesRegex(ValueError, "naive hour"):
            sources.source_hash("2024-01-10T10:30:00")
        with self.assertRaisesRegex(ValueError, "naive hour"):
            sources.source_hash("2024-01-10T10:00:00+00:00")

    def test_sqlite_is_rejected_for_live_measurement_guard(self):
        with _capital_measurement_guard_tempfile.TemporaryDirectory() as directory:
            db = Database(f"sqlite:///{_capital_measurement_guard_Path(directory) / 'test.sqlite'}")
            db.initialize()
            with self.assertRaisesRegex(ValueError, "requires PostgreSQL"):
                with hold_observation_snapshot(db, self.frame, self.hash):
                    pass


# ========================================================================
# test_capital_benchmark_observer
# ========================================================================

import unittest as _capital_benchmark_observer_unittest
from unittest.mock import Mock, patch as _capital_benchmark_observer_patch

from bike_mlops.operational_validation import CapitalBenchmarkObserver


class BenchmarkObserverTests(_capital_benchmark_observer_unittest.TestCase):
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
        with _capital_benchmark_observer_patch('bike_mlops.operational_validation._benchmark_observer_verify_prediction_audit_batch', return_value={"verified_rows": 1050}) as verify:
            self.assertEqual(self.observer.verify_audits(), {"verified_rows": 1050})
            self.assertEqual(len(verify.call_args.args[1]), 1050)
            self.assertEqual(verify.call_args.kwargs["candidate_id"], "a" * 64)
            self.assertEqual(verify.call_args.kwargs["request_prefix"], self.attempt.request_prefix)


# ========================================================================
# test_capital_measurement_preflight
# ========================================================================

import copy
import unittest as _capital_measurement_preflight_unittest
from contextlib import ExitStack
from pathlib import Path as _capital_measurement_preflight_Path
from unittest.mock import Mock as _capital_measurement_preflight_Mock, patch as _capital_measurement_preflight_patch

import pandas as _capital_measurement_preflight_pd

from bike_mlops import operational_runner as preflight
from bike_mlops import operational as ledger
from bike_mlops import operational as plans
from bike_mlops import staging as capital_staging
from bike_mlops.evaluation import sha256_file
import test_operational as plan_tests


class MeasurementPreflightTests(_capital_measurement_preflight_unittest.TestCase):
    def setUp(self):
        self.fixture = plan_tests.OperationalPlanTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.csv = self.root / "input.csv"
        self.manifest = self.root / "manifest.json"
        self.csv.write_text("synthetic dataset")
        self.manifest.write_text("synthetic manifest")
        self.evidence = self.fixture.evidence
        self.window = self.fixture.window
        self.evidence["selection"]["data_sha256"] = self.window["data_sha256"] = sha256_file(self.csv)
        self.evidence["selection"]["data_source"]["source_manifest_sha256"] = self.window["manifest_sha256"] = sha256_file(self.manifest)
        with _capital_measurement_preflight_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"):
            tags = capital_staging._registration_tags(self.evidence)
        tags["artifact_sha256"] = self.fixture.registered.tags["artifact_sha256"]
        self.fixture.registered.tags = tags
        self.plan = self.fixture.build()
        self.plan_path = self.root / "data/evaluations/capital-bikeshare/operational-plans/test.json"
        self.ledger_path = self.root / "data/evaluations/capital-bikeshare/attempts.json"
        self.lock_dir = self.root / "data/evaluations/capital-bikeshare/locks"
        with _capital_measurement_preflight_patch.object(plans, '_operational_plan_PROJECT_ROOT', self.root):
            plans.save_operational_plan(self.plan, self.plan_path)
        self.frame = _capital_measurement_preflight_pd.DataFrame({"timestamp": _capital_measurement_preflight_pd.to_datetime(["2024-01-01"]), "cnt": [3]})
        self.client = _capital_measurement_preflight_Mock()
        self.client.get_model_version.return_value = self.fixture.registered
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for p in (
            _capital_measurement_preflight_patch.object(ledger, '_operational_ledger_PROJECT_ROOT', self.root),
            _capital_measurement_preflight_patch.object(preflight, "DATA_PROFILE_ID", "capital-bikeshare"),
            _capital_measurement_preflight_patch.object(preflight, "MODEL_NAME", "bike-demand-capital"),
            _capital_measurement_preflight_patch.object(preflight, "DEFAULT_DATASET", self.csv),
            _capital_measurement_preflight_patch.object(preflight, "DATASET_MANIFEST", self.manifest),
            _capital_measurement_preflight_patch.object(preflight, "check_candidate_training_window", return_value=self.window),
            _capital_measurement_preflight_patch.object(preflight, "verify_candidate_for_staging", side_effect=lambda *_: copy.deepcopy(self.evidence)),
            _capital_measurement_preflight_patch.object(preflight, "_load_profile_hourly", return_value=self.frame),
            _capital_measurement_preflight_patch.object(capital_staging, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
            _capital_measurement_preflight_patch("mlflow.tracking.MlflowClient", return_value=self.client),
        ):
            self.stack.enter_context(p)
        self.download = self.stack.enter_context(_capital_measurement_preflight_patch("mlflow.artifacts.download_artifacts", return_value=str(self.fixture.artifact)))

    def attempt(self):
        return ledger.measurement_attempt(self.plan_path, ledger_path=self.ledger_path, lock_dir=self.lock_dir)

    def verify(self, attempt):
        return preflight.verify_measurement_inputs(attempt, self.plan_path, self.root / "selection.json", self.root / "final.json")

    def test_binds_started_attempt_frame_and_registry_without_predictions(self):
        with self.attempt() as attempt:
            result = self.verify(attempt)
            self.assertEqual(result.plan["plan_sha256"], attempt.plan_sha256)
            self.assertEqual(result.plan_file_sha256, attempt.plan_file_sha256)
            _capital_measurement_preflight_pd.testing.assert_frame_equal(result.frame, self.frame)
            self.assertEqual(self.client.get_model_version.call_count, 2)
            self.download.assert_called_once()
            self.assertEqual(self.download.call_args.kwargs["registry_uri"], "http://127.0.0.1:5001")
            self.assertEqual(self.download.call_args.kwargs["tracking_uri"], "http://127.0.0.1:5001")
            self.assertIsNone(ledger.read_operational_ledger(self.ledger_path)["records"][0]["attempts"][0]["requests_started_at_utc"])
        with self.assertRaisesRegex(ValueError, "active unfinished"):
            self.verify(attempt)

    def test_rejects_changed_plan_or_dataset_before_registry_access(self):
        with self.attempt() as attempt:
            self.csv.write_text("different")
            with self.assertRaisesRegex(ValueError, "CSV or manifest"):
                self.verify(attempt)
            self.client.get_model_version.assert_not_called()
            self.download.assert_not_called()
        self.csv.write_text("synthetic dataset")
        # The same candidate cannot be silently retried after the interruption.
        with self.assertRaisesRegex(ValueError, "already has"):
            with self.attempt():
                pass

    def test_rejects_registry_change_during_download(self):
        changed = copy.deepcopy(self.fixture.registered)
        changed.tags["candidate_id"] = "0" * 64
        self.client.get_model_version.side_effect = [self.fixture.registered, changed]
        with self.attempt() as attempt:
            with self.assertRaisesRegex(ValueError, "candidate_id"):
                self.verify(attempt)
            self.assertIsNone(ledger.read_operational_ledger(self.ledger_path)["records"][0]["attempts"][0]["requests_started_at_utc"])

    def test_rejects_plan_or_csv_changed_during_load(self):
        def alter_plan(_):
            self.plan_path.write_text(self.plan_path.read_text() + " ")
            return self.frame
        with _capital_measurement_preflight_patch.object(preflight, "_load_profile_hourly", side_effect=alter_plan):
            with self.attempt() as attempt:
                with self.assertRaisesRegex(ValueError, "plan changed during preflight"):
                    self.verify(attempt)

    def test_rejects_dataset_modified_while_loading(self):
        def alter_csv(_):
            self.csv.write_text("changed during loading")
            return self.frame
        with _capital_measurement_preflight_patch.object(preflight, "_load_profile_hourly", side_effect=alter_csv):
            with self.attempt() as attempt:
                with self.assertRaisesRegex(ValueError, "CSV or manifest"):
                    self.verify(attempt)
                self.client.get_model_version.assert_not_called()

    def test_rejects_changed_candidate_evidence_after_download(self):
        changed = copy.deepcopy(self.evidence)
        changed["final_report_sha256"] = "0" * 64
        with _capital_measurement_preflight_patch.object(preflight, "verify_candidate_for_staging", side_effect=[self.evidence, changed]):
            with self.attempt() as attempt:
                with self.assertRaisesRegex(ValueError, "evidence changed during preflight"):
                    self.verify(attempt)

    def test_rejects_modified_downloaded_model(self):
        (self.fixture.artifact / "model.skops").write_bytes(b"tampered")
        with self.attempt() as attempt:
            with self.assertRaisesRegex(ValueError, "artifact"):
                self.verify(attempt)


# ========================================================================
# test_capital_measurement_runner
# ========================================================================

import hashlib as _capital_measurement_runner_hashlib
import json
import unittest as _capital_measurement_runner_unittest
from contextlib import ExitStack as _capital_measurement_runner_ExitStack, contextmanager
from pathlib import Path as _capital_measurement_runner_Path
from types import SimpleNamespace
from unittest.mock import Mock as _capital_measurement_runner_Mock, patch as _capital_measurement_runner_patch

import pandas as _capital_measurement_runner_pd

from bike_mlops import operational_runner as runner
from bike_mlops import operational as _capital_measurement_runner_ledger
from bike_mlops import operational as benchmark
from bike_mlops.operational import request_schedule
import test_operational as ledger_tests


class MeasurementRunnerTests(_capital_measurement_runner_unittest.TestCase):
    def setUp(self):
        self.fixture = ledger_tests.OperationalLedgerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = self.fixture.plan
        self.db = SimpleNamespace(url="postgresql://bike_app:unused@127.0.0.1:55434/bikeops", sqlite=False,
                                  data_profile_id="capital-bikeshare", target_contract_id="capital-trip-starts-hourly-v1")
        self.snapshot = _capital_measurement_runner_Mock(unsafe=True)
        self.snapshot.row_count = 350
        self.sources = _capital_measurement_runner_Mock()
        self.audit_fn = _capital_measurement_runner_Mock(side_effect=self.audit)
        self.frame = _capital_measurement_runner_pd.DataFrame({"timestamp": _capital_measurement_runner_pd.to_datetime(["2024-01-01"]), "cnt": [1]})
        self.verified = SimpleNamespace(plan=self.plan, frame=self.frame)
        self.events = []
        self.measurement_status = "passed"
        self.stack = _capital_measurement_runner_ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(_capital_measurement_runner_patch.object(runner, "PROJECT_ROOT", self.fixture.root))
        self.stack.enter_context(_capital_measurement_runner_patch.object(benchmark, "PROJECT_ROOT", self.fixture.root))
        self.stack.enter_context(_capital_measurement_runner_patch.object(runner, "measurement_attempt", side_effect=lambda p: self.fixture.attempt()))
        self.preflight = self.stack.enter_context(_capital_measurement_runner_patch.object(runner, '_measurement_runner_verify_measurement_inputs', return_value=self.verified))
        self.stack.enter_context(_capital_measurement_runner_patch.object(runner, "hold_observation_snapshot", side_effect=self.hold))
        self.stack.enter_context(_capital_measurement_runner_patch.object(runner, "FeatureSourceExpectations", return_value=self.sources))
        self.stack.enter_context(_capital_measurement_runner_patch('bike_mlops.operational_validation._benchmark_observer_verify_prediction_audit_batch', self.audit_fn))
        self.run = self.stack.enter_context(_capital_measurement_runner_patch.object(runner, "run_capital_operational_benchmark", side_effect=self.measure))

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
        return {"verified_rows": len(ids), "request_id_sha256": _capital_measurement_runner_hashlib.sha256("\n".join(ids).encode()).hexdigest()}

    def measure(self, *, request_observer, attempt_id, **kwargs):
        self.events.append("measure")
        ids = []
        for payload in request_schedule(attempt_id, self.plan):
            response = {"data_version_sha256": "b" * 64}
            request_observer.before_request(payload)
            request_observer.after_response(payload, response)
            request_observer.request_result(payload, http_status=200, latency_ms=1.0, error=None)
            ids.append(payload["request_id"])
        identity = self.plan["identity"]
        return {"benchmark_id": attempt_id, "completed": True, "measurements": [],
                "model_identity": {"candidate_id": identity["candidate_id"], "model_name": identity["model_name"],
                    "model_version": identity["model_version"], "artifact_sha256": identity["registered_artifact_sha256"],
                    "feature_contract_version": identity["feature_contract_version"]},
                "operational_gate": {"status": "not_evaluated", "measurement_status": self.measurement_status, "observed": {
                    "request_id_sha256": _capital_measurement_runner_hashlib.sha256("\n".join(ids).encode()).hexdigest()}}}

    def execute(self):
        return runner.run_staging_measurement(self.fixture.plan_path, self.fixture.root / "selection.json",
                                              self.fixture.root / "final.json", self.db)

    def read_record(self):
        return _capital_measurement_runner_ledger.read_operational_ledger(self.fixture.ledger_path)["records"][0]

    def test_complete_measurement_audit_and_report_stay_unevaluated(self):
        result = self.execute()
        self.assertEqual(self.events, ["lock", "measure", "audit", "release"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["operational_gate"], "not_evaluated")
        self.assertEqual(self.read_record()["status"], "completed")
        report = json.loads(_capital_measurement_runner_Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(report["operational_gate"]["status"], "not_evaluated")
        self.assertEqual(report["audit"]["verified_rows"], 1050)
        self.assertEqual(report["snapshot"]["observation_count"], 350)
        self.assertEqual(self.read_record()["attempts"][0]["report"]["gate_status"], "not_evaluated")

    def test_numeric_threshold_failure_still_completes_and_audits_all_requests(self):
        self.measurement_status = "failed"
        result = self.execute()
        self.assertEqual(self.events, ["lock", "measure", "audit", "release"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["measurement_status"], "failed")
        self.assertEqual(result["operational_gate"], "not_evaluated")
        self.assertEqual(result["deployment_permission"], "blocked")
        self.assertEqual(self.read_record()["status"], "completed")
        report = json.loads(_capital_measurement_runner_Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(report["operational_gate"]["measurement_status"], "failed")
        self.assertEqual(report["audit"]["status"], "passed")
        self.assertEqual(report["audit"]["verified_rows"], 1050)

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
        with (_capital_measurement_runner_patch.object(benchmark, "DATA_PROFILE_ID", "capital-bikeshare"),
              _capital_measurement_runner_patch.object(benchmark, "TARGET_CONTRACT_ID", "capital-trip-starts-hourly-v1"),
              _capital_measurement_runner_patch.object(benchmark, "DATA_PROFILE", profile)):
            result = self.execute()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(calls), 1050)
        report = json.loads(_capital_measurement_runner_Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(report["measurements"]), 1050)
        self.assertEqual(report["operational_gate"]["status"], "not_evaluated")
        self.assertEqual(report["audit"]["verified_rows"], 1050)

    def test_preflight_failure_records_failed_report_without_request(self):
        self.preflight.side_effect = ValueError("hidden-secret")
        result = self.execute()
        self.run.assert_not_called()
        self.assertEqual(result["failure_stage"], "preflight")
        self.assertEqual(self.read_record()["status"], "failed")
        report = json.loads(_capital_measurement_runner_Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertFalse(report["completed"])
        self.assertNotIn("hidden-secret", json.dumps(report))
        self.assertIsNone(self.read_record()["attempts"][0]["requests_started_at_utc"])

    def test_audit_mismatch_keeps_attempt_failed(self):
        self.audit_fn.side_effect = ValueError("mismatch")
        result = self.execute()
        self.assertEqual(result["failure_stage"], "audit")
        self.assertEqual(self.read_record()["attempts"][0]["failure_code"], "audit_mismatch")
        report = json.loads(_capital_measurement_runner_Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertFalse(report["completed"])
        self.assertTrue(report["request_sequence_completed"])
        self.assertEqual(report["operational_gate"]["status"], "failed")
        self.assertEqual(report["audit"]["status"], "not_evaluated")
        self.assertEqual(report["partial_evidence"]["verified_response_count"], 1050)
        self.assertIsNone(report["partial_evidence"]["pending_request_id"])

    def test_snapshot_release_failure_does_not_commit_success(self):
        @contextmanager
        def failed_exit(*_):
            yield self.snapshot
            raise RuntimeError("connection lost")
        with _capital_measurement_runner_patch.object(runner, "hold_observation_snapshot", side_effect=failed_exit):
            result = self.execute()
        self.assertEqual(self.read_record()["status"], "failed")
        self.assertEqual(result["failure_stage"], "snapshot_release")
        self.assertTrue((self.fixture.report_dir / ("operational-" + result["attempt_id"] + ".json")).exists())
        self.assertTrue(result["report_path"].endswith("-failure.json"))

    def test_interrupt_preserves_pending_request_and_failed_attempt(self):
        def stop(*, request_observer, attempt_id, **_):
            request_observer.before_request(request_schedule(attempt_id, self.plan)[0])
            raise KeyboardInterrupt
        self.run.side_effect = stop
        with self.assertRaises(KeyboardInterrupt):
            self.execute()
        record = self.read_record()
        self.assertEqual(record["status"], "failed")
        report_path = self.fixture.root / record["attempts"][0]["report"]["path"]
        report = json.loads(report_path.read_text())
        self.assertTrue(report["pending_request_id"].endswith(":warmup:0000"))
        self.assertEqual(report["partial_evidence"]["pending_request_status"], "outcome_unknown_may_have_been_sent")
        self.assertEqual(report["operational_gate"]["status"], "failed")

    def test_returned_incomplete_report_includes_verified_and_pending_ids(self):
        def partial(*, request_observer, attempt_id, **_):
            first, second = request_schedule(attempt_id, self.plan)[:2]
            request_observer.before_request(first)
            request_observer.after_response(first, {"data_version_sha256": "b" * 64})
            request_observer.request_result(first, http_status=200, latency_ms=1.0, error=None)
            request_observer.before_request(second)
            return {"completed": False, "measurements": [], "operational_gate": {"measurement_status": "failed"}}
        self.run.side_effect = partial
        result = self.execute()
        report = json.loads(_capital_measurement_runner_Path(result["report_path"]).read_text())
        evidence = report["partial_evidence"]
        self.assertEqual(evidence["verified_response_count"], 1)
        self.assertTrue(evidence["verified_response_ids"][0].endswith(":warmup:0000"))
        self.assertTrue(evidence["pending_request_id"].endswith(":warmup:0001"))
        self.assertEqual(evidence["pending_request_status"], "outcome_unknown_may_have_been_sent")

    def test_foreign_database_rejected_before_attempt(self):
        self.db.url = "postgresql://bike_app:unused@127.0.0.1:55432/bikeops"
        with self.assertRaisesRegex(ValueError, "isolated local"):
            self.execute()
        self.assertFalse(self.fixture.ledger_path.exists())
        self.preflight.assert_not_called()


# ========================================================================
# test_capital_measurement_recheck
# ========================================================================

import copy as _capital_measurement_recheck_copy
import hashlib as _capital_measurement_recheck_hashlib
import unittest as _capital_measurement_recheck_unittest
import json as _capital_measurement_recheck_json
from unittest.mock import patch as _capital_measurement_recheck_patch

import pandas as _capital_measurement_recheck_pd

from bike_mlops.operational_validation import recheck_linked_measurement, recheck_measurement_records
from bike_mlops import operational as _capital_measurement_recheck_benchmark
from bike_mlops import operational_validation as recovery
from bike_mlops import operational as _capital_measurement_recheck_ledger
import test_operational as _capital_measurement_recheck_ledger_tests
from bike_mlops.operational import RequestJournal


class MeasurementRecheckTests(_capital_measurement_recheck_unittest.TestCase):
    def setUp(self):
        self.fixture = _capital_measurement_recheck_ledger_tests.OperationalLedgerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = self.fixture.plan
        self.attempt_id = "3ae0909d-7ae1-4a09-a756-96a7ed582edf"
        self.file_sha = "9" * 64
        identity = self.plan["identity"]
        start = _capital_measurement_recheck_pd.Timestamp(self.plan["input_window"]["start_inclusive"])
        rows = []
        for pos in range(1050):
            phase = "warmup" if pos < 50 else "measured"
            index = pos if pos < 50 else pos - 50
            rows.append({"phase": phase, "index": index, "request_id": f"capital-op:{self.attempt_id}:{phase}:{index:04d}",
                         "target_hour": (start + _capital_measurement_recheck_pd.Timedelta(hours=index % 168)).isoformat(sep=" "),
                         "http_status": 200, "latency_ms": float(index + 1) / 10,
                         "prediction": 2.5, "data_version_sha256": "f" * 64, "error": None})
        digest = _capital_measurement_recheck_hashlib.sha256("\n".join(row["request_id"] for row in rows).encode()).hexdigest()
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
                value = _capital_measurement_recheck_copy.deepcopy(self.report)
                if change == "id": value["measurements"][10]["request_id"] = value["measurements"][11]["request_id"]
                if change == "target": value["measurements"][50]["target_hour"] = "2024-01-01 00:00:00"
                if change == "prediction": value["measurements"][0]["prediction"] = float("nan")
                if change == "count": value["operational_gate"]["observed"]["unique_request_ids"] = True
                if change == "p95": value["operational_gate"]["observed"]["p95_ms"] = 0
                if change == "artifact": value["model_identity"]["artifact_sha256"] = "0" * 64
                if change == "filehash": value["operational_plan_file_sha256"] = "0" * 64
                with self.assertRaises(ValueError): self.check(value)

    def test_over_threshold_is_recomputed_without_approval(self):
        value = _capital_measurement_recheck_copy.deepcopy(self.report)
        for row in value["measurements"][50:]: row["latency_ms"] = 251.0
        value["operational_gate"]["observed"]["p95_ms"] = 251.0
        value["operational_gate"]["measurement_status"] = "failed"
        self.assertEqual(self.check(value)["numeric_status"], "failed")
        value["operational_gate"]["measurement_status"] = "passed"
        with self.assertRaisesRegex(ValueError, "numerical status"):
            self.check(value)

    def test_refuses_incomplete_or_preapproved_report(self):
        for key, value in (("completed", False), ("operational_gate", {"status": "passed", "observed": {}})):
            changed = _capital_measurement_recheck_copy.deepcopy(self.report)
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
        with (_capital_measurement_recheck_patch.object(_capital_measurement_recheck_benchmark, "DATA_PROFILE_ID", identity["data_profile_id"]),
              _capital_measurement_recheck_patch.object(_capital_measurement_recheck_benchmark, "TARGET_CONTRACT_ID", identity["target_contract_id"]),
              _capital_measurement_recheck_patch.object(_capital_measurement_recheck_benchmark, "DATA_PROFILE", profile)):
            report = _capital_measurement_recheck_benchmark.run_capital_operational_benchmark(
                api_url=self.plan["api_url"], model_version=identity["model_version"], candidate_id=identity["candidate_id"],
                start=self.plan["input_window"]["start_inclusive"], end=self.plan["input_window"]["end_exclusive"],
                attempt_id=self.attempt_id, request_fn=request, resource_fn=lambda: {})
        report.update({"operational_attempt_id": self.attempt_id, "operational_plan_sha256": self.plan["plan_sha256"],
                       "operational_plan_file_sha256": self.file_sha})
        self.assertEqual(self.check(report)["numeric_status"], "passed")

    def test_ledger_link_and_hash_are_checked_without_rewriting_evidence(self):
        with self.fixture.attempt() as lease:
            lease.mark_requests_started()
            report = _capital_measurement_recheck_copy.deepcopy(self.report)
            report.update({"operational_attempt_id": lease.attempt_id, "benchmark_id": lease.attempt_id,
                           "operational_plan_file_sha256": lease.plan_file_sha256})
            for row in report["measurements"]:
                row["request_id"] = row["request_id"].replace(self.attempt_id, lease.attempt_id)
            report["operational_gate"]["observed"]["request_id_sha256"] = _capital_measurement_recheck_hashlib.sha256(
                "\n".join(row["request_id"] for row in report["measurements"]).encode()).hexdigest()
            journal = RequestJournal(self.fixture.report_dir / f"operational-{lease.attempt_id}.requests.jsonl", lease, self.plan)
            try:
                for payload, row in zip(journal.schedule, report["measurements"], strict=True):
                    journal.sending(payload)
                    journal.outcome(payload, http_status=200, latency_ms=row["latency_ms"], error=None)
                report["request_journal"] = journal.descriptor()
            finally:
                journal.close()
            path = self.fixture.report_dir / f"operational-{lease.attempt_id}.json"
            path.write_text(_capital_measurement_recheck_json.dumps(report))
            lease.finish(status="completed", report_path=path)
        before = (path.read_bytes(), self.fixture.ledger_path.read_bytes())
        args = dict(candidate_id=self.fixture.candidate, attempt_id=lease.attempt_id,
                    ledger_path=self.fixture.ledger_path, lock_dir=self.fixture.lock_dir)
        with _capital_measurement_recheck_patch.object(recovery, "PROJECT_ROOT", self.fixture.root):
            result = recheck_linked_measurement(**args)
            self.assertEqual(result["ledger_binding"], "matched")
            self.assertEqual(result["numeric_status"], "passed")
            self.assertEqual(before, (path.read_bytes(), self.fixture.ledger_path.read_bytes()))
            with _capital_measurement_recheck_ledger._exclusive_lock(self.fixture.lock_dir / f"{self.fixture.candidate}.lock"):
                with self.assertRaisesRegex(RuntimeError, "locked"):
                    recheck_linked_measurement(**args)
            path.write_text(path.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "different SHA-256"):
                recheck_linked_measurement(**args)


# ========================================================================
# test_capital_operational_registry
# ========================================================================

import unittest as _capital_operational_registry_unittest
from types import SimpleNamespace as _capital_operational_registry_SimpleNamespace

from bike_mlops import operational_validation as registry
from bike_mlops.operational_validation import _validate_completed_evidence, _write_operational_evidence_tags


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


class CapitalOperationalRegistryEvidenceTests(_capital_operational_registry_unittest.TestCase):
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
                return _capital_operational_registry_SimpleNamespace(name=registry.MODEL_NAME, version=version, status="READY",
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
                return _capital_operational_registry_SimpleNamespace(name=registry.MODEL_NAME, version=version, status="READY",
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
                return _capital_operational_registry_SimpleNamespace(name=registry.MODEL_NAME, version=version, status="READY",
                                       tags={"operational_gate_status": "passed",
                                             "deployment_permission": "eligible"})

            def set_model_version_tag(self, *args):
                raise AssertionError("must not write")

        with self.assertRaisesRegex(ValueError, "only be attached while deployment remains blocked"):
            _write_operational_evidence_tags(Client(), "1", {"capital_operational_report_sha256": "a" * 64})


# ========================================================================
# test_capital_operational_recovery
# ========================================================================

import json as _capital_operational_recovery_json
import unittest as _capital_operational_recovery_unittest
from pathlib import Path as _capital_operational_recovery_Path
from unittest.mock import patch as _capital_operational_recovery_patch

from bike_mlops import operational as _capital_operational_recovery_ledger
from bike_mlops import operational_validation as _capital_operational_recovery_recovery
import test_operational as _capital_operational_recovery_ledger_tests
from bike_mlops.operational import RequestJournal as _capital_operational_recovery_RequestJournal


class OperationalRecoveryInspectionTests(_capital_operational_recovery_unittest.TestCase):
    def setUp(self):
        self.fixture = _capital_operational_recovery_ledger_tests.OperationalLedgerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root_patch = _capital_operational_recovery_patch.object(_capital_operational_recovery_recovery, "PROJECT_ROOT", self.fixture.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def inspect(self):
        return _capital_operational_recovery_recovery.inspect_operational_reports(self.fixture.candidate, ledger_path=self.fixture.ledger_path,
                                                    lock_dir=self.fixture.lock_dir)

    def make_report(self, lease, *, failure=False):
        original = self.fixture.report(lease, passed=not failure)
        body = _capital_operational_recovery_json.loads(original.read_text())
        if not failure:
            body["operational_gate"]["status"] = "not_evaluated"
        if not failure:
            journal = _capital_operational_recovery_RequestJournal(self.fixture.report_dir / f"operational-{lease.attempt_id}.requests.jsonl", lease, self.fixture.plan)
            try:
                for payload in journal.schedule:
                    journal.sending(payload)
                    journal.outcome(payload, http_status=200, latency_ms=1.0, error=None)
                body["request_journal"] = journal.descriptor()
            finally:
                journal.close()
        destination = self.fixture.report_dir / f"operational-{lease.attempt_id}{'-failure' if failure else ''}.json"
        destination.write_text(_capital_operational_recovery_json.dumps(body))
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
        body = _capital_operational_recovery_json.loads(report.read_text())
        body["completed"] = False
        body["operational_gate"]["status"] = "failed"
        orphan.write_text(_capital_operational_recovery_json.dumps(body))
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
        body = _capital_operational_recovery_json.loads(report.read_text())
        body["model_identity"]["candidate_id"] = "0" * 64
        report.write_text(_capital_operational_recovery_json.dumps(body))
        # Simulate a damaged ledger with a matching file hash; identity is still checked.
        import hashlib
        state = _capital_operational_recovery_json.loads(self.fixture.ledger_path.read_text())
        state["records"][0]["attempts"][0]["report"]["sha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
        self.fixture.ledger_path.write_text(_capital_operational_recovery_json.dumps(state))
        item = self.inspect()["attempts"][0]
        self.assertEqual(item["linked_report"]["reason"], "candidate_mismatch")
        self.assertTrue(item["unresolved"])

    def test_top_level_candidate_and_profile_claims_must_match(self):
        import hashlib
        _, report = self.completed()
        original = _capital_operational_recovery_json.loads(report.read_text())
        state = _capital_operational_recovery_json.loads(self.fixture.ledger_path.read_text())
        for changes, reason in (({"candidate_id": "0" * 64}, "candidate_mismatch"),
                                ({"data_profile_id": "uci-bike-sharing"}, "profile_or_target_mismatch")):
            body = {**original, **changes}
            report.write_text(_capital_operational_recovery_json.dumps(body))
            state["records"][0]["attempts"][0]["report"]["sha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
            self.fixture.ledger_path.write_text(_capital_operational_recovery_json.dumps(state))
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
        self.assertEqual(_capital_operational_recovery_ledger.read_operational_ledger(self.fixture.ledger_path)["records"][0]["status"], "interrupted")

    def test_live_ledger_lock_refuses_inspection(self):
        self.completed()
        lock = self.fixture.ledger_path.with_suffix(self.fixture.ledger_path.suffix + ".lock")
        with _capital_operational_recovery_ledger._exclusive_lock(lock):
            with self.assertRaisesRegex(RuntimeError, "locked"):
                self.inspect()

    def test_failed_preflight_report_can_identify_candidate_via_plan_and_ledger(self):
        with self.fixture.attempt() as lease:
            report = self.make_report(lease, failure=True)
            body = _capital_operational_recovery_json.loads(report.read_text())
            body.pop("model_identity")
            report.write_text(_capital_operational_recovery_json.dumps(body))
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


# ========================================================================
# test_capital_pending_audit
# ========================================================================

import json as _capital_pending_audit_json
import unittest as _capital_pending_audit_unittest
from contextlib import nullcontext
from unittest.mock import Mock as _capital_pending_audit_Mock, patch as _capital_pending_audit_patch

from bike_mlops import operational as _capital_pending_audit_ledger
from bike_mlops import operational as _capital_pending_audit_plans
from bike_mlops import operational_validation as _capital_pending_audit_recovery
from bike_mlops.operational_runner import inspect_pending_audit
import test_operational as _capital_pending_audit_ledger_tests


class PendingAuditTests(_capital_pending_audit_unittest.TestCase):
    def setUp(self):
        self.fixture = _capital_pending_audit_ledger_tests.OperationalLedgerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.patch = _capital_pending_audit_patch.object(_capital_pending_audit_recovery, "PROJECT_ROOT", self.fixture.root)
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
            path.write_text(_capital_pending_audit_json.dumps(report))
            lease.finish(status="failed", report_path=path, failure_code="request_failed")
        # Emulate a historical plan and linked report without per-request journal.
        self.fixture.plan["policy"].pop("request_journal")
        self.fixture.plan["plan_sha256"] = _capital_pending_audit_plans._digest(self.fixture.plan)
        self.fixture.plan_path.write_text(_capital_pending_audit_json.dumps(self.fixture.plan))
        state = _capital_pending_audit_json.loads(self.fixture.ledger_path.read_text())
        import hashlib
        attempt = state["records"][0]["attempts"][0]
        attempt["plan_sha256"] = self.fixture.plan["plan_sha256"]
        attempt["plan_file_sha256"] = hashlib.sha256(self.fixture.plan_path.read_bytes()).hexdigest()
        report["operational_plan_sha256"] = attempt["plan_sha256"]
        report["operational_plan_file_sha256"] = attempt["plan_file_sha256"]
        path.write_text(_capital_pending_audit_json.dumps(report))
        attempt["report"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.fixture.ledger_path.write_text(_capital_pending_audit_json.dumps(state))
        self.path = path
        self.db = _capital_pending_audit_Mock()
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
        with _capital_pending_audit_ledger._exclusive_lock(self.fixture.lock_dir / f"{self.fixture.candidate}.lock"):
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


# ========================================================================
# test_capital_measurement_evidence
# ========================================================================

import hashlib as _capital_measurement_evidence_hashlib
import io as _capital_measurement_evidence_io
import os
import shutil
import uuid
from contextlib import ExitStack as _capital_measurement_evidence_ExitStack, redirect_stdout as _capital_measurement_evidence_redirect_stdout, redirect_stderr
from unittest.mock import patch as _capital_measurement_evidence_patch
import json as _capital_measurement_evidence_json
import subprocess
import sys as _capital_measurement_evidence_sys
import tempfile as _capital_measurement_evidence_tempfile
import unittest as _capital_measurement_evidence_unittest
import zipfile
from pathlib import Path as _capital_measurement_evidence_Path

import test_measurement as recheck_tests
from bike_mlops.operational_validation import MEMBERS, _workspace_marker, export_synthetic_evidence, verify_archive_process
from bike_mlops.operational import JOURNAL_POLICY, _hash, request_schedule as _capital_measurement_evidence_request_schedule


class CapitalMeasurementEvidenceTests(_capital_measurement_evidence_unittest.TestCase):
    def setUp(self):
        self.fixture = recheck_tests.MeasurementRecheckTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = self.fixture.plan
        self.report = self.fixture.report
        self.candidate_id = self.plan["identity"]["candidate_id"]
        self.attempt_id = self.fixture.attempt_id
        self.plan_raw = _capital_measurement_evidence_json.dumps(self.plan, ensure_ascii=False, allow_nan=False).encode()
        self.plan_sha = _capital_measurement_evidence_hashlib.sha256(self.plan_raw).hexdigest()
        self.report["operational_plan_file_sha256"] = self.plan_sha
        rows = _capital_measurement_evidence_request_schedule(self.attempt_id, self.plan)
        events = []

        def append(value):
            body = {**value, "sequence": len(events), "previous_sha256": events[-1]["event_sha256"] if events else None,
                    "recorded_at_utc": "2026-11-02T00:00:00+00:00"}
            events.append({**body, "event_sha256": _hash(body)})

        append({"event": "schedule", "schema_version": 1, "attempt_id": self.attempt_id,
                "candidate_id": self.candidate_id, "plan_sha256": self.plan["plan_sha256"],
                "plan_file_sha256": self.plan_sha, "policy": JOURNAL_POLICY, "requests": rows})
        for row, report_row in zip(rows, self.report["measurements"], strict=True):
            append({"event": "sending", "request_id": row["request_id"]})
            append({"event": "response_verified", "request_id": row["request_id"], "http_status": 200,
                    "latency_ms": report_row["latency_ms"], "error_type": None})
        self.journal_raw = b"".join((_capital_measurement_evidence_json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
                                    for event in events)
        self.report["request_journal"] = {"file_name": f"operational-{self.attempt_id}.requests.jsonl",
                                           "sha256": _capital_measurement_evidence_hashlib.sha256(self.journal_raw).hexdigest(),
                                           "events": len(events), "write_and_fsync_ms": 0,
                                           "policy_id": JOURNAL_POLICY["policy_id"]}
        self.report_raw = _capital_measurement_evidence_json.dumps(self.report, ensure_ascii=False, allow_nan=False).encode()
        self.ledger_raw = _capital_measurement_evidence_json.dumps({"schema_version": 1, "records": [{
            "candidate_id": self.candidate_id, "status": "completed", "reviews": [],
            "attempts": [{"attempt_id": self.attempt_id, "status": "completed",
                "plan_path": "data/evaluations/capital-bikeshare/operational-plans/synthetic-system-plan.json",
                "plan_sha256": self.plan["plan_sha256"], "plan_file_sha256": self.plan_sha,
                "report": {"path": f"data/reports/capital-bikeshare/operational-{self.attempt_id}.json",
                           "sha256": _capital_measurement_evidence_hashlib.sha256(self.report_raw).hexdigest(), "gate_status": "not_evaluated"}}]}]},
            sort_keys=True, ensure_ascii=False).encode()
        self.payloads = {
            "payload/plan.json": self.plan_raw,
            "payload/report.json": self.report_raw,
            "payload/operational-ledger.json": self.ledger_raw,
            "payload/request-journal.jsonl": self.journal_raw,
        }
        self.paths = {
            "payload/plan.json": "data/evaluations/capital-bikeshare/operational-plans/synthetic-system-plan.json",
            "payload/report.json": f"data/reports/capital-bikeshare/operational-{self.attempt_id}.json",
            "payload/operational-ledger.json": "data/evaluations/capital-bikeshare/operational-attempts.json",
            "payload/request-journal.jsonl": f"data/reports/capital-bikeshare/operational-{self.attempt_id}.requests.jsonl",
        }
        self.temp = _capital_measurement_evidence_tempfile.TemporaryDirectory(prefix="t16-test-")
        self.addCleanup(self.temp.cleanup)
        self.archive = _capital_measurement_evidence_Path(self.temp.name) / "synthetic-measurement-evidence.zip"
        self._write_archive()

    def _write_archive(self, payloads=None, *, extras=(), duplicate=None, manifest_changes=None):
        payloads = dict(self.payloads if payloads is None else payloads)
        members = {name: {"source_relative_path": self.paths[name], "sha256": _capital_measurement_evidence_hashlib.sha256(raw).hexdigest(),
                          "bytes": len(raw)} for name, raw in payloads.items()}
        manifest = {"schema_version": 1, "synthetic_only": True, "candidate_id": self.candidate_id,
                    "attempt_id": self.attempt_id, "source_revision": {"commit": "a" * 40, "dirty": True},
                    "member_map": {name: MEMBERS[name] for name in MEMBERS}, "members": members,
                    "verification_scope": "test", "limitations": ["synthetic only"]}
        if manifest_changes:
            manifest_changes(manifest)
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("manifest.json", _capital_measurement_evidence_json.dumps(manifest).encode())
            for name, raw in payloads.items():
                bundle.writestr(name, raw)
            for name in extras:
                bundle.writestr(name, b"extra")
            if duplicate:
                bundle.writestr(duplicate, payloads.get(duplicate, b"duplicate"))

    def test_archive_only_process_recalculates_1000_after_warmup_and_keeps_blocked(self):
        result = verify_archive_process(self.archive)
        self.assertEqual(result["measured_requests_recalculated"], 1000)
        self.assertEqual(result["warmup_requests_excluded"], 50)
        self.assertEqual(result["journal_verified_responses"], 1050)
        self.assertEqual(result["p95_ms"], 95.0)
        self.assertEqual(result["operational_gate"], "not_evaluated")
        self.assertEqual(result["deployment_permission"], "blocked")

    def test_separate_cli_process_receives_only_archive_argument(self):
        proc = subprocess.run([_capital_measurement_evidence_sys.executable, "-m", 'bike_mlops.operational_validation',
                              "--verify-archive", str(self.archive)], capture_output=True, text=True,
                             env={"PATH": _capital_measurement_evidence_sys.executable.rsplit("/", 1)[0], "PYTHONPATH": str(_capital_measurement_evidence_Path(__file__).resolve().parents[1] / "src")},
                             timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(_capital_measurement_evidence_json.loads(proc.stdout)["p95_ms"], 95.0)

    def test_rejects_missing_extra_duplicate_and_tampered_members(self):
        with self.assertRaises(ValueError):
            self._write_archive({k: v for k, v in self.payloads.items() if k != "payload/report.json"})
            verify_archive_process(self.archive)
        with self.assertRaises(ValueError):
            self._write_archive(extras=("payload/unexpected.txt",))
            verify_archive_process(self.archive)
        with self.assertRaises(ValueError):
            self._write_archive(duplicate="payload/plan.json")
            verify_archive_process(self.archive)
        changed = dict(self.payloads)
        changed["payload/report.json"] += b" "
        with self.assertRaises(ValueError):
            self._write_archive(changed)
            verify_archive_process(self.archive)

    def test_rejects_latency_tamper_even_when_manifest_hash_is_updated(self):
        changed = _capital_measurement_evidence_json.loads(self.report_raw)
        changed["measurements"][60]["latency_ms"] += 5
        raw = _capital_measurement_evidence_json.dumps(changed, ensure_ascii=False).encode()
        payloads = dict(self.payloads)
        payloads["payload/report.json"] = raw
        with self.assertRaises(ValueError):
            self._write_archive(payloads)
            verify_archive_process(self.archive)

    def test_rejects_torn_journal_even_when_descriptors_match_the_truncated_bytes(self):
        payloads = dict(self.payloads)
        payloads["payload/request-journal.jsonl"] = self.journal_raw[:-1]
        report = _capital_measurement_evidence_json.loads(self.report_raw)
        report["request_journal"]["sha256"] = _capital_measurement_evidence_hashlib.sha256(payloads["payload/request-journal.jsonl"]).hexdigest()
        payloads["payload/report.json"] = _capital_measurement_evidence_json.dumps(report, ensure_ascii=False).encode()
        ledger = _capital_measurement_evidence_json.loads(self.ledger_raw)
        ledger["records"][0]["attempts"][0]["report"]["sha256"] = _capital_measurement_evidence_hashlib.sha256(payloads["payload/report.json"]).hexdigest()
        payloads["payload/operational-ledger.json"] = _capital_measurement_evidence_json.dumps(ledger, sort_keys=True).encode()
        with self.assertRaises(ValueError):
            self._write_archive(payloads)
            verify_archive_process(self.archive)

    def test_rejects_sensitive_fields_absolute_paths_and_external_urls(self):
        for key, value in (("database_password", "redacted"), ("log_path", "/tmp/private.log"),
                           ("reference_url", "https://example.com/private")):
            changed = _capital_measurement_evidence_json.loads(self.plan_raw)
            changed[key] = value
            payloads = dict(self.payloads)
            payloads["payload/plan.json"] = _capital_measurement_evidence_json.dumps(changed).encode()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self._write_archive(payloads)
                verify_archive_process(self.archive)

    def test_rejects_different_attempt_and_path_traversal_member(self):
        with self.assertRaises(ValueError):
            self._write_archive(manifest_changes=lambda manifest: manifest.update(attempt_id="different"))
            verify_archive_process(self.archive)
        with self.assertRaises(ValueError):
            self._write_archive(manifest_changes=lambda manifest: manifest.update(harness_token="private"))
            verify_archive_process(self.archive)
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("manifest.json", b"{}")
            bundle.writestr("../outside.json", b"no")
        with self.assertRaises(ValueError):
            verify_archive_process(self.archive)

    def test_rejects_symlink_member(self):
        payloads = dict(self.payloads)
        manifest = {"schema_version": 1, "synthetic_only": True, "candidate_id": self.candidate_id,
                    "attempt_id": self.attempt_id, "source_revision": {"commit": "a" * 40, "dirty": True},
                    "member_map": {name: MEMBERS[name] for name in MEMBERS},
                    "members": {name: {"source_relative_path": self.paths[name],
                        "sha256": _capital_measurement_evidence_hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for name, raw in payloads.items()},
                    "verification_scope": "test", "limitations": ["synthetic only"]}
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("manifest.json", _capital_measurement_evidence_json.dumps(manifest).encode())
            for name, raw in payloads.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (0o120777 << 16)
                bundle.writestr(info, raw)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            verify_archive_process(self.archive)

    def test_export_rejects_external_workspace_and_cleans_partial_output(self):
        base = _capital_measurement_evidence_Path(__file__).resolve().parents[1] / "data" / "reports"
        output = base / f"t16-unit-failure-{self.attempt_id}"
        self.assertFalse(output.exists())
        with _capital_measurement_evidence_tempfile.TemporaryDirectory(prefix="t16-not-harness-") as directory:
            outside = _capital_measurement_evidence_Path(directory)
            (outside / ".t16-workspace-identity.json").write_text(
                _capital_measurement_evidence_json.dumps({"project": "p", "token": "t"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                export_synthetic_evidence(root=outside, project="p", token="t", candidate_id=self.candidate_id,
                    attempt_id=self.attempt_id, plan_relative="missing.json", report_path=outside / "missing.json",
                    evidence_dir=output, source_commit="a" * 40, source_dirty=False)
        with _capital_measurement_evidence_tempfile.TemporaryDirectory(prefix="capital-system-harness-") as directory:
            workspace = _capital_measurement_evidence_Path(directory)
            _workspace_marker(workspace, "bikemlopstest0000000000000000", "b" * 32)
            with self.assertRaises(FileNotFoundError):
                export_synthetic_evidence(root=workspace, project="bikemlopstest0000000000000000",
                    token="b" * 32, candidate_id=self.candidate_id, attempt_id=self.attempt_id,
                    plan_relative="missing.json", report_path=workspace / "missing.json", evidence_dir=output,
                    source_commit="a" * 40, source_dirty=False)
        self.assertFalse(output.exists())

    def test_existing_output_is_never_overwritten(self):
        output = _capital_measurement_evidence_Path(__file__).resolve().parents[1] / "data" / "reports" / f"t16-unit-existing-{self.attempt_id}"
        output.mkdir(parents=True, exist_ok=False)
        sentinel = output / "keep.txt"
        sentinel.write_text("existing", encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(output, ignore_errors=True))
        with _capital_measurement_evidence_tempfile.TemporaryDirectory(prefix="capital-system-harness-") as directory:
            workspace = _capital_measurement_evidence_Path(directory)
            project, token = "bikemlopstest0000000000000000", "b" * 32
            _workspace_marker(workspace, project, token)
            with self.assertRaises(FileExistsError):
                export_synthetic_evidence(root=workspace, project=project, token=token,
                    candidate_id=self.candidate_id, attempt_id=self.attempt_id, plan_relative="missing.json",
                    report_path=workspace / "missing.json", evidence_dir=output,
                    source_commit="a" * 40, source_dirty=False)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "existing")


    def _rebind_report(self, report, payloads=None):
        payloads = dict(self.payloads if payloads is None else payloads)
        payloads["payload/report.json"] = _capital_measurement_evidence_json.dumps(report).encode()
        ledger = _capital_measurement_evidence_json.loads(payloads["payload/operational-ledger.json"])
        ledger["records"][0]["attempts"][0]["report"]["sha256"] = _capital_measurement_evidence_hashlib.sha256(payloads["payload/report.json"]).hexdigest()
        payloads["payload/operational-ledger.json"] = _capital_measurement_evidence_json.dumps(ledger).encode()
        return payloads

    def _workspace(self):
        temp = _capital_measurement_evidence_tempfile.TemporaryDirectory(prefix="capital-system-harness-")
        self.addCleanup(temp.cleanup)
        root = _capital_measurement_evidence_Path(temp.name)
        for name, raw in self.payloads.items():
            path = root / self.paths[name]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        project, token = "bikemlopstest" + "a" * 16, "b" * 32
        _workspace_marker(root, project, token)
        output = _capital_measurement_evidence_Path(__file__).resolve().parents[1] / "data/reports" / ("t16-sol-test-" + uuid.uuid4().hex)
        self.addCleanup(lambda: shutil.rmtree(output, ignore_errors=True))
        return dict(root=root, project=project, token=token, candidate_id=self.candidate_id,
                    attempt_id=self.attempt_id, plan_relative=self.paths["payload/plan.json"],
                    report_path=root / self.paths["payload/report.json"], evidence_dir=output,
                    source_commit="a" * 40, source_dirty=True)

    def test_verifier_rejects_secrets_in_ledger_and_rehashed_journal(self):
        payloads = dict(self.payloads)
        ledger = _capital_measurement_evidence_json.loads(self.ledger_raw)
        ledger["database_password"] = "synthetic-secret"
        payloads["payload/operational-ledger.json"] = _capital_measurement_evidence_json.dumps(ledger).encode()
        self._write_archive(payloads)
        with self.assertRaisesRegex(ValueError, "sensitive"):
            verify_archive_process(self.archive)
        events = [_capital_measurement_evidence_json.loads(line) for line in self.journal_raw.splitlines()]
        previous = None
        for index, event in enumerate(events):
            event.pop("event_sha256")
            event["previous_sha256"] = previous
            if index == 1:
                event["access_token"] = "synthetic-secret"
            previous = event["event_sha256"] = _hash(event)
        raw = b"".join((_capital_measurement_evidence_json.dumps(event)+"\n").encode() for event in events)
        payloads = dict(self.payloads)
        payloads["payload/request-journal.jsonl"] = raw
        report = _capital_measurement_evidence_json.loads(self.report_raw)
        report["request_journal"]["sha256"] = _capital_measurement_evidence_hashlib.sha256(raw).hexdigest()
        self._write_archive(self._rebind_report(report, payloads))
        with self.assertRaisesRegex(ValueError, "sensitive"):
            verify_archive_process(self.archive)

    def test_latency_mismatch_reaches_journal_comparison_after_rebinding(self):
        report = _capital_measurement_evidence_json.loads(self.report_raw)
        report["measurements"][60]["latency_ms"] += 5
        self._write_archive(self._rebind_report(report))
        with self.assertRaisesRegex(ValueError, "journal latency"):
            verify_archive_process(self.archive)

    def test_warmup_cannot_be_counted_as_measured(self):
        report = _capital_measurement_evidence_json.loads(self.report_raw)
        report["measurements"][0]["phase"] = "measured"
        self._write_archive(self._rebind_report(report))
        with self.assertRaisesRegex(ValueError, "order or target"):
            verify_archive_process(self.archive)

    def test_journal_source_descriptor_must_match_report_filename(self):
        self._write_archive(manifest_changes=lambda m: m["members"]["payload/request-journal.jsonl"].update(
            source_relative_path="data/reports/capital-bikeshare/unrelated.requests.jsonl"))
        with self.assertRaisesRegex(ValueError, "journal.*path"):
            verify_archive_process(self.archive)

    def test_export_rejects_root_and_parent_directory_symlinks(self):
        kwargs = self._workspace()
        alias = _capital_measurement_evidence_Path(self.temp.name) / "workspace-link"
        alias.symlink_to(kwargs["root"], target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            export_synthetic_evidence(**{**kwargs, "root": alias})
        plan_dir = kwargs["root"] / "plan-link"
        plan_dir.symlink_to((kwargs["root"] / kwargs["plan_relative"]).parent, target_is_directory=True)
        relative = "plan-link/" + _capital_measurement_evidence_Path(kwargs["plan_relative"]).name
        with self.assertRaisesRegex(ValueError, "symlink"):
            export_synthetic_evidence(**{**kwargs, "plan_relative": relative})

    def test_export_subprocess_failure_timeout_and_interruption_remove_output(self):
        for error in (RuntimeError("verifier failed"), subprocess.TimeoutExpired("verifier", 60), KeyboardInterrupt()):
            kwargs = self._workspace()
            with self.subTest(error=type(error).__name__), _capital_measurement_evidence_patch(
                    'bike_mlops.operational_validation.subprocess.run', side_effect=error):
                with self.assertRaises(type(error)):
                    export_synthetic_evidence(**kwargs)
                self.assertFalse(kwargs["evidence_dir"].exists())

    def test_bounded_reader_detects_replacement_and_oversized_input(self):
        from bike_mlops.operational_validation import _read_regular
        source = _capital_measurement_evidence_Path(self.temp.name) / "file"
        source.write_bytes(b"original")
        original_fstat = os.fstat
        calls = 0
        def changed(fd):
            nonlocal calls
            calls += 1
            if calls == 2:  # _read_regular has read the bytes and now checks its open inode.
                replacement = source.with_name("replacement")
                replacement.write_bytes(b"changed-after-read")
                os.replace(replacement, source)
            return original_fstat(fd)
        with _capital_measurement_evidence_patch('bike_mlops.operational_validation._measurement_evidence_os.fstat', side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed"):
                _read_regular(source)
        with self.assertRaisesRegex(ValueError, "bounded"):
            _read_regular(source, max_bytes=1)

    def test_manifest_descriptor_cannot_hide_extra_fields_or_unsafe_paths(self):
        for update in ({"private_note": "not-allowed"}, {"source_relative_path": ""},
                       {"source_relative_path": "data/./report.json"}):
            self._write_archive(manifest_changes=lambda m: m["members"]["payload/report.json"].update(update))
            with self.subTest(update=update), self.assertRaises(ValueError):
                verify_archive_process(self.archive)


    def test_harness_export_failure_and_term_still_attempt_resource_cleanup(self):
        import test_system as harness_tests
        harness = harness_tests.harness
        for failure in (ValueError("export failed"), SystemExit(143)):
            roots = []
            with self.subTest(failure=type(failure).__name__), _capital_measurement_evidence_ExitStack() as stack:
                stack.enter_context(_capital_measurement_evidence_redirect_stdout(_capital_measurement_evidence_io.StringIO()))
                stack.enter_context(redirect_stderr(_capital_measurement_evidence_io.StringIO()))
                stack.enter_context(_capital_measurement_evidence_patch("sys.argv", ["harness", "--run-synthetic-system", "--evidence-dir", "data/reports/t16-mock"]))
                for name in ("make_override", "write_synthetic_credentials", "check_local_daemon", "check_ports_free", "wait_ready"):
                    stack.enter_context(_capital_measurement_evidence_patch.object(harness, name))
                stack.enter_context(_capital_measurement_evidence_patch.object(harness, "copy_workspace", side_effect=lambda source, root: roots.append(root)))
                stack.enter_context(_capital_measurement_evidence_patch.object(harness, "prepare_synthetic_workspace", return_value={}))
                stack.enter_context(_capital_measurement_evidence_patch.object(harness, "docker", return_value=""))
                for name in ("assert_empty", "verify_ports", "verify_profiles"):
                    stack.enter_context(_capital_measurement_evidence_patch.object(harness.Harness, name))
                cleanup = stack.enter_context(_capital_measurement_evidence_patch.object(harness.Harness, "cleanup"))
                result = {"candidate_id": self.candidate_id, "measurement_attempt": self.attempt_id,
                          "plan_relative_path": self.paths["payload/plan.json"],
                          "report_relative_path": self.paths["payload/report.json"]}
                stack.enter_context(_capital_measurement_evidence_patch.object(harness, "run_synthetic_system", return_value=result))
                stack.enter_context(_capital_measurement_evidence_patch('bike_mlops.operational_validation.export_synthetic_evidence', side_effect=failure))
                with self.assertRaises(type(failure)) as caught:
                    harness.main()
                if isinstance(failure, SystemExit):
                    self.assertEqual(caught.exception.code, 143)
                cleanup.assert_called_once_with()
                self.assertFalse(roots[0].exists())

    def test_partial_export_cleanup_failure_retains_both_errors(self):
        kwargs = self._workspace()
        original = shutil.rmtree
        def denied(path, *args, **kw):
            if _capital_measurement_evidence_Path(path) == kwargs["evidence_dir"]:
                raise PermissionError("partial-output removal denied")
            return original(path, *args, **kw)
        with _capital_measurement_evidence_patch('bike_mlops.operational_validation.subprocess.run', side_effect=RuntimeError("verifier failure")), \
             _capital_measurement_evidence_patch('bike_mlops.operational_validation.shutil.rmtree', side_effect=denied):
            with self.assertRaises(BaseExceptionGroup) as caught:
                export_synthetic_evidence(**kwargs)
        self.assertEqual([type(e) for e in caught.exception.exceptions], [RuntimeError, PermissionError])

    def test_export_raw_environment_and_inventory_path_escape_are_refused(self):
        from bike_mlops.operational_validation import _assert_no_sensitive_or_external
        for value in ({"env": {"CUSTOM": "private"}}, {"containers": "/tmp/private|x"},
                      {"environment": []}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _assert_no_sensitive_or_external(value)


    def test_export_success_is_byte_exact_and_subprocess_environment_is_reduced(self):
        kwargs = self._workspace()
        original_run = subprocess.run
        with _capital_measurement_evidence_patch('bike_mlops.operational_validation.subprocess.run', wraps=original_run) as process:
            result = export_synthetic_evidence(**kwargs)
        self.assertEqual(result["recalculation"]["p95_ms"], 95.0)
        self.assertEqual(set(process.call_args.kwargs["env"]) - {"PATH", "VIRTUAL_ENV", "MLFLOW_DISABLE_AGENT_HINT"}, set())
        with zipfile.ZipFile(kwargs["evidence_dir"] / "synthetic-measurement-evidence.zip") as archive:
            for name, raw in self.payloads.items():
                self.assertEqual(archive.read(name), raw)

    def test_harness_resource_cleanup_failure_removes_success_archive(self):
        import test_system as harness_tests
        harness = harness_tests.harness
        kwargs = self._workspace()
        output = kwargs["evidence_dir"]
        roots = []
        def exported(**arguments):
            output.mkdir()
            (output / "synthetic-measurement-evidence.zip").write_bytes(b"synthetic placeholder")
            return {"status": "synthetic"}
        with _capital_measurement_evidence_ExitStack() as stack:
            stack.enter_context(_capital_measurement_evidence_redirect_stdout(_capital_measurement_evidence_io.StringIO()))
            stack.enter_context(redirect_stderr(_capital_measurement_evidence_io.StringIO()))
            stack.enter_context(_capital_measurement_evidence_patch("sys.argv", ["harness", "--run-synthetic-system", "--evidence-dir", str(output)]))
            for name in ("make_override", "write_synthetic_credentials", "check_local_daemon", "check_ports_free", "wait_ready"):
                stack.enter_context(_capital_measurement_evidence_patch.object(harness, name))
            stack.enter_context(_capital_measurement_evidence_patch.object(harness, "copy_workspace", side_effect=lambda source, root: roots.append(root)))
            stack.enter_context(_capital_measurement_evidence_patch.object(harness, "prepare_synthetic_workspace", return_value={}))
            stack.enter_context(_capital_measurement_evidence_patch.object(harness, "docker", return_value=""))
            for name in ("assert_empty", "verify_ports", "verify_profiles"):
                stack.enter_context(_capital_measurement_evidence_patch.object(harness.Harness, name))
            stack.enter_context(_capital_measurement_evidence_patch.object(harness.Harness, "cleanup", side_effect=harness.HarnessError("resource cleanup failed")))
            stack.enter_context(_capital_measurement_evidence_patch.object(harness, "run_synthetic_system", return_value={
                "candidate_id": self.candidate_id, "measurement_attempt": self.attempt_id,
                "plan_relative_path": self.paths["payload/plan.json"], "report_relative_path": self.paths["payload/report.json"]}))
            stack.enter_context(_capital_measurement_evidence_patch('bike_mlops.operational_validation.export_synthetic_evidence', side_effect=exported))
            with self.assertRaisesRegex(harness.HarnessError, "resource cleanup failed"):
                harness.main()
        self.addCleanup(lambda: shutil.rmtree(roots[0], ignore_errors=True))
        self.assertTrue(roots[0].exists())
        self.assertFalse(output.exists())


    def test_hardware_at_sign_is_not_uri_authority_but_credentials_stay_rejected(self):
        from bike_mlops.operational_validation import _assert_no_sensitive_or_external
        for cpu in ('Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz',
                    'Intel(R) Xeon(R) CPU E5-2673 v4 @2.30GHz'):
            with self.subTest(cpu=cpu):
                _assert_no_sensitive_or_external({'resource_context':{'host':{'cpu_model':cpu}}})
        for value in ('postgresql://user:private@host/db', 'http://user:private@127.0.0.1/x',
                      'http://@localhost/x', 'user:private@host/db', 'person@example.invalid',
                      'notes postgresql://user:private@host/db', '/private/data', 'https://example.invalid/data'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _assert_no_sensitive_or_external({'value':value})

    def test_cpu_at_sign_roundtrip_recomputes_archive_and_does_not_exempt_credentials(self):
        report = _capital_measurement_evidence_json.loads(self.report_raw)
        report['resource_context'] = {'host': {'cpu_model': 'Intel(R) Xeon(R) Platinum CPU @ 2.80GHz'}}
        self._write_archive(self._rebind_report(report))
        result = verify_archive_process(self.archive)
        self.assertEqual(result['measured_requests_recalculated'], 1000)
        self.assertEqual(result['p95_ms'], 95.0)
        self.assertEqual(result['deployment_permission'], 'blocked')
        report['resource_context']['host']['cpu_model'] = 'http://user:private@127.0.0.1/x'
        self._write_archive(self._rebind_report(report))
        with self.assertRaises(ValueError):
            verify_archive_process(self.archive)


if __name__ == "__main__":
    import unittest as _unittest_entry
    _unittest_entry.main()
