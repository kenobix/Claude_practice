from __future__ import annotations

import copy
import ast
import hashlib
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests.capital_deployment_fixture import fixture
from bike_mlops import cli
from bike_mlops import training_monitor_reference as refs
from bike_mlops.candidate_artifacts import candidate_identity, persist_candidate_bundle, sha256_file, source_tree_inventory
from bike_mlops.capital_deployment_evidence import canonical_bytes, digest, strict_json
from bike_mlops.db import Database, canonical_time, stable_hash
from bike_mlops.features import FEATURE_COLUMNS, available_observation_frame, feature_frame_for_dataset
from bike_mlops.monitoring import population_stability_index, score_monitor_windows
from bike_mlops.monitoring_psi_v2 import PSI_ALGORITHM_V2, make_psi_reference


class Registry:
    def __init__(self, tags, selection, reference):
        self.tags = dict(tags)
        self.bodies = {"evidence/candidate-report.json": canonical_bytes(selection),
                       refs.REFERENCE_PATH: canonical_bytes(reference)}
        self.after_download = None

    def get_model_version(self, name, version):
        return SimpleNamespace(name=name, version=str(version), status="READY", run_id="synthetic-run",
                               tags=dict(self.tags))

    def download_artifacts(self, run_id, path, dst_path):
        result = Path(dst_path) / Path(path).name
        result.write_bytes(self.bodies[path])
        if self.after_download:
            self.after_download()
        return str(result)


class TrainingMonitorReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.frame = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=480, freq="h"), "cnt": 10})
        self.csv = self.root / "hourly.csv"
        pd.DataFrame({"dteday": self.frame.timestamp.dt.strftime("%Y-%m-%d"),
                      "hr": self.frame.timestamp.dt.hour, "cnt": self.frame.cnt}).to_csv(self.csv, index=False)
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"synthetic": True, "data_sha256": sha256_file(self.csv)}))
        _, bundle, _ = fixture()
        self.selection = strict_json(bundle["reports"]["candidate"]["content"])
        self.selection.update(data_sha256=sha256_file(self.csv), feature_columns=list(FEATURE_COLUMNS))
        self.selection["data_source"]["source_manifest_sha256"] = sha256_file(self.manifest)
        self.mask = self.frame.timestamp + pd.Timedelta(hours=3) <= pd.Timestamp(self.selection["training_cutoff"])
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
        cid = hashlib.sha256(json.dumps(ci, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.selection["candidate_id"] = cid
        self.selection["candidate_bundle"] = f"data/candidates/{cid}"
        self.tags = {key: self.selection[key] for key in (
            "data_sha256", "data_profile_id", "target_contract_id", "feature_contract_version",
            "evaluation_protocol_sha256", "candidate_id")}
        self.tags.update(candidate_model_sha256=self.selection["candidate_artifact"]["sha256"],
                         candidate_report_sha256=hashlib.sha256(canonical_bytes(self.selection)).hexdigest(),
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
            with (patch.object(training,"DATA_PROFILE_ID","capital-bikeshare"),
                  patch.object(training,"read_protocol",return_value=protocol),
                  patch.object(training,"begin_evaluation") as begin,
                  patch.object(training,"build_estimator") as estimator):
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
        changed = copy.deepcopy(self.selection)
        changed["monitor_reference"]["sha256"] = "9"*64
        self.assertNotEqual(candidate_identity(changed), candidate_identity(self.selection))
        changed.pop("monitor_reference")
        self.assertNotIn("monitor_reference", candidate_identity(changed))
        with self.assertRaisesRegex(ValueError, "no frozen"):
            refs.read_candidate_reference(changed, self.root)

    def test_rehashed_bins_from_another_distribution_are_rejected_by_training_reconstruction(self):
        component = self.reference["features"]["lag_24"]
        self.reference["features"]["lag_24"] = make_psi_reference(
            [11]*component["input_counts"]["finite"]+[None]*component["input_counts"]["missing"],
            version=PSI_ALGORITHM_V2, feature_name="lag_24", reference_id=component["reference_id"],
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
        report = copy.deepcopy(self.selection)
        report.pop("monitor_reference")
        report["source_revision"].update(source_tree_sha256=source_sha, source_file_count=count)
        with patch("bike_mlops.candidate_artifacts.PROJECT_ROOT", self.root):
            with self.assertRaisesRegex(ValueError, "exact training rows"):
                persist_candidate_bundle(model, report, pd.DataFrame({"timestamp": []}))
            result = persist_candidate_bundle(model, report, pd.DataFrame({"timestamp": []}),
                                               training_frame=self.train, training_features=self.features)
        path = self.root / result["candidate_bundle"]
        frozen = json.loads((path / "candidate-report.json").read_text())
        reference = refs.read_candidate_reference(frozen, path, frame=self.frame)
        self.assertEqual(frozen["monitor_reference"]["sha256"], sha256_file(path / "monitor-reference.json"))
        self.assertEqual(reference["identity"]["candidate_model_sha256"], frozen["candidate_artifact"]["sha256"])
        self.assertFalse(list(path.parent.glob('.candidate-build-*')))

    def score(self, values, validation_mae=None):
        times = pd.date_range("2024-01-18", periods=len(values), freq="h")
        predictions = pd.DataFrame({"target_hour": times, "predicted": 10.,
            "features_json": [json.dumps({"lag_24":v, "lag_168":10, "hour":i%24}) for i,v in enumerate(values)]})
        labels = pd.DataFrame({"timestamp": times, "cnt": 10,
                               "available_at": times+pd.Timedelta(hours=3)})
        clock = times[-1]+pd.Timedelta(hours=3)
        return score_monitor_windows(predictions, labels, pd.DataFrame(), clock, clock+pd.Timedelta(hours=1),
            minimum_labels=1, validation_mae=validation_mae, psi_version=PSI_ALGORITHM_V2,
            frozen_reference=self.reference).iloc[-1]

    def test_constant_shift_is_detected_without_changing_legacy_uci_psi(self):
        self.assertEqual(population_stability_index([10]*24, [20]*24), 0.)
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
        self.assertEqual(result.psi_algorithm, PSI_ALGORITHM_V2)

    def test_small_training_reference_is_unavailable_instead_of_normal(self):
        selection = copy.deepcopy(self.selection)
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
        pd.DataFrame({"dteday": self.frame.timestamp.dt.strftime("%Y-%m-%d"),
                      "hr": self.frame.timestamp.dt.hour, "cnt": self.frame.cnt}).to_csv(self.csv, index=False)
        self.manifest.write_text(json.dumps({"synthetic": True, "data_sha256": sha256_file(self.csv)}))
        self.mask = self.frame.timestamp + pd.Timedelta(hours=3) <= pd.Timestamp(self.selection["training_cutoff"])
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
        self.database = Database(f"sqlite:///{self.root / 'ops.db'}", data_profile_id="capital-bikeshare",
                                 target_contract_id=refs.PROFILE["target_contract_id"])
        self.database.initialize()
        self.database.add_observations(available_observation_frame(self.frame), self.tags["data_sha256"])
        features = feature_frame_for_dataset(self.frame)
        source_counts = dict(zip(self.frame.timestamp, self.frame.cnt))
        target_times = pd.date_range("2024-01-18", periods=24, freq="h")
        for pos, target in enumerate(target_times):
            sources = {}
            for lag in (24, 168):
                source_timestamp = target - pd.Timedelta(hours=lag)
                count = source_counts.get(source_timestamp)
                sources[f"lag_{lag}"] = ({"timestamp":canonical_time(source_timestamp),
                    "cnt":int(count),"data_hash":self.tags["data_sha256"]} if count is not None else None)
            frame_pos = int(self.frame.index[self.frame.timestamp.eq(target)][0])
            feature_values = {name: None if pd.isna(value) else float(value)
                              for name, value in features.iloc[frame_pos].items()}
            self.database.save_prediction({**self.tags,"model_name":"bike-demand-capital", "model_version":"7",
                "model_quality_gate":"passed", "request_id":f"replay:synthetic:v7:{pos}", "input_hash":str(pos),
                "target_hour":canonical_time(target), "as_of":canonical_time(target), "predicted":prediction,
                "data_version_sha256":stable_hash(sources), "features_json":json.dumps(feature_values, allow_nan=False),
                "created_at":"2024-02-01T00:00:00+00:00"})
        args = cli.build_parser().parse_args(["monitor", "--csv",str(self.csv), "--manifest",str(self.manifest),
            "--replay-id","synthetic", "--model-version","7", "--psi-version",PSI_ALGORITHM_V2,
            "--start","2024-01-18", "--end",end, "--output",str(self.root/'monitor.csv')])
        return args

    def run_cli(self, args, *, availability_shift_hours=0):
        availability_patch = nullcontext()
        if availability_shift_hours:
            observations = available_observation_frame(self.frame)
            # Delay the 24 monitored labels only; keep the historical lag sources
            # available when the already-persisted predictions were produced.
            monitored = observations.timestamp.between("2024-01-18", "2024-01-19", inclusive="left")
            self.assertEqual(int(monitored.sum()), 24)
            observations.loc[monitored, "available_at"] += pd.Timedelta(hours=availability_shift_hours)
            availability_patch = patch.object(cli, "available_observation_frame", return_value=observations)
        with (patch.object(cli,"DATA_PROFILE_ID","capital-bikeshare"),
              patch.object(cli,"MODEL_NAME","bike-demand-capital"),
              patch.object(cli,"TARGET_CONTRACT_ID", refs.PROFILE["target_contract_id"]),
              patch.object(cli,"REPORT_DIR",self.root), patch.object(cli,"_database",return_value=self.database),
              patch.object(cli,"_model_registry_client",return_value=self.registry), patch.object(cli,"_print"),
              availability_patch):
            args.func(args)

    def test_real_cli_scores_registered_reference_and_persists_versioned_summary_and_db_windows(self):
        args = self.setup_cli()
        self.run_cli(args)
        summary = json.loads((self.root/'monitor.json').read_text())
        self.assertTrue((self.root/'monitor.complete.json').is_file())
        self.assertEqual(summary["reference"]["reference_sha256"], self.reference["reference_sha256"])
        self.assertEqual(summary["latest_window"]["psi_algorithm"], PSI_ALGORITHM_V2)
        self.assertEqual(summary["latest_window"]["scored_count"], 24)
        with self.database.session() as connection:
            count=self.database.execute(connection,"SELECT count(*) AS n FROM monitor_windows").fetchone()["n"]
        self.assertEqual(count,27)

    def test_cli_refuses_missing_explicit_algorithm_foreign_prediction_and_changed_manifest_before_report(self):
        args = self.setup_cli()
        args.psi_version = None
        with self.assertRaisesRegex(ValueError,"requires --psi-version"):
            self.run_cli(args)
        args.psi_version = PSI_ALGORITHM_V2
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
            values = json.loads(record["features_json"])
            values["lag_24"] = 999
            self.database.execute(connection, "UPDATE predictions SET features_json=? WHERE request_id=?",
                                  (json.dumps(values),record["request_id"]))
        with self.assertRaisesRegex(ValueError,"features differ"):
            self.run_cli(args)
        self.assertFalse((self.root/'monitor.csv').exists())

    def read_monitor_acceptance_outputs(self):
        csv_path = self.root / "monitor.csv"
        summary_path = self.root / "monitor.json"
        rows = pd.read_csv(csv_path)
        summary = json.loads(summary_path.read_text())
        marker = json.loads((self.root / "monitor.complete.json").read_text())
        run_id = summary["monitor_run_id"]
        self.assertEqual(marker["monitor_run_id"], run_id)
        self.assertEqual(marker["status"], "database_commit_succeeded")
        self.assertEqual(marker["csv_sha256"], sha256_file(csv_path))
        self.assertEqual(summary["csv_sha256"], sha256_file(csv_path))
        self.assertEqual(marker["summary_sha256"], sha256_file(summary_path))
        self.assertEqual(marker["window_count"], len(rows))
        self.assertEqual(summary["window_count"], len(rows))
        self.assertEqual(rows.monitor_run_id.tolist(), [run_id] * len(rows))
        expected_clocks = pd.date_range(summary["monitor_period"]["start_inclusive"],
                                       summary["monitor_period"]["end_exclusive"], freq="h", inclusive="left")
        self.assertEqual(rows.window_end.tolist(), [canonical_time(t) for t in expected_clocks])
        self.assertEqual(rows.window_start.tolist(), [canonical_time(t - pd.Timedelta(hours=168)) for t in expected_clocks])
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
                if pd.isna(row[name]):
                    self.assertIsNone(persisted[name], name)
                else:
                    self.assertAlmostEqual(persisted[name], row[name], msg=name)
            self.assertEqual(json.loads(persisted["psi_json"]), ast.literal_eval(row["psi"]))
            self.assertEqual(json.loads(persisted["missing_json"]), ast.literal_eval(row["feature_missing_rates"]))
        for name, expected in summary["latest_window"].items():
            observed = rows.iloc[-1][name]
            if isinstance(expected, dict):
                self.assertEqual(ast.literal_eval(observed), expected, name)
            elif expected is None:
                self.assertTrue(pd.isna(observed), name)
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
        source = pd.read_csv(self.csv)
        duplicate = source.loc[source.dteday.eq("2024-01-18") & source.hr.eq(0)].copy()
        for count in (10, 999):
            with self.subTest(duplicate_count=count):
                duplicate["cnt"] = count
                pd.concat([source, duplicate], ignore_index=True).to_csv(self.csv, index=False)
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
    unittest.main()
