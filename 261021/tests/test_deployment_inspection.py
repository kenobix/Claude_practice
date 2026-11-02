import hashlib
from contextlib import redirect_stdout
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bike_mlops.db import Database
from bike_mlops.deployment_inspection import inspect_state

PROFILE, TARGET = "uci-bike-sharing", "uci-bike-sharing-cnt-dataset-local-hour"


class DeploymentInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "state.json"
        self.url = f"sqlite:///{self.root / 'db.sqlite'}"
        self.db = Database(self.url, PROFILE, TARGET)
        self.db.initialize()
        self.state = dict(active_slot="green", active_version="3", last_deployment_id="operation-3",
                          approver="operator", reason="reason", data_profile_id=PROFILE, target_contract_id=TARGET)
        self.path.write_text(json.dumps(self.state))

    def row(self, **changes):
        row = dict(deployment_id="operation-3", status="completed", phase="completed",
                   data_profile_id=PROFILE, target_contract_id=TARGET, from_slot="blue", to_slot="green",
                   from_version="2", to_version="3", approver="operator", reason="reason",
                   created_at="fixture", updated_at="fixture")
        row.update(changes)
        with self.db.transaction() as connection:
            self.db.execute(connection, "INSERT INTO deployments(" + ",".join(row) + ") VALUES (" +
                            ",".join("?" for _ in row) + ")", tuple(row.values()))

    def check(self):
        before = (self.path.read_bytes(), hashlib.sha256((self.root / "db.sqlite").read_bytes()).hexdigest())
        result = inspect_state(self.path, self.url, PROFILE, TARGET)
        self.assertEqual(before, (self.path.read_bytes(), hashlib.sha256((self.root / "db.sqlite").read_bytes()).hexdigest()))
        self.assertFalse(result["automatic_repair"])
        self.assertFalse(result["approval_evidence"])
        return result

    def test_matched_completed_ledger_and_state_do_not_write(self):
        self.row()
        self.assertEqual(self.check()["status"], "matched")

    def test_old_approval_mismatch_is_diagnosed_not_overwritten(self):
        self.row(approver="previous-operator")
        result = self.check()
        self.assertEqual(result["status"], "metadata_mismatch")
        self.assertEqual(result["differences"], ["approver"])

    def test_missing_bootstrap_ledger_remains_legacy_unverifiable(self):
        self.assertEqual(self.check()["status"], "legacy_unverifiable")

    def test_pending_operation_is_unconfirmed_even_if_completed_entry_matches(self):
        self.row()
        self.row(deployment_id="pending", status="in_progress")
        result = self.check()
        self.assertEqual(result["status"], "incomplete_deployment_requires_reconciliation")
        self.assertEqual(result["exit_code"], 2)

    def test_profile_checked_before_any_deployment_select(self):
        with self.db.transaction() as connection:
            self.db.execute(connection, "UPDATE data_profile_metadata SET data_profile_id='foreign'")
        queries = []
        connect = sqlite3.connect
        def traced(*args, **kwargs):
            c = connect(*args, **kwargs)
            c.set_trace_callback(queries.append)
            return c
        with patch("bike_mlops.deployment_inspection.sqlite3.connect", side_effect=traced):
            self.assertEqual(self.check()["status"], "database_profile_mismatch")
        self.assertFalse(any("from deployments" in q.lower() for q in queries))

    def test_missing_database_is_not_created_and_native_errors_are_redacted(self):
        url = f"sqlite:///{self.root / 'missing' / 'database.sqlite'}"
        result = inspect_state(self.path, url, PROFILE, TARGET)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse((self.root / "missing").exists())
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_symlink_oversize_and_duplicate_state_are_rejected(self):
        self.path.unlink()
        self.path.symlink_to(self.root / "outside")
        self.assertEqual(inspect_state(self.path, self.url, PROFILE, TARGET)["status"], "unavailable")
        self.path.unlink()
        for content in ('{"active_version":"3","active_version":"4"}', 'x' * 65537):
            self.path.write_text(content)
            self.assertEqual(inspect_state(self.path, self.url, PROFILE, TARGET)["status"], "unavailable")

    def test_atomic_state_replacement_during_db_read_is_not_matched(self):
        from bike_mlops import deployment_inspection as module
        self.row()
        read = module._read_state_file
        calls = []
        def changed(path):
            calls.append(1)
            if len(calls) == 2:
                other = self.path.with_suffix(".replacement")
                other.write_bytes(self.path.read_bytes())
                other.replace(self.path)
            return read(path)
        with patch.object(module, "_read_state_file", side_effect=changed):
            self.assertEqual(self.check()["status"], "state_changed_during_inspection")

    def test_cli_json_and_exit_code_do_not_use_database_initializer(self):
        from bike_mlops import cli
        self.row()
        self.path.rename(self.root / "deployment-state.json")
        with patch.object(cli, "RUNTIME_DIR", self.root), patch.dict(os.environ, {"DATABASE_URL": self.url}), \
             patch.object(cli, "_database", side_effect=AssertionError("Initializer must not run")) as initialize, \
             redirect_stdout(io.StringIO()) as output:
            args = cli.build_parser().parse_args(["deploy", "inspect"])
            with self.assertRaises(SystemExit) as error:
                args.func(args)
        self.assertEqual(error.exception.code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "matched")
        initialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
