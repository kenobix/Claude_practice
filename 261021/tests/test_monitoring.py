"""Contract and regression tests: test_monitoring."""
from __future__ import annotations


# ========================================================================
# test_monitoring_psi_v2
# ========================================================================

import copy
import json
import math
import unittest

import numpy as np

from bike_mlops.monitoring import population_stability_index
from bike_mlops.monitoring import PSI_ALGORITHM_V2, evaluate_psi_reference, make_psi_reference


class VersionedPSITests(unittest.TestCase):
    def make(self, values, **kwargs):
        return make_psi_reference(values, version=PSI_ALGORITHM_V2, feature_name="lag_24", reference_id="synthetic-training", **kwargs)

    def evaluate(self, reference, values, **kwargs):
        return evaluate_psi_reference(reference, values, version=PSI_ALGORITHM_V2, **kwargs)

    def test_constant_reference_detects_changes_both_directions_and_identical_is_zero(self):
        ref = self.make([7, 7, 7, 7])
        self.assertEqual(ref["mode"], "constant_three_way")
        self.assertEqual(ref["reference_counts"], [0, 4, 0])
        self.assertEqual(self.evaluate(ref, [7, 7, 7])["psi"], 0.0)
        for current, expected in (([8, 8, 8], [0, 0, 3]), ([6, 6, 6], [3, 0, 0]),
                                  ([6, 7, 8], [1, 1, 1])):
            with self.subTest(current=current):
                result = self.evaluate(ref, current)
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["current_bin_counts"], expected)
                self.assertGreater(result["psi"], 0.2)
        # Historical implementation is intentionally unchanged.
        self.assertEqual(population_stability_index([7, 7, 7], [8, 8, 8]), 0.0)

    def test_nonconstant_bins_are_frozen_and_reusable(self):
        ref = self.make([0, 1, 2, 3, 4, 5], bins=4)
        self.assertEqual(ref["mode"], "quantile")
        same = self.evaluate(json.loads(json.dumps(ref, allow_nan=False)), [0, 1, 2, 3, 4, 5])
        self.assertEqual(same["psi"], 0.0)
        self.assertGreater(self.evaluate(ref, [20, 21, 22, 23, 24, 25])["psi"], 0.2)
        self.assertEqual(self.make([5, 4, 3, 2, 1, 0], bins=4)["reference_sha256"], ref["reference_sha256"])
        self.assertEqual(self.make([1, 1, 2, 2], bins=2)["edges"], [1.5])

    def test_missing_invalid_and_insufficient_counts_are_explicit(self):
        for values, expected in (([], "no_reference"), ([None, float("nan")], "no_reference"),
                                 ([3], "insufficient_reference"), ([2, 2, float("inf")], "invalid_reference_values")):
            with self.subTest(values=values):
                ref = self.make(values)
                self.assertEqual(ref["status"], expected)
                result = self.evaluate(ref, [4, 4])
                self.assertEqual(result["status"], expected)
                self.assertIsNone(result["psi"])
        ref = self.make([0, 1, None, float("nan")])
        self.assertEqual(ref["input_counts"], {"total": 4, "finite": 2, "missing": 2, "nonfinite": 0})
        for values, status in (([], "no_current_data"), ([None, None], "no_current_data"),
                               ([1], "insufficient_current_data"), ([1, 1, -float("inf")], "invalid_current_values")):
            with self.subTest(values=values):
                result = self.evaluate(ref, values)
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["psi"])
        result = self.evaluate(ref, [0, 1, None])
        self.assertEqual(result["current_input_counts"]["missing"], 1)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.evaluate(ref, [0, 1], minimum_count=3)["status"], "insufficient_current_data")

    def test_version_policy_and_changed_reference_are_rejected(self):
        ref = self.make([1, 1, 1])
        with self.assertRaisesRegex(ValueError, "version"):
            make_psi_reference([1, 1], version="legacy", feature_name="x", reference_id="id")
        with self.assertRaisesRegex(ValueError, "version"):
            evaluate_psi_reference(ref, [1, 1], version="legacy")
        altered = copy.deepcopy(ref)
        altered["constant_value"] = 2
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.evaluate(altered, [2, 2])
        altered["reference_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.evaluate(altered, [2, 2])
        with self.assertRaisesRegex(ValueError, "minimum"):
            self.evaluate(ref, [1, 1], minimum_count=1)

    def test_adjacent_floats_can_still_be_separated(self):
        lower, upper = 1.0, float(np.nextafter(1.0, 2.0))
        ref = self.make([lower, upper], bins=2)
        self.assertEqual(ref["reference_counts"], [1, 1])
        self.assertEqual(self.evaluate(ref, [lower, upper])["psi"], 0.0)
        self.assertGreater(self.evaluate(ref, [upper, upper])["psi"], 0.2)

    def test_input_shape_and_bad_parameters_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "one-dimensional"):
            self.make([[1, 2]])
        with self.assertRaisesRegex(ValueError, "numeric"):
            self.make([1, "bad"])
        for count in (True, 1, 101):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "bin count"):
                self.make([1, 2], bins=count)
        for count in (True, 1, 2.5):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "minimum"):
                self.make([1, 2], minimum_count=count)
        for values in ([1, float("nan")], [2, 2, 2], [3, 4, 5, 6]):
            result = self.evaluate(self.make([1, 2, 3]), values)
            if result["psi"] is not None:
                self.assertTrue(math.isfinite(result["psi"]))


# ========================================================================
# test_monitoring_window
# ========================================================================

import json as _monitoring_window_json
import tempfile
import unittest as _monitoring_window_unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from bike_mlops import cli
from bike_mlops.cli import build_parser
from bike_mlops.db import stable_hash
from bike_mlops.monitoring import resolve_monitor_window


class MonitoringWindowTests(_monitoring_window_unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = {"replay_id": "abc-2012100100-2013010100", "model_version": "3", "data_sha256": "a" * 64,
                     "profile_id": "uci-bike-sharing", "target_contract_id": "uci-target", "report_dir": self.root,
                     "start": None, "end": None}
        self.report = {"replay_id": self.args["replay_id"], "model_version": "3", "data_sha256": "a" * 64,
                       "data_profile_id": self.args["profile_id"], "target_contract_id": self.args["target_contract_id"],
                       "start": "2012-10-01 00:00:00", "end_exclusive": "2013-01-01 00:00:00"}
        self.path = self.root / f"replay-{self.args['replay_id']}-v3.json"
        self.path.write_text(_monitoring_window_json.dumps(self.report))

    def resolve(self, **updates):
        return resolve_monitor_window(**{**self.args, **updates})

    def test_default_inherits_october_to_december_instead_of_july(self):
        first, end, source = self.resolve()
        self.assertEqual(first, pd.Timestamp("2012-10-01"))
        self.assertEqual(end, pd.Timestamp("2013-01-01"))
        self.assertEqual(source["source"], "replay_report")
        args = build_parser().parse_args(["monitor", "--replay-id", self.args["replay_id"], "--model-version", "3"])
        self.assertIsNone(args.start)
        self.assertIsNone(args.end)

    def test_explicit_subperiod_requires_both_bounds_and_cannot_escape_replay(self):
        first, end, source = self.resolve(start="2012-11-01", end="2012-12-01")
        self.assertEqual((first, end), (pd.Timestamp("2012-11-01"), pd.Timestamp("2012-12-01")))
        self.assertEqual(source["source"], "explicit")
        with self.assertRaisesRegex(ValueError, "both"):
            self.resolve(start="2012-11-01")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.resolve(start="2012-07-01", end="2013-01-01")
        with self.assertRaisesRegex(ValueError, "naive hourly"):
            self.resolve(start="2012-11-01 00:30", end="2012-12-01")
        with self.assertRaisesRegex(ValueError, "naive hourly"):
            self.resolve(start=0, end="2012-12-01")

    def test_missing_report_requires_explicit_period_and_records_missing_identity(self):
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, "report is missing"):
            self.resolve()
        _, _, source = self.resolve(start="2012-10-01", end="2013-01-01")
        self.assertEqual(source["profile_identity_source"], "not_in_report")

    def test_identity_and_outside_symlink_are_rejected(self):
        self.report["model_version"] = "2"
        self.path.write_text(_monitoring_window_json.dumps(self.report))
        with self.assertRaisesRegex(ValueError, "identity"):
            self.resolve()
        self.path.unlink()
        with tempfile.TemporaryDirectory() as external:
            other = Path(external) / "other.json"
            other.write_text(_monitoring_window_json.dumps(self.report))
            self.path.symlink_to(other)
            with self.assertRaisesRegex(ValueError, "outside"):
                self.resolve()
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.resolve(replay_id="../../other")

    def test_legacy_uci_report_has_explicit_inferred_identity(self):
        known_hash = "e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f"
        self.report.pop("data_profile_id")
        self.report.pop("target_contract_id")
        self.report["data_sha256"] = known_hash
        self.path.write_text(_monitoring_window_json.dumps(self.report))
        _, _, source = self.resolve(data_sha256=known_hash,
                                    target_contract_id="uci-bike-sharing-cnt-dataset-local-hour")
        self.assertEqual(source["profile_identity_source"], "legacy_uci_dataset_hash_inference")
        with self.assertRaisesRegex(ValueError, "profile and target"):
            self.resolve(data_sha256=known_hash, profile_id="capital-bikeshare")

    def test_monitor_command_queries_and_reports_inherited_period(self):
        frame = pd.DataFrame({"timestamp": pd.to_datetime(["2011-01-01", "2012-10-01"]), "cnt": [1, 2]})
        prediction = {"target_hour": "2012-10-01 00:00:00", "data_version_sha256": stable_hash({"lag_24": None, "lag_168": None})}
        database = Mock()
        database.sqlite = True
        database.list_predictions.return_value = [prediction]
        database.session.return_value = nullcontext(object())
        database.transaction.return_value = nullcontext(object())
        database.execute.return_value.fetchall.return_value = [{"data_hash": "a" * 64}]
        registry = Mock()
        registry.get_model_version.return_value.tags = {}
        windows = pd.DataFrame([{"window_end": "2012-10-01 00:00:00", "prediction_count": 1,
                                 "scored_count": 0, "label_coverage": 0.0, "mae": None, "rmse": None,
                                 "psi": {}, "feature_missing_rates": {}}])
        args = SimpleNamespace(csv=Path("dummy.csv"), replay_id=self.args["replay_id"], model_version="3",
                               start=None, end=None, window_hours=168, minimum_labels=24, output=self.root / "out.csv")
        with (patch.object(cli, "REPORT_DIR", self.root), patch.object(cli, "DATA_PROFILE_ID", self.args["profile_id"]),
              patch.object(cli, "TARGET_CONTRACT_ID", self.args["target_contract_id"]),
              patch.object(cli, "DATA_PROFILE", {"monitor_reference": {"start_inclusive": "2011-01-01", "end_exclusive": "2012-01-01"}}),
              patch.object(cli, "load_hourly", return_value=frame), patch.object(cli, "sha256_file", return_value="a" * 64),
              patch.object(cli, "_database", return_value=database), patch.object(cli, "_model_registry_client", return_value=registry),
              patch.object(cli, "available_observation_frame", return_value=frame),
              patch.object(cli, "feature_frame_for_dataset", return_value=frame),
              patch.object(cli, "score_monitor_windows", return_value=windows) as score, patch.object(cli, "_print")):
            cli.command_monitor(args)
        self.assertEqual(database.list_predictions.call_args.args[:2], ("2012-10-01 00:00:00", "2013-01-01 00:00:00"))
        self.assertEqual(score.call_args.args[3], pd.Timestamp("2012-10-01"))
        report = _monitoring_window_json.loads((self.root / "out.json").read_text())
        self.assertEqual(report["monitor_period"]["source"], "replay_report")


# ========================================================================
# test_monitor_persistence
# ========================================================================

import hashlib
import json as _monitor_persistence_json
import tempfile as _monitor_persistence_tempfile
import unittest as _monitor_persistence_unittest
from pathlib import Path as _monitor_persistence_Path
from unittest.mock import patch as _monitor_persistence_patch

import pandas as _monitor_persistence_pd

from bike_mlops.db import Database
from bike_mlops import monitoring as store


class MonitorPersistenceTests(_monitor_persistence_unittest.TestCase):
    def setUp(self):
        self.tmp = _monitor_persistence_tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = _monitor_persistence_Path(self.tmp.name)
        self.database = Database(f"sqlite:///{self.root / 'ops.sqlite'}")
        self.database.initialize()
        self.run_a = "3ae0909d-7ae1-4a09-a756-96a7ed582edf"
        self.run_b = "ba4c92da-3fd6-41d4-b3d9-c614d74f3e66"
        self.windows = _monitor_persistence_pd.DataFrame([
            {"window_end": f"2012-10-01 {hour:02d}:00:00", "prediction_count": 2, "scored_count": 1,
             "label_coverage": 0.5, "mae": 5.0, "rmse": 5.0, "psi": {"hour": 0.1},
             "feature_missing_rates": {"lag_24": 0.0}}
            for hour in (0, 1)
        ])
        self.report = {"replay_id": "historical-replay", "model_version": "3", "window_count": 2}

    def rows(self):
        with self.database.session() as connection:
            return [dict(row) for row in self.database.execute(connection, "SELECT * FROM monitor_windows ORDER BY window_id").fetchall()]

    def persist(self, name, run_id, **options):
        return store.persist_monitor_run(self.database, options.get("windows", self.windows), self.root / name,
                                         self.report, run_id=run_id)

    def test_separate_runs_keep_old_rows_and_each_other(self):
        with self.database.transaction(immediate=True) as conn:
            self.database.execute(conn, "INSERT INTO monitor_windows VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("historical-replay:v3:2012-10-01 00:00:00", "historical-replay", "2012-10-01 00:00:00",
                 99, 3, 0.1, 1.0, 2.0, "{}", "{}", "old"))
        original = self.rows()[0]
        one = self.persist("run-a.csv", self.run_a)
        two = self.persist("run-b.csv", self.run_b)
        rows = self.rows()
        self.assertIn(original, rows)
        self.assertEqual(len(rows), 5)
        self.assertEqual(len([r for r in rows if r["window_id"].startswith(f"monitor:{self.run_a}:")]), 2)
        self.assertEqual(len([r for r in rows if r["window_id"].startswith(f"monitor:{self.run_b}:")]), 2)
        for result in (one, two):
            summary = result["summary_path"].read_bytes()
            csv = result["report_path"].read_bytes()
            marker = _monitor_persistence_json.loads(result["completion_marker_path"].read_text())
            self.assertEqual(marker["status"], "database_commit_succeeded")
            self.assertEqual(marker["summary_sha256"], hashlib.sha256(summary).hexdigest())
            self.assertEqual(marker["csv_sha256"], hashlib.sha256(csv).hexdigest())
            self.assertEqual(_monitor_persistence_json.loads(summary)["monitor_run_id"], result["monitor_run_id"])

    def test_existing_csv_json_marker_or_symlink_are_never_overwritten(self):
        for existing in ("occupied.csv", "occupied.json", "occupied.complete.json"):
            with self.subTest(existing=existing):
                path = self.root / existing
                path.write_bytes(b"original")
                with self.assertRaises(FileExistsError):
                    self.persist("occupied.csv", self.run_a)
                self.assertEqual(path.read_bytes(), b"original")
                self.assertFalse(self.rows())
                path.unlink()
        (self.root / "occupied.csv").symlink_to(self.root / "missing")
        with self.assertRaises(FileExistsError):
            self.persist("occupied.csv", self.run_a)
        self.assertTrue((self.root / "occupied.csv").is_symlink())

    def test_db_failure_rolls_back_and_leaves_unconfirmed_file_pair(self):
        original_execute = self.database.execute
        calls = 0
        def fail_second_insert(connection, sql, params=()):
            nonlocal calls
            if sql.startswith("INSERT INTO monitor_windows"):
                calls += 1
                if calls == 2:
                    raise RuntimeError("synthetic DB failure")
            return original_execute(connection, sql, params)
        with _monitor_persistence_patch.object(self.database, "execute", side_effect=fail_second_insert):
            with self.assertRaisesRegex(store.MonitorPersistenceError, "failed during database"):
                self.persist("failed.csv", self.run_a)
        self.assertEqual(self.rows(), [])
        self.assertTrue((self.root / "failed.csv").is_file())
        summary = _monitor_persistence_json.loads((self.root / "failed.json").read_text())
        self.assertEqual(summary["monitor_run_id"], self.run_a)
        self.assertEqual(summary["persistence_status"], "database_commit_unconfirmed_without_completion_marker")
        self.assertFalse((self.root / "failed.complete.json").exists())

    def test_postcommit_marker_failure_is_explicit_and_does_not_delete_db_rows(self):
        writer = store._write_new_file
        def fail_marker(path, body):
            if path.name.endswith(".complete.json"):
                raise OSError("synthetic marker failure")
            writer(path, body)
        with _monitor_persistence_patch.object(store, "_write_new_file", side_effect=fail_marker):
            with self.assertRaisesRegex(store.MonitorPersistenceError, "failed during completion_marker"):
                self.persist("missing-marker.csv", self.run_a)
        self.assertEqual(len(self.rows()), 2)
        self.assertFalse((self.root / "missing-marker.complete.json").exists())
        self.assertEqual(_monitor_persistence_json.loads((self.root / "missing-marker.json").read_text())["monitor_run_id"], self.run_a)

    def test_report_creation_failure_keeps_orphan_and_never_touches_db(self):
        writer = store._write_new_file
        def fail_summary(path, body):
            if path.name == "partial.json":
                raise OSError("synthetic summary failure")
            writer(path, body)
        with _monitor_persistence_patch.object(store, "_write_new_file", side_effect=fail_summary):
            with self.assertRaisesRegex(store.MonitorPersistenceError, "failed during report_files"):
                self.persist("partial.csv", self.run_a)
        self.assertTrue((self.root / "partial.csv").exists())
        self.assertEqual(_monitor_persistence_pd.read_csv(self.root / "partial.csv")["monitor_run_id"].tolist(), [self.run_a, self.run_a])
        self.assertFalse((self.root / "partial.json").exists())
        self.assertFalse(self.rows())

    def test_duplicate_ids_roll_back_without_changing_previous_run(self):
        self.persist("one.csv", self.run_a)
        original = self.rows()
        with self.assertRaisesRegex(store.MonitorPersistenceError, "failed during database"):
            self.persist("two.csv", self.run_a)
        self.assertEqual(self.rows(), original)
        self.assertFalse((self.root / "two.complete.json").exists())

    def test_empty_duplicate_window_or_bad_output_fail_before_files(self):
        for invalid in (self.windows.iloc[:0], _monitor_persistence_pd.concat([self.windows, self.windows.iloc[:1]])):
            with self.assertRaisesRegex(ValueError, "unique window"):
                self.persist("empty.csv", self.run_a, windows=invalid)
            self.assertFalse((self.root / "empty.csv").exists())
        with self.assertRaisesRegex(ValueError, ".csv extension"):
            self.persist("bad.json", self.run_a)


# ========================================================================
# test_training_monitor_reference
# ========================================================================

import copy as _training_monitor_reference_copy
import ast
import hashlib as _training_monitor_reference_hashlib
import json as _training_monitor_reference_json
import tempfile as _training_monitor_reference_tempfile
import unittest as _training_monitor_reference_unittest
from contextlib import nullcontext as _training_monitor_reference_nullcontext
from pathlib import Path as _training_monitor_reference_Path
from types import SimpleNamespace as _training_monitor_reference_SimpleNamespace
from unittest.mock import patch as _training_monitor_reference_patch

import numpy as _training_monitor_reference_np
import pandas as _training_monitor_reference_pd

from support import fixture
from bike_mlops import cli as _training_monitor_reference_cli
from bike_mlops import monitoring as refs
from bike_mlops.evaluation import candidate_identity, persist_candidate_bundle, sha256_file, source_tree_inventory
from bike_mlops.deployment import canonical_bytes, digest, strict_json
from bike_mlops.db import Database as _training_monitor_reference_Database, canonical_time, stable_hash as _training_monitor_reference_stable_hash
from bike_mlops.data import FEATURE_COLUMNS, available_observation_frame, feature_frame_for_dataset
from bike_mlops.monitoring import population_stability_index as _training_monitor_reference_population_stability_index, score_monitor_windows
from bike_mlops.monitoring import PSI_ALGORITHM_V2 as _training_monitor_reference_PSI_ALGORITHM_V2, make_psi_reference as _training_monitor_reference_make_psi_reference


class Registry:
    def __init__(self, tags, selection, reference):
        self.tags = dict(tags)
        self.bodies = {"evidence/candidate-report.json": canonical_bytes(selection),
                       refs.REFERENCE_PATH: canonical_bytes(reference)}
        self.after_download = None

    def get_model_version(self, name, version):
        return _training_monitor_reference_SimpleNamespace(name=name, version=str(version), status="READY", run_id="synthetic-run",
                               tags=dict(self.tags))

    def download_artifacts(self, run_id, path, dst_path):
        result = _training_monitor_reference_Path(dst_path) / _training_monitor_reference_Path(path).name
        result.write_bytes(self.bodies[path])
        if self.after_download:
            self.after_download()
        return str(result)


class TrainingMonitorReferenceTests(_training_monitor_reference_unittest.TestCase):
    def setUp(self):
        self.temp = _training_monitor_reference_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = _training_monitor_reference_Path(self.temp.name)
        self.frame = _training_monitor_reference_pd.DataFrame({"timestamp": _training_monitor_reference_pd.date_range("2024-01-01", periods=480, freq="h"), "cnt": 10})
        self.csv = self.root / "hourly.csv"
        _training_monitor_reference_pd.DataFrame({"dteday": self.frame.timestamp.dt.strftime("%Y-%m-%d"),
                      "hr": self.frame.timestamp.dt.hour, "cnt": self.frame.cnt}).to_csv(self.csv, index=False)
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(_training_monitor_reference_json.dumps({"synthetic": True, "data_sha256": sha256_file(self.csv)}))
        _, bundle, _ = fixture()
        self.selection = strict_json(bundle["reports"]["candidate"]["content"])
        self.selection.update(data_sha256=sha256_file(self.csv), feature_columns=list(FEATURE_COLUMNS))
        self.selection["data_source"]["source_manifest_sha256"] = sha256_file(self.manifest)
        self.mask = self.frame.timestamp + _training_monitor_reference_pd.Timedelta(hours=3) <= _training_monitor_reference_pd.Timestamp(self.selection["training_cutoff"])
        self.train = self.frame.loc[self.mask].reset_index(drop=True)
        self.features = feature_frame_for_dataset(self.frame).loc[self.mask].reset_index(drop=True)
        self.selection["split_counts"]["train"] = len(self.train)
        self.selection["training_row_keys_sha256"] = refs.row_keys_sha256(self.train)
        self.reference = refs.make_training_reference(self.selection, self.train, self.features)
        self.refresh()

    def refresh(self):
        body = canonical_bytes(self.reference)
        self.selection["monitor_reference"] = refs.reference_descriptor(self.reference, body)
        ci = candidate_identity(self.selection)
        self.selection["candidate_identity"] = ci
        cid = _training_monitor_reference_hashlib.sha256(_training_monitor_reference_json.dumps(ci, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.selection["candidate_id"] = cid
        self.selection["candidate_bundle"] = f"data/candidates/{cid}"
        self.tags = {key: self.selection[key] for key in (
            "data_sha256", "data_profile_id", "target_contract_id", "feature_contract_version",
            "evaluation_protocol_sha256", "candidate_id")}
        self.tags.update(candidate_model_sha256=self.selection["candidate_artifact"]["sha256"],
                         candidate_report_sha256=_training_monitor_reference_hashlib.sha256(canonical_bytes(self.selection)).hexdigest(),
                         artifact_sha256="1"*64, **refs.reference_registry_tags(self.selection))
        self.registry = Registry(self.tags, self.selection, self.reference)

    def registered(self):
        return refs.registered_training_reference(self.registry, "7", data_sha256=self.tags["data_sha256"],
                                                   manifest_path=self.manifest, frame=self.frame)

    def test_training_rows_exclude_unavailable_last_two_hours_and_later_data_never_changes_bins(self):
        self.assertEqual(self.reference["training_window"]["end_exclusive"], "2024-01-14 22:00:00")
        self.assertEqual(self.reference["training_window"]["rows"], 334)
        poisoned = self.frame.copy()
        poisoned.loc[~self.mask, "cnt"] = 999999
        refs.validate_training_reference(self.reference, self.selection, frame=poisoned)
        frozen, selection, tags = self.registered()
        self.assertEqual(frozen, self.reference)
        self.assertEqual(tags["candidate_id"], selection["candidate_id"])

    def test_protocol_policy_is_copied_frozen_and_required_before_fit_or_ledger_consumption(self):
        from bike_mlops import training
        original = refs.frozen_monitor_policy()
        changed = refs.frozen_monitor_policy()
        changed["policy"]["features"].append("other")
        self.assertEqual(refs.frozen_monitor_policy(), original)
        for policy in (None, changed):
            protocol = {"incumbent": None}
            if policy is not None:
                protocol["monitoring_reference_policy"] = policy
            with (_training_monitor_reference_patch.object(training,"DATA_PROFILE_ID","capital-bikeshare"),
                  _training_monitor_reference_patch.object(training,"read_protocol",return_value=protocol),
                  _training_monitor_reference_patch.object(training,"begin_evaluation") as begin,
                  _training_monitor_reference_patch.object(training,"build_estimator") as estimator):
                with self.assertRaisesRegex(ValueError,"frozen training monitoring policy"):
                    training.train_candidate(register=False, evaluation_protocol_path=self.root/'synthetic.json')
                begin.assert_not_called()
                estimator.assert_not_called()

    def test_missing_reordered_and_unavailable_training_rows_are_rejected(self):
        for train, features in ((self.train.iloc[:-1], self.features.iloc[:-1]),
                                (self.train.iloc[::-1], self.features.iloc[::-1]),
                                (self.frame.iloc[:336], feature_frame_for_dataset(self.frame).iloc[:336])):
            with self.subTest(rows=len(train)):
                with self.assertRaises(ValueError):
                    refs.make_training_reference(self.selection, train, features)

    def test_candidate_identity_binds_reference_and_preserves_legacy_identity(self):
        self.assertIn("monitor_reference", candidate_identity(self.selection))
        changed = _training_monitor_reference_copy.deepcopy(self.selection)
        changed["monitor_reference"]["sha256"] = "9"*64
        self.assertNotEqual(candidate_identity(changed), candidate_identity(self.selection))
        changed.pop("monitor_reference")
        self.assertNotIn("monitor_reference", candidate_identity(changed))
        with self.assertRaisesRegex(ValueError, "no frozen"):
            refs.read_candidate_reference(changed, self.root)

    def test_rehashed_bins_from_another_distribution_are_rejected_by_training_reconstruction(self):
        component = self.reference["features"]["lag_24"]
        self.reference["features"]["lag_24"] = _training_monitor_reference_make_psi_reference(
            [11]*component["input_counts"]["finite"]+[None]*component["input_counts"]["missing"],
            version=_training_monitor_reference_PSI_ALGORITHM_V2, feature_name="lag_24", reference_id=component["reference_id"],
            minimum_count=24)
        self.reference["reference_sha256"] = digest({k:v for k,v in self.reference.items() if k!="reference_sha256"})
        self.refresh()
        with self.assertRaisesRegex(ValueError, "original training rows"):
            self.registered()

    def test_changed_profile_feature_model_manifest_and_file_hash_fail_closed(self):
        for key in ("data_profile_id", "feature_contract_version", "candidate_model_sha256", "data_sha256"):
            with self.subTest(key=key):
                tags = self.registry.tags
                self.registry.tags = dict(tags, **{key: "9"*64})
                with self.assertRaises(ValueError):
                    self.registered()
                self.registry.tags = tags
        self.registry.bodies[refs.REFERENCE_PATH] += b" "
        with self.assertRaisesRegex(ValueError, "descriptor"):
            self.registered()
        self.refresh()
        self.manifest.write_text('{}')
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.registered()

    def test_registry_mutation_during_download_and_missing_artifact_are_rejected(self):
        self.registry.after_download = lambda: self.registry.tags.update(artifact_sha256="2"*64)
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.registered()
        self.refresh()
        del self.registry.bodies[refs.REFERENCE_PATH]
        with self.assertRaises(KeyError):
            self.registered()

    def test_reference_is_written_atomically_with_candidate_and_missing_training_rows_are_refused(self):
        from sklearn.dummy import DummyRegressor
        model = DummyRegressor().fit(self.features, self.train.cnt)
        (self.root / "README.md").write_text("synthetic candidate source\n")
        source_sha, count = source_tree_inventory(self.root)
        report = _training_monitor_reference_copy.deepcopy(self.selection)
        report.pop("monitor_reference")
        report["source_revision"].update(source_tree_sha256=source_sha, source_file_count=count)
        with _training_monitor_reference_patch('bike_mlops.evaluation.PROJECT_ROOT', self.root):
            with self.assertRaisesRegex(ValueError, "exact training rows"):
                persist_candidate_bundle(model, report, _training_monitor_reference_pd.DataFrame({"timestamp": []}))
            result = persist_candidate_bundle(model, report, _training_monitor_reference_pd.DataFrame({"timestamp": []}),
                                               training_frame=self.train, training_features=self.features)
        path = self.root / result["candidate_bundle"]
        frozen = _training_monitor_reference_json.loads((path / "candidate-report.json").read_text())
        reference = refs.read_candidate_reference(frozen, path, frame=self.frame)
        self.assertEqual(frozen["monitor_reference"]["sha256"], sha256_file(path / "monitor-reference.json"))
        self.assertEqual(reference["identity"]["candidate_model_sha256"], frozen["candidate_artifact"]["sha256"])
        self.assertFalse(list(path.parent.glob('.candidate-build-*')))

    def score(self, values, validation_mae=None):
        times = _training_monitor_reference_pd.date_range("2024-01-18", periods=len(values), freq="h")
        predictions = _training_monitor_reference_pd.DataFrame({"target_hour": times, "predicted": 10.,
            "features_json": [_training_monitor_reference_json.dumps({"lag_24":v, "lag_168":10, "hour":i%24}) for i,v in enumerate(values)]})
        labels = _training_monitor_reference_pd.DataFrame({"timestamp": times, "cnt": 10,
                               "available_at": times+_training_monitor_reference_pd.Timedelta(hours=3)})
        clock = times[-1]+_training_monitor_reference_pd.Timedelta(hours=3)
        return score_monitor_windows(predictions, labels, _training_monitor_reference_pd.DataFrame(), clock, clock+_training_monitor_reference_pd.Timedelta(hours=1),
            minimum_labels=1, validation_mae=validation_mae, psi_version=_training_monitor_reference_PSI_ALGORITHM_V2,
            frozen_reference=self.reference).iloc[-1]

    def test_constant_shift_is_detected_without_changing_legacy_uci_psi(self):
        self.assertEqual(_training_monitor_reference_population_stability_index([10]*24, [20]*24), 0.)
        result = self.score([20]*24, 1.)
        self.assertGreater(result.psi["lag_24"], .2)
        self.assertEqual(result.distribution_status, "investigate_distribution_change")
        self.assertEqual(result.quality_status, "within_review_threshold")

    def test_small_nonfinite_missing_and_unset_performance_reference_stay_explicit(self):
        self.assertEqual(self.score([10]).psi_details["lag_24"]["status"], "insufficient_current_data")
        self.assertEqual(self.score([float('inf')]*24).distribution_status, "invalid_values")
        missing = self.score([None]*24)
        self.assertEqual(missing.psi_details["lag_24"]["status"], "no_current_data")
        self.assertEqual(missing.feature_missing_rates["lag_24"], 1.)
        result = self.score([10]*24)
        self.assertEqual(result.quality_status, "performance_reference_unset")
        self.assertEqual(result.psi_algorithm, _training_monitor_reference_PSI_ALGORITHM_V2)

    def test_small_training_reference_is_unavailable_instead_of_normal(self):
        selection = _training_monitor_reference_copy.deepcopy(self.selection)
        train = self.train.iloc[:2]
        selection["split_counts"]["train"] = 2
        selection["training_row_keys_sha256"] = refs.row_keys_sha256(train)
        self.reference = refs.make_training_reference(selection, train, self.features.iloc[:2])
        result = self.score([10]*24)
        self.assertEqual(result.distribution_status, "reference_unavailable")
        self.assertEqual(result.psi_details["hour"]["reference_status"], "insufficient_reference")
        self.assertEqual(result.psi_details["lag_24"]["reference_status"], "no_reference")

    def replace_source_frame(self, frame):
        """Rebuild the synthetic registered identity after a post-training source change."""
        training_rows_sha = self.selection["training_row_keys_sha256"]
        training_features_sha = self.reference["training_features_sha256"]
        self.frame = frame.reset_index(drop=True)
        _training_monitor_reference_pd.DataFrame({"dteday": self.frame.timestamp.dt.strftime("%Y-%m-%d"),
                      "hr": self.frame.timestamp.dt.hour, "cnt": self.frame.cnt}).to_csv(self.csv, index=False)
        self.manifest.write_text(_training_monitor_reference_json.dumps({"synthetic": True, "data_sha256": sha256_file(self.csv)}))
        self.mask = self.frame.timestamp + _training_monitor_reference_pd.Timedelta(hours=3) <= _training_monitor_reference_pd.Timestamp(self.selection["training_cutoff"])
        self.train = self.frame.loc[self.mask].reset_index(drop=True)
        self.features = feature_frame_for_dataset(self.frame).loc[self.mask].reset_index(drop=True)
        self.selection.update(data_sha256=sha256_file(self.csv), feature_columns=list(FEATURE_COLUMNS))
        self.selection["data_source"]["source_manifest_sha256"] = sha256_file(self.manifest)
        self.selection["split_counts"]["train"] = len(self.train)
        self.selection["training_row_keys_sha256"] = refs.row_keys_sha256(self.train)
        self.reference = refs.make_training_reference(self.selection, self.train, self.features)
        self.assertEqual(self.selection["training_row_keys_sha256"], training_rows_sha)
        self.assertEqual(self.reference["training_features_sha256"], training_features_sha)
        self.refresh()

    def setup_cli(self, *, prediction=11.0, end="2024-01-19 03:00:00"):
        self.database = _training_monitor_reference_Database(f"sqlite:///{self.root / 'ops.db'}", data_profile_id="capital-bikeshare",
                                 target_contract_id=refs.PROFILE["target_contract_id"])
        self.database.initialize()
        self.database.add_observations(available_observation_frame(self.frame), self.tags["data_sha256"])
        features = feature_frame_for_dataset(self.frame)
        source_counts = dict(zip(self.frame.timestamp, self.frame.cnt))
        target_times = _training_monitor_reference_pd.date_range("2024-01-18", periods=24, freq="h")
        for pos, target in enumerate(target_times):
            sources = {}
            for lag in (24, 168):
                source_timestamp = target - _training_monitor_reference_pd.Timedelta(hours=lag)
                count = source_counts.get(source_timestamp)
                sources[f"lag_{lag}"] = ({"timestamp":canonical_time(source_timestamp),
                    "cnt":int(count),"data_hash":self.tags["data_sha256"]} if count is not None else None)
            frame_pos = int(self.frame.index[self.frame.timestamp.eq(target)][0])
            feature_values = {name: None if _training_monitor_reference_pd.isna(value) else float(value)
                              for name, value in features.iloc[frame_pos].items()}
            self.database.save_prediction({**self.tags,"model_name":"bike-demand-capital", "model_version":"7",
                "model_quality_gate":"passed", "request_id":f"replay:synthetic:v7:{pos}", "input_hash":str(pos),
                "target_hour":canonical_time(target), "as_of":canonical_time(target), "predicted":prediction,
                "data_version_sha256":_training_monitor_reference_stable_hash(sources), "features_json":_training_monitor_reference_json.dumps(feature_values, allow_nan=False),
                "created_at":"2024-02-01T00:00:00+00:00"})
        args = _training_monitor_reference_cli.build_parser().parse_args(["monitor", "--csv",str(self.csv), "--manifest",str(self.manifest),
            "--replay-id","synthetic", "--model-version","7", "--psi-version",_training_monitor_reference_PSI_ALGORITHM_V2,
            "--start","2024-01-18", "--end",end, "--output",str(self.root/'monitor.csv')])
        return args

    def run_cli(self, args, *, availability_shift_hours=0):
        availability_patch = _training_monitor_reference_nullcontext()
        if availability_shift_hours:
            observations = available_observation_frame(self.frame)
            # Delay the 24 monitored labels only; keep the historical lag sources
            # available when the already-persisted predictions were produced.
            monitored = observations.timestamp.between("2024-01-18", "2024-01-19", inclusive="left")
            self.assertEqual(int(monitored.sum()), 24)
            observations.loc[monitored, "available_at"] += _training_monitor_reference_pd.Timedelta(hours=availability_shift_hours)
            availability_patch = _training_monitor_reference_patch.object(_training_monitor_reference_cli, "available_observation_frame", return_value=observations)
        with (_training_monitor_reference_patch.object(_training_monitor_reference_cli,"DATA_PROFILE_ID","capital-bikeshare"),
              _training_monitor_reference_patch.object(_training_monitor_reference_cli,"MODEL_NAME","bike-demand-capital"),
              _training_monitor_reference_patch.object(_training_monitor_reference_cli,"TARGET_CONTRACT_ID", refs.PROFILE["target_contract_id"]),
              _training_monitor_reference_patch.object(_training_monitor_reference_cli,"REPORT_DIR",self.root), _training_monitor_reference_patch.object(_training_monitor_reference_cli,"_database",return_value=self.database),
              _training_monitor_reference_patch.object(_training_monitor_reference_cli,"_model_registry_client",return_value=self.registry), _training_monitor_reference_patch.object(_training_monitor_reference_cli,"_print"),
              availability_patch):
            args.func(args)

    def test_real_cli_scores_registered_reference_and_persists_versioned_summary_and_db_windows(self):
        args = self.setup_cli()
        self.run_cli(args)
        summary = _training_monitor_reference_json.loads((self.root/'monitor.json').read_text())
        self.assertTrue((self.root/'monitor.complete.json').is_file())
        self.assertEqual(summary["reference"]["reference_sha256"], self.reference["reference_sha256"])
        self.assertEqual(summary["latest_window"]["psi_algorithm"], _training_monitor_reference_PSI_ALGORITHM_V2)
        self.assertEqual(summary["latest_window"]["scored_count"], 24)
        with self.database.session() as connection:
            count=self.database.execute(connection,"SELECT count(*) AS n FROM monitor_windows").fetchone()["n"]
        self.assertEqual(count,27)

    def test_cli_refuses_missing_explicit_algorithm_foreign_prediction_and_changed_manifest_before_report(self):
        args = self.setup_cli()
        args.psi_version = None
        with self.assertRaisesRegex(ValueError,"requires --psi-version"):
            self.run_cli(args)
        args.psi_version = _training_monitor_reference_PSI_ALGORITHM_V2
        with self.database.transaction() as connection:
            self.database.execute(connection, "UPDATE predictions SET artifact_sha256=?", ("9"*64,))
        with self.assertRaisesRegex(ValueError,"registered identity"):
            self.run_cli(args)
        self.assertFalse((self.root/'monitor.csv').exists())
        self.manifest.write_text('{}')
        with self.assertRaisesRegex(ValueError,"manifest"):
            self.run_cli(args)

    def test_cli_rejects_changed_feature_values_even_if_source_hash_still_matches(self):
        args = self.setup_cli()
        with self.database.transaction() as connection:
            record = self.database.execute(connection, "SELECT request_id,features_json FROM predictions LIMIT 1").fetchone()
            values = _training_monitor_reference_json.loads(record["features_json"])
            values["lag_24"] = 999
            self.database.execute(connection, "UPDATE predictions SET features_json=? WHERE request_id=?",
                                  (_training_monitor_reference_json.dumps(values),record["request_id"]))
        with self.assertRaisesRegex(ValueError,"features differ"):
            self.run_cli(args)
        self.assertFalse((self.root/'monitor.csv').exists())

    def read_monitor_acceptance_outputs(self):
        csv_path = self.root / "monitor.csv"
        summary_path = self.root / "monitor.json"
        rows = _training_monitor_reference_pd.read_csv(csv_path)
        summary = _training_monitor_reference_json.loads(summary_path.read_text())
        marker = _training_monitor_reference_json.loads((self.root / "monitor.complete.json").read_text())
        run_id = summary["monitor_run_id"]
        self.assertEqual(marker["monitor_run_id"], run_id)
        self.assertEqual(marker["status"], "database_commit_succeeded")
        self.assertEqual(marker["csv_sha256"], sha256_file(csv_path))
        self.assertEqual(summary["csv_sha256"], sha256_file(csv_path))
        self.assertEqual(marker["summary_sha256"], sha256_file(summary_path))
        self.assertEqual(marker["window_count"], len(rows))
        self.assertEqual(summary["window_count"], len(rows))
        self.assertEqual(rows.monitor_run_id.tolist(), [run_id] * len(rows))
        expected_clocks = _training_monitor_reference_pd.date_range(summary["monitor_period"]["start_inclusive"],
                                       summary["monitor_period"]["end_exclusive"], freq="h", inclusive="left")
        self.assertEqual(rows.window_end.tolist(), [canonical_time(t) for t in expected_clocks])
        self.assertEqual(rows.window_start.tolist(), [canonical_time(t - _training_monitor_reference_pd.Timedelta(hours=168)) for t in expected_clocks])
        self.assertEqual(summary["reference"]["reference_sha256"], self.reference["reference_sha256"])
        self.assertFalse(summary["automatic_promotion"])
        with self.database.session() as connection:
            stored = [dict(row) for row in self.database.execute(connection,
                "SELECT * FROM monitor_windows ORDER BY window_end").fetchall()]
        self.assertEqual(len(stored), len(rows))
        for persisted, row in zip(stored, rows.to_dict("records")):
            self.assertEqual(persisted["window_id"], f"monitor:{run_id}:{row['window_end']}")
            self.assertEqual(persisted["replay_id"], summary["replay_id"])
            self.assertEqual(persisted["window_end"], row["window_end"])
            for name in ("prediction_count", "scored_count", "label_coverage", "mae", "rmse"):
                if _training_monitor_reference_pd.isna(row[name]):
                    self.assertIsNone(persisted[name], name)
                else:
                    self.assertAlmostEqual(persisted[name], row[name], msg=name)
            self.assertEqual(_training_monitor_reference_json.loads(persisted["psi_json"]), ast.literal_eval(row["psi"]))
            self.assertEqual(_training_monitor_reference_json.loads(persisted["missing_json"]), ast.literal_eval(row["feature_missing_rates"]))
        for name, expected in summary["latest_window"].items():
            observed = rows.iloc[-1][name]
            if isinstance(expected, dict):
                self.assertEqual(ast.literal_eval(observed), expected, name)
            elif expected is None:
                self.assertTrue(_training_monitor_reference_pd.isna(observed), name)
            elif isinstance(expected, (int, float)):
                self.assertAlmostEqual(observed, expected, msg=name)
            else:
                self.assertEqual(observed, expected, name)
        return rows, summary

    def assert_acceptance_timeline(self, rows, *, distribution, quality, delay=3):
        """Assert the frozen 24-request scenario, without recalculating PSI or MAE."""
        clocks = len(rows)
        self.assertEqual(rows.prediction_count.tolist(), list(range(1, min(clocks, 24) + 1)) + [24] * max(0, clocks - 24))
        self.assertEqual(rows.scored_count.tolist(), [0] * min(delay, clocks)
                         + list(range(1, min(24, max(0, clocks - delay)) + 1))
                         + [24] * max(0, clocks - delay - 24))
        self.assertEqual(rows.distribution_status.tolist(), ["insufficient_data"] * min(23, clocks)
                         + [distribution] * max(0, clocks - 23))
        self.assertEqual(rows.quality_status.tolist(), ["insufficient_labels"] * min(delay + 23, clocks)
                         + [quality] * max(0, clocks - delay - 23))

    def test_monitor_cli_acceptance_distribution_change_only_and_detection_hour(self):
        changed = self.frame.copy()
        changed.loc[changed.timestamp.between("2024-01-17", "2024-01-17 23:00:00"), "cnt"] = 20
        self.replace_source_frame(changed)
        args = self.setup_cli(prediction=10.)
        self.run_cli(args)
        rows, summary = self.read_monitor_acceptance_outputs()
        self.assert_acceptance_timeline(rows, distribution="investigate_distribution_change", quality="within_review_threshold")
        first = rows.loc[rows.distribution_status.eq("investigate_distribution_change"), "window_end"].iloc[0]
        self.assertEqual(len(rows), 27)
        self.assertEqual(first, "2024-01-18 23:00:00")
        self.assertEqual(summary["latest_window"]["quality_status"], "within_review_threshold")
        self.assertEqual(summary["latest_window"]["scored_count"], 24)

    def test_monitor_cli_acceptance_error_change_only_and_detection_hour(self):
        args = self.setup_cli(prediction=12.)
        self.run_cli(args)
        rows, summary = self.read_monitor_acceptance_outputs()
        self.assert_acceptance_timeline(rows, distribution="no_threshold_crossing", quality="investigate_error_increase")
        first = rows.loc[rows.quality_status.eq("investigate_error_increase"), "window_end"].iloc[0]
        self.assertEqual(first, "2024-01-19 02:00:00")
        self.assertEqual(summary["latest_window"]["distribution_status"], "no_threshold_crossing")
        self.assertEqual(summary["latest_window"]["mae"], 2.0)

    def test_monitor_cli_acceptance_records_distribution_and_error_as_independent_signals(self):
        changed = self.frame.copy()
        changed.loc[changed.timestamp.between("2024-01-17", "2024-01-17 23:00:00"), "cnt"] = 20
        self.replace_source_frame(changed)
        args = self.setup_cli(prediction=12.)
        self.run_cli(args)
        rows, summary = self.read_monitor_acceptance_outputs()
        self.assert_acceptance_timeline(rows, distribution="investigate_distribution_change", quality="investigate_error_increase")
        first_distribution = rows.loc[rows.distribution_status.eq("investigate_distribution_change"), "window_end"].iloc[0]
        first_error = rows.loc[rows.quality_status.eq("investigate_error_increase"), "window_end"].iloc[0]
        self.assertEqual(first_distribution, "2024-01-18 23:00:00")
        self.assertEqual(first_error, "2024-01-19 02:00:00")
        self.assertEqual(summary["latest_window"]["distribution_status"], "investigate_distribution_change")
        self.assertEqual(summary["latest_window"]["quality_status"], "investigate_error_increase")

    def test_monitor_cli_acceptance_delayed_labels_wait_until_their_availability_time(self):
        args = self.setup_cli(prediction=12., end="2024-01-19 06:00:00")
        self.run_cli(args, availability_shift_hours=3)
        rows, summary = self.read_monitor_acceptance_outputs()
        self.assert_acceptance_timeline(rows, distribution="no_threshold_crossing", quality="investigate_error_increase", delay=6)
        full = rows.loc[rows.scored_count.eq(24), "window_end"].iloc[0]
        first_error = rows.loc[rows.quality_status.eq("investigate_error_increase"), "window_end"].iloc[0]
        self.assertEqual(len(rows), 30)
        self.assertEqual(full, "2024-01-19 05:00:00")
        self.assertEqual(first_error, "2024-01-19 05:00:00")
        self.assertEqual(summary["latest_window"]["scored_count"], 24)

    def test_monitor_cli_acceptance_stopped_labels_remain_insufficient_not_normal(self):
        args = self.setup_cli(end="2024-01-19 03:00:00")
        self.run_cli(args, availability_shift_hours=69)
        rows, summary = self.read_monitor_acceptance_outputs()
        self.assert_acceptance_timeline(rows, distribution="no_threshold_crossing", quality="insufficient_labels", delay=72)
        self.assertEqual(len(rows), 27)
        self.assertEqual(summary["latest_window"]["scored_count"], 0)
        self.assertEqual(summary["latest_window"]["quality_status"], "insufficient_labels")
        self.assertIsNone(summary["latest_window"]["mae"])
        self.assertFalse(rows.quality_status.eq("within_review_threshold").any())

    def test_monitor_cli_acceptance_duplicate_timestamp_is_rejected_before_outputs(self):
        args = self.setup_cli()
        source = _training_monitor_reference_pd.read_csv(self.csv)
        duplicate = source.loc[source.dteday.eq("2024-01-18") & source.hr.eq(0)].copy()
        for count in (10, 999):
            with self.subTest(duplicate_count=count):
                duplicate["cnt"] = count
                _training_monitor_reference_pd.concat([source, duplicate], ignore_index=True).to_csv(self.csv, index=False)
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    self.run_cli(args)
                for name in ("monitor.csv", "monitor.json", "monitor.complete.json"):
                    self.assertFalse((self.root / name).exists())
                with self.database.session() as connection:
                    n = self.database.execute(connection, "SELECT count(*) AS n FROM monitor_windows").fetchone()["n"]
                self.assertEqual(n, 0)

    def test_monitor_cli_acceptance_constant_distribution_has_zero_psi(self):
        args = self.setup_cli(prediction=10.)
        self.run_cli(args)
        rows, summary = self.read_monitor_acceptance_outputs()
        self.assert_acceptance_timeline(rows, distribution="no_threshold_crossing", quality="within_review_threshold")
        detail = summary["latest_window"]["psi_details"]["lag_24"]
        self.assertEqual(self.reference["features"]["lag_24"]["mode"], "constant_three_way")
        self.assertEqual(detail["status"], "ready")
        self.assertEqual(detail["psi"], 0.0)
        self.assertEqual(summary["latest_window"]["distribution_status"], "no_threshold_crossing")

    def test_monitor_cli_acceptance_small_sample_preserves_insufficient_states(self):
        args = self.setup_cli(end="2024-01-18 23:00:00")
        self.run_cli(args)
        rows, summary = self.read_monitor_acceptance_outputs()
        self.assert_acceptance_timeline(rows, distribution="insufficient_data", quality="insufficient_labels")
        latest = summary["latest_window"]
        self.assertEqual(summary["window_count"], 23)
        self.assertEqual(latest["prediction_count"], 23)
        self.assertEqual(latest["scored_count"], 20)
        self.assertEqual(latest["quality_status"], "insufficient_labels")
        self.assertEqual(latest["psi_details"]["lag_24"]["status"], "insufficient_current_data")
        self.assertEqual(latest["distribution_status"], "insufficient_data")
        self.assertIsNone(latest["mae"])

    def test_monitor_cli_acceptance_missing_error_reference_is_explicit(self):
        self.selection["validation"]["model"]["mae"] = None
        self.refresh()
        args = self.setup_cli(prediction=10.)
        self.run_cli(args)
        rows, summary = self.read_monitor_acceptance_outputs()
        self.assert_acceptance_timeline(rows, distribution="no_threshold_crossing", quality="performance_reference_unset")
        self.assertEqual(summary["latest_window"]["scored_count"], 24)
        self.assertEqual(summary["latest_window"]["quality_status"], "performance_reference_unset")

    def test_monitor_cli_acceptance_missing_feature_increase_is_recorded_not_called_stable(self):
        changed = self.frame.loc[~self.frame.timestamp.between("2024-01-17", "2024-01-17 23:00:00")].copy()
        self.replace_source_frame(changed)
        args = self.setup_cli(prediction=10.)
        self.run_cli(args)
        rows, summary = self.read_monitor_acceptance_outputs()
        self.assert_acceptance_timeline(rows, distribution="insufficient_data", quality="within_review_threshold")
        latest = summary["latest_window"]
        self.assertEqual(latest["scored_count"], 24)
        self.assertEqual(latest["feature_missing_rates"]["lag_24"], 1.0)
        self.assertEqual(latest["psi_details"]["lag_24"]["current_input_counts"]["missing"], 24)
        self.assertEqual(latest["psi_details"]["lag_24"]["status"], "no_current_data")
        self.assertEqual(latest["distribution_status"], "insufficient_data")

    def test_monitor_cli_acceptance_error_threshold_equality_does_not_alert(self):
        args = self.setup_cli(prediction=11.25)
        self.run_cli(args)
        rows, summary = self.read_monitor_acceptance_outputs()
        self.assert_acceptance_timeline(rows, distribution="no_threshold_crossing", quality="within_review_threshold")
        self.assertEqual(summary["latest_window"]["mae"], 1.25)

    def test_monitor_cli_acceptance_168_hour_boundary_excludes_oldest_prediction(self):
        args = self.setup_cli(prediction=10., end="2024-01-25 01:00:00")
        self.run_cli(args)
        rows, summary = self.read_monitor_acceptance_outputs()
        before = rows.loc[rows.window_end.eq("2024-01-24 23:00:00")].iloc[0]
        boundary = summary["latest_window"]
        self.assertEqual(len(rows), 169)
        self.assertEqual((before.prediction_count, before.scored_count), (24, 24))
        self.assertEqual(before.distribution_status, "no_threshold_crossing")
        self.assertEqual(boundary["window_end"], "2024-01-25 00:00:00")
        self.assertEqual((boundary["prediction_count"], boundary["scored_count"]), (23, 23))
        self.assertEqual(boundary["distribution_status"], "insufficient_data")
        self.assertEqual(boundary["quality_status"], "insufficient_labels")


if __name__ == "__main__":
    import unittest as _unittest_entry
    _unittest_entry.main()
