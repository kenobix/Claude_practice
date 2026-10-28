from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from bike_mlops.db import Database
from bike_mlops import monitor_persistence as store


class MonitorPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.database = Database(f"sqlite:///{self.root / 'ops.sqlite'}")
        self.database.initialize()
        self.run_a = "3ae0909d-7ae1-4a09-a756-96a7ed582edf"
        self.run_b = "ba4c92da-3fd6-41d4-b3d9-c614d74f3e66"
        self.windows = pd.DataFrame([
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
            marker = json.loads(result["completion_marker_path"].read_text())
            self.assertEqual(marker["status"], "database_commit_succeeded")
            self.assertEqual(marker["summary_sha256"], hashlib.sha256(summary).hexdigest())
            self.assertEqual(marker["csv_sha256"], hashlib.sha256(csv).hexdigest())
            self.assertEqual(json.loads(summary)["monitor_run_id"], result["monitor_run_id"])

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
        with patch.object(self.database, "execute", side_effect=fail_second_insert):
            with self.assertRaisesRegex(store.MonitorPersistenceError, "failed during database"):
                self.persist("failed.csv", self.run_a)
        self.assertEqual(self.rows(), [])
        self.assertTrue((self.root / "failed.csv").is_file())
        summary = json.loads((self.root / "failed.json").read_text())
        self.assertEqual(summary["monitor_run_id"], self.run_a)
        self.assertEqual(summary["persistence_status"], "database_commit_unconfirmed_without_completion_marker")
        self.assertFalse((self.root / "failed.complete.json").exists())

    def test_postcommit_marker_failure_is_explicit_and_does_not_delete_db_rows(self):
        writer = store._write_new_file
        def fail_marker(path, body):
            if path.name.endswith(".complete.json"):
                raise OSError("synthetic marker failure")
            writer(path, body)
        with patch.object(store, "_write_new_file", side_effect=fail_marker):
            with self.assertRaisesRegex(store.MonitorPersistenceError, "failed during completion_marker"):
                self.persist("missing-marker.csv", self.run_a)
        self.assertEqual(len(self.rows()), 2)
        self.assertFalse((self.root / "missing-marker.complete.json").exists())
        self.assertEqual(json.loads((self.root / "missing-marker.json").read_text())["monitor_run_id"], self.run_a)

    def test_report_creation_failure_keeps_orphan_and_never_touches_db(self):
        writer = store._write_new_file
        def fail_summary(path, body):
            if path.name == "partial.json":
                raise OSError("synthetic summary failure")
            writer(path, body)
        with patch.object(store, "_write_new_file", side_effect=fail_summary):
            with self.assertRaisesRegex(store.MonitorPersistenceError, "failed during report_files"):
                self.persist("partial.csv", self.run_a)
        self.assertTrue((self.root / "partial.csv").exists())
        self.assertEqual(pd.read_csv(self.root / "partial.csv")["monitor_run_id"].tolist(), [self.run_a, self.run_a])
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
        for invalid in (self.windows.iloc[:0], pd.concat([self.windows, self.windows.iloc[:1]])):
            with self.assertRaisesRegex(ValueError, "unique window"):
                self.persist("empty.csv", self.run_a, windows=invalid)
            self.assertFalse((self.root / "empty.csv").exists())
        with self.assertRaisesRegex(ValueError, ".csv extension"):
            self.persist("bad.json", self.run_a)


if __name__ == "__main__":
    unittest.main()
