"""Isolated journal/state recovery; Docker and registry IO are controlled doubles."""
from __future__ import annotations

import fcntl
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from bike_mlops import cli
from bike_mlops.db import Database

PROFILE = "uci-bike-sharing"
TARGET = "uci-bike-sharing-cnt-dataset-local-hour"


class DeploymentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.db = Database(f"sqlite:///{self.root / 'ops.db'}", PROFILE, TARGET)
        self.db.initialize()
        self.state = dict(active_slot="blue", active_version="2", data_profile_id=PROFILE,
                          target_contract_id=TARGET, deployment_mode="approved",
                          previous_versions=["1"], last_deployment_id="bootstrap",
                          approver="old-operator", reason="old decision")
        self.write_state(self.state)
        (self.runtime / "Caddyfile").write_text("old config\n")
        self.csv = self.root / "synthetic.csv"
        self.csv.write_text("dteday,hr,cnt\n2012-01-01,0,1\n")
        self.registry = SimpleNamespace(get_model_version=Mock(side_effect=self.registered))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for key, value in dict(RUNTIME_DIR=self.runtime, DATA_PROFILE_ID=PROFILE,
                               TARGET_CONTRACT_ID=TARGET, MODEL_NAME="bike-demand",
                               DEFAULT_DATASET=self.csv).items():
            self.stack.enter_context(patch.object(cli, key, value))
        self.stack.enter_context(patch.object(cli, "_database", return_value=self.db))
        self.stack.enter_context(patch.object(cli, "_model_registry_client", return_value=self.registry))
        self.compose = self.stack.enter_context(patch.object(cli, "_compose"))
        self.http = self.stack.enter_context(patch.object(cli, "_url_json", side_effect=self.http_reply))
        self.live = self.version("3")
        self.stack.enter_context(patch.object(cli, "_seed_observations", return_value=(0, "synthetic", pd.DataFrame())))
        self.stack.enter_context(patch.object(cli, "_wait_ready", return_value={"status": "ready"}))
        self.probe = self.stack.enter_context(patch.object(cli, "_deployment_probe", return_value={"model_version": "3"}))

    @staticmethod
    def registered(name, version):
        return SimpleNamespace(name=name, version=str(version), status="READY",
                               tags={"quality_gate": "passed", "artifact_sha256": str(version) * 64,
                                     "data_profile_id": PROFILE, "target_contract_id": TARGET})

    @staticmethod
    def version(version):
        return dict(data_profile_id=PROFILE, target_contract_id=TARGET, model_name="bike-demand",
                    model_version=version, artifact_sha256=version * 64,
                    quality_gate_status="passed", feature_contract_version="bike-demand-features-v1")

    def http_reply(self, url, *args, **kwargs):
        return dict(self.live, status="ready") if url.endswith("/ready") else dict(self.live)

    def write_state(self, state):
        (self.runtime / "deployment-state.json").write_text(json.dumps(state))

    def read_state(self):
        return json.loads((self.runtime / "deployment-state.json").read_text())

    def pending(self, operation="operation-3", context=True, **changes):
        row = dict(deployment_id=operation, status="in_progress", phase="candidate_ready",
                   data_profile_id=PROFILE, target_contract_id=TARGET, from_slot="blue", to_slot="green",
                   from_version="2", to_version="3", approver="new-operator", reason="new decision",
                   probe_json=json.dumps({"recovery_context": {"schema_version": 2, "previous_state": self.state,
                        "previous_identity": self.version("2"), "candidate_identity": self.version("3")}})
                   if context else None, created_at="2026-11-01T10:00:00+00:00", updated_at="fixture")
        row.update(changes)
        with self.db.transaction() as connection:
            self.db.execute(connection, "INSERT INTO deployments(" + ",".join(row) + ") VALUES (" +
                            ",".join("?" for _ in row) + ")", tuple(row.values()))
        return row

    def journal(self):
        with self.db.session() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM deployments ORDER BY deployment_id")]

    def recover(self):
        with redirect_stdout(io.StringIO()) as output:
            cli.command_deploy_recover(SimpleNamespace())
        return json.loads(output.getvalue())

    def assert_refused_unchanged(self, regex):
        state = (self.runtime / "deployment-state.json").read_bytes()
        rows = self.journal()
        with self.assertRaisesRegex((RuntimeError, ValueError), regex):
            self.recover()
        self.assertEqual((self.runtime / "deployment-state.json").read_bytes(), state)
        self.assertEqual(self.journal(), rows)

    def test_candidate_recovery_preserves_operation_approval_and_history(self):
        self.pending()
        result = self.recover()
        state = self.read_state()
        self.assertEqual((state["active_version"], state["active_slot"]), ("3", "green"))
        self.assertEqual((state["approver"], state["reason"], state["last_deployment_id"]),
                         ("new-operator", "new decision", "operation-3"))
        self.assertEqual(state["previous_versions"], ["2", "1"])
        self.assertEqual(self.journal()[0]["status"], "completed")
        self.assertEqual(result["proxy_version"]["model_version"], "3")

    def test_legacy_pending_candidate_can_recover_metadata_from_journal(self):
        self.pending(context=False)
        self.recover()
        self.assertEqual(self.read_state()["approver"], "new-operator")

    def test_old_proxy_restores_prior_state_even_after_candidate_state_was_written(self):
        self.pending()
        self.write_state(dict(self.state, active_slot="green", active_version="3",
                              approver="new-operator", reason="new decision"))
        self.live = self.version("2")
        self.recover()
        self.assertEqual(self.read_state(), self.state)
        self.assertEqual(self.journal()[0]["phase"], "retry_candidate")

    def test_legacy_old_proxy_with_old_state_preserves_existing_approval(self):
        self.pending(context=False)
        self.live = self.version("2")
        self.recover()
        self.assertEqual(self.read_state(), self.state)

    def test_legacy_old_proxy_cannot_invent_missing_old_approval(self):
        self.pending(context=False)
        self.write_state(dict(self.state, active_slot="green", active_version="3", approver="new"))
        self.live = self.version("2")
        self.assert_refused_unchanged("Previous approval metadata unavailable")
        self.compose.assert_not_called()

    def test_multiple_pending_operations_are_not_ordered_by_utc(self):
        self.pending(created_at="2026-11-01T10:00:02+00:00")
        self.pending(operation="operation-4", created_at="2026-11-01T09:59:59+00:00")
        self.assert_refused_unchanged("Multiple incomplete")
        self.compose.assert_not_called()

    def test_foreign_journal_or_invalid_slots_are_refused_before_reload(self):
        for change in ({"data_profile_id": "capital-bikeshare"}, {"to_slot": "blue"},
                       {"to_slot": "other"}, {"to_version": "0"}, {"approver": " "}):
            with self.subTest(change=change):
                with self.db.transaction() as connection:
                    connection.execute("DELETE FROM deployments")
                self.pending(**change)
                self.assert_refused_unchanged("journal")
        self.compose.assert_not_called()

    def test_wrong_live_identity_is_refused_before_reload(self):
        self.pending()
        for field, value in (("model_version", "99"), ("model_name", "other"),
                             ("artifact_sha256", "f" * 64), ("quality_gate_status", "failed"),
                             ("feature_contract_version", "other"), ("data_profile_id", "other")):
            with self.subTest(field=field):
                self.live = dict(self.version("3"), **{field: value})
                self.assert_refused_unchanged("Proxy")
        self.compose.assert_not_called()

    def test_live_identity_change_after_reload_remains_pending(self):
        self.pending()
        self.http.side_effect = [self.version("3"), dict(self.version("3"), artifact_sha256="bad")]
        self.assert_refused_unchanged("artifact_sha256")
        self.assertGreater(self.compose.call_count, 0)

    def test_wrong_ready_identity_does_not_finalize(self):
        self.pending()
        self.http.side_effect = [self.version("3"), self.version("3"), dict(self.version("2"), status="ready")]
        self.assert_refused_unchanged("Ready model_version")

    def test_blocked_staging_response_is_not_a_deployment(self):
        self.pending()
        self.live["candidate_id"] = "synthetic-staging-candidate"
        self.live["deployment_permission"] = "blocked"
        self.assert_refused_unchanged("blocked staging")
        self.compose.assert_not_called()

    def test_mismatched_previous_state_is_refused(self):
        wrong = dict(self.state, active_version="99")
        self.pending(probe_json=json.dumps({"recovery_context": {"schema_version": 1, "previous_state": wrong}}))
        self.assert_refused_unchanged("Previous state does not match")
        self.compose.assert_not_called()

    def test_registry_and_proxy_cannot_jointly_replace_the_prepared_artifact(self):
        self.pending()
        self.live["artifact_sha256"] = "f" * 64
        registered = self.registered("bike-demand", 3)
        registered.tags["artifact_sha256"] = "f" * 64
        self.registry.get_model_version.side_effect = None
        self.registry.get_model_version.return_value = registered
        self.assert_refused_unchanged("prepared deployment")
        self.compose.assert_not_called()

    def test_missing_prepared_identity_in_schema2_is_not_legacy_fallback(self):
        self.pending(probe_json=json.dumps({"recovery_context": {"schema_version": 2, "previous_state": self.state}}))
        self.assert_refused_unchanged("prepared deployment")
        self.compose.assert_not_called()

    def test_schema1_context_keeps_the_documented_legacy_path(self):
        self.pending(probe_json=json.dumps({"recovery_context": {"schema_version": 1, "previous_state": self.state}}))
        self.recover()
        self.assertEqual(self.read_state()["approver"], "new-operator")

    def test_capital_recovery_cannot_bypass_required_decision_evidence(self):
        target = "capital-trip-starts-hourly-v1"
        state = dict(self.state, data_profile_id="capital-bikeshare", target_contract_id=target)
        self.write_state(state)
        self.pending(context=False, data_profile_id="capital-bikeshare", target_contract_id=target)
        registered = SimpleNamespace(name="bike-demand-capital", version="3", status="READY",
                                     tags={"quality_gate": "passed", "artifact_sha256": "3" * 64,
                                           "data_profile_id": "capital-bikeshare", "target_contract_id": target})
        self.registry.get_model_version.side_effect = None
        self.registry.get_model_version.return_value = registered
        with patch.object(cli, "DATA_PROFILE_ID", "capital-bikeshare"), patch.object(cli, "TARGET_CONTRACT_ID", target), patch.object(cli, "MODEL_NAME", "bike-demand-capital"):
            self.assert_refused_unchanged("not eligible for deployment")
        self.compose.assert_not_called()

    def test_reload_failure_does_not_finalize(self):
        self.pending()
        self.compose.side_effect = RuntimeError("reload failed")
        self.assert_refused_unchanged("reload failed")

    def test_no_pending_still_checks_state_against_proxy(self):
        self.live = self.version("99")
        self.assert_refused_unchanged("model_version")

    def test_recover_shares_promote_lock(self):
        with (self.runtime / "deployment.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.recover()
            with self.assertRaises(BlockingIOError):
                cli._promote("3", "operator", "reason", False, False)
        self.http.assert_not_called()

    def test_successful_promotion_records_new_approval_and_previous_snapshot(self):
        self.http.side_effect = [self.version("2"), self.version("3")]
        result = cli._promote("3", "operator-3", "promote reason", False, False)
        state = self.read_state()
        self.assertEqual((state["approver"], state["reason"]), ("operator-3", "promote reason"))
        self.assertEqual(state["last_deployment_id"], result["deployment_id"])
        context = json.loads(self.journal()[0]["probe_json"])["recovery_context"]
        self.assertEqual(context["previous_state"], self.state)

    def test_compensation_failure_remains_recoverable(self):
        self.live = self.version("2")
        self.probe.side_effect = RuntimeError("candidate parity failed")
        self.compose.side_effect = [None, RuntimeError("compensation reload failed")]
        with self.assertRaisesRegex(RuntimeError, "unresolved; run deploy recover"):
            cli._promote("3", "operator", "reason", False, False)
        self.assertEqual((self.journal()[0]["status"], self.journal()[0]["phase"]),
                         ("in_progress", "recovery_required"))
        self.assertEqual(self.read_state(), self.state)
        self.compose.side_effect = None
        self.recover()
        self.assertEqual(self.journal()[0]["status"], "failed")

    def test_verified_compensation_marks_failed_and_keeps_old_metadata(self):
        self.live = self.version("2")
        self.probe.side_effect = RuntimeError("candidate parity failed")
        with self.assertRaisesRegex(RuntimeError, "candidate parity failed"):
            cli._promote("3", "operator", "reason", False, False)
        self.assertEqual(self.journal()[0]["status"], "failed")
        self.assertEqual(self.read_state(), self.state)

    def test_pending_operation_prevents_new_promotion(self):
        self.pending()
        self.live = self.version("2")
        with self.assertRaisesRegex(RuntimeError, "incomplete deployment exists"):
            cli._promote("3", "operator", "reason", False, False)
        self.assertEqual(len(self.journal()), 1)
        self.compose.assert_not_called()

    def test_unloadable_history_is_retained_but_rejected_before_any_deployment_write(self):
        self.live = self.version("2")
        with patch("bike_mlops.model.load_registered_model", side_effect=FileNotFoundError("No such artifact")):
            with self.assertRaises(FileNotFoundError):
                cli._promote("1", "operator", "rollback", True, False)
        self.assertEqual(self.read_state()["previous_versions"], ["1"])
        self.assertEqual(self.journal(), [])
        self.assertFalse((self.runtime / "green.env").exists())
        self.compose.assert_not_called()
