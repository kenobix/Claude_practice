from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from bike_mlops import cli
from bike_mlops.cli import build_parser
from bike_mlops.db import stable_hash
from bike_mlops.monitoring_window import resolve_monitor_window


class MonitoringWindowTests(unittest.TestCase):
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
        self.path.write_text(json.dumps(self.report))

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
        self.path.write_text(json.dumps(self.report))
        with self.assertRaisesRegex(ValueError, "identity"):
            self.resolve()
        self.path.unlink()
        with tempfile.TemporaryDirectory() as external:
            other = Path(external) / "other.json"
            other.write_text(json.dumps(self.report))
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
        self.path.write_text(json.dumps(self.report))
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
        report = json.loads((self.root / "out.json").read_text())
        self.assertEqual(report["monitor_period"]["source"], "replay_report")


if __name__ == "__main__":
    unittest.main()
