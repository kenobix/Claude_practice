"""Contract and regression tests: test_data."""
from __future__ import annotations


# ========================================================================
# test_features
# ========================================================================

import unittest

import numpy as np
import pandas as pd

from bike_mlops.data import FEATURE_COLUMNS, build_feature_row, feature_frame_for_dataset


class FeatureContractTests(unittest.TestCase):
    def setUp(self):
        self.target = pd.Timestamp("2012-07-01 10:00:00")
        self.history = pd.DataFrame(
            [
                {
                    "timestamp": self.target - pd.Timedelta(hours=24),
                    "cnt": 120,
                    "available_at": self.target - pd.Timedelta(hours=21),
                },
                {
                    "timestamp": self.target - pd.Timedelta(hours=168),
                    "cnt": 95,
                    "available_at": self.target - pd.Timedelta(hours=165),
                },
                {
                    "timestamp": self.target - pd.Timedelta(hours=1),
                    "cnt": 500,
                    "available_at": self.target + pd.Timedelta(hours=2),
                },
                {
                    "timestamp": self.target,
                    "cnt": 999,
                    "available_at": self.target + pd.Timedelta(hours=3),
                },
            ]
        )

    def test_only_exact_lags_available_at_prediction_time_are_used(self):
        features = build_feature_row(self.target, self.target, self.history)
        self.assertEqual(features["lag_24"], 120.0)
        self.assertEqual(features["lag_168"], 95.0)
        self.assertEqual(features["lag_24_missing"], 0.0)
        self.assertEqual(features["hour"], 10.0)
        self.assertEqual(set(features), set(FEATURE_COLUMNS))
        self.assertNotIn(999.0, features.values())
        self.assertNotIn(500.0, features.values())

    def test_missing_exact_hour_is_not_replaced_by_nearest_row(self):
        history = self.history.loc[
            self.history["timestamp"] != self.target - pd.Timedelta(hours=24)
        ]
        # A neighboring row exists, but the exact previous-day timestamp does not.
        history = pd.concat(
            [
                history,
                pd.DataFrame(
                    [{
                        "timestamp": self.target - pd.Timedelta(hours=25),
                        "cnt": 777,
                        "available_at": self.target - pd.Timedelta(hours=22),
                    }]
                ),
            ],
            ignore_index=True,
        )
        features = build_feature_row(self.target, self.target, history)
        self.assertTrue(np.isnan(features["lag_24"]))
        self.assertEqual(features["lag_24_missing"], 1.0)

    def test_contract_rejects_non_hour_and_different_as_of(self):
        with self.assertRaisesRegex(ValueError, "as_of"):
            build_feature_row(self.target, self.target + pd.Timedelta(minutes=1), self.history)
        with self.assertRaisesRegex(ValueError, "aligned"):
            build_feature_row(self.target + pd.Timedelta(minutes=1), self.target, self.history)

    def test_batch_features_join_by_timestamp_and_preserve_gaps(self):
        start = pd.Timestamp("2012-01-01 00:00:00")
        timestamps = pd.date_range(start, periods=180, freq="h").delete(100)
        frame = pd.DataFrame({"timestamp": timestamps, "cnt": np.arange(len(timestamps))})
        features = feature_frame_for_dataset(frame)
        target_index = frame.index[frame["timestamp"] == start + pd.Timedelta(hours=124)][0]
        target_time = frame.loc[target_index, "timestamp"]
        self.assertTrue(np.isnan(features.loc[target_index, "lag_24"]))
        self.assertEqual(features.loc[target_index, "lag_24_missing"], 1.0)
        # A 168-hour source may still exist independently of the 24-hour gap.
        if target_time - pd.Timedelta(hours=168) in set(frame["timestamp"]):
            self.assertEqual(features.loc[target_index, "lag_168_missing"], 0.0)


# ========================================================================
# test_data_profiles
# ========================================================================

import json
import os
import subprocess
import sys
import tempfile
import unittest as _data_profiles_unittest
from pathlib import Path
from unittest.mock import patch

from bike_mlops.config import _profiles_load_data_profile as load_data_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DataProfileTests(_data_profiles_unittest.TestCase):
    def test_uci_and_capital_profiles_declare_distinct_targets_and_runtime(self):
        uci = load_data_profile("uci-bike-sharing")
        capital = load_data_profile("capital-bikeshare")
        self.assertNotEqual(uci["target_contract_id"], capital["target_contract_id"])
        self.assertNotEqual(uci["model_name"], capital["model_name"])
        self.assertNotEqual(uci["runtime_ports"], capital["runtime_ports"])
        self.assertIsNone(capital["monitor_reference"])
        self.assertEqual(capital["status"], "migration-only")
        self.assertTrue(capital["default_csv"].endswith("capitalbikeshare-next/hourly.csv"))

    def test_capital_process_uses_separate_local_paths(self):
        environment = os.environ.copy()
        environment.update(
            {
                "DATA_PROFILE_ID": "capital-bikeshare",
                "PYTHONPATH": str(PROJECT_ROOT / "src"),
            }
        )
        environment.pop("DATABASE_URL", None)
        environment.pop("MLFLOW_TRACKING_URI", None)
        code = (
            "import json; from bike_mlops.config import DATA_PROFILE_ID, DEFAULT_DATASET, "
            "DEFAULT_DATABASE_URL, DEFAULT_MLFLOW_URI, REPORT_DIR, RUNTIME_DIR; "
            "print(json.dumps([DATA_PROFILE_ID, str(DEFAULT_DATASET), DEFAULT_DATABASE_URL, "
            "DEFAULT_MLFLOW_URI, str(REPORT_DIR), str(RUNTIME_DIR)]))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        values = json.loads(completed.stdout)
        self.assertEqual(values[0], "capital-bikeshare")
        self.assertIn("capitalbikeshare-next/hourly.csv", values[1])
        self.assertIn("bikeops-capital-bikeshare.db", values[2])
        self.assertIn("mlflow-capital-bikeshare.db", values[3])
        self.assertTrue(values[4].endswith("reports/capital-bikeshare"))
        self.assertTrue(values[5].endswith("runtime-capital-bikeshare"))

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown data profile"):
            load_data_profile("not-a-real-profile")

    def test_malformed_label_availability_is_rejected_as_a_profile_error(self):
        profile = load_data_profile("uci-bike-sharing")
        profile["profile_id"] = "test-profile"
        profile["label_availability"] = []
        with tempfile.TemporaryDirectory() as temporary_directory:
            profile_dir = Path(temporary_directory)
            (profile_dir / "test-profile.json").write_text(json.dumps(profile), encoding="utf-8")
            with patch('bike_mlops.config.PROFILE_DIR', profile_dir):
                with self.assertRaisesRegex(ValueError, "label_availability as an object"):
                    load_data_profile("test-profile")


# ========================================================================
# test_timezones
# ========================================================================

import hashlib
import unittest as _timezones_unittest
from importlib.resources import files
from io import BytesIO
from zoneinfo import ZoneInfo

import pandas as _timezones_pd
import tzdata

from bike_mlops.config import load_pinned_timezone, timezone_data_identity


class PinnedTimezoneTests(_timezones_unittest.TestCase):
    def test_dst_grid_uses_the_locked_package_and_preserves_transition_rules(self):
        zone_name = "America/New_York"
        zone = load_pinned_timezone(zone_name)

        spring = _timezones_pd.date_range(
            "2024-03-10 00:00", "2024-03-10 05:00", freq="h", inclusive="left", tz=zone
        ).tz_localize(None)
        self.assertEqual(
            spring.tolist(),
            [_timezones_pd.Timestamp("2024-03-10 00:00"), _timezones_pd.Timestamp("2024-03-10 01:00"),
             _timezones_pd.Timestamp("2024-03-10 03:00"), _timezones_pd.Timestamp("2024-03-10 04:00")],
        )

        fall_aware = _timezones_pd.date_range(
            "2024-11-03 00:00", "2024-11-03 04:00", freq="h", inclusive="left", tz=zone
        )
        fall_local = fall_aware.tz_localize(None)
        self.assertEqual(fall_local.duplicated(keep=False).sum(), 2)
        self.assertEqual(fall_aware[1].utcoffset(), _timezones_pd.Timedelta(hours=-4))
        self.assertEqual(fall_aware[2].utcoffset(), _timezones_pd.Timedelta(hours=-5))

    def test_timezone_identity_hashes_the_package_resource(self):
        name = "America/New_York"
        resource = files("tzdata.zoneinfo").joinpath(*name.split("/"))
        with resource.open("rb") as stream:
            raw = stream.read()
        identity = timezone_data_identity(name)

        self.assertEqual(identity["provider"], "python-tzdata-package")
        self.assertEqual(identity["package"], "tzdata")
        self.assertEqual(identity["package_version"], tzdata.__version__)
        self.assertEqual(identity["zone_file_bytes"], len(raw))
        self.assertEqual(
            identity["zone_file_sha256"],
            hashlib.sha256(raw).hexdigest(),
        )
        identity["zone_file_sha256"] = "0" * 64
        self.assertEqual(timezone_data_identity(name)["zone_file_sha256"], hashlib.sha256(raw).hexdigest())
        with resource.open("rb") as stream:
            from_file_zone = ZoneInfo.from_file(BytesIO(stream.read()), key=name)
        self.assertEqual(load_pinned_timezone(name).utcoffset(_timezones_pd.Timestamp("2024-01-01").to_pydatetime()),
                         from_file_zone.utcoffset(_timezones_pd.Timestamp("2024-01-01").to_pydatetime()))

    def test_timezone_path_traversal_is_rejected(self):
        for key in ("../UTC", "/etc/localtime", "America\\New_York"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                load_pinned_timezone(key)


# ========================================================================
# test_storage
# ========================================================================

import tempfile as _storage_tempfile
import unittest as _storage_unittest
from pathlib import Path as _storage_Path
import sqlite3

import pandas as _storage_pd

from bike_mlops.db import Database, RequestConflict, SCHEMA


class StorageTests(_storage_unittest.TestCase):
    def setUp(self):
        self.tempdir = _storage_tempfile.TemporaryDirectory()
        self.database = Database(f"sqlite:///{_storage_Path(self.tempdir.name) / 'ops.sqlite'}")
        self.database.initialize()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_prediction_schema_declares_double_precision(self):
        prediction_schema = next(statement for statement in SCHEMA
                                 if "CREATE TABLE IF NOT EXISTS predictions" in statement)
        self.assertIn("predicted DOUBLE PRECISION", prediction_schema)
        precise_value = 15.189926280821982
        record = {
            "request_id": "precision-check",
            "input_hash": "same-input",
            "target_hour": "2012-07-01 10:00:00",
            "as_of": "2012-07-01 10:00:00",
            "predicted": precise_value,
            "model_name": "bike-demand",
            "model_version": "1",
            "model_quality_gate": "passed",
            "artifact_sha256": "model-sha",
            "data_version_sha256": "data-sha",
            "feature_contract_version": "bike-demand-features-v1",
            "features_json": "{}",
            "created_at": "2012-07-01T10:00:00+00:00",
        }
        self.assertEqual(self.database.save_prediction(record)["predicted"], precise_value)

    def test_query_excludes_late_and_future_observations(self):
        target = _storage_pd.Timestamp("2012-07-01 10:00:00")
        rows = _storage_pd.DataFrame(
            [
                (target - _storage_pd.Timedelta(hours=24), 120, target - _storage_pd.Timedelta(hours=21)),
                (target - _storage_pd.Timedelta(hours=1), 400, target + _storage_pd.Timedelta(hours=2)),
                (target, 800, target + _storage_pd.Timedelta(hours=3)),
            ],
            columns=["timestamp", "cnt", "available_at"],
        )
        self.database.add_observations(rows, "dataset-sha")
        available = self.database.observations_as_of(target, target)
        self.assertEqual(available["timestamp"].tolist(), ["2012-06-30 10:00:00"])

    def test_prediction_is_idempotent_and_rejects_changed_input(self):
        self.database.add_observation(
            "2012-06-30 10:00:00", 120, "2012-06-30 13:00:00", "dataset-sha"
        )
        record = {
            "request_id": "idempotency-key-1",
            "input_hash": "same-input",
            "target_hour": "2012-07-01 10:00:00",
            "as_of": "2012-07-01 10:00:00",
            "predicted": 123.4,
            "model_name": "bike-demand",
            "model_version": "1",
            "model_quality_gate": "passed",
            "artifact_sha256": "model-sha",
            "data_version_sha256": "data-sha",
            "feature_contract_version": "bike-demand-features-v1",
            "features_json": "{}",
            "created_at": "2012-07-01T10:00:00+00:00",
        }
        first = self.database.save_prediction(record)
        second = self.database.save_prediction(record)
        self.assertEqual(first["predicted"], second["predicted"])
        self.assertEqual(first["model_version"], second["model_version"])
        changed = {**record, "input_hash": "different-input", "predicted": 999.0}
        with self.assertRaises(RequestConflict):
            self.database.save_prediction(changed)

    def test_observation_timestamp_cannot_be_rewritten(self):
        self.database.add_observation(
            "2012-06-30 10:00:00", 120, "2012-06-30 13:00:00", "dataset-sha"
        )
        with self.assertRaises(ValueError):
            self.database.add_observation(
                "2012-06-30 10:00:00", 121, "2012-06-30 13:00:00", "dataset-sha"
            )

    def test_database_is_bound_to_its_data_profile(self):
        with self.assertRaisesRegex(ValueError, "Database profile mismatch"):
            Database(
                self.database.url,
                data_profile_id="capital-bikeshare",
                target_contract_id="capital-trip-starts-hourly-v1",
            ).initialize()

    def test_legacy_populated_database_cannot_be_claimed_by_capital_profile(self):
        path = _storage_Path(self.tempdir.name) / "legacy.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE observations (timestamp TEXT PRIMARY KEY, cnt INTEGER NOT NULL, "
                "available_at TEXT NOT NULL, data_hash TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO observations VALUES ('2012-01-01 00:00:00',1,'2012-01-01 03:00:00','old-hash')"
            )
        capital = Database(
            f"sqlite:///{path}",
            data_profile_id="capital-bikeshare",
            target_contract_id="capital-trip-starts-hourly-v1",
        )
        with self.assertRaisesRegex(ValueError, "Refusing to assign a non-UCI profile"):
            capital.initialize()

    def test_legacy_prediction_table_is_migrated_and_bound_as_uci(self):
        path = _storage_Path(self.tempdir.name) / "legacy-uci.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE observations (timestamp TEXT PRIMARY KEY, cnt INTEGER NOT NULL, "
                "available_at TEXT NOT NULL, data_hash TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE predictions (request_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,"
                "target_hour TEXT NOT NULL,as_of TEXT NOT NULL,predicted REAL NOT NULL,model_name TEXT NOT NULL,"
                "model_version TEXT NOT NULL,model_quality_gate TEXT NOT NULL,artifact_sha256 TEXT NOT NULL,"
                "data_version_sha256 TEXT NOT NULL,feature_contract_version TEXT NOT NULL,"
                "features_json TEXT NOT NULL,created_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO observations VALUES ('2012-01-01 00:00:00',1,'2012-01-01 03:00:00','old-hash')"
            )
            connection.execute(
                "INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("legacy-request", "input-hash", "2012-01-01 01:00:00", "2012-01-01 01:00:00",
                 1.0, "bike-demand", "1", "passed", "model-hash", "data-hash",
                 "bike-demand-features-v1", "{}", "2012-01-01T01:00:00+00:00"),
            )
        Database(f"sqlite:///{path}").initialize()
        with sqlite3.connect(path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(predictions)")}
            profile = connection.execute(
                "SELECT data_profile_id,target_contract_id FROM data_profile_metadata WHERE singleton=1"
            ).fetchone()
            candidate = connection.execute(
                "SELECT candidate_id FROM predictions WHERE request_id='legacy-request'"
            ).fetchone()
        self.assertIn("data_profile_id", columns)
        self.assertIn("target_contract_id", columns)
        self.assertIn("candidate_id", columns)
        self.assertEqual(candidate, (None,))
        self.assertEqual(profile, ("uci-bike-sharing", "uci-bike-sharing-cnt-dataset-local-hour"))

    def test_deployment_audit_row_is_bound_to_data_profile(self):
        capital = Database(
            f"sqlite:///{_storage_Path(self.tempdir.name) / 'capital.sqlite'}",
            data_profile_id="capital-bikeshare",
            target_contract_id="capital-trip-starts-hourly-v1",
        )
        capital.initialize()
        with capital.transaction(immediate=True) as connection:
            capital.execute(
                connection,
                "INSERT INTO deployments(deployment_id,status,phase,data_profile_id,target_contract_id,"
                "to_slot,to_version,approver,reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "deployment-capital-1", "completed", "bootstrap", "capital-bikeshare",
                    "capital-trip-starts-hourly-v1", "blue", "1", "reviewer",
                    "test fixture", "2026-10-28T00:00:00+00:00", "2026-10-28T00:00:00+00:00",
                ),
            )
            row = capital.execute(
                connection,
                "SELECT data_profile_id,target_contract_id FROM deployments WHERE deployment_id=?",
                ("deployment-capital-1",),
            ).fetchone()
        self.assertEqual(
            (row["data_profile_id"], row["target_contract_id"]),
            ("capital-bikeshare", "capital-trip-starts-hourly-v1"),
        )


# ========================================================================
# test_data_monitoring
# ========================================================================

import tempfile as _data_monitoring_tempfile
import unittest as _data_monitoring_unittest
from pathlib import Path as _data_monitoring_Path

import numpy as _data_monitoring_np
import pandas as _data_monitoring_pd

from bike_mlops.data import audit_hourly, load_hourly
from bike_mlops.monitoring import population_stability_index, score_monitor_windows


class DataAndMonitoringTests(_data_monitoring_unittest.TestCase):
    def test_loader_reports_profile_neutral_required_columns(self):
        with _data_monitoring_tempfile.TemporaryDirectory() as directory:
            path = _data_monitoring_Path(directory) / "hour.csv"
            path.write_text("timestamp,cnt\n2012-01-01 00:00:00,1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing required hourly data columns"):
                load_hourly(path)

    def test_loader_rejects_duplicate_timestamp(self):
        with _data_monitoring_tempfile.TemporaryDirectory() as directory:
            path = _data_monitoring_Path(directory) / "hour.csv"
            path.write_text(
                "dteday,hr,cnt\n2012-01-01,0,1\n2012-01-01,0,2\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_hourly(path)

    def test_audit_counts_missing_hours_without_inventing_rows(self):
        frame = _data_monitoring_pd.DataFrame(
            {
                "timestamp": _data_monitoring_pd.to_datetime(
                    ["2012-01-01 00:00:00", "2012-01-01 01:00:00", "2012-01-01 03:00:00"]
                ),
                "cnt": [1, 2, 3],
            }
        )
        report = audit_hourly(frame)
        self.assertEqual(report["rows"], 3)
        self.assertEqual(report["expected_hours_in_range"], 4)
        self.assertEqual(report["missing_hour_slots_in_range"], 1)

    def test_population_stability_detects_shift(self):
        self.assertAlmostEqual(
            population_stability_index([0, 1, 2, 3, 4, 5], [0, 1, 2, 3, 4, 5]), 0.0
        )
        self.assertGreater(
            population_stability_index([0, 1, 2, 3, 4, 5], [20, 21, 22, 23, 24, 25]), 0.2
        )

    def test_monitor_waits_for_label_availability(self):
        target = _data_monitoring_pd.Timestamp("2012-07-01 00:00:00")
        predictions = _data_monitoring_pd.DataFrame(
            [
                {
                    "target_hour": target,
                    "predicted": 8.0,
                    "features_json": '{"lag_24": 1, "lag_168": 2, "hour": 0}',
                }
            ]
        )
        labels = _data_monitoring_pd.DataFrame(
            [{
                "timestamp": target,
                "cnt": 10,
                "available_at": target + _data_monitoring_pd.Timedelta(hours=3),
            }]
        )
        reference = _data_monitoring_pd.DataFrame({"lag_24": [1, 2, 3], "lag_168": [1, 2, 3], "hour": [0, 1, 2]})
        report = score_monitor_windows(
            predictions,
            labels,
            reference,
            target,
            target + _data_monitoring_pd.Timedelta(hours=5),
            window_hours=168,
            minimum_labels=1,
        )
        at_target = report.loc[report["window_end"] == target.isoformat(sep=" ")].iloc[0]
        at_arrival = report.loc[
            report["window_end"] == (target + _data_monitoring_pd.Timedelta(hours=3)).isoformat(sep=" ")
        ].iloc[0]
        self.assertEqual(at_target["scored_count"], 0)
        self.assertTrue(_data_monitoring_np.isnan(at_target["mae"]))
        self.assertEqual(at_arrival["scored_count"], 1)
        self.assertEqual(at_arrival["mae"], 2.0)


# ========================================================================
# test_quality_gates
# ========================================================================

import unittest as _quality_gates_unittest

from bike_mlops.evaluation import baseline_accuracy_gate


class BaselineAccuracyGateTests(_quality_gates_unittest.TestCase):
    def test_requires_strict_mae_improvement_and_allows_equal_rmse(self):
        result = baseline_accuracy_gate(
            {"mae": 9.0, "rmse": 12.0},
            {"mae": 10.0, "rmse": 12.0},
            period="validation_selection",
        )
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["passed"])
        self.assertTrue(result["conditions"]["candidate_mae_strictly_below_baseline"]["passed"])
        self.assertTrue(result["conditions"]["candidate_rmse_not_above_baseline"]["passed"])
        self.assertRegex(result["policy_sha256"], r"^[0-9a-f]{64}$")

    def test_rejects_mae_tie_or_rmse_regression(self):
        mae_tie = baseline_accuracy_gate(
            {"mae": 10.0, "rmse": 11.0},
            {"mae": 10.0, "rmse": 12.0},
            period="validation_selection",
        )
        rmse_regression = baseline_accuracy_gate(
            {"mae": 9.0, "rmse": 12.01},
            {"mae": 10.0, "rmse": 12.0},
            period="final_evaluation",
        )
        self.assertFalse(mae_tie["passed"])
        self.assertEqual(mae_tie["status"], "failed")
        self.assertFalse(rmse_regression["passed"])
        self.assertEqual(rmse_regression["status"], "failed")

    def test_missing_nonfinite_and_negative_metrics_are_not_evaluable(self):
        for candidate in (
            {"mae": 1.0},
            {"mae": float("nan"), "rmse": 1.0},
            {"mae": 1.0, "rmse": float("inf")},
            {"mae": -1.0, "rmse": 1.0},
        ):
            with self.subTest(candidate=candidate):
                result = baseline_accuracy_gate(
                    candidate,
                    {"mae": 2.0, "rmse": 2.0},
                    period="validation_selection",
                )
                self.assertFalse(result["passed"])
                self.assertEqual(result["status"], "not_evaluable")
                self.assertTrue(result["reasons"])


# ========================================================================
# test_evaluation_protocol
# ========================================================================

import json as _evaluation_protocol_json
import io
import sys as _evaluation_protocol_sys
import tempfile as _evaluation_protocol_tempfile
import threading
import unittest as _evaluation_protocol_unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path as _evaluation_protocol_Path
from contextlib import redirect_stdout
from unittest.mock import patch as _evaluation_protocol_patch

import pandas as _evaluation_protocol_pd

from bike_mlops.evaluation import BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256, begin_evaluation, build_capital_protocol, finish_evaluation, read_ledger, review_reopen_evaluation, save_protocol, protocol_sha256, validate_protocol
from bike_mlops.config import _profiles_load_data_profile as _evaluation_protocol_load_data_profile
from bike_mlops.training import HYPERPARAMETERS, train_candidate
from scripts.data_tools import aggregate_archives


class EvaluationProtocolTests(_evaluation_protocol_unittest.TestCase):
    def setUp(self):
        self.temp = _evaluation_protocol_tempfile.TemporaryDirectory()
        self.root = _evaluation_protocol_Path(self.temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.csv = self.root / "hourly.csv"
        self.manifest = self.root / "manifest.json"
        self.write_archive()
        aggregate_archives(
            ["202401"],
            _evaluation_protocol_pd.Timestamp("2024-01-01"),
            _evaluation_protocol_pd.Timestamp("2024-01-06"),
            self.csv,
            self.manifest,
            raw_dir=self.raw,
        )
        self.ledger = self.root / "consumption-ledger.json"
        self.legacy = self.root / "legacy.json"
        self.legacy.write_text(_evaluation_protocol_json.dumps({"schema_version": 1, "windows": []}))

    def tearDown(self):
        self.temp.cleanup()

    def write_archive(self):
        import zipfile

        path = self.raw / "202401-capitalbikeshare-tripdata.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "trips.csv",
                "ride_id,started_at\nfirst,2024-01-01 00:00:00\nlast,2024-01-05 23:00:00\n",
            )

    def build_protocol(self, evaluation_start="2024-01-03 02:00:00"):
        return build_capital_protocol(
            self.csv,
            self.manifest,
            training_cutoff="2024-01-02 00:00:00",
            validation_start="2024-01-02 00:00:00",
            validation_end="2024-01-03 00:00:00",
            evaluation_start=evaluation_start,
            evaluation_end="2024-01-04 00:00:00",
            incumbent_version="3",
            incumbent_artifact_sha256="a" * 64,
            incumbent_quality_gate="passed",
            candidate_recipe={"model_family": "synthetic", "hyperparameters": {"seed": 17}},
            raw_dir=self.raw,
        )

    def build_bootstrap_protocol(self):
        return build_capital_protocol(
            self.csv,
            self.manifest,
            training_cutoff="2024-01-02 00:00:00",
            validation_start="2024-01-02 00:00:00",
            validation_end="2024-01-03 00:00:00",
            evaluation_start="2024-01-03 02:00:00",
            evaluation_end="2024-01-04 00:00:00",
            incumbent_version=None,
            incumbent_artifact_sha256=None,
            incumbent_quality_gate=None,
            candidate_recipe={"model_family": "synthetic", "hyperparameters": {"seed": 17}},
            raw_dir=self.raw,
        )

    def test_protocol_fixes_row_counts_and_waits_for_validation_labels(self):
        with self.assertRaisesRegex(ValueError, "precedes the time"):
            self.build_protocol("2024-01-03 00:00:00")
        protocol = self.build_protocol()
        self.assertEqual(protocol["selection_decision_time"], "2024-01-03 02:00:00")
        self.assertGreater(protocol["training"]["rows"], 0)
        self.assertEqual(protocol["validation"]["rows"], 24)
        self.assertEqual(protocol["evaluation"]["rows"], 22)
        path = self.root / "frozen.json"
        save_protocol(protocol, path)
        with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
            save_protocol(protocol, path)

    def test_bootstrap_protocol_freezes_baseline_only_mae_rmse_policy(self):
        protocol = self.build_bootstrap_protocol()
        self.assertEqual(protocol["schema_version"], 2)
        self.assertIsNone(protocol["incumbent"])
        self.assertEqual(protocol["selection_gate"]["policy"], BOOTSTRAP_QUALITY_GATE_POLICY)
        self.assertEqual(protocol["selection_gate"]["policy_sha256"], BOOTSTRAP_QUALITY_GATE_POLICY_SHA256)
        self.assertEqual(
            protocol["selection_gate"]["policy_id"], "capital-bootstrap-baseline-mae-rmse-v1"
        )
        self.assertEqual(protocol["selection_gate"]["final_evaluation_used_for_selection"], False)
        self.assertEqual(validate_protocol(protocol), protocol)

        tampered = dict(protocol)
        tampered["selection_gate"] = dict(protocol["selection_gate"])
        tampered["selection_gate"]["policy"] = dict(BOOTSTRAP_QUALITY_GATE_POLICY)
        tampered["selection_gate"]["policy"]["segments"] = "gate"
        tampered["protocol_sha256"] = protocol_sha256(tampered)
        with self.assertRaisesRegex(ValueError, "policy is missing, unsupported, or modified"):
            validate_protocol(tampered)

    def test_bootstrap_protocol_requires_all_incumbent_fields_or_none(self):
        with self.assertRaisesRegex(ValueError, "complete version/hash/passed-gate tuple"):
            build_capital_protocol(
                self.csv,
                self.manifest,
                training_cutoff="2024-01-02 00:00:00",
                validation_start="2024-01-02 00:00:00",
                validation_end="2024-01-03 00:00:00",
                evaluation_start="2024-01-03 02:00:00",
                evaluation_end="2024-01-04 00:00:00",
                incumbent_version=None,
                incumbent_artifact_sha256="a" * 64,
                incumbent_quality_gate=None,
                candidate_recipe={"model_family": "synthetic", "hyperparameters": {"seed": 17}},
                raw_dir=self.raw,
            )

    def test_new_protocol_rejects_legacy_timezone_unpinned_manifest(self):
        manifest = _evaluation_protocol_json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["manifest_schema_version"] = 2
        manifest["processing"]["processing_version"] = "capital-hourly-aggregation-v2"
        manifest.pop("timezone_database")
        legacy_manifest = self.root / "legacy-manifest.json"
        legacy_manifest.write_text(_evaluation_protocol_json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "require manifest schema 3"):
            build_capital_protocol(
                self.csv,
                legacy_manifest,
                training_cutoff="2024-01-02 00:00:00",
                validation_start="2024-01-02 00:00:00",
                validation_end="2024-01-03 00:00:00",
                evaluation_start="2024-01-03 02:00:00",
                evaluation_end="2024-01-04 00:00:00",
                incumbent_version="3",
                incumbent_artifact_sha256="a" * 64,
                incumbent_quality_gate="passed",
                candidate_recipe={"model_family": "synthetic", "hyperparameters": {"seed": 17}},
                raw_dir=self.raw,
            )

    def test_protocol_rejects_overlapping_declared_windows_even_when_rows_are_missing(self):
        protocol = self.build_protocol()
        protocol["validation"]["end_exclusive"] = "2024-01-04 00:00:00"
        protocol["protocol_sha256"] = protocol_sha256(protocol)
        with self.assertRaisesRegex(ValueError, "overlaps the validation window"):
            validate_protocol(protocol)

    def test_ledger_blocks_reuse_and_requires_human_review_after_failure(self):
        protocol = self.build_protocol()
        stale_lock = self.ledger.with_suffix(self.ledger.suffix + ".lock")
        stale_lock.write_text("pid=999999999\n")
        attempt = begin_evaluation(
            protocol, "validation_selection", None, self.ledger, self.legacy
        )
        ledger = read_ledger(self.ledger)
        key = ledger["records"][0]["consumption_key"]
        with self.assertRaisesRegex(ValueError, "overlaps an already consumed/started"):
            begin_evaluation(protocol, "validation_selection", None, self.ledger, self.legacy)
        changed_snapshot = dict(protocol)
        changed_snapshot["dataset"] = dict(protocol["dataset"], data_sha256="f" * 64)
        changed_snapshot["protocol_sha256"] = protocol_sha256(changed_snapshot)
        with self.assertRaisesRegex(ValueError, "overlaps an already consumed/started"):
            begin_evaluation(changed_snapshot, "validation_selection", None, self.ledger, self.legacy)
        finish_evaluation(attempt, "failed", self.ledger, detail="synthetic interruption")
        with self.assertRaisesRegex(ValueError, "overlaps an already consumed/started"):
            begin_evaluation(protocol, "validation_selection", None, self.ledger, self.legacy)
        review_reopen_evaluation(key, "tester", "checked synthetic outputs", self.ledger)
        retry = begin_evaluation(protocol, "validation_selection", "b" * 64, self.ledger, self.legacy)
        finish_evaluation(retry, "completed", self.ledger)
        record = read_ledger(self.ledger)["records"][0]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(len(record["attempts"]), 2)
        self.assertEqual(record["attempts"][0]["status"], "failed")
        self.assertEqual(record["attempts"][1]["status"], "completed")

    def test_bootstrap_ledger_records_absent_incumbent_as_null(self):
        protocol = self.build_bootstrap_protocol()
        attempt = begin_evaluation(protocol, "validation_selection", None, self.ledger, self.legacy)
        record = read_ledger(self.ledger)["records"][0]
        self.assertEqual(record["attempts"][0]["attempt_id"], attempt)
        self.assertIsNone(record["attempts"][0]["incumbent_artifact_sha256"])
        finish_evaluation(attempt, "failed", self.ledger, detail="synthetic policy test")

    def test_bootstrap_candidate_fit_and_final_evaluation_need_no_registry_model(self):
        from bike_mlops import training
        from bike_mlops.evaluation import persist_candidate_bundle, source_tree_inventory
        from scripts import data_tools as final_evaluation

        dense_raw = self.root / "dense-raw"
        dense_raw.mkdir()
        dense_archive = dense_raw / "202401-capitalbikeshare-tripdata.zip"
        rows = ["ride_id,started_at"]
        ride_id = 0
        for timestamp in _evaluation_protocol_pd.date_range("2024-01-01", "2024-02-01", freq="h", inclusive="left"):
            hourly_count = 5 + timestamp.hour % 12 + (0 if timestamp.dayofweek >= 5 else 25)
            for _ in range(hourly_count):
                rows.append(f"ride-{ride_id},{timestamp:%Y-%m-%d %H:%M:%S}")
                ride_id += 1
        import zipfile

        with zipfile.ZipFile(dense_archive, "w") as archive:
            archive.writestr("trips.csv", "\n".join(rows) + "\n")
        dense_csv = self.root / "dense-hourly.csv"
        dense_manifest = self.root / "dense-manifest.json"
        aggregate_archives(
            ["202401"],
            _evaluation_protocol_pd.Timestamp("2024-01-01"),
            _evaluation_protocol_pd.Timestamp("2024-02-01"),
            dense_csv,
            dense_manifest,
            raw_dir=dense_raw,
        )
        protocol = build_capital_protocol(
            dense_csv,
            dense_manifest,
            training_cutoff="2024-01-20 00:00:00",
            validation_start="2024-01-20 00:00:00",
            validation_end="2024-01-21 00:00:00",
            evaluation_start="2024-01-21 03:00:00",
            evaluation_end="2024-01-22 00:00:00",
            incumbent_version=None,
            incumbent_artifact_sha256=None,
            incumbent_quality_gate=None,
            candidate_recipe={
                "model_family": "scikit-learn HistGradientBoostingRegressor in a Pipeline",
                "hyperparameters": HYPERPARAMETERS,
            },
            raw_dir=dense_raw,
        )
        protocol_path = self.root / "bootstrap-protocol.json"
        protocol_path.write_text(_evaluation_protocol_json.dumps(protocol), encoding="utf-8")
        capital_profile = _evaluation_protocol_load_data_profile("capital-bikeshare")
        original_validator = training.validate_capital_manifest

        def validate_local_manifest(manifest, csv_path):
            return original_validator(manifest, csv_path, raw_dir=dense_raw)

        captured = {}

        def capture_candidate_bundle(estimator, candidate_report, validation_predictions, *, protocol_bytes=None,
                                     training_frame=None, training_features=None):
            captured.update({
                "estimator": estimator,
                "report": candidate_report,
                "validation_predictions": validation_predictions,
                "protocol_bytes": protocol_bytes,
                "training_frame": training_frame,
                "training_features": training_features,
            })
            return {"candidate_artifact_sha256": "a" * 64}

        with _evaluation_protocol_patch.multiple(
            training,
            DATA_PROFILE_ID="capital-bikeshare",
            TARGET_CONTRACT_ID="capital-trip-starts-hourly-v1",
            DATA_PROFILE=capital_profile,
        ), _evaluation_protocol_patch(
            "bike_mlops.training.load_registered_model", side_effect=AssertionError("registry must not be used")
        ), _evaluation_protocol_patch(
            "bike_mlops.training.persist_candidate_bundle", side_effect=capture_candidate_bundle
        ), _evaluation_protocol_patch(
            "bike_mlops.training.validate_capital_manifest", side_effect=validate_local_manifest
        ), _evaluation_protocol_patch(
            "bike_mlops.training.begin_evaluation", return_value="synthetic-attempt"
        ) as begin, _evaluation_protocol_patch(
            "bike_mlops.training.finish_evaluation"
        ) as finish:
            report = train_candidate(
                data_path=dense_csv,
                incumbent_version=None,
                source_manifest_path=dense_manifest,
                register=False,
                evaluation_protocol_path=protocol_path,
            )
        begin.assert_called_once()
        finish.assert_called_once()
        self.assertEqual(finish.call_args.args[1], "completed")
        self.assertEqual(report["comparison"]["reference"], "historical_baseline_only")
        self.assertIsNone(report["comparison"]["incumbent"])
        self.assertEqual(report["quality_gate_policy_id"], "capital-bootstrap-baseline-mae-rmse-v1")
        self.assertIn(report["validation_gate_status"], {"passed", "failed", "not_evaluable"})
        self.assertTrue(report["quality_gate_passed"], report["comparison"]["validation_gate"])
        self.assertEqual(report["final_accuracy_gate_status"], "not_evaluated")
        self.assertEqual(report["operational_gate_status"], "not_evaluated")
        self.assertEqual(report["deployment_permission"]["status"], "blocked")

        (self.root / "README.md").write_text("synthetic source snapshot\n", encoding="utf-8")
        source_sha, source_file_count = source_tree_inventory(self.root)
        report["source_revision"] = {
            **report["source_revision"],
            "source_tree_sha256": source_sha,
            "source_file_count": source_file_count,
        }
        with _evaluation_protocol_patch('bike_mlops.evaluation.PROJECT_ROOT', self.root):
            bundle_result = persist_candidate_bundle(
                captured["estimator"],
                report,
                captured["validation_predictions"],
                protocol_bytes=captured["protocol_bytes"],
                training_frame=captured["training_frame"],
                training_features=captured["training_features"],
            )
        output_path = self.root / "data/reports/bootstrap-final-evaluation.json"
        original_final_validator = final_evaluation.validate_capital_manifest

        def validate_final_manifest(manifest, csv_path):
            return original_final_validator(manifest, csv_path, raw_dir=dense_raw)

        arguments = [
            'data_tools.py',
            "--csv", str(dense_csv),
            "--source-manifest", str(dense_manifest),
            "--selection-report", str(self.root / bundle_result["candidate_report"]),
            "--output", str(output_path),
        ]
        with _evaluation_protocol_patch.multiple(
            final_evaluation,
            _evaluate_capitalbikeshare_holdout_PROJECT_ROOT=self.root,
            _evaluate_capitalbikeshare_holdout_DATA_PROFILE_ID="capital-bikeshare",
            _evaluate_capitalbikeshare_holdout_TARGET_CONTRACT_ID="capital-trip-starts-hourly-v1",
        ), _evaluation_protocol_patch(
            'scripts.data_tools.validate_capital_manifest',
            side_effect=validate_final_manifest,
        ), _evaluation_protocol_patch(
            'scripts.data_tools.begin_evaluation',
            return_value="synthetic-final-attempt",
        ) as begin_final, _evaluation_protocol_patch(
            'scripts.data_tools.finish_evaluation'
        ) as finish_final, _evaluation_protocol_patch(
            'scripts.data_tools._evaluate_capitalbikeshare_holdout_load_registered_model',
            side_effect=AssertionError("baseline-only final evaluation must not load a registered model"),
        ), _evaluation_protocol_patch.object(_evaluation_protocol_sys, "argv", arguments), redirect_stdout(io.StringIO()):
            final_evaluation._evaluate_capitalbikeshare_holdout_main()

        begin_final.assert_called_once()
        finish_final.assert_called_once()
        self.assertEqual(finish_final.call_args.args[1], "completed")
        final_report = _evaluation_protocol_json.loads(output_path.read_text(encoding="utf-8"))
        self.assertIsNone(final_report["incumbent"])
        self.assertFalse(final_report["model_fit_performed_during_evaluation"])
        self.assertEqual(
            final_report["final_accuracy_gate"]["policy_id"], "capital-bootstrap-baseline-mae-rmse-v1"
        )
        self.assertEqual(final_report["operational_gate_status"], "not_evaluated")
        self.assertEqual(final_report["deployment_permission"]["status"], "blocked")
        self.assertTrue(output_path.with_suffix(".predictions.csv").is_file())

    def test_legacy_overlap_blocks_before_any_ledger_write(self):
        protocol = self.build_protocol()
        legacy = {
            "schema_version": 1,
            "windows": [{
                "record_id": "old",
                "data_sha256": protocol["dataset"]["data_sha256"],
                "target_contract": protocol["dataset"]["target_contract"],
                "start_inclusive": "2024-01-02 12:00:00",
                "end_exclusive": "2024-01-03 12:00:00",
            }],
        }
        self.legacy.write_text(_evaluation_protocol_json.dumps(legacy))
        with self.assertRaisesRegex(ValueError, "overlaps a previously used"):
            begin_evaluation(protocol, "validation_selection", None, self.ledger, self.legacy)
        self.assertFalse(self.ledger.exists())

    def test_concurrent_start_allows_only_one_attempt_for_a_window(self):
        protocol = self.build_protocol()
        barrier = threading.Barrier(2)

        def start_once():
            barrier.wait()
            try:
                return begin_evaluation(protocol, "validation_selection", None, self.ledger, self.legacy)
            except (ValueError, RuntimeError) as error:
                return f"blocked:{type(error).__name__}"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: start_once(), range(2)))
        self.assertEqual(sum(isinstance(value, str) and not value.startswith("blocked:") for value in outcomes), 1)
        ledger = read_ledger(self.ledger)
        self.assertEqual(len(ledger["records"]), 1)
        self.assertEqual(len(ledger["records"][0]["attempts"]), 1)
        self.assertEqual(ledger["records"][0]["status"], "started")


# ========================================================================
# test_candidate_artifacts
# ========================================================================

import json as _candidate_artifacts_json
import ast
import hashlib as _candidate_artifacts_hashlib
import tempfile as _candidate_artifacts_tempfile
import unittest as _candidate_artifacts_unittest
from pathlib import Path as _candidate_artifacts_Path
from unittest.mock import patch as _candidate_artifacts_patch

import numpy as _candidate_artifacts_np
import pandas as _candidate_artifacts_pd

from bike_mlops.evaluation import _write_source_snapshot, candidate_identity, load_candidate_model, persist_candidate_bundle, sha256_file, source_snapshot_inventory, source_tree_inventory, source_tree_sha256
from bike_mlops.training import build_estimator
from bike_mlops.data import FEATURE_COLUMNS as _candidate_artifacts_FEATURE_COLUMNS
from bike_mlops.data import feature_frame_for_dataset as _candidate_artifacts_feature_frame_for_dataset
from bike_mlops.evaluation import baseline_accuracy_gate as _candidate_artifacts_baseline_accuracy_gate
from bike_mlops.training import HistoricalBaseline, regression_metrics, segment_comparison, segment_mae
from scripts.data_tools import _verify_validation_evidence


def _test_row_hash(frame: _candidate_artifacts_pd.DataFrame) -> str:
    payload = "".join(
        f"{timestamp.isoformat(sep=' ')}\t{int(target)}\n"
        for timestamp, target in zip(frame["timestamp"], frame["cnt"])
    )
    return _candidate_artifacts_hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CandidateArtifactTests(_candidate_artifacts_unittest.TestCase):
    def setUp(self):
        self.temp = _candidate_artifacts_tempfile.TemporaryDirectory()
        self.root = _candidate_artifacts_Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _fitted_model(self):
        x = _candidate_artifacts_pd.DataFrame(
            {
                "hour": _candidate_artifacts_np.arange(48) % 24,
                "weekday": _candidate_artifacts_np.arange(48) % 7,
                "month": _candidate_artifacts_np.ones(48),
                "day_of_year": _candidate_artifacts_np.arange(48) + 1,
                "is_weekend": (_candidate_artifacts_np.arange(48) % 7 >= 5).astype(float),
                "lag_24": _candidate_artifacts_np.arange(48, dtype=float),
                "lag_24_missing": _candidate_artifacts_np.zeros(48),
                "lag_168": _candidate_artifacts_np.arange(48, dtype=float),
                "lag_168_missing": _candidate_artifacts_np.zeros(48),
            },
            columns=_candidate_artifacts_FEATURE_COLUMNS,
        )
        model = build_estimator().fit(x, _candidate_artifacts_np.arange(48, dtype=float) * 2)
        return model, x.iloc[-5:].copy()

    def test_skops_artifact_is_hash_checked_and_predictions_round_trip(self):
        model, x = self._fitted_model()
        import skops.io as sio

        path = self.root / "model.skops"
        sio.dump(model, path)
        expected_hash = sha256_file(path)
        restored, actual_hash = load_candidate_model(path, expected_hash)
        _candidate_artifacts_np.testing.assert_allclose(model.predict(x), restored.predict(x), rtol=0, atol=0)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            load_candidate_model(path, "0" * 64)

    def test_two_fits_on_the_same_development_data_reproduce_predictions(self):
        first, x = self._fitted_model()
        second, _ = self._fitted_model()
        _candidate_artifacts_np.testing.assert_allclose(first.predict(x), second.predict(x), rtol=0, atol=0)

    def test_source_snapshot_is_deterministic_and_excludes_local_data_and_env(self):
        source = self.root / "source"
        (source / "src").mkdir(parents=True)
        (source / "data").mkdir()
        (source / "src" / "app.py").write_text("value = 1\n")
        (source / ".env").write_text("SECRET=not-for-snapshot\n")
        (source / ".env.production").write_text("SECRET=also-not-for-snapshot\n")
        (source / "data" / "raw.csv").write_text("private,local,data\n")
        first = self.root / "first.tar.gz"
        second = self.root / "second.tar.gz"
        _write_source_snapshot(first, source)
        _write_source_snapshot(second, source)
        self.assertEqual(sha256_file(first), sha256_file(second))
        import tarfile

        with tarfile.open(first, "r:gz") as archive:
            names = archive.getnames()
        self.assertIn("src/app.py", names)
        self.assertNotIn(".env", names)
        self.assertNotIn(".env.production", names)
        self.assertFalse(any(name.startswith("data/") for name in names))
        self.assertEqual(source_snapshot_inventory(first), source_tree_inventory(source))

    def test_reserved_evaluation_script_never_fits_the_candidate(self):
        script = _candidate_artifacts_Path(__file__).resolve().parents[1] / 'scripts/data_tools.py'
        tree = ast.parse(script.read_text(encoding="utf-8"))
        candidate_fit_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fit"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "candidate"
        ]
        self.assertEqual(candidate_fit_calls, [])

    def test_final_evaluation_recomputes_validation_predictions_and_gate(self):
        timestamps = _candidate_artifacts_pd.date_range("2024-01-01", periods=24 * 10, freq="h")
        frame = _candidate_artifacts_pd.DataFrame({"timestamp": timestamps, "cnt": _candidate_artifacts_np.arange(len(timestamps), dtype=int)})
        validation_start = _candidate_artifacts_pd.Timestamp("2024-01-07 00:00:00")
        validation_end = _candidate_artifacts_pd.Timestamp("2024-01-08 00:00:00")
        cutoff = _candidate_artifacts_pd.Timestamp("2024-01-07 03:00:00")
        train_mask = (frame["timestamp"] < validation_start) & (
            frame["timestamp"] + _candidate_artifacts_pd.Timedelta(hours=3) <= cutoff
        )
        validation_mask = (frame["timestamp"] >= validation_start) & (frame["timestamp"] < validation_end)
        validation = frame.loc[validation_mask].reset_index(drop=True)
        features = _candidate_artifacts_feature_frame_for_dataset(frame)

        class FixedPredictions:
            def __init__(self, values):
                self.values = _candidate_artifacts_np.asarray(values, dtype=float)

            def predict(self, rows):
                if len(rows) != len(self.values):
                    raise AssertionError("unexpected prediction row count")
                return self.values

        candidate = FixedPredictions(validation["cnt"].to_numpy())
        incumbent = FixedPredictions(_candidate_artifacts_np.full(len(validation), 500.0))
        candidate_predictions = candidate.predict(features.loc[validation_mask, _candidate_artifacts_FEATURE_COLUMNS])
        incumbent_predictions = incumbent.predict(features.loc[validation_mask, _candidate_artifacts_FEATURE_COLUMNS])
        baseline_model = HistoricalBaseline().fit(frame.loc[train_mask].reset_index(drop=True), frame)
        baseline_predictions = baseline_model.predict(validation["timestamp"])
        evidence = _candidate_artifacts_pd.DataFrame({
            "timestamp": validation["timestamp"].map(lambda stamp: stamp.isoformat(sep=" ")),
            "label_available_at": (validation["timestamp"] + _candidate_artifacts_pd.Timedelta(hours=3)).map(
                lambda stamp: stamp.isoformat(sep=" ")
            ),
            "actual": validation["cnt"].astype(int),
            "candidate_prediction": candidate_predictions,
            "incumbent_prediction": incumbent_predictions,
            "baseline_prediction": baseline_predictions,
        })
        path = self.root / "validation-predictions.csv"
        evidence.to_csv(path, index=False, float_format="%.17g")
        validation_metrics = {
            "model": regression_metrics(validation["cnt"], candidate_predictions),
            "baseline": regression_metrics(validation["cnt"], baseline_predictions),
            "model_by_segment": segment_mae(validation, candidate_predictions),
            "baseline_by_segment": segment_mae(validation, baseline_predictions),
        }
        incumbent_metrics = regression_metrics(validation["cnt"], incumbent_predictions)
        protocol = {
            "incumbent": {
                "model_version": "1",
                "artifact_sha256": "a" * 64,
                "quality_gate": "passed",
            },
            "training": {"cutoff": cutoff.isoformat(sep=" "), "rows": int(train_mask.sum())},
            "validation": {
                "start_inclusive": validation_start.isoformat(sep=" "),
                "end_exclusive": validation_end.isoformat(sep=" "),
                "rows": len(validation),
            },
            "label_availability": {"delay_hours": 3},
        }
        selection = {
            "validation_predictions": {
                "path": "validation-predictions.csv",
                "sha256": sha256_file(path),
                "rows": len(evidence),
            },
            "training_row_keys_sha256": _test_row_hash(frame.loc[train_mask].reset_index(drop=True)),
            "validation_row_keys_sha256": _test_row_hash(validation),
            "validation": validation_metrics,
            "comparison": {
                "incumbent": incumbent_metrics,
                "incumbent_quality_gate": "passed",
            },
            "quality_gate_passed": True,
        }
        _verify_validation_evidence(selection, path, frame, protocol, candidate, incumbent)
        with self.assertRaisesRegex(ValueError, "quality gate"):
            _verify_validation_evidence(
                dict(selection, quality_gate_passed=False), path, frame, protocol, candidate, incumbent
            )

    def test_bootstrap_validation_evidence_uses_only_historical_baseline_gate(self):
        timestamps = _candidate_artifacts_pd.date_range("2024-01-01", periods=24 * 10, freq="h")
        frame = _candidate_artifacts_pd.DataFrame({"timestamp": timestamps, "cnt": _candidate_artifacts_np.arange(len(timestamps), dtype=int)})
        validation_start = _candidate_artifacts_pd.Timestamp("2024-01-07 00:00:00")
        validation_end = _candidate_artifacts_pd.Timestamp("2024-01-08 00:00:00")
        cutoff = _candidate_artifacts_pd.Timestamp("2024-01-07 03:00:00")
        train_mask = (frame["timestamp"] < validation_start) & (
            frame["timestamp"] + _candidate_artifacts_pd.Timedelta(hours=3) <= cutoff
        )
        validation_mask = (frame["timestamp"] >= validation_start) & (frame["timestamp"] < validation_end)
        validation = frame.loc[validation_mask].reset_index(drop=True)
        features = _candidate_artifacts_feature_frame_for_dataset(frame)

        class FixedPredictions:
            def predict(self, rows):
                if len(rows) != len(validation):
                    raise AssertionError("unexpected prediction row count")
                return validation["cnt"].to_numpy(dtype=float)

        candidate = FixedPredictions()
        candidate_predictions = candidate.predict(features.loc[validation_mask, _candidate_artifacts_FEATURE_COLUMNS])
        baseline_model = HistoricalBaseline().fit(frame.loc[train_mask].reset_index(drop=True), frame)
        baseline_predictions = baseline_model.predict(validation["timestamp"])
        path = self.root / "bootstrap-validation-predictions.csv"
        evidence = _candidate_artifacts_pd.DataFrame({
            "timestamp": validation["timestamp"].map(lambda stamp: stamp.isoformat(sep=" ")),
            "label_available_at": (validation["timestamp"] + _candidate_artifacts_pd.Timedelta(hours=3)).map(
                lambda stamp: stamp.isoformat(sep=" ")
            ),
            "actual": validation["cnt"].astype(int),
            "candidate_prediction": candidate_predictions,
            "baseline_prediction": baseline_predictions,
        })
        evidence.to_csv(path, index=False, float_format="%.17g")
        candidate_metrics = regression_metrics(validation["cnt"], candidate_predictions)
        baseline_metrics = regression_metrics(validation["cnt"], baseline_predictions)
        gate = _candidate_artifacts_baseline_accuracy_gate(candidate_metrics, baseline_metrics, period="validation_selection")
        validation_report = {
            "model": candidate_metrics,
            "baseline": baseline_metrics,
            "model_by_segment": segment_mae(validation, candidate_predictions),
            "baseline_by_segment": segment_mae(validation, baseline_predictions),
            "segment_comparison": segment_comparison(validation, candidate_predictions, baseline_predictions),
        }
        selection = {
            "validation_predictions": {
                "path": "validation-predictions.csv",
                "sha256": sha256_file(path),
                "rows": len(evidence),
            },
            "training_row_keys_sha256": _test_row_hash(frame.loc[train_mask].reset_index(drop=True)),
            "validation_row_keys_sha256": _test_row_hash(validation),
            "validation": validation_report,
            "comparison": {
                "reference": "historical_baseline_only",
                "incumbent_model_version": None,
                "incumbent_artifact_sha256": None,
                "incumbent_quality_gate": None,
                "incumbent": None,
                "validation_gate": gate,
            },
            "quality_gate_policy_id": gate["policy_id"],
            "quality_gate_policy_sha256": gate["policy_sha256"],
            "quality_gate_passed": gate["passed"],
        }
        protocol = {
            "incumbent": None,
            "training": {"cutoff": cutoff.isoformat(sep=" "), "rows": int(train_mask.sum())},
            "validation": {
                "start_inclusive": validation_start.isoformat(sep=" "),
                "end_exclusive": validation_end.isoformat(sep=" "),
                "rows": len(validation),
            },
            "label_availability": {"delay_hours": 3},
        }
        _verify_validation_evidence(selection, path, frame, protocol, candidate, incumbent=None)
        with self.assertRaisesRegex(ValueError, "quality gate"):
            _verify_validation_evidence(
                dict(selection, quality_gate_passed=False), path, frame, protocol, candidate, incumbent=None
            )


    def test_candidate_bundle_is_atomic_immutable_and_contains_row_level_evidence(self):
        model, _ = self._fitted_model()
        report = {
            "data_sha256": "1" * 64,
            "data_source": {"source_manifest_sha256": "2" * 64},
            "source_revision": {"source_tree_sha256": "3" * 64, "source_file_count": 0},
            "environment": {"direct_dependencies": {"scikit-learn": "test"}},
            "feature_contract_version": "bike-demand-features-v1",
            "feature_columns": list(_candidate_artifacts_FEATURE_COLUMNS),
            "hyperparameters": {"random_state": 17},
            "training_cutoff": "2024-01-01 00:00:00",
            "validation_window": {"start": "2024-01-01 00:00:00", "end_exclusive": "2024-01-02 00:00:00"},
            "comparison": {"incumbent_artifact_sha256": "4" * 64},
            "training_row_keys_sha256": "5" * 64,
            "validation_row_keys_sha256": "6" * 64,
            "quality_gate_passed": True,
            "quality_gate": "candidate must pass synthetic test metrics",
            "validation": {
                "model": {"mae": 1.0, "rmse": 1.0},
                "baseline": {"mae": 2.0, "rmse": 2.0},
                "model_by_segment": {},
                "baseline_by_segment": {},
            },
        }
        validation = _candidate_artifacts_pd.DataFrame(
            {"timestamp": ["2024-01-01 00:00:00"], "actual": [1], "candidate_prediction": [1.0]}
        )
        with _candidate_artifacts_patch('bike_mlops.evaluation.PROJECT_ROOT', self.root):
            report["source_revision"]["source_tree_sha256"] = source_tree_sha256(self.root)
            result = persist_candidate_bundle(model, report, validation)
            bundle = self.root / result["candidate_bundle"]
            frozen = _candidate_artifacts_json.loads((bundle / "candidate-report.json").read_text())
            self.assertTrue((bundle / "model.skops").is_file())
            self.assertTrue((bundle / "validation-predictions.csv").is_file())
            self.assertEqual(sha256_file(bundle / "model.skops"), frozen["candidate_artifact"]["sha256"])
            self.assertEqual(candidate_identity(frozen), frozen["candidate_identity"])
            changed_report = dict(frozen, quality_gate_passed=False)
            self.assertNotEqual(candidate_identity(changed_report), frozen["candidate_identity"])
            with _candidate_artifacts_patch(
                'bike_mlops.evaluation.candidate_identity',
                return_value=frozen["candidate_identity"],
            ):
                with self.assertRaisesRegex(FileExistsError, "immutable"):
                    persist_candidate_bundle(model, report, validation)


if __name__ == "__main__":
    import unittest as _unittest_entry
    _unittest_entry.main()
