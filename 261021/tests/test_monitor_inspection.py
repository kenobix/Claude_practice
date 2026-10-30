from __future__ import annotations

import csv
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
import math
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from bike_mlops.config import DATA_PROFILE_ID, TARGET_CONTRACT_ID
from bike_mlops.db import Database
from bike_mlops.monitor_inspection import inspect_monitor_run
from bike_mlops.monitor_persistence import persist_monitor_run

_CONNECT = sqlite3.connect


class MonitorInspectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "ops.sqlite"
        self.db_url = f"sqlite:///{self.db_path}"
        self.database = Database(self.db_url, DATA_PROFILE_ID, TARGET_CONTRACT_ID)
        self.database.initialize()
        self.run_id = str(uuid.uuid4())
        self.csv = self.root / "monitor-run.csv"
        self.windows = self._windows()
        self.report = self._report()
        persist_monitor_run(self.database, self.windows, self.csv, self.report, run_id=self.run_id)
        self.paths = (self.csv, self.csv.with_suffix(".json"), self.csv.with_suffix(".complete.json"))

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _windows():
        return pd.DataFrame([
            {"window_end": "2012-01-01 01:00:00", "window_start": "2012-01-01 00:00:00",
             "prediction_count": 4, "scored_count": 3, "label_coverage": 0.75, "mae": 1.25,
             "rmse": 1.5, "quality_status": "within_review_threshold", "psi": {"lag_24": 0.1},
             "feature_missing_rates": {"lag_24": 0.0}, "distribution_status": "no_threshold_crossing"},
            {"window_end": "2012-01-01 02:00:00", "window_start": "2012-01-01 01:00:00",
             "prediction_count": 4, "scored_count": 4, "label_coverage": 1.0, "mae": 0.0,
             "rmse": 0.0, "quality_status": "investigate_error_increase", "psi": {"lag_24": 0.2},
             "feature_missing_rates": {"lag_24": 0.0}, "distribution_status": "investigate_distribution_change"},
        ])

    def _report(self):
        latest = self.windows.iloc[-1].to_dict()
        return {
            "replay_id": "replay-synthetic-v1", "model_name": "bike-demand",
            "model_version": "2", "data_sha256": "a" * 64,
            "prediction_rows": 4, "window_count": len(self.windows),
            "data_profile_id": DATA_PROFILE_ID, "target_contract_id": TARGET_CONTRACT_ID,
            "monitor_period": {"start_inclusive": "2012-01-01 01:00:00",
                               "end_exclusive": "2012-01-01 03:00:00"},
            "thresholds": {"window_hours": 1}, "automatic_promotion": False,
            "latest_window": latest,
        }

    @staticmethod
    def _file_snapshot(paths):
        snapshot = []
        for path in paths:
            try:
                info = path.lstat()
            except FileNotFoundError:
                snapshot.append(None)
                continue
            value = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)
            if stat.S_ISREG(info.st_mode):
                with path.open("rb") as stream:
                    value += (hashlib.file_digest(stream, "sha256").hexdigest(),)
            elif stat.S_ISLNK(info.st_mode):
                value += (os.readlink(path),)
            snapshot.append(value)
        return snapshot

    @staticmethod
    def _logical_snapshot(path):
        if not path.is_file():
            return None
        connection = _CONNECT(path.as_uri() + "?mode=ro", uri=True)
        try:
            result = {}
            for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                quoted = '"' + name.replace('"', '""') + '"'
                result[name] = connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid").fetchall()
            return result
        finally:
            connection.close()

    def inspect(self, csv=None, db_url=None, run_id=None, *, preserve=True):
        selected = csv or self.csv
        files = (selected, selected.with_suffix(".json"), selected.with_suffix(".complete.json"))
        before_files = self._file_snapshot(files) if preserve else None
        before_rows = self._logical_snapshot(self.db_path) if preserve else None
        result = inspect_monitor_run(
            selected, run_id or self.run_id, database_url=db_url or self.db_url,
            data_profile_id=DATA_PROFILE_ID, target_contract_id=TARGET_CONTRACT_ID,
        )
        if preserve:
            self.assertEqual(self._file_snapshot(files), before_files)
            self.assertEqual(self._logical_snapshot(self.db_path), before_rows)
        json.dumps(result, allow_nan=False)  # All API outcomes must be finite JSON.
        return result

    def _db_rows(self):
        with self.database.session() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM monitor_windows ORDER BY window_id").fetchall()]

    def _delete_run_rows(self):
        with self.database.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM monitor_windows WHERE window_id LIKE ?", (f"monitor:{self.run_id}:%",))

    def _rewrite_summary(self, change):
        value = json.loads(self.paths[1].read_text())
        change(value)
        self.paths[1].write_text(json.dumps(value))
        marker = json.loads(self.paths[2].read_text())
        marker["summary_sha256"] = hashlib.sha256(self.paths[1].read_bytes()).hexdigest()
        self.paths[2].write_text(json.dumps(marker))

    def test_review_period_requires_every_clock_through_end_exclusive(self):
        self._rewrite_summary(lambda value: value["monitor_period"].update(end_exclusive="2012-01-01 04:00:00"))
        self.assertEqual(self.inspect()["status"], "invalid_evidence")

    def test_review_database_json_boolean_is_not_numeric_zero(self):
        with self.database.transaction(immediate=True) as connection:
            connection.execute("UPDATE monitor_windows SET missing_json=?", ('{"lag_24":false}',))
        self.assertEqual(self.inspect()["status"], "mismatch")

    def test_review_latest_boolean_is_invalid_even_when_equal_to_zero(self):
        self._rewrite_summary(lambda value: value["latest_window"].update(mae=False))
        self.assertEqual(self.inspect()["status"], "invalid_evidence")

    def test_review_missing_window_hours_is_unsupported(self):
        self._rewrite_summary(lambda value: value["thresholds"].pop("window_hours"))
        self.assertEqual(self.inspect()["status"], "unsupported_input")

    def test_review_legacy_file_does_not_hide_database_profile_mismatch(self):
        self.csv.write_text(self.csv.read_text().replace("monitor_run_id,", "legacy_id,", 1))
        with self.database.transaction(immediate=True) as connection:
            connection.execute("UPDATE data_profile_metadata SET data_profile_id='foreign'")
        self.assertEqual(self.inspect()["status"], "database_profile_mismatch")

    def test_review_legacy_file_does_not_hide_database_unavailability(self):
        self.csv.write_text(self.csv.read_text().replace("monitor_run_id,", "legacy_id,", 1))
        self.assertEqual(self.inspect(db_url=f"sqlite:///{self.root / 'absent.sqlite'}")["status"],
                         "database_unavailable")

    def test_review_latest_mismatch_precedes_database_unavailability(self):
        self._rewrite_summary(lambda value: value["latest_window"].update(mae=111))
        self.assertEqual(self.inspect(db_url=f"sqlite:///{self.root / 'absent.sqlite'}")["status"], "mismatch")

    def test_review_replacement_during_last_reread_is_detected(self):
        from bike_mlops.monitor_inspection import _read_file
        calls = 0
        def replace_during_read(path, kind):
            nonlocal calls
            if path == self.csv:
                calls += 1
                if calls == 3:
                    info = path.stat()
                    replacement = path.with_suffix(".replacement")
                    replacement.write_bytes(path.read_bytes())
                    os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
                    os.replace(replacement, path)
            return _read_file(path, kind)
        with patch("bike_mlops.monitor_inspection._read_file", side_effect=replace_during_read):
            self.assertEqual(self.inspect(preserve=False)["status"], "changed_during_inspection")

    def test_review_field_over_limit_is_unsupported(self):
        with self.csv.open(newline="") as stream:
            rows = list(csv.reader(stream))
        rows[1][rows[0].index("quality_status")] = "x" * (1024 * 1024 + 1)
        with self.csv.open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
        self.assertEqual(self.inspect()["status"], "unsupported_input")

    def test_review_secret_in_database_exception_is_not_printed(self):
        secret = "postgresql://fake:DO_NOT_EXPOSE@host/db"
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("bike_mlops.monitor_inspection.sqlite3.connect",
                   side_effect=sqlite3.OperationalError(secret)), redirect_stdout(stdout), redirect_stderr(stderr):
            result = self.inspect()
        self.assertEqual(result["status"], "database_unavailable")
        self.assertNotIn("DO_NOT_EXPOSE", json.dumps(result, allow_nan=False))
        self.assertNotIn("DO_NOT_EXPOSE", stdout.getvalue() + stderr.getvalue())

    def test_review_marker_appearing_after_database_read_is_changed(self):
        from bike_mlops.monitor_inspection import _database_rows
        marker = self.paths[2].read_bytes()
        self.paths[2].unlink()
        def concurrent_marker(*args, **kwargs):
            rows = _database_rows(*args, **kwargs)
            self.paths[2].write_bytes(marker)
            return rows
        with patch("bike_mlops.monitor_inspection._database_rows", side_effect=concurrent_marker):
            result = self.inspect(preserve=False)
        self.assertEqual(result["status"], "changed_during_inspection")
        self.assertEqual(result["files"]["marker"]["state"], "changed")

    def test_review_csv_multiple_run_ids_are_mismatch(self):
        with self.csv.open(newline="") as stream:
            rows = list(csv.reader(stream))
        rows[-1][0] = str(uuid.uuid4())
        with self.csv.open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
        self.assertEqual(self.inspect()["reason"], "run_identity_mismatch")

    def test_review_marker_types_status_and_unknown_schema(self):
        original = json.loads(self.paths[2].read_text())
        for change, status in (({"schema_version": True}, "invalid_evidence"),
                               ({"schema_version": 2}, "unsupported_input"),
                               ({"status": "unfinished"}, "invalid_evidence")):
            with self.subTest(change=change):
                self.paths[2].write_text(json.dumps({**original, **change}))
                self.assertEqual(self.inspect()["status"], status)

    def test_review_all_database_comparison_fields_reject_changes(self):
        changes = {
            "prediction_count": 99, "scored_count": 0, "label_coverage": 0.0,
            "mae": math.nextafter(1.25, math.inf), "rmse": 111.0,
            "psi_json": '{"lag_24":0.10000000000000002}',
            "missing_json": '{"lag_24":0.0000000000000001}',
        }
        original = self._db_rows()[0]
        for field, changed in changes.items():
            with self.subTest(field=field):
                with self.database.transaction(immediate=True) as connection:
                    connection.execute(f"UPDATE monitor_windows SET {field}=? WHERE window_id=?",
                                       (changed, original["window_id"]))
                result = self.inspect()
                self.assertEqual(result["status"], "mismatch")
                self.assertTrue(any(item["field"] == field for item in result["differences"]), result)
                with self.database.transaction(immediate=True) as connection:
                    connection.execute(f"UPDATE monitor_windows SET {field}=? WHERE window_id=?",
                                       (original[field], original["window_id"]))

    def test_review_unknown_numeric_schema_is_unsupported(self):
        with self.database.transaction(immediate=True) as connection:
            connection.execute("ALTER TABLE monitor_windows RENAME TO saved_windows")
            schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='saved_windows'").fetchone()[0]
            connection.execute(schema.replace('"saved_windows"', "monitor_windows").replace("mae REAL", "mae TEXT"))
            connection.execute("INSERT INTO monitor_windows SELECT * FROM saved_windows")
            connection.execute("DROP TABLE saved_windows")
        self.assertEqual(self.inspect()["reason"], "monitor_numeric_type_unsupported")

    def test_review_table_read_denied_and_locked_errors_are_unavailable(self):
        def denied(*args, **kwargs):
            connection = _CONNECT(*args, **kwargs)
            connection.set_authorizer(lambda action, table, column, database, trigger:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == "monitor_windows"
                else sqlite3.SQLITE_OK)
            return connection
        with patch("bike_mlops.monitor_inspection.sqlite3.connect", side_effect=denied):
            result = self.inspect()
        self.assertEqual(result["status"], "database_unavailable")
        self.assertIsNone(result["database"]["row_count"])
        with patch("bike_mlops.monitor_inspection.sqlite3.connect",
                   side_effect=sqlite3.OperationalError("database is locked; password=PRIVATE")):
            result = self.inspect()
        self.assertEqual(result["status"], "database_unavailable")
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_review_monitor_rows_and_profile_share_a_sqlite_snapshot(self):
        writer_has_committed = False
        database_path = self.db_path
        class ConcurrentConnection(sqlite3.Connection):
            def execute(connection, sql, params=()):
                nonlocal writer_has_committed
                cursor = super().execute(sql, params)
                if sql.startswith("SELECT data_profile_id") and not writer_has_committed:
                    writer_has_committed = True
                    writer = _CONNECT(database_path)
                    try:
                        writer.execute("UPDATE monitor_windows SET mae=222")
                        writer.execute("UPDATE data_profile_metadata SET data_profile_id='later-profile'")
                        writer.commit()
                    finally:
                        writer.close()
                return cursor
        def concurrent(*args, **kwargs):
            return _CONNECT(*args, **kwargs, factory=ConcurrentConnection)
        with patch("bike_mlops.monitor_inspection.sqlite3.connect", side_effect=concurrent):
            result = self.inspect(preserve=False)
        self.assertTrue(writer_has_committed)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(self.inspect()["status"], "database_profile_mismatch")

    def test_review_row_limit_uses_one_extra_row_for_detection(self):
        with self.database.transaction(immediate=True) as connection:
            connection.execute("INSERT INTO monitor_windows SELECT ?,replay_id,window_end,prediction_count,"
                               "scored_count,label_coverage,mae,rmse,psi_json,missing_json,created_at "
                               "FROM monitor_windows LIMIT 1", (f"monitor:{self.run_id}:extra",))
        with patch("bike_mlops.monitor_inspection.MAX_ROWS", 2):
            result = self.inspect()
        self.assertEqual((result["status"], result["reason"]),
                         ("unsupported_input", "database_row_limit_exceeded"))

    def test_review_capital_extended_columns_and_nested_types(self):
        from bike_mlops.profiles import load_data_profile
        capital = load_data_profile("capital-bikeshare")
        db_path = self.root / "capital.sqlite"
        database = Database(f"sqlite:///{db_path}", "capital-bikeshare", capital["target_contract_id"])
        database.initialize()
        windows = self._windows()
        windows["psi_algorithm"] = "psi-v2-constant-bins"
        windows["psi_details"] = [{"lag_24": {"ready": True, "bins": [0.0, 1.0], "psi": 0.1}}] * 2
        windows["training_reference_sha256"] = "f" * 64
        report = self._report()
        report.update(data_profile_id="capital-bikeshare", target_contract_id=capital["target_contract_id"],
                      model_name="bike-demand-capital", latest_window=windows.iloc[-1].to_dict())
        output = self.root / "capital.csv"
        run_id = str(uuid.uuid4())
        persist_monitor_run(database, windows, output, report, run_id=run_id)
        paths = (output, output.with_suffix(".json"), output.with_suffix(".complete.json"))
        before = self._file_snapshot(paths), self._logical_snapshot(db_path)
        result = inspect_monitor_run(output, run_id, database_url=database.url,
                                     data_profile_id="capital-bikeshare", target_contract_id=capital["target_contract_id"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual((self._file_snapshot(paths), self._logical_snapshot(db_path)), before)

    def test_review_bad_uuid_and_extension_return_json(self):
        self.assertEqual(self.inspect(run_id="bad-uuid")["status"], "invalid_evidence")
        result = inspect_monitor_run(Path("/"), self.run_id, database_url=self.db_url,
                                     data_profile_id=DATA_PROFILE_ID, target_contract_id=TARGET_CONTRACT_ID)
        self.assertEqual(result["status"], "unsupported_input")
        json.dumps(result, allow_nan=False)

    def test_review_differences_are_counted_and_bounded(self):
        rows = []
        start = pd.Timestamp("2012-02-01 00:00:00")
        for offset in range(60):
            clock = start + pd.Timedelta(hours=offset)
            row = self._windows().iloc[0].to_dict()
            row.update(window_end=str(clock), window_start=str(clock - pd.Timedelta(hours=1)))
            rows.append(row)
        windows = pd.DataFrame(rows)
        report = self._report()
        report.update(window_count=60, latest_window=windows.iloc[-1].to_dict(),
                      monitor_period={"start_inclusive": str(start),
                                      "end_exclusive": str(start + pd.Timedelta(hours=60))})
        output, run_id = self.root / "many.csv", str(uuid.uuid4())
        persist_monitor_run(self.database, windows, output, report, run_id=run_id)
        with self.database.transaction(immediate=True) as connection:
            connection.execute("UPDATE monitor_windows SET replay_id=? WHERE window_id LIKE ?",
                               ("x" * 600, f"monitor:{run_id}:%"))
        result = self.inspect(csv=output, run_id=run_id)
        self.assertEqual(result["difference_count"], 60)
        self.assertEqual(len(result["differences"]), 50)
        self.assertTrue(result["differences_truncated"])
        self.assertTrue(all(len(item["observed"]) <= 512 for item in result["differences"]))

    def test_s01_complete_matching_run_is_matched_and_read_only(self):
        before_files = [path.read_bytes() for path in self.paths]
        before_rows = self._db_rows()
        statements = []
        original_connect = sqlite3.connect
        def traced_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        with patch.object(Database, "initialize", side_effect=AssertionError("initialize called")), \
             patch.object(Database, "connect", side_effect=AssertionError("connect called")), \
             patch("bike_mlops.monitor_inspection.sqlite3.connect", side_effect=traced_connect):
            result = self.inspect()
        self.assertEqual((result["status"], result["exit_code"]), ("matched", 0))
        self.assertEqual(result["checks"]["database_rows"], "passed")
        self.assertEqual((result["model_name"], result["model_version"], result["model_binding"]),
                         ("bike-demand", "2", "report_only"))
        self.assertEqual([path.read_bytes() for path in self.paths], before_files)
        self.assertEqual(self._db_rows(), before_rows)
        self.assertTrue(statements)
        self.assertTrue(all(sql.lstrip().upper().startswith(("SELECT", "PRAGMA", "BEGIN", "ROLLBACK"))
                            for sql in statements), statements)

    def test_s02_and_s03_missing_marker_remain_unconfirmed(self):
        self.paths[2].unlink()
        self._delete_run_rows()
        result = self.inspect()
        self.assertEqual((result["status"], result["reason"]), ("unconfirmed", "files_only"))
        self.assertTrue(result["later_commit_possible"])
        self.assertEqual(len(self._db_rows()), 0)

    def test_files_only_still_checks_csv_latest_window_against_summary(self):
        self.paths[2].unlink()
        self._delete_run_rows()
        summary = json.loads(self.paths[1].read_text())
        summary["latest_window"]["mae"] = 999
        self.paths[1].write_text(json.dumps(summary))
        result = self.inspect()
        self.assertEqual((result["status"], result["reason"]), ("mismatch", "latest_window_mismatch"))

    def test_s03_full_database_without_marker_is_not_matched(self):
        self.paths[2].unlink()
        result = self.inspect()
        self.assertEqual((result["status"], result["reason"]), ("unconfirmed", "database_without_marker"))
        self.assertEqual(len(self._db_rows()), 2)

    def test_s04_partial_files_are_unconfirmed_without_database_claim(self):
        self.paths[1].unlink()
        self.paths[2].unlink()
        result = self.inspect()
        self.assertEqual((result["status"], result["reason"]), ("unconfirmed", "incomplete_files"))
        self.assertEqual(result["database"]["row_count"], None)
        self._delete_run_rows()
        self.assertEqual(self.inspect()["reason"], "incomplete_files")

    def test_s04_summary_only_is_unconfirmed(self):
        original = json.loads(self.paths[1].read_text())
        self.paths[0].unlink()
        self.paths[2].unlink()
        alternate = self.root / "summary-only.csv"
        original["csv_report"] = alternate.name
        original["completion_marker"] = alternate.with_suffix(".complete.json").name
        alternate.with_suffix(".json").write_text(json.dumps(original))
        result = self.inspect(csv=alternate)
        self.assertEqual((result["status"], result["reason"]), ("unconfirmed", "incomplete_files"))
        self._delete_run_rows()
        self.assertEqual(self.inspect(csv=alternate)["reason"], "incomplete_files")

    def test_s05_and_s06_no_files_classify_database_only_or_not_observed(self):
        for path in self.paths:
            path.unlink()
        self.assertEqual(self.inspect()["reason"], "database_only")
        self._delete_run_rows()
        self.assertEqual(self.inspect()["reason"], "not_observed")

    def test_s07_marker_with_missing_required_file_is_mismatch(self):
        self.csv.unlink()
        result = self.inspect()
        self.assertEqual((result["status"], result["reason"]), ("mismatch", "marker_without_required_files"))

    def test_s07_marker_with_missing_summary_is_mismatch(self):
        self.paths[1].unlink()
        result = self.inspect()
        self.assertEqual((result["status"], result["reason"]), ("mismatch", "marker_without_required_files"))

    def test_s08_hash_tampering_is_mismatch(self):
        marker = json.loads(self.paths[2].read_text())
        marker["csv_sha256"] = "b" * 64
        self.paths[2].write_text(json.dumps(marker))
        self.assertEqual(self.inspect()["status"], "mismatch")

    def test_s08_changed_summary_identity_is_mismatch(self):
        summary = json.loads(self.paths[1].read_text())
        summary["monitor_run_id"] = str(uuid.uuid4())
        self.paths[1].write_text(json.dumps(summary))
        self.assertEqual(self.inspect()["reason"], "run_identity_mismatch")

    def test_s09_missing_database_window_is_mismatch(self):
        with self.database.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM monitor_windows WHERE window_id LIKE ?",
                               (f"monitor:{self.run_id}:%",))
        result = self.inspect()
        self.assertEqual((result["status"], result["reason"]), ("mismatch", "database_rows_differ"))

    def test_s09_extra_window_and_changed_values_are_mismatch(self):
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO monitor_windows VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"monitor:{self.run_id}:2012-01-01 03:00:00", "replay-synthetic-v1",
                 "2012-01-01 03:00:00", 1, 1, 1.0, 0.0, 0.0, "{}", "{}", "now"),
            )
        self.assertEqual(self.inspect()["status"], "mismatch")
        with self.database.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM monitor_windows WHERE window_id LIKE ?",
                               (f"monitor:{self.run_id}:2012-01-01 03:00:00",))
            connection.execute(
                "UPDATE monitor_windows SET replay_id='different',mae=9 WHERE window_id LIKE ?",
                (f"monitor:{self.run_id}:%",),
            )
        result = self.inspect()
        self.assertEqual(result["reason"], "database_rows_differ")
        self.assertGreaterEqual(result["difference_count"], 2)

    def test_s09_database_timestamp_change_is_mismatch(self):
        with self.database.transaction(immediate=True) as connection:
            connection.execute("UPDATE monitor_windows SET window_end='2012-01-01 00:00:00' "
                               "WHERE window_id LIKE ?", (f"monitor:{self.run_id}:%",))
        self.assertEqual(self.inspect()["reason"], "database_rows_differ")

    def test_s10_profile_mismatch_is_rejected_before_target_select(self):
        statements = []
        with self.database.transaction(immediate=True) as connection:
            connection.execute("UPDATE data_profile_metadata SET data_profile_id='other-profile' WHERE singleton=1")
        original = sqlite3.connect
        def traced(*args, **kwargs):
            con = original(*args, **kwargs)
            con.set_trace_callback(statements.append)
            return con
        with patch("bike_mlops.monitor_inspection.sqlite3.connect", side_effect=traced):
            result = self.inspect()
        self.assertEqual(result["status"], "database_profile_mismatch")
        self.assertFalse(any("FROM monitor_windows" in sql for sql in statements))

    def test_s10_target_contract_mismatch_is_rejected(self):
        with self.database.transaction(immediate=True) as connection:
            connection.execute("UPDATE data_profile_metadata SET target_contract_id='other-target' WHERE singleton=1")
        self.assertEqual(self.inspect()["status"], "database_profile_mismatch")

    def test_s11_duplicate_window_timestamp_is_invalid(self):
        content = self.csv.read_text()
        lines = content.splitlines()
        self.csv.write_text("\n".join((lines[0], lines[1], lines[1])) + "\n")
        result = self.inspect()
        self.assertEqual((result["status"], result["reason"]), ("invalid_evidence", "duplicate_window_timestamp"))

    def test_s11_nonfinite_metric_and_legacy_csv_are_not_accepted(self):
        original = self.csv.read_text()
        self.csv.write_text(original.replace(",1.25,", ",NaN,"))
        self.assertEqual(self.inspect()["status"], "invalid_evidence")
        self.csv.write_text(original.replace("monitor_run_id,", "legacy_id,", 1))
        result = self.inspect()
        self.assertEqual((result["status"], result["reason"]),
                         ("legacy_unverifiable", "legacy_csv_without_run_id"))

    def test_s11_duplicate_json_key_is_invalid(self):
        self.paths[1].write_text('{"replay_id":"a","replay_id":"b"}')
        self.assertEqual(self.inspect()["status"], "invalid_evidence")

    def test_s12_missing_metadata_is_legacy_unverifiable_without_migration(self):
        legacy_path = self.root / "legacy.sqlite"
        connection = sqlite3.connect(legacy_path)
        connection.execute("CREATE TABLE monitor_windows (window_id TEXT)")
        connection.commit()
        connection.close()
        before = legacy_path.read_bytes()
        result = self.inspect(db_url=f"sqlite:///{legacy_path}")
        self.assertEqual((result["status"], result["reason"]), ("legacy_unverifiable", "missing_profile_metadata"))
        self.assertEqual(legacy_path.read_bytes(), before)

    def test_s12_old_json_without_run_id_is_legacy_unverifiable(self):
        self.paths[1].write_text(json.dumps({"replay_id": "old"}))
        self.paths[2].unlink()
        self.assertEqual(self.inspect()["reason"], "legacy_summary_without_run_id")

    def test_s13_unavailable_database_does_not_create_path_or_leak_details(self):
        missing = self.root / "missing-parent" / "ops.sqlite"
        result = self.inspect(db_url="sqlite:///" + str(missing))
        self.assertEqual(result["status"], "database_unavailable")
        self.assertFalse(missing.parent.exists())
        rejected = self.inspect(db_url="sqlite:///" + str(missing) + "?token=DO_NOT_LEAK")
        self.assertEqual(rejected["status"], "unsupported_input")
        result = rejected
        self.assertNotIn("DO_NOT_LEAK", json.dumps(result))

    def test_s14_file_race_is_changed_during_inspection(self):
        with patch("bike_mlops.monitor_inspection._unchanged", return_value=False):
            result = self.inspect()
        self.assertEqual((result["status"], result["reason"]), ("changed_during_inspection", "file_snapshot_changed"))

    def test_s14_real_file_change_after_database_read_is_detected(self):
        from bike_mlops.monitor_inspection import _database_rows
        def changing_reader(*args, **kwargs):
            rows = _database_rows(*args, **kwargs)
            with self.paths[2].open("ab") as marker:
                marker.write(b" ")
            return rows
        with patch("bike_mlops.monitor_inspection._database_rows", side_effect=changing_reader):
            result = self.inspect(preserve=False)
        self.assertEqual(result["status"], "changed_during_inspection")

    def test_s15_symlink_and_fifo_are_rejected_without_waiting(self):
        real = self.root / "target.csv"
        real.write_bytes(self.csv.read_bytes())
        self.csv.unlink()
        self.csv.symlink_to(real)
        self.assertEqual(self.inspect()["status"], "invalid_evidence")
        self.csv.unlink()
        os.mkfifo(self.csv)
        self.assertEqual(self.inspect()["status"], "invalid_evidence")

    def test_s15_unknown_columns_and_oversize_file_are_unsupported(self):
        original = self.csv.read_bytes()
        self.csv.write_bytes(original.replace(b"quality_status", b"new_status", 1))
        self.assertEqual(self.inspect()["status"], "unsupported_input")
        self.csv.unlink()
        with self.csv.open("wb") as stream:
            stream.truncate(128 * 1024 * 1024 + 1)
        self.assertEqual(self.inspect()["reason"], "file_size_limit_exceeded")

    def test_s16_other_runs_are_preserved_and_not_selected(self):
        other = f"monitor:{uuid.uuid4()}:2012-01-01 00:00:00"
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO monitor_windows VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (other, "other-replay", "2012-01-01 00:00:00", 1, 1, 1.0, 0.0, 0.0, "{}", "{}", "now"),
            )
        before = len(self._db_rows())
        result = self.inspect()
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["database"]["row_count"], 2)
        self.assertEqual(len(self._db_rows()), before)

    def test_s16_legacy_row_is_preserved_and_not_selected(self):
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO monitor_windows VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("legacy-monitor-window", "legacy-replay", "2011-01-01 00:00:00",
                 1, 0, None, None, None, "{}", "{}", "legacy"),
            )
        result = self.inspect()
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["database"]["row_count"], 2)
        self.assertEqual(len(self._db_rows()), 3)

    def test_s17_blank_metrics_match_sql_null_and_zero_does_not(self):
        blank = self._windows()
        blank[["label_coverage", "mae", "rmse"]] = None
        latest = blank.iloc[-1].to_dict()
        report = self._report()
        report["latest_window"] = latest
        run_id = str(uuid.uuid4())
        output = self.root / "blank-monitor.csv"
        persist_monitor_run(self.database, blank, output, report, run_id=run_id)
        matched = self.inspect(csv=output, run_id=run_id)
        self.assertEqual(matched["status"], "matched")
        self.assertEqual(matched["database"]["missing_representations"]["sql_null"], 6)
        with self.database.transaction(immediate=True) as connection:
            connection.execute("UPDATE monitor_windows SET mae=NULL,rmse=NULL WHERE window_id LIKE ?",
                               (f"monitor:{self.run_id}:%",))
        self.assertEqual(self.inspect()["status"], "mismatch")

    def test_s20_sqlite_connection_is_read_only(self):
        rejected = []
        class ProbeConnection(sqlite3.Connection):
            def execute(connection, sql, params=()):
                cursor = super().execute(sql, params)
                if sql == "BEGIN":
                    self.assertEqual(super(ProbeConnection, connection).execute("PRAGMA query_only").fetchone()[0], 1)
                    try:
                        super(ProbeConnection, connection).execute("UPDATE monitor_windows SET mae=9")
                    except sqlite3.OperationalError as error:
                        rejected.append(error.sqlite_errorcode)
                    else:
                        self.fail("Inspector connection allowed UPDATE")
                return cursor
        def probe(*args, **kwargs):
            return _CONNECT(*args, **kwargs, factory=ProbeConnection)
        with patch("bike_mlops.monitor_inspection.sqlite3.connect", side_effect=probe):
            result = self.inspect()
        self.assertEqual(result["status"], "matched")
        self.assertEqual(rejected, [sqlite3.SQLITE_READONLY])
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM monitor_windows").fetchone()[0], 2)
        connection = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
        try:
            with self.assertRaises(sqlite3.OperationalError) as error:
                connection.execute("UPDATE monitor_windows SET mae=9")
            self.assertIn("readonly", str(error.exception).lower())
        finally:
            connection.close()

    def test_s22_call_expression_in_dict_is_never_evaluated_and_secret_not_exposed(self):
        sentinel = self.root / "must-not-exist"
        expression = f"__import__('pathlib').Path({str(sentinel)!r}).touch()"
        body = self.csv.read_text().replace("{'lag_24': 0.1}", expression)
        self.csv.write_text(body)
        result = self.inspect(db_url=self.db_url + "?password=hidden")
        self.assertEqual(result["status"], "invalid_evidence")
        self.assertNotIn("hidden", json.dumps(result))
        self.assertFalse(sentinel.exists())

    def test_s23_postgresql_is_rejected_without_connecting(self):
        with patch("bike_mlops.monitor_inspection.sqlite3.connect", side_effect=AssertionError("connected")), \
             patch("psycopg.connect", side_effect=AssertionError("PostgreSQL connected")), \
             patch.object(Database, "initialize", side_effect=AssertionError("initialize called")), \
             patch.object(Database, "connect", side_effect=AssertionError("connect called")):
            result = self.inspect(db_url="postgresql://user:secret@host/db")
        self.assertEqual((result["status"], result["exit_code"]), ("unsupported_input", 2))
        self.assertNotIn("secret", json.dumps(result))

    def test_cli_prints_json_and_uses_documented_exit_code(self):
        env = os.environ.copy()
        env["DATABASE_URL"] = self.db_url
        process = subprocess.run(
            [sys.executable, "-m", "bike_mlops.cli", "monitor-inspect", "--csv", str(self.csv),
             "--run-id", self.run_id],
            env=env, text=True, capture_output=True, cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["status"], "matched")
        self.paths[2].unlink()
        unconfirmed = subprocess.run(
            [sys.executable, "-m", "bike_mlops.cli", "monitor-inspect", "--csv", str(self.csv),
             "--run-id", self.run_id],
            env=env, text=True, capture_output=True, cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(unconfirmed.returncode, 2, unconfirmed.stderr)
        self.assertEqual(json.loads(unconfirmed.stdout)["status"], "unconfirmed")
        self.csv.write_text(self.csv.read_text().replace(",1.25,", ",NaN,"))
        invalid = subprocess.run(
            [sys.executable, "-m", "bike_mlops.cli", "monitor-inspect", "--csv", str(self.csv),
             "--run-id", self.run_id], env=env, text=True, capture_output=True, timeout=30,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(invalid.returncode, 1, invalid.stderr)
        self.assertEqual(json.loads(invalid.stdout)["status"], "invalid_evidence")
        old_cli = subprocess.run([sys.executable, "-m", "bike_mlops.cli", "monitor", "--help"],
                                 env=env, text=True, capture_output=True,
                                 cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(old_cli.returncode, 0, old_cli.stderr)


if __name__ == "__main__":
    unittest.main()
