"""Contract and regression tests: test_deployment."""
from __future__ import annotations


# ========================================================================
# test_deployment_inspection
# ========================================================================

import hashlib
from contextlib import redirect_stdout
import io
import json
import os
import sqlite3
import tempfile as _deployment_inspection_tempfile
import unittest as _deployment_inspection_unittest
from pathlib import Path as _deployment_inspection_Path
from unittest.mock import patch as _deployment_inspection_patch

from bike_mlops.db import Database
from bike_mlops.deployment import inspect_state

PROFILE, TARGET = "uci-bike-sharing", "uci-bike-sharing-cnt-dataset-local-hour"


class DeploymentInspectionTests(_deployment_inspection_unittest.TestCase):
    def setUp(self):
        self.temp = _deployment_inspection_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = _deployment_inspection_Path(self.temp.name)
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
        with _deployment_inspection_patch('bike_mlops.deployment._deployment_inspection_sqlite3.connect', side_effect=traced):
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
        from bike_mlops import deployment as module
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
        with _deployment_inspection_patch.object(module, "_read_state_file", side_effect=changed):
            self.assertEqual(self.check()["status"], "state_changed_during_inspection")

    def test_cli_json_and_exit_code_do_not_use_database_initializer(self):
        from bike_mlops import cli
        self.row()
        self.path.rename(self.root / "deployment-state.json")
        with _deployment_inspection_patch.object(cli, "RUNTIME_DIR", self.root), _deployment_inspection_patch.dict(os.environ, {"DATABASE_URL": self.url}), \
             _deployment_inspection_patch.object(cli, "_database", side_effect=AssertionError("Initializer must not run")) as initialize, \
             redirect_stdout(io.StringIO()) as output:
            args = cli.build_parser().parse_args(["deploy", "inspect"])
            with self.assertRaises(SystemExit) as error:
                args.func(args)
        self.assertEqual(error.exception.code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "matched")
        initialize.assert_not_called()


# ========================================================================
# test_deployment_metadata
# ========================================================================

"""Operation binding never infers approval or selects by latest version."""
import json as _deployment_metadata_json
import os as _deployment_metadata_os
from pathlib import Path as _deployment_metadata_Path
import tempfile as _deployment_metadata_tempfile
import unittest as _deployment_metadata_unittest
from unittest.mock import patch as _deployment_metadata_patch

from bike_mlops.db import Database as _deployment_metadata_Database
from bike_mlops.deployment import inspect_api_operation, operation_identity, read_connection

P, T = 'uci-bike-sharing', 'uci-bike-sharing-cnt-dataset-local-hour'


class DeploymentMetadataTests(_deployment_metadata_unittest.TestCase):
    def setUp(self):
        self.temp = _deployment_metadata_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.url = 'sqlite:///' + str(_deployment_metadata_Path(self.temp.name) / 'audit.db')
        self.db = _deployment_metadata_Database(self.url, P, T)
        self.db.initialize()
        self.row = dict(deployment_id='operation-1', status='in_progress', phase='prepared',
            data_profile_id=P, target_contract_id=T, from_slot='blue', to_slot='green', from_version='2',
            to_version='3', approver='operator declaration', reason='user supplied reason',
            created_at='2026-11-03T00:00:05Z', updated_at='2026-11-03T00:00:05Z')
        self.binding = operation_identity(self.row, 'a'*64)
        identity = dict(model_name='bike-demand', model_version='3', artifact_sha256='a'*64,
            data_profile_id=P, target_contract_id=T, deployment_operation=self.binding)
        self.row['probe_json'] = _deployment_metadata_json.dumps(dict(recovery_context=dict(schema_version=3, candidate_identity=identity)))
        self.insert(self.row)

    def insert(self, row):
        with self.db.transaction() as c:
            self.db.execute(c, 'INSERT INTO deployments(' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))

    def inspect(self, **changes):
        args = dict(url=self.url, operation_id='operation-1', slot='green', profile=P, target=T,
                    model_name='bike-demand', version='3', artifact_hash='a'*64)
        return inspect_api_operation(**dict(args, **changes))

    def test_prepared_then_completed_reads_same_row_and_declarations(self):
        self.assertEqual(self.inspect()['status'], 'matched_pending')
        with self.db.transaction() as c:
            c.execute("UPDATE deployments SET status='completed',phase='completed'")
        result = self.inspect()
        self.assertEqual(result['status'], 'matched_completed')
        self.assertEqual(result['operation'], self.binding)
        self.assertFalse(result['approval_evidence'])

    def test_same_version_later_operation_and_utc_regression_do_not_replace_binding(self):
        self.insert(dict(self.row, deployment_id='operation-2', approver='different', created_at='2026-11-02T00:00:00Z'))
        self.assertEqual(self.inspect()['operation'], self.binding)
        self.assertEqual(self.inspect(operation_id='operation-2')['status'], 'operation_metadata_mismatch')

    def test_missing_legacy_wrong_slot_profile_artifact_are_distinct(self):
        for changes, expected in (({'operation_id':None}, 'legacy_unverifiable'),
            ({'operation_id':'absent'}, 'operation_missing'), ({'slot':'blue'}, 'runtime_identity_mismatch'),
            ({'version':'2'}, 'runtime_identity_mismatch'), ({'profile':'other'}, 'database_profile_mismatch'),
            ({'artifact_hash':'b'*64}, 'artifact_identity_mismatch'), ({'slot':'other'}, 'invalid_operation_binding')):
            with self.subTest(changes=changes):
                self.assertEqual(self.inspect(**changes)['status'], expected)

    def test_changed_approval_or_failed_row_is_not_presented_as_bound(self):
        with self.db.transaction() as c:
            c.execute("UPDATE deployments SET approver='changed'")
        self.assertEqual(self.inspect()['status'], 'operation_metadata_mismatch')
        with self.db.transaction() as c:
            c.execute("UPDATE deployments SET approver='operator declaration',status='failed'")
        self.assertEqual(self.inspect()['status'], 'operation_not_active')

    def test_no_creation_readonly_and_safe_error_response(self):
        missing = _deployment_metadata_Path(self.temp.name) / 'missing.db'
        self.assertEqual(self.inspect(url='sqlite:///' + str(missing))['status'], 'unavailable')
        self.assertFalse(missing.exists())
        with read_connection(self.url) as (c, _):
            with self.assertRaises(Exception):
                c.execute("DELETE FROM deployments")
        result = self.inspect(url='postgresql://secret:password@localhost:1/private')
        self.assertNotIn('secret', _deployment_metadata_json.dumps(result))
        self.assertNotIn('password', _deployment_metadata_json.dumps(result))

    def test_profile_is_checked_before_any_deployment_select(self):
        import sqlite3
        from contextlib import contextmanager
        selected=[]
        @contextmanager
        def connection(_):
            with read_connection(self.url) as (c, ph):
                c.set_trace_callback(selected.append)
                yield c, ph
        with _deployment_metadata_patch('bike_mlops.deployment.read_connection', connection):
            # Keep original helper callable inside the fixture context.
            result=self.inspect(profile='foreign')
        self.assertEqual(result['status'], 'database_profile_mismatch')
        self.assertFalse(any('FROM deployments' in s for s in selected))

    def test_api_presents_pending_then_completed_and_refuses_tampered_binding(self):
        from fastapi.testclient import TestClient
        from bike_mlops import api
        class Constant:
            def predict(self, frame): return [1.0]
        with _deployment_metadata_patch.dict(_deployment_metadata_os.environ, DATABASE_URL=self.url, MODEL_VERSION='3', DEPLOYMENT_ID='operation-1', DEPLOYMENT_SLOT='green'), \
             _deployment_metadata_patch.object(api, '_runtime_model', return_value=(Constant(), '3', 'a'*64, 'passed', None)), TestClient(api.app) as client:
            self.assertEqual(client.get('/ready').json()['deployment_operation']['status'], 'matched_pending')
            with self.db.transaction() as c:
                c.execute("UPDATE deployments SET status='completed',phase='completed'")
            self.assertEqual(client.get('/version').json()['deployment_operation']['operation'], self.binding)
            self.assertEqual(client.get('/ready').json()['deployment_operation']['status'], 'matched_completed')
            with self.db.transaction() as c:
                c.execute("UPDATE deployments SET reason='tampered'")
            self.assertEqual(client.get('/ready').status_code, 503)
            self.assertNotIn('tampered', client.get('/version').text)


# ========================================================================
# test_deployment_process
# ========================================================================

"""Real child processes prove timeout, interruption, and JSON/env boundaries."""
import json as _deployment_process_json
import os as _deployment_process_os
from pathlib import Path as _deployment_process_Path
import signal
import subprocess
import sys
import tempfile as _deployment_process_tempfile
import time
import unittest as _deployment_process_unittest
from unittest.mock import patch as _deployment_process_patch

from bike_mlops import cli
from bike_mlops.deployment_runtime import run_operation, remaining_seconds, strict_result, interruption_guard
from bike_mlops.deployment_runtime import DeploymentTimeout


class DeploymentProcessTests(_deployment_process_unittest.TestCase):
    def test_success_is_json_only_and_environment_is_explicit(self):
        code = "import json,os,sys; p=json.load(sys.stdin); print(json.dumps({'mode':p['mode'],'secret_present':'UNRELATED_SECRET' in os.environ,'deadline':os.environ['BIKE_DEPLOYMENT_DEADLINE_NS']}))"
        with _deployment_process_patch.dict(_deployment_process_os.environ, UNRELATED_SECRET='must not propagate'):
            result = run_operation({'mode':'recover'}, command=[sys.executable, '-c', code], timeout_seconds=3)
        self.assertFalse(result['secret_present'])
        self.assertGreater(int(result['deadline']), 0)

    def test_deadline_kills_a_term_ignoring_child_without_wait_extension(self):
        with _deployment_process_tempfile.TemporaryDirectory() as d:
            pid = _deployment_process_Path(d)/'pid'
            code = f"import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); open({str(pid)!r},'w').write(str(os.getpid())); time.sleep(60)"
            began=time.monotonic()
            with _deployment_process_patch('bike_mlops.deployment_runtime.TERM_SECONDS', .1), self.assertRaisesRegex(DeploymentTimeout, 'unconfirmed'):
                run_operation({'mode':'recover'}, command=[sys.executable,'-c',code], timeout_seconds=.3)
            self.assertLess(time.monotonic()-began, 3)
            self.assertFalse(_deployment_process_Path('/proc/'+pid.read_text()).exists())

    def test_response_lost_after_side_effect_is_unknown_and_not_retried(self):
        with _deployment_process_tempfile.TemporaryDirectory() as d:
            ledger = _deployment_process_Path(d)/'ledger'
            code = f"import time; open({str(ledger)!r},'a').write('sent-fixed-request-id\\n'); time.sleep(30)"
            with self.assertRaises(DeploymentTimeout):
                run_operation({'mode':'recover'}, command=[sys.executable,'-c',code], timeout_seconds=.3)
            self.assertEqual(ledger.read_text().splitlines(), ['sent-fixed-request-id'])

    def test_nonzero_and_invalid_json_do_not_echo_native_error(self):
        with self.assertRaisesRegex(RuntimeError, 'exit 23') as caught:
            run_operation({'mode':'recover'}, command=[sys.executable,'-c',"print('secret'); raise SystemExit(23)"], timeout_seconds=3)
        self.assertNotIn('secret', str(caught.exception))
        for raw in ('{"x":1e999}', '{"x":NaN}', '{"x":1,"x":2}', '[]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                strict_result(raw)

    def test_utc_regression_cannot_extend_monotonic_budget(self):
        with _deployment_process_patch.dict(_deployment_process_os.environ, BIKE_DEPLOYMENT_DEADLINE_NS='2000000000'), _deployment_process_patch('time.monotonic_ns', return_value=1500000000):
            self.assertEqual(remaining_seconds(30), .5)
        with _deployment_process_patch.dict(_deployment_process_os.environ, BIKE_DEPLOYMENT_DEADLINE_NS='2000000000'), _deployment_process_patch('time.monotonic_ns', return_value=2000000001):
            with self.assertRaises(DeploymentTimeout): remaining_seconds(30)

    def test_term_during_registry_wait_is_recorded_and_child_exits(self):
        with _deployment_process_tempfile.TemporaryDirectory() as d:
            ready = _deployment_process_Path(d)/'ready'
            code = f"from bike_mlops.deployment_runtime import interruption_guard,run_operation; from pathlib import Path; import sys; Path({str(ready)!r}).touch();\nwith interruption_guard(): run_operation({{'mode':'recover'}}, command=[sys.executable,'-c','import time; time.sleep(30)'], timeout_seconds=20)"
            p=subprocess.Popen([sys.executable,'-c',code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                until=time.monotonic()+5
                while not ready.exists() and time.monotonic()<until: time.sleep(.01)
                self.assertTrue(ready.exists())
                time.sleep(.1)
                p.send_signal(signal.SIGTERM)
                out,err=p.communicate(timeout=5)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn(b'outcome unconfirmed', err)
            finally:
                if p.poll() is None: p.kill(); p.wait()

    def test_cli_uses_supervisor_for_promote_and_recover(self):
        from types import SimpleNamespace
        with _deployment_process_patch.object(cli,'run_operation',return_value={'status':'unconfirmed'}) as run, _deployment_process_patch.object(cli,'_print'):
            cli.command_deploy_promote(SimpleNamespace(approver='declared',version=3,reason='test',rollback=False,diagnostic=False))
            self.assertEqual(run.call_args.args[0]['mode'],'promote')
            cli.command_deploy_recover(SimpleNamespace())
            self.assertEqual(run.call_args.args[0], {'mode':'recover'})

    def test_term_of_leader_also_kills_term_ignoring_descendant(self):
        with _deployment_process_tempfile.TemporaryDirectory() as d:
            path=_deployment_process_Path(d)/'descendant'
            child=f"import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); open({str(path)!r},'w').write(str(os.getpid())); time.sleep(60)"
            code=f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
            with self.assertRaises(DeploymentTimeout):
                run_operation({'mode':'recover'},command=[sys.executable,'-c',code],timeout_seconds=.5)
            pid=path.read_text()
            stat=_deployment_process_Path('/proc/'+pid+'/stat')
            # A short-lived zombie awaiting system reaping is not a running child.
            self.assertTrue(not stat.exists() or stat.read_text().rsplit(')',1)[1].split()[0]=='Z')

    def test_phase_interrupts_keep_prepared_operation_pending_and_do_not_compensate(self):
        for phase in ('registry', 'artifact', 'proxy', 'state'):
            with self.subTest(phase=phase), _deployment_process_tempfile.TemporaryDirectory() as d:
                result=_deployment_process_Path(d)/'outcome.json'
                code='import sys,json,time,os,signal\nfrom pathlib import Path\nsys.path.insert(0, \'tests\')\nfrom test_deployment import DeploymentRecoveryTests\nfrom bike_mlops.deployment_runtime import interruption_guard\nx=DeploymentRecoveryTests(); x.setUp()\nphase=PHASE\nx.live=x.version(\'2\')\nx.http.side_effect=lambda *a,**kw: x.version(\'2\') if not x.journal() else x.current_candidate()\ndef hang(*a,**kw): os.kill(os.getpid(), signal.SIGTERM); raise AssertionError("TERM ignored")\nif phase==\'registry\': x.registry.get_model_version.side_effect=hang\nelif phase==\'artifact\': x.probe.side_effect=hang\nelse:\n original=x.compose.side_effect\n def compose(*a,**kw):\n  if phase==\'proxy\' and a[0]==\'exec\': hang()\n x.compose.side_effect=compose\n if phase==\'state\':\n  original_write=__import__(\'bike_mlops.cli\',fromlist=[\'_write_atomic\'])._write_atomic\n  def write(path, content):\n   original_write(path,content)\n   if path.name==\'deployment-state.json\': hang()\n  x.stack.enter_context(__import__(\'unittest.mock\',fromlist=[\'patch\']).patch(\'bike_mlops.cli._write_atomic\',side_effect=write))\ntry:\n with interruption_guard(): x.result=__import__(\'bike_mlops.cli\',fromlist=[\'_promote\'])._promote(\'3\',\'operator\',\'test\',False,False)\nexcept BaseException:\n Path(OUT).write_text(json.dumps({\'rows\':x.journal(),\'state\':x.read_state(),\'compose_count\':x.compose.call_count}))\n raise SystemExit(1)\n'.replace('PHASE',repr(phase)).replace('OUT',repr(str(result)))
                with self.assertRaises(RuntimeError):
                    run_operation({'mode':'promote'},command=[sys.executable,'-c',code],timeout_seconds=15)
                outcome=_deployment_process_json.loads(result.read_text())
                if phase=='registry':
                    self.assertEqual(outcome['rows'],[])
                    self.assertEqual(outcome['compose_count'],0)
                else:
                    self.assertEqual(outcome['rows'][0]['status'],'in_progress')
                    self.assertEqual(outcome['rows'][0]['phase'],'recovery_required')
                    if phase=='state': self.assertEqual(outcome['state']['active_version'],'3')

    def test_safe_diagnostics_drop_native_output_and_invalid_json(self):
        import io
        from bike_mlops.deployment_runtime import _SafeStderr
        stream=io.StringIO(); safe=_SafeStderr(stream)
        safe.write('postgresql://secret:password@host/db\n')
        safe.write('{"kind":"deployment_progress","argv":"secret"}\n')
        safe.write('{"kind":"deployment_worker_failure","error_type":"TimeoutError","outcome":"unconfirmed"}\n')
        self.assertNotIn('secret',stream.getvalue())
        self.assertEqual(len(stream.getvalue().splitlines()),1)

    def test_cleanup_failure_is_reported_as_unconfirmed(self):
        with _deployment_process_patch('bike_mlops.deployment_runtime._group_alive',return_value=True), \
             _deployment_process_patch('bike_mlops.deployment_runtime.KILL_SECONDS',.05):
            with self.assertRaisesRegex(DeploymentTimeout,'descendants did not exit; cleanup unconfirmed'):
                run_operation({'mode':'recover'},command=[sys.executable,'-c','import time;time.sleep(30)'],timeout_seconds=.2)

    def test_http_side_effect_can_finish_after_client_timeout_without_retry(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        received, release = threading.Event(), threading.Event()
        ledger=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a): pass
            def do_POST(self):
                payload=_deployment_process_json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.set()
                release.wait(5)
                ledger.append(payload['request_id'])
                try:
                    self.send_response(200);self.end_headers();self.wfile.write(b'{}')
                except (BrokenPipeError,ConnectionResetError): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_address[1]}/predict'
        code=f"import urllib.request,json; urllib.request.urlopen(urllib.request.Request({url!r},data=json.dumps({{'request_id':'deploy-probe:fixed-operation'}}).encode(),headers={{'Content-Type':'application/json'}}),timeout=30)"
        try:
            with self.assertRaises(DeploymentTimeout):
                run_operation({'mode':'promote'},command=[sys.executable,'-c',code],timeout_seconds=.8)
            self.assertTrue(received.is_set())
            self.assertEqual(ledger,[])
            release.set()
            until=time.monotonic()+2
            while not ledger and time.monotonic()<until: time.sleep(.01)
            self.assertEqual(ledger,['deploy-probe:fixed-operation'])
        finally:
            release.set();server.shutdown();server.server_close();thread.join(timeout=2)

    def test_killed_supervisor_notifies_worker_without_touching_other_processes(self):
        with _deployment_process_tempfile.TemporaryDirectory() as d:
            ready=_deployment_process_Path(d)/'ready'; stopped=_deployment_process_Path(d)/'stopped'
            worker=f"import signal,time; from pathlib import Path; from bike_mlops.deployment_runtime import _bind_supervisor; signal.signal(signal.SIGTERM,lambda *a:(Path({str(stopped)!r}).write_text('term'),exit(1))); _bind_supervisor(); Path({str(ready)!r}).touch(); time.sleep(30)"
            supervisor=f"import sys; from bike_mlops.deployment_runtime import run_operation; run_operation({{'mode':'recover'}},command=[sys.executable,'-c',{worker!r}],timeout_seconds=20)"
            p=subprocess.Popen([sys.executable,'-c',supervisor],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                until=time.monotonic()+5
                while not ready.exists() and time.monotonic()<until: time.sleep(.01)
                self.assertTrue(ready.exists())
                p.kill();p.wait(timeout=2)
                until=time.monotonic()+2
                while not stopped.exists() and time.monotonic()<until: time.sleep(.01)
                self.assertEqual(stopped.read_text(),'term')
            finally:
                if p.poll() is None: p.kill();p.wait()

    def test_real_worker_entry_dispatches_and_returns_finite_json_without_model_transport(self):
        code="from unittest.mock import patch\nimport sys\nfrom bike_mlops import cli\nfrom bike_mlops.deployment_runtime import main\ndef promote(*args):\n sys.stderr.write('native postgresql://secret:password@host/db\\n')\n return {'model_version': args[0], 'prediction': 1.25}\nwith patch.object(cli,'_promote',side_effect=promote): main()\n"
        result=run_operation(dict(mode='promote',version='3',approver='declared',reason='fixture',rollback=False,diagnostic=False),
                             command=[sys.executable,'-c',code],timeout_seconds=15)
        self.assertEqual(result,dict(model_version='3',prediction=1.25))

    def test_compose_configuration_is_forwarded_without_forwarding_unrelated_secrets(self):
        keys=['APP_DB_PASSWORD','MLFLOW_DB_PASSWORD','POSTGRES_ADMIN_PASSWORD','APP_DB_HOST_PORT']
        code=f"import json,os; print(json.dumps({{'configuration_present':all(k in os.environ for k in {keys!r}),'unrelated_secret_present':'UNRELATED_SECRET' in os.environ}}))"
        with _deployment_process_patch.dict(_deployment_process_os.environ, **dict.fromkeys(keys,'synthetic-only'), UNRELATED_SECRET='excluded'):
            result=run_operation({'mode':'recover'},command=[sys.executable,'-c',code],timeout_seconds=3)
        self.assertTrue(result['configuration_present'])
        self.assertFalse(result['unrelated_secret_present'])


# ========================================================================
# test_deployment_progress
# ========================================================================

import io as _deployment_progress_io
import json as _deployment_progress_json
from pathlib import Path as _deployment_progress_Path
import subprocess as _deployment_progress_subprocess
import tempfile as _deployment_progress_tempfile
import time as _deployment_progress_time
import unittest as _deployment_progress_unittest
from contextlib import redirect_stderr
from unittest.mock import patch as _deployment_progress_patch

from bike_mlops import cli as _deployment_progress_cli
from bike_mlops.deployment_runtime import DeploymentTimeout as _deployment_progress_DeploymentTimeout, deployment_stage


class DeploymentProgressTests(_deployment_progress_unittest.TestCase):
    def test_heartbeat_is_process_monotonic_with_safe_stage_and_journal(self):
        with _deployment_progress_tempfile.TemporaryDirectory() as directory, redirect_stderr(_deployment_progress_io.StringIO()) as output:
            root = _deployment_progress_Path(directory)
            with deployment_stage(root, "candidate_ready", interval=.005, operation_id="operation-1"):
                _deployment_progress_time.sleep(.025)
            events = [_deployment_progress_json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(events, [_deployment_progress_json.loads(line) for line in (root / "deployment-progress.jsonl").read_text().splitlines()])
            self.assertEqual(events[0]["status"], "started")
            self.assertEqual(events[-1]["status"], "stage_completed")
            self.assertIn("in_progress", [item["status"] for item in events])
            self.assertEqual([e["monotonic_ns"] for e in events], sorted(e["monotonic_ns"] for e in events))
            self.assertTrue(all(e["deployment_id"] == "operation-1" for e in events))

    def test_docker_timeout_is_bounded_unconfirmed_and_does_not_leak_native_output(self):
        with _deployment_progress_tempfile.TemporaryDirectory() as directory, _deployment_progress_patch.object(_deployment_progress_cli, "RUNTIME_DIR", _deployment_progress_Path(directory)), redirect_stderr(_deployment_progress_io.StringIO()) as output:
            with _deployment_progress_patch.object(_deployment_progress_cli.subprocess, "run", side_effect=_deployment_progress_subprocess.TimeoutExpired(["secret-command"], 600, output="secret-value")) as run:
                with self.assertRaisesRegex(_deployment_progress_DeploymentTimeout, "unconfirmed") as error:
                    _deployment_progress_cli._compose("up", "-d", "--build", "api-green")
            self.assertEqual(run.call_args.kwargs["timeout"], 600)
            self.assertNotIn("secret", output.getvalue() + str(error.exception))
            self.assertEqual(_deployment_progress_json.loads(output.getvalue().splitlines()[-1])["outcome"], "unconfirmed")

    def test_nonbuild_docker_timeout_and_nonzero_are_not_swallowed(self):
        with _deployment_progress_tempfile.TemporaryDirectory() as directory, _deployment_progress_patch.object(_deployment_progress_cli, "RUNTIME_DIR", _deployment_progress_Path(directory)), redirect_stderr(_deployment_progress_io.StringIO()):
            with _deployment_progress_patch.object(_deployment_progress_cli.subprocess, "run", side_effect=_deployment_progress_subprocess.CalledProcessError(23, ["native-secret"], output="secret-value")) as run:
                with self.assertRaisesRegex(RuntimeError, "exit 23") as error:
                    _deployment_progress_cli._compose("exec", "caddy")
            self.assertEqual(run.call_args.kwargs["timeout"], 30)
            self.assertNotIn("secret", str(error.exception))

    def test_interrupt_and_unsafe_log_are_not_reported_as_complete(self):
        with _deployment_progress_tempfile.TemporaryDirectory() as directory, redirect_stderr(_deployment_progress_io.StringIO()) as output:
            root = _deployment_progress_Path(directory)
            with self.assertRaises(KeyboardInterrupt):
                with deployment_stage(root, "recover"):
                    raise KeyboardInterrupt()
            self.assertEqual(_deployment_progress_json.loads(output.getvalue().splitlines()[-1])["status"], "interrupted_or_failed")
            (root / "deployment-progress.jsonl").unlink()
            (root / "deployment-progress.jsonl").symlink_to(root / "outside")
            with self.assertRaises(OSError):
                with deployment_stage(root, "recover"):
                    self.fail("Symlink journal was accepted")


# ========================================================================
# test_deployment_recovery
# ========================================================================

"""Isolated journal/state recovery; Docker and registry IO are controlled doubles."""


import fcntl
import io as _deployment_recovery_io
import json as _deployment_recovery_json
import tempfile as _deployment_recovery_tempfile
import unittest as _deployment_recovery_unittest
from contextlib import ExitStack, redirect_stdout as _deployment_recovery_redirect_stdout
from pathlib import Path as _deployment_recovery_Path
from types import SimpleNamespace
from unittest.mock import Mock, patch as _deployment_recovery_patch

import pandas as pd

from bike_mlops import cli as _deployment_recovery_cli
from bike_mlops.db import Database as _deployment_recovery_Database

_deployment_recovery_PROFILE = "uci-bike-sharing"
_deployment_recovery_TARGET = "uci-bike-sharing-cnt-dataset-local-hour"


class DeploymentRecoveryTests(_deployment_recovery_unittest.TestCase):
    def setUp(self):
        self.temp = _deployment_recovery_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = _deployment_recovery_Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.db = _deployment_recovery_Database(f"sqlite:///{self.root / 'ops.db'}", _deployment_recovery_PROFILE, _deployment_recovery_TARGET)
        self.db.initialize()
        self.state = dict(active_slot="blue", active_version="2", data_profile_id=_deployment_recovery_PROFILE,
                          target_contract_id=_deployment_recovery_TARGET, deployment_mode="approved",
                          previous_versions=["1"], last_deployment_id="bootstrap",
                          approver="old-operator", reason="old decision")
        self.write_state(self.state)
        (self.runtime / "Caddyfile").write_text("old config\n")
        self.csv = self.root / "synthetic.csv"
        self.csv.write_text("dteday,hr,cnt\n2012-01-01,0,1\n")
        self.registry = SimpleNamespace(get_model_version=Mock(side_effect=self.registered))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for key, value in dict(RUNTIME_DIR=self.runtime, DATA_PROFILE_ID=_deployment_recovery_PROFILE,
                               TARGET_CONTRACT_ID=_deployment_recovery_TARGET, MODEL_NAME="bike-demand",
                               DEFAULT_DATASET=self.csv).items():
            self.stack.enter_context(_deployment_recovery_patch.object(_deployment_recovery_cli, key, value))
        self.stack.enter_context(_deployment_recovery_patch.object(_deployment_recovery_cli, "_database", return_value=self.db))
        self.stack.enter_context(_deployment_recovery_patch.object(_deployment_recovery_cli, "_model_registry_client", return_value=self.registry))
        self.compose = self.stack.enter_context(_deployment_recovery_patch.object(_deployment_recovery_cli, "_compose"))
        self.http = self.stack.enter_context(_deployment_recovery_patch.object(_deployment_recovery_cli, "_url_json", side_effect=self.http_reply))
        self.live = self.version("3")
        self.stack.enter_context(_deployment_recovery_patch.object(_deployment_recovery_cli, "_seed_observations", return_value=(0, "synthetic", pd.DataFrame())))
        self.stack.enter_context(_deployment_recovery_patch.object(_deployment_recovery_cli, "_wait_ready", side_effect=lambda *a: {"version": self.current_candidate()}))
        self.probe = self.stack.enter_context(_deployment_recovery_patch.object(_deployment_recovery_cli, "_deployment_probe", return_value={"model_version": "3"}))

    @staticmethod
    def registered(name, version):
        return SimpleNamespace(name=name, version=str(version), status="READY",
                               tags={"quality_gate": "passed", "artifact_sha256": str(version) * 64,
                                     "data_profile_id": _deployment_recovery_PROFILE, "target_contract_id": _deployment_recovery_TARGET})

    @staticmethod
    def version(version):
        return dict(data_profile_id=_deployment_recovery_PROFILE, target_contract_id=_deployment_recovery_TARGET, model_name="bike-demand",
                    model_version=version, artifact_sha256=version * 64,
                    quality_gate_status="passed", feature_contract_version="bike-demand-features-v1")

    def current_candidate(self):
        rows = self.journal()
        if rows and _deployment_recovery_json.loads(rows[-1]["probe_json"] or "{}").get("recovery_context", {}).get("schema_version") == 3:
            row = rows[-1]
            from bike_mlops.deployment import operation_identity
            return dict(self.version(str(row["to_version"])), deployment_operation=dict(
                status="matched_pending", operation=operation_identity(row, str(row["to_version"])*64)))
        return self.version("3")

    def http_reply(self, url, *args, **kwargs):
        return dict(self.live, status="ready") if url.endswith("/ready") else dict(self.live)

    def write_state(self, state):
        (self.runtime / "deployment-state.json").write_text(_deployment_recovery_json.dumps(state))

    def read_state(self):
        return _deployment_recovery_json.loads((self.runtime / "deployment-state.json").read_text())

    def pending(self, operation="operation-3", context=True, **changes):
        row = dict(deployment_id=operation, status="in_progress", phase="candidate_ready",
                   data_profile_id=_deployment_recovery_PROFILE, target_contract_id=_deployment_recovery_TARGET, from_slot="blue", to_slot="green",
                   from_version="2", to_version="3", approver="new-operator", reason="new decision",
                   probe_json=_deployment_recovery_json.dumps({"recovery_context": {"schema_version": 2, "previous_state": self.state,
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
        with _deployment_recovery_redirect_stdout(_deployment_recovery_io.StringIO()) as output:
            _deployment_recovery_cli._print(_deployment_recovery_cli._recover_deployment())
        return _deployment_recovery_json.loads(output.getvalue())

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
        self.pending(probe_json=_deployment_recovery_json.dumps({"recovery_context": {"schema_version": 1, "previous_state": wrong}}))
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
        self.pending(probe_json=_deployment_recovery_json.dumps({"recovery_context": {"schema_version": 2, "previous_state": self.state}}))
        self.assert_refused_unchanged("prepared deployment")
        self.compose.assert_not_called()

    def test_schema1_context_keeps_the_documented_legacy_path(self):
        self.pending(probe_json=_deployment_recovery_json.dumps({"recovery_context": {"schema_version": 1, "previous_state": self.state}}))
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
        with _deployment_recovery_patch.object(_deployment_recovery_cli, "DATA_PROFILE_ID", "capital-bikeshare"), _deployment_recovery_patch.object(_deployment_recovery_cli, "TARGET_CONTRACT_ID", target), _deployment_recovery_patch.object(_deployment_recovery_cli, "MODEL_NAME", "bike-demand-capital"):
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
                with _deployment_recovery_cli._deployment_lock():
                    self.recover()
            with self.assertRaises(BlockingIOError):
                _deployment_recovery_cli._promote("3", "operator", "reason", False, False)
        self.http.assert_not_called()

    def test_successful_promotion_records_new_approval_and_previous_snapshot(self):
        self.http.side_effect = lambda *a, **k: self.version("2") if not self.journal() else self.current_candidate()
        result = _deployment_recovery_cli._promote("3", "operator-3", "promote reason", False, False)
        state = self.read_state()
        self.assertEqual((state["approver"], state["reason"]), ("operator-3", "promote reason"))
        self.assertEqual(state["last_deployment_id"], result["deployment_id"])
        context = _deployment_recovery_json.loads(self.journal()[0]["probe_json"])["recovery_context"]
        self.assertEqual(context["previous_state"], self.state)

    def test_compensation_failure_remains_recoverable(self):
        self.live = self.version("2")
        self.probe.side_effect = RuntimeError("candidate parity failed")
        self.compose.side_effect = [None, RuntimeError("compensation reload failed")]
        with self.assertRaisesRegex(RuntimeError, "unresolved; run deploy recover"):
            _deployment_recovery_cli._promote("3", "operator", "reason", False, False)
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
            _deployment_recovery_cli._promote("3", "operator", "reason", False, False)
        self.assertEqual(self.journal()[0]["status"], "failed")
        self.assertEqual(self.read_state(), self.state)

    def test_docker_timeout_retains_pending_operation_without_claiming_compensation(self):
        from bike_mlops.deployment_runtime import DeploymentTimeout
        self.live = self.version("2")
        self.compose.side_effect = DeploymentTimeout("daemon outcome unconfirmed")
        with self.assertRaises(DeploymentTimeout):
            _deployment_recovery_cli._promote("3", "operator", "reason", False, False)
        self.assertEqual((self.journal()[0]["status"], self.journal()[0]["phase"]),
                         ("in_progress", "recovery_required"))
        self.assertEqual(self.read_state(), self.state)
        self.assertEqual(self.compose.call_count, 1)

    def test_pending_operation_prevents_new_promotion(self):
        self.pending()
        self.live = self.version("2")
        with self.assertRaisesRegex(RuntimeError, "incomplete deployment exists"):
            _deployment_recovery_cli._promote("3", "operator", "reason", False, False)
        self.assertEqual(len(self.journal()), 1)
        self.compose.assert_not_called()

    def test_unloadable_history_is_retained_but_rejected_before_any_deployment_write(self):
        self.live = self.version("2")
        with _deployment_recovery_patch("bike_mlops.model.load_registered_model", side_effect=FileNotFoundError("No such artifact")):
            with self.assertRaises(FileNotFoundError):
                _deployment_recovery_cli._promote("1", "operator", "rollback", True, False)
        self.assertEqual(self.read_state()["previous_versions"], ["1"])
        self.assertEqual(self.journal(), [])
        self.assertFalse((self.runtime / "green.env").exists())
        self.compose.assert_not_called()

    def test_timeout_after_completion_commit_does_not_rewrite_completed_history(self):
        from bike_mlops.deployment_runtime import DeploymentTimeout
        self.http.side_effect = lambda *a, **k: self.version("2") if not self.journal() else self.current_candidate()
        original = _deployment_recovery_cli._deployment_row
        def committed_then_lost(database, operation, phase, *a, **k):
            result = original(database, operation, phase, *a, **k)
            if phase == "completed":
                raise DeploymentTimeout("response lost after commit")
            return result
        with _deployment_recovery_patch.object(_deployment_recovery_cli, "_deployment_row", side_effect=committed_then_lost):
            with self.assertRaisesRegex(RuntimeError, "committed but response"):
                _deployment_recovery_cli._promote("3", "operator", "test", False, False)
        self.assertEqual(self.journal()[0]["status"], "completed")
        self.assertEqual(self.read_state()["active_version"], "3")

    def test_legacy_candidate_recovery_does_not_inherit_previous_operation_binding(self):
        self.state['deployment_operation'] = {'deployment_id': 'previous-operation', 'model_version': '2'}
        self.write_state(self.state)
        self.pending(context=False)
        self.recover()
        self.assertEqual(self.read_state()['active_version'], '3')
        self.assertNotIn('deployment_operation', self.read_state())


# ========================================================================
# test_capital_deployment_decisions
# ========================================================================

import unittest as _capital_deployment_decisions_unittest
from types import SimpleNamespace as _capital_deployment_decisions_SimpleNamespace
from pathlib import Path as _capital_deployment_decisions_Path
import tempfile as _capital_deployment_decisions_tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch as _capital_deployment_decisions_patch
from support import diagnose_decision_test
from support import fixture

from bike_mlops.deployment import CAPITAL_MODEL_NAME, record_deployment_approval as _capital_deployment_decisions_record_deployment_approval, record_operational_review as _capital_deployment_decisions_record_operational_review
from bike_mlops.deployment import require_deployment_eligible as _capital_deployment_decisions_require_deployment_eligible
from bike_mlops.evaluation import BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
from bike_mlops.config import _profiles_load_data_profile as load_data_profile


class FakeRegistry:
    def __init__(self, tags=None, version="2", fail_after_tag=None, body=None):
        self.version = version
        self.tags = dict(tags or {})
        self.fail_after_tag = fail_after_tag
        self.body = body
        self.run_id = "synthetic-run"
        self.artifact_path = self.tags.get("capital_deployment_evidence_path")

    def get_model_version(self, name, version):
        return _capital_deployment_decisions_SimpleNamespace(name=name, version=str(version), status="READY", run_id=self.run_id, tags=dict(self.tags))

    def download_artifacts(self, run_id, artifact_path, dst_path):
        if run_id != self.run_id or artifact_path != self.artifact_path or self.body is None:
            raise FileNotFoundError("Synthetic evidence not found")
        path = _capital_deployment_decisions_Path(dst_path) / "bundle.json"
        path.write_bytes(self.body)
        return str(path)

    def log_artifact(self, run_id, local_path, artifact_path):
        if run_id != self.run_id:
            raise ValueError("Wrong synthetic source run")
        self.body = _capital_deployment_decisions_Path(local_path).read_bytes()
        self.artifact_path = artifact_path + "/" + _capital_deployment_decisions_Path(local_path).name

    def set_model_version_tag(self, name, version, key, value):
        self.tags[key] = value
        if self.fail_after_tag == key:
            self.fail_after_tag = None
            raise RuntimeError("simulated lost response after registry tag write")


def eligible_registry():
    tags, _bundle, body = fixture()
    return FakeRegistry(tags, body=body)


class CapitalDeploymentDecisionTests(_capital_deployment_decisions_unittest.TestCase):
    def setUp(self):
        from bike_mlops import operational_validation as capital_operational_registry
        directory = _capital_deployment_decisions_tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        change = _capital_deployment_decisions_patch.object(capital_operational_registry, '_operational_registry_PROJECT_ROOT', _capital_deployment_decisions_Path(directory.name))
        change.start()
        self.addCleanup(change.stop)
        self.client = eligible_registry()

    @diagnose_decision_test
    def test_utc_boundary_is_strict_and_rejection_retains_exact_clock_values(self):
        from bike_mlops import deployment as evidence
        fixed = datetime(2026, 11, 1, tzinfo=timezone.utc)

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        with _capital_deployment_decisions_patch.object(evidence, "datetime", FixedClock):
            self.assertEqual(evidence.utc_time(fixed.isoformat()), fixed)
            for delta in (timedelta(microseconds=1), timedelta(days=1)):
                future = fixed + delta
                with self.assertRaisesRegex(ValueError, "must not be in the future") as caught:
                    evidence.utc_time(future.isoformat())
                note = " ".join(caught.exception.__notes__)
                self.assertIn("timestamp_utc=" + future.isoformat(), note)
                self.assertIn("observed_at_utc=" + fixed.isoformat(), note)
                self.assertIn("ahead_microseconds=" + str(delta // timedelta(microseconds=1)), note)
                self.assertRegex(note, r"monotonic_ns=\d+")

    @diagnose_decision_test
    def test_regressed_review_validation_clock_blocks_approval_without_tag_writes(self):
        from bike_mlops import deployment as decisions
        from bike_mlops import deployment as evidence
        review = datetime(2026, 11, 1, 0, 0, 1, tzinfo=timezone.utc)

        class ReviewClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return review

        with _capital_deployment_decisions_patch.object(decisions, '_deployment_decisions_datetime', ReviewClock), _capital_deployment_decisions_patch.object(evidence, "datetime", ReviewClock):
            _capital_deployment_decisions_record_operational_review(self.client, "2", "reviewer-a", "passed", "synthetic clock test")
        before = dict(self.client.tags)
        review_text = before["capital_operational_review_at_utc"]
        self.assertEqual(datetime.fromisoformat(review_text), review)
        later = review + timedelta(seconds=1)
        original_check = decisions._deployment_decisions_utc_time

        class LaterClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return later

        class RegressedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return review - timedelta(microseconds=1)

        def check(value):
            if value == review_text:
                with _capital_deployment_decisions_patch.object(evidence, "datetime", RegressedClock):
                    return original_check(value)
            return original_check(value)

        with (_capital_deployment_decisions_patch.object(decisions, '_deployment_decisions_datetime', LaterClock),
              _capital_deployment_decisions_patch.object(evidence, "datetime", LaterClock), _capital_deployment_decisions_patch.object(decisions, '_deployment_decisions_utc_time', check)):
            with self.assertRaisesRegex(ValueError, "must not be in the future") as caught:
                _capital_deployment_decisions_record_deployment_approval(self.client, "2", "approver-b", "synthetic clock test")
            self.assertIn("ahead_microseconds=1 ", " ".join(caught.exception.__notes__))
        self.assertEqual(self.client.tags, before)
        self.assertEqual(self.client.tags["deployment_permission"], "blocked")
        # A separate explicit attempt at a stable time succeeds; no clamp, sleep or implicit retry.
        with _capital_deployment_decisions_patch.object(decisions, '_deployment_decisions_datetime', LaterClock), _capital_deployment_decisions_patch.object(evidence, "datetime", LaterClock):
            result = _capital_deployment_decisions_record_deployment_approval(self.client, "2", "approver-b", "synthetic clock test")
        self.assertEqual(result["deployment_permission"], "eligible")
        self.assertEqual(self.client.tags["capital_operational_review_at_utc"], review_text)

    def test_review_and_separate_approval_bind_exact_version_and_evidence(self):
        reviewed = _capital_deployment_decisions_record_operational_review(self.client, "2", "reviewer-a", "passed", "all fixed checks reviewed")
        self.assertEqual(reviewed["operational_gate_status"], "passed")
        self.assertEqual(reviewed["deployment_permission"], "blocked")
        with self.assertRaisesRegex(ValueError, "must differ"):
            _capital_deployment_decisions_record_deployment_approval(self.client, "2", "Reviewer-A", "approve")

        approved = _capital_deployment_decisions_record_deployment_approval(self.client, "2", "approver-b", "review evidence and authorize")
        self.assertEqual(approved["deployment_permission"], "eligible")
        _capital_deployment_decisions_require_deployment_eligible(
            "capital-bikeshare", self.client.tags,
            model_name=CAPITAL_MODEL_NAME, model_version="2", client=self.client,
        )

    def test_review_is_idempotent_for_same_record_but_immutable_on_conflict(self):
        first = _capital_deployment_decisions_record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")
        second = _capital_deployment_decisions_record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")
        self.assertEqual(first["review_sha256"], second["review_sha256"])
        with self.assertRaisesRegex(ValueError, "overwrite a different decision tag"):
            _capital_deployment_decisions_record_operational_review(self.client, "2", "reviewer-a", "rejected", "changed decision")

    def test_p95_over_limit_cannot_pass_review_or_deploy(self):
        self.client.tags["capital_operational_p95_ms"] = "250.0001"
        with self.assertRaisesRegex(ValueError, "fixed gate"):
            _capital_deployment_decisions_record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")

    def test_failed_measurement_cannot_be_reviewed_as_passed(self):
        self.client.tags["capital_operational_measurement_status"] = "failed"
        with self.assertRaisesRegex(ValueError, "fixed gate"):
            _capital_deployment_decisions_record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")
        _capital_deployment_decisions_record_operational_review(self.client, "2", "reviewer-a", "rejected", "measurement failed")
        with self.assertRaisesRegex(ValueError, "passed operational review"):
            _capital_deployment_decisions_record_deployment_approval(self.client, "2", "approver-b", "approve")

    def test_missing_or_changed_evidence_and_wrong_version_are_rejected(self):
        self.client.tags.pop("capital_operational_audit_status")
        with self.assertRaisesRegex(ValueError, "capital_operational_audit_status"):
            _capital_deployment_decisions_record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")

        self.client = eligible_registry()
        _capital_deployment_decisions_record_operational_review(self.client, "2", "reviewer-a", "passed", "reviewed")
        _capital_deployment_decisions_record_deployment_approval(self.client, "2", "approver-b", "approved")
        with self.assertRaisesRegex(ValueError, "different model version"):
            _capital_deployment_decisions_require_deployment_eligible(
                "capital-bikeshare", self.client.tags,
                model_name=CAPITAL_MODEL_NAME, model_version="3",
            )
        self.client.tags["capital_operational_review_reason"] = "tampered"
        with self.assertRaisesRegex(ValueError, "record hash"):
            _capital_deployment_decisions_require_deployment_eligible(
                "capital-bikeshare", self.client.tags,
                model_name=CAPITAL_MODEL_NAME, model_version="2", client=self.client,
            )

    def test_rejected_review_keeps_deployment_blocked(self):
        result = _capital_deployment_decisions_record_operational_review(self.client, "2", "reviewer-a", "rejected", "evidence is incomplete")
        self.assertEqual(result["operational_gate_status"], "failed")
        self.assertEqual(self.client.tags["deployment_permission"], "blocked")

    def test_partial_review_or_approval_tag_write_fails_closed_and_same_retry_recovers(self):
        client = eligible_registry()
        client.fail_after_tag = "capital_operational_review_reviewer"
        with self.assertRaisesRegex(RuntimeError, "simulated lost response"):
            _capital_deployment_decisions_record_operational_review(client, "2", "reviewer-a", "passed", "reviewed")
        self.assertEqual(client.tags["operational_gate_status"], "not_evaluated")
        self.assertEqual(client.tags["deployment_permission"], "blocked")
        _capital_deployment_decisions_record_operational_review(client, "2", "reviewer-a", "passed", "reviewed")

        client.fail_after_tag = "capital_deployment_approver"
        with self.assertRaisesRegex(RuntimeError, "simulated lost response"):
            _capital_deployment_decisions_record_deployment_approval(client, "2", "approver-b", "approved")
        self.assertEqual(client.tags["deployment_permission"], "blocked")
        _capital_deployment_decisions_record_deployment_approval(client, "2", "approver-b", "approved")
        self.assertEqual(client.tags["deployment_permission"], "eligible")
        _capital_deployment_decisions_require_deployment_eligible(
            "capital-bikeshare", client.tags,
            model_name=CAPITAL_MODEL_NAME, model_version="2", client=client,
        )


# ========================================================================
# test_deployment_gates
# ========================================================================

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from bike_mlops.deployment import record_deployment_approval, record_operational_review
from bike_mlops.deployment import require_deployment_eligible
_deployment_gates_eligible_registry = eligible_registry


class DeploymentGateTests(unittest.TestCase):
    def setUp(self):
        from bike_mlops import operational_validation as capital_operational_registry
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        change = patch.object(capital_operational_registry, '_operational_registry_PROJECT_ROOT', Path(directory.name))
        change.start()
        self.addCleanup(change.stop)
        self.registry = _deployment_gates_eligible_registry()
        record_operational_review(self.registry, "2", "reviewer-a", "passed", "all evidence reviewed")
        record_deployment_approval(self.registry, "2", "approver-b", "approve after review")
        self.tags = self.registry.tags

    def test_capital_requires_all_gates_and_matching_decision_evidence(self):
        require_deployment_eligible(
            "capital-bikeshare", self.tags,
            model_name="bike-demand-capital", model_version="2", client=self.registry,
        )
        for key in (
            "quality_gate", "validation_gate_status", "final_accuracy_gate_status",
            "operational_gate_status", "deployment_permission", "quality_gate_policy_id",
            "quality_gate_policy_sha256", "capital_operational_evidence_status",
            "capital_operational_report_sha256", "capital_operational_measurement_status",
            "capital_operational_numeric_recheck_status", "capital_operational_input_provenance_status",
            "capital_operational_audit_status", "capital_operational_resource_context_status",
            "capital_operational_review_status", "capital_operational_review_sha256",
            "capital_deployment_approval_status", "capital_deployment_approval_sha256",
        ):
            with self.subTest(key=key):
                incomplete = dict(self.tags)
                incomplete.pop(key)
                with self.assertRaisesRegex(ValueError, "not eligible for deployment"):
                    require_deployment_eligible(
                        "capital-bikeshare", incomplete,
                        model_name="bike-demand-capital", model_version="2", client=self.registry,
                    )

    def test_failed_gate_and_diagnostic_permission_do_not_bypass_capital_checks(self):
        for key, value in (
            ("validation_gate_status", "failed"),
            ("final_accuracy_gate_status", "not_evaluable"),
            ("operational_gate_status", "not_evaluated"),
            ("deployment_permission", "blocked"),
            ("quality_gate_policy_sha256", "0" * 64),
            ("capital_operational_measurement_status", "failed"),
        ):
            with self.subTest(key=key, value=value):
                tags = dict(self.tags, **{key: value})
                with self.assertRaisesRegex(ValueError, "not eligible for deployment"):
                    require_deployment_eligible(
                        "capital-bikeshare", tags,
                        model_name="bike-demand-capital", model_version="2", client=self.registry,
                    )

    def test_wrong_model_version_or_modified_decision_reason_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not eligible for deployment"):
            require_deployment_eligible(
                "capital-bikeshare", self.tags,
                model_name="bike-demand-capital", model_version="3",
            )
        tags = dict(self.tags, capital_deployment_approval_reason="changed after approval")
        with self.assertRaisesRegex(ValueError, "not eligible for deployment"):
            require_deployment_eligible(
                "capital-bikeshare", tags,
                model_name="bike-demand-capital", model_version="2", client=self.registry,
            )

    def test_existing_uci_deployment_policy_remains_unchanged(self):
        require_deployment_eligible("uci-bike-sharing", {})


# ========================================================================
# test_capital_deployment_evidence
# ========================================================================

import copy
import json as _capital_deployment_evidence_json
import os as _capital_deployment_evidence_os
import tempfile as _capital_deployment_evidence_tempfile
import unittest as _capital_deployment_evidence_unittest
from pathlib import Path as _capital_deployment_evidence_Path
from types import SimpleNamespace as _capital_deployment_evidence_SimpleNamespace
from unittest.mock import patch as _capital_deployment_evidence_patch

from support import fixture as _capital_deployment_evidence_fixture
_capital_deployment_evidence_FakeRegistry = FakeRegistry
from bike_mlops import deployment as evidence
from bike_mlops import operational_validation as registry
from bike_mlops.deployment import record_operational_review as _capital_deployment_evidence_record_operational_review, record_deployment_approval as _capital_deployment_evidence_record_deployment_approval
from bike_mlops.deployment import require_deployment_eligible as _capital_deployment_evidence_require_deployment_eligible


class CompleteDeploymentEvidenceTests(_capital_deployment_evidence_unittest.TestCase):
    def setUp(self):
        self.temp = _capital_deployment_evidence_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = _capital_deployment_evidence_Path(self.temp.name)
        change = _capital_deployment_evidence_patch.object(registry, '_operational_registry_PROJECT_ROOT', self.root)
        change.start()
        self.addCleanup(change.stop)
        self.tags, self.bundle, self.body = _capital_deployment_evidence_fixture()
        self.client = _capital_deployment_evidence_FakeRegistry(self.tags, body=self.body)

    def approve(self):
        _capital_deployment_evidence_record_operational_review(self.client, "2", "reviewer-a", "passed", "synthetic evidence")
        return _capital_deployment_evidence_record_deployment_approval(self.client, "2", "approver-b", "synthetic evidence")

    def check(self):
        return _capital_deployment_evidence_require_deployment_eligible("capital-bikeshare", self.client.tags,
            model_name=evidence.MODEL, model_version="2", client=self.client)

    def replace_bundle(self, bundle):
        self.client.body = evidence.canonical_bytes(bundle)
        self.client.tags.update(evidence.reference_tags(self.client.body, self.client.run_id))
        self.client.artifact_path = self.client.tags["capital_deployment_evidence_path"]

    def test_real_report_bodies_and_independent_saved_row_recheck_are_required(self):
        result = evidence.validate_bundle(self.bundle, self.tags, "2", "synthetic-run")
        self.assertEqual(result["p95_ms"], 100.0)
        self.approve()
        self.check()
        self.client.body = None
        with self.assertRaisesRegex(ValueError, "not eligible"):
            self.check()

    def test_all_bound_tag_mutations_and_invalid_numbers_are_rejected_after_approval(self):
        self.approve()
        original = dict(self.client.tags)
        changes = [
            ("final_report_sha256", "9"*64), ("capital_operational_plan_sha256", "8"*64),
            ("evaluation_protocol_sha256", "7"*64), ("data_sha256", "6"*64),
            ("capital_operational_request_id_sha256", "5"*64),
            ("capital_operational_p95_ms", "-1"), ("capital_operational_p95_ms", "nan"),
            ("capital_operational_p95_ms", "inf"), ("capital_operational_p95_ms", "250.01"),
            ("capital_deployment_approval_at_utc", "2020-01-01T00:00:00+00:00"),
            ("capital_operational_review_at_utc", "2024-01-01T00:00:00"),
            ("capital_deployment_decision_schema_version", "1"),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.client.tags = dict(original, **{key: value})
                with self.assertRaises(ValueError):
                    self.check()
        self.client.tags = original
        self.check()

    def test_missing_prerequisites_cannot_review_or_become_eligible(self):
        for category in evidence.CASES:
            with self.subTest(category=category):
                changed = copy.deepcopy(self.bundle)
                del changed["reports"][category]
                self.replace_bundle(changed)
                with self.assertRaisesRegex(ValueError, "report bodies"):
                    _capital_deployment_evidence_record_operational_review(self.client, "2", "reviewer-a", "passed", "synthetic")
                self.assertEqual(self.client.tags["deployment_permission"], "blocked")

    def test_hash_consistent_failed_uncertain_foreign_and_missing_cases_are_rejected(self):
        for category in evidence.CASES:
            for change in ("failed", "uncertain", "foreign", "missing_case", "bad_outcome", "future", "no_observations"):
                with self.subTest(category=category, change=change):
                    bundle = copy.deepcopy(self.bundle)
                    report = evidence.unpack(bundle["reports"][category])
                    first = next(iter(report["checks"]))
                    if change == "failed":
                        report["status"] = "failed"
                    elif change == "uncertain":
                        report["evidence_state"] = "database_commit_unknown"
                    elif change == "foreign":
                        report["identity"]["model_version"] = "99"
                    elif change == "missing_case":
                        del report["checks"][first]
                    elif change == "future":
                        report["completed_at_utc"] = "2999-01-01T00:00:00+00:00"
                    else:
                        case = evidence.unpack(report["checks"][first])
                        if change == "bad_outcome":
                            case["observed_outcome"] = "failed"
                        else:
                            case["observations"] = {}
                        report["checks"][first] = evidence.document(evidence.canonical_bytes(case))
                    bundle["reports"][category] = evidence.document(evidence.canonical_bytes(report))
                    # Rehash every changed descriptor: this must fail semantic checks, not just SHA checks.
                    self.replace_bundle(bundle)
                    with self.assertRaises(ValueError):
                        evidence.verified_registered_evidence(self.client, "2", self.client.tags)

    def test_bundle_corruption_duplicate_json_keys_and_unknown_policy_fail_closed(self):
        for mode in ("bytes", "duplicate", "policy"):
            with self.subTest(mode=mode):
                self.client = _capital_deployment_evidence_FakeRegistry(self.tags, body=self.body)
                if mode == "bytes":
                    self.client.body = self.body + b"changed"
                elif mode == "duplicate":
                    self.client.body = b'{"schema_version":1,"schema_version":1}'
                    self.client.tags.update(evidence.reference_tags(self.client.body, self.client.run_id))
                    self.client.artifact_path = self.client.tags["capital_deployment_evidence_path"]
                else:
                    changed = copy.deepcopy(self.bundle)
                    changed["policy"]["policy_id"] = "different"
                    self.replace_bundle(changed)
                with self.assertRaises(ValueError):
                    _capital_deployment_evidence_record_operational_review(self.client, "2", "reviewer-a", "passed", "synthetic")
                self.assertEqual(self.client.tags["deployment_permission"], "blocked")

    def test_saved_operational_latency_cannot_disagree_with_reported_p95(self):
        changed = copy.deepcopy(self.bundle)
        report = evidence.unpack(changed["reports"]["operational"])
        for row in report["measurements"]:
            row["latency_ms"] = 200.0
        changed["reports"]["operational"] = evidence.document(evidence.canonical_bytes(report))
        self.client.tags["capital_operational_report_sha256"] = changed["reports"]["operational"]["sha256"]
        changed["identity"] = evidence.evidence_identity(self.client.tags, "2", self.client.run_id)
        self.replace_bundle(changed)
        with self.assertRaisesRegex(ValueError, "p95"):
            evidence.verified_registered_evidence(self.client, "2", self.client.tags)

    def test_changed_accuracy_rows_cannot_support_the_original_metrics(self):
        rows = evidence.unpack(self.bundle["reports"]["final_rows"], csv=True)
        rows = rows.replace(",2,1.0,4.0", ",200,1.0,4.0")
        with self.assertRaisesRegex(ValueError, "accuracy differs"):
            evidence._accuracy_rows(rows, evidence.unpack(self.bundle["reports"]["protocol"])["evaluation"],
                                   3, {"mae": 1.0, "rmse": 1.0}, {"mae": 2.0, "rmse": 2.0}, "final_evaluation")

    def test_report_identity_changes_during_download_are_rejected(self):
        self.approve()
        original = self.client.download_artifacts
        def changing(*args):
            path = original(*args)
            self.client.tags["final_report_sha256"] = "7"*64
            return path
        self.client.download_artifacts = changing
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.check()

    def test_decision_metadata_changes_during_download_are_rejected(self):
        self.approve()
        original = self.client.download_artifacts
        def changing(*args):
            path = original(*args)
            self.client.tags["capital_deployment_approval_reason"] = "changed"
            return path
        self.client.download_artifacts = changing
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.check()

    def test_last_eligible_write_lost_response_is_queryable_and_same_retry_is_idempotent(self):
        _capital_deployment_evidence_record_operational_review(self.client, "2", "reviewer-a", "passed", "synthetic")
        self.client.fail_after_tag = "deployment_permission"
        with self.assertRaises(RuntimeError):
            _capital_deployment_evidence_record_deployment_approval(self.client, "2", "approver-b", "synthetic")
        self.check()  # Server completed all bound fields before the response was lost.
        previous = dict(self.client.tags)
        _capital_deployment_evidence_record_deployment_approval(self.client, "2", "approver-b", "synthetic")
        self.assertEqual(previous, self.client.tags)

    def test_oversized_or_outside_download_cannot_supply_report_evidence(self):
        with _capital_deployment_evidence_patch.object(evidence, "MAX_BUNDLE_BYTES", 1024):
            with self.assertRaisesRegex(ValueError, "too large"):
                evidence.verified_registered_evidence(self.client, "2", self.client.tags)
        outside = self.root / "outside.json"
        outside.write_bytes(self.body)
        self.client.download_artifacts = lambda *args: str(outside)
        with self.assertRaisesRegex(ValueError, "temporary directory"):
            evidence.verified_registered_evidence(self.client, "2", self.client.tags)

    def test_noncanonical_policy_types_and_missing_body_are_rejected(self):
        for change in ("bool_schema", "bool_policy", "missing_content"):
            with self.subTest(change=change):
                bundle = copy.deepcopy(self.bundle)
                if change == "bool_schema":
                    bundle["schema_version"] = True
                elif change == "bool_policy":
                    bundle["policy"]["schema_version"] = True
                else:
                    del bundle["reports"]["metadata"]["content"]
                self.replace_bundle(bundle)
                with self.assertRaises(ValueError):
                    evidence.verified_registered_evidence(self.client, "2", self.client.tags)

    def test_capital_normal_loader_pins_registry_and_rejects_bad_body_before_model_download(self):
        import mlflow.artifacts
        from bike_mlops import model
        self.approve()
        self.client.body = b"corrupt"
        with (_capital_deployment_evidence_patch.object(model, "DATA_PROFILE_ID", "capital-bikeshare"),
              _capital_deployment_evidence_patch.object(model, "TARGET_CONTRACT_ID", self.tags["target_contract_id"]),
              _capital_deployment_evidence_patch.dict(_capital_deployment_evidence_os.environ, {"MLFLOW_REGISTRY_URI": "http://127.0.0.1:5999"}),
              _capital_deployment_evidence_patch("mlflow.set_tracking_uri"),
              _capital_deployment_evidence_patch("mlflow.tracking.MlflowClient", return_value=self.client) as factory,
              _capital_deployment_evidence_patch.object(mlflow.artifacts, "download_artifacts") as download):
            with self.assertRaises(ValueError):
                model.load_registered_model(evidence.MODEL, "2", "http://127.0.0.1:5001")
            factory.assert_called_once_with(tracking_uri="http://127.0.0.1:5001", registry_uri="http://127.0.0.1:5001")
            download.assert_not_called()

    def test_capital_cli_client_pins_registry_even_with_conflicting_environment(self):
        from bike_mlops import cli
        with (_capital_deployment_evidence_patch.object(cli, "DATA_PROFILE_ID", "capital-bikeshare"),
              _capital_deployment_evidence_patch.dict(_capital_deployment_evidence_os.environ, {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5001",
                                      "MLFLOW_REGISTRY_URI": "http://127.0.0.1:5999"}),
              _capital_deployment_evidence_patch("mlflow.set_tracking_uri"),
              _capital_deployment_evidence_patch("mlflow.tracking.MlflowClient", return_value=self.client) as factory):
            self.assertIs(cli._model_registry_client(), self.client)
            factory.assert_called_once_with(tracking_uri="http://127.0.0.1:5001", registry_uri="http://127.0.0.1:5001")

    def test_runtime_init_rejects_corrupt_reports_before_writing_slot_or_state_files(self):
        from bike_mlops import cli
        self.approve()
        self.client.body = b"corrupt"
        runtime = self.root / "runtime"
        args = _capital_deployment_evidence_SimpleNamespace(version="2", approver="operator", reason="synthetic", diagnostic=False)
        with (_capital_deployment_evidence_patch.object(cli, "DATA_PROFILE_ID", "capital-bikeshare"),
              _capital_deployment_evidence_patch.object(cli, "TARGET_CONTRACT_ID", self.tags["target_contract_id"]),
              _capital_deployment_evidence_patch.object(cli, "MODEL_NAME", evidence.MODEL),
              _capital_deployment_evidence_patch.object(cli, "RUNTIME_DIR", runtime),
              _capital_deployment_evidence_patch.object(cli, "_model_registry_client", return_value=self.client)):
            with self.assertRaises(ValueError):
                cli.command_runtime_init(args)
        self.assertFalse((runtime / "blue.env").exists())
        self.assertFalse((runtime / "green.env").exists())
        self.assertFalse((runtime / "deployment-state.json").exists())


# ========================================================================
# test_capital_deployment_evidence_publisher
# ========================================================================

import copy as _capital_deployment_evidence_publisher_copy
import hashlib as _capital_deployment_evidence_publisher_hashlib
import os as _capital_deployment_evidence_publisher_os
import tempfile as _capital_deployment_evidence_publisher_tempfile
import unittest as _capital_deployment_evidence_publisher_unittest
from pathlib import Path as _capital_deployment_evidence_publisher_Path
from unittest.mock import patch as _capital_deployment_evidence_publisher_patch
from support import diagnose_decision_test as _capital_deployment_evidence_publisher_diagnose_decision_test

from support import fixture as _capital_deployment_evidence_publisher_fixture
from bike_mlops.operational import RequestJournal
_capital_deployment_evidence_publisher_FakeRegistry = FakeRegistry
from bike_mlops import deployment as _capital_deployment_evidence_publisher_evidence
from bike_mlops.deployment import record_operational_review as _capital_deployment_evidence_publisher_record_operational_review, record_deployment_approval as _capital_deployment_evidence_publisher_record_deployment_approval
from bike_mlops.deployment import publish_deployment_evidence
from bike_mlops.deployment import require_deployment_eligible as _capital_deployment_evidence_publisher_require_deployment_eligible


class DeploymentEvidencePublisherTests(_capital_deployment_evidence_publisher_unittest.TestCase):
    def setUp(self):
        from bike_mlops import config, operational as ledger
        from bike_mlops import operational as plans
        from bike_mlops import operational_validation as recovery
        from bike_mlops import operational_validation as registry
        self.temp = _capital_deployment_evidence_publisher_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = _capital_deployment_evidence_publisher_Path(self.temp.name)
        for module, binding in ((config, "PROJECT_ROOT"), (ledger, "_operational_ledger_PROJECT_ROOT"),
                                (plans, "_operational_plan_PROJECT_ROOT"), (recovery, "PROJECT_ROOT"),
                                (registry, "_operational_registry_PROJECT_ROOT")):
            change = _capital_deployment_evidence_publisher_patch.object(module, binding, self.root)
            change.start()
            self.addCleanup(change.stop)
        profile = _capital_deployment_evidence_publisher_patch.object(config, "DATA_PROFILE_ID", "capital-bikeshare")
        profile.start()
        self.addCleanup(profile.stop)
        self.ledger_path = self.root / "data/evaluations/capital-bikeshare/operational-attempts.json"
        self.lock_dir = self.ledger_path.parent / "locks"
        for name, value in (("DEFAULT_LEDGER", self.ledger_path), ("DEFAULT_LOCK_DIR", self.lock_dir)):
            change = _capital_deployment_evidence_publisher_patch.object(ledger, name, value)
            change.start()
            self.addCleanup(change.stop)
        self.tags, bundle, body = _capital_deployment_evidence_publisher_fixture()
        selection = _capital_deployment_evidence_publisher_evidence.unpack(bundle["reports"]["candidate"])
        self.cid = selection["candidate_id"]
        self.selection_path = self.root / "data/candidates" / self.cid / "candidate-report.json"
        self.final_path = self.root / "data/reports/capital-bikeshare/final.json"
        self.plan_path = self.ledger_path.parent / "operational-plans/test.json"
        paths = {
            "candidate": self.selection_path, "final": self.final_path,
            "protocol": self.selection_path.parent / "evaluation-protocol.json",
            "validation_rows": self.selection_path.parent / "validation-predictions.csv",
            "final_rows": self.final_path.parent / "final.predictions.csv",
        }
        for name, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bundle["reports"][name]["content"].encode())
        plan = _capital_deployment_evidence_publisher_evidence.unpack(bundle["reports"]["plan"])
        plans.save_operational_plan(plan, self.plan_path)
        with ledger.measurement_attempt(self.plan_path, ledger_path=self.ledger_path, lock_dir=self.lock_dir) as attempt:
            self.attempt_id = attempt.attempt_id
            attempt.mark_requests_started()
            report = _capital_deployment_evidence_publisher_evidence.unpack(bundle["reports"]["operational"])
            old_id = report["operational_attempt_id"]
            report.update(operational_attempt_id=attempt.attempt_id, benchmark_id=attempt.attempt_id)
            report["operational_plan_file_sha256"] = attempt.plan_file_sha256
            for row in report["measurements"]:
                row["request_id"] = row["request_id"].replace(old_id, attempt.attempt_id)
            ids_sha = _capital_deployment_evidence_publisher_hashlib.sha256("\n".join(row["request_id"] for row in report["measurements"]).encode()).hexdigest()
            report["operational_gate"]["observed"]["request_id_sha256"] = ids_sha
            report["audit"]["request_id_sha256"] = ids_sha
            self.journal_path = self.final_path.parent / f"operational-{attempt.attempt_id}.requests.jsonl"
            journal = RequestJournal(self.journal_path, attempt, plan)
            try:
                # Fixture preparation does not test disk durability; dedicated interruption tests do.
                with _capital_deployment_evidence_publisher_patch('bike_mlops.operational.os.fsync'):
                    for payload, row in zip(journal.schedule, report["measurements"], strict=True):
                        journal.sending(payload)
                        journal.outcome(payload, http_status=200, latency_ms=row["latency_ms"], error=None)
                report["request_journal"] = journal.descriptor()
            finally:
                journal.close()
            self.report_path = self.final_path.parent / f"operational-{attempt.attempt_id}.json"
            self.report_path.write_bytes(_capital_deployment_evidence_publisher_evidence.canonical_bytes(report))
            attempt.finish(status="completed", report_path=self.report_path)
        self.tags.update(registry._validate_completed_evidence(
            plan, report, _capital_deployment_evidence_publisher_hashlib.sha256(self.report_path.read_bytes()).hexdigest(),
            self.attempt_id, _capital_deployment_evidence_publisher_hashlib.sha256(self.plan_path.read_bytes()).hexdigest()))
        for key in _capital_deployment_evidence_publisher_evidence.REF_KEYS:
            self.tags.pop(key, None)
        self.client = _capital_deployment_evidence_publisher_FakeRegistry(self.tags, body=None)
        self.paths = {}
        for category in _capital_deployment_evidence_publisher_evidence.CASES:
            path = self.final_path.parent / f"{category}.json"
            path.write_bytes(bundle["reports"][category]["content"].encode())
            self.paths[category] = path
        self.verified = {"selection_report_path": self.selection_path, "final_report_path": self.final_path,
                         "selection": selection, "final": _capital_deployment_evidence_publisher_evidence.unpack(bundle["reports"]["final"])}

    def publish(self):
        with (_capital_deployment_evidence_publisher_patch.dict(_capital_deployment_evidence_publisher_os.environ, {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5001",
                                      "MLFLOW_REGISTRY_URI": "http://127.0.0.1:5001"}),
              _capital_deployment_evidence_publisher_patch("mlflow.tracking.MlflowClient", return_value=self.client),
              _capital_deployment_evidence_publisher_patch('bike_mlops.staging.verify_candidate_for_staging', return_value=self.verified)):
            return publish_deployment_evidence(self.cid, self.attempt_id, self.final_path, self.paths)

    def test_publisher_reads_completed_attempt_and_report_files_without_approving(self):
        result = self.publish()
        self.assertEqual(result["deployment_permission"], "blocked")
        self.assertEqual(self.client.tags["operational_gate_status"], "not_evaluated")
        _capital_deployment_evidence_publisher_evidence.verified_registered_evidence(self.client, "2", self.client.tags)
        previous = dict(self.client.tags)
        self.publish()
        self.assertEqual(previous, self.client.tags)
        _capital_deployment_evidence_publisher_record_operational_review(self.client, "2", "synthetic-reviewer", "passed", "fixture")
        _capital_deployment_evidence_publisher_record_deployment_approval(self.client, "2", "synthetic-approver", "fixture")
        _capital_deployment_evidence_publisher_require_deployment_eligible("capital-bikeshare", self.client.tags,
            model_name=_capital_deployment_evidence_publisher_evidence.MODEL, model_version="2", client=self.client)

    def test_missing_or_torn_journal_is_rejected_before_upload(self):
        original = self.journal_path.read_bytes()
        for content in (None, original + b'{"event":'):
            with self.subTest(missing=content is None):
                if content is None: self.journal_path.unlink()
                else: self.journal_path.write_bytes(content)
                with _capital_deployment_evidence_publisher_patch.object(self.client, "log_artifact") as upload:
                    with self.assertRaisesRegex(ValueError, "journal"):
                        self.publish()
                    upload.assert_not_called()
                self.assertEqual(self.client.tags["deployment_permission"], "blocked")

    def test_changed_operational_report_is_rejected_before_upload(self):
        self.report_path.write_bytes(self.report_path.read_bytes()+b" ")
        with _capital_deployment_evidence_publisher_patch.object(self.client, "log_artifact") as upload:
            with self.assertRaisesRegex(ValueError, "report differs"):
                self.publish()
            upload.assert_not_called()
        self.assertEqual(self.client.tags["deployment_permission"], "blocked")

    def test_failed_prerequisite_is_rejected_before_upload(self):
        report = _capital_deployment_evidence_publisher_evidence.strict_json(self.paths["monitoring"].read_text())
        report["status"] = "failed"
        self.paths["monitoring"].write_bytes(_capital_deployment_evidence_publisher_evidence.canonical_bytes(report))
        with _capital_deployment_evidence_publisher_patch.object(self.client, "log_artifact") as upload:
            with self.assertRaises(ValueError):
                self.publish()
            upload.assert_not_called()
        self.assertNotIn(_capital_deployment_evidence_publisher_evidence.REF_KEYS[0], self.client.tags)

    def test_upload_response_loss_stays_blocked_and_same_retry_recovers(self):
        original = self.client.log_artifact
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("synthetic lost upload response")
        with _capital_deployment_evidence_publisher_patch.object(self.client, "log_artifact", side_effect=lost):
            with self.assertRaises(RuntimeError):
                self.publish()
        self.assertNotIn(_capital_deployment_evidence_publisher_evidence.REF_KEYS[0], self.client.tags)
        self.assertEqual(self.client.tags["deployment_permission"], "blocked")
        self.publish()
        _capital_deployment_evidence_publisher_evidence.verified_registered_evidence(self.client, "2", self.client.tags)


class IsolatedMlflowDeploymentEvidenceTests(_capital_deployment_evidence_publisher_unittest.TestCase):
    @_capital_deployment_evidence_publisher_diagnose_decision_test
    def test_report_artifact_review_approval_and_tamper_rejection_in_temporary_sqlite_registry(self):
        from mlflow.tracking import MlflowClient
        from bike_mlops import operational_validation as registry
        with _capital_deployment_evidence_publisher_tempfile.TemporaryDirectory(prefix="capital-decision-mlflow-test-") as directory:
            root = _capital_deployment_evidence_publisher_Path(directory)
            uri = f"sqlite:///{root / 'mlflow.db'}"
            client = MlflowClient(tracking_uri=uri, registry_uri=uri)
            experiment = client.create_experiment("synthetic-only", artifact_location=(root / "artifacts").as_uri())
            run = client.create_run(experiment)
            run_id = run.info.run_id
            client.create_registered_model(_capital_deployment_evidence_publisher_evidence.MODEL)
            # The reusable fixture binds exact version 2. Version 1 is synthetic metadata only.
            source = root / "synthetic-model"
            source.mkdir()
            client.create_model_version(_capital_deployment_evidence_publisher_evidence.MODEL, str(source), run_id=run_id)
            version = client.create_model_version(_capital_deployment_evidence_publisher_evidence.MODEL, str(source), run_id=run_id)
            self.assertEqual(str(version.version), "2")
            tags, bundle, _ = _capital_deployment_evidence_publisher_fixture()
            bundle["identity"]["run_id"] = run_id
            body = _capital_deployment_evidence_publisher_evidence.canonical_bytes(bundle)
            refs = _capital_deployment_evidence_publisher_evidence.reference_tags(body, run_id)
            tags.update(refs)
            path = root / "bundle.json"
            path.write_bytes(body)
            client.log_artifact(run_id, str(path), artifact_path=refs["capital_deployment_evidence_path"].rsplit("/", 1)[0])
            for key, value in tags.items():
                client.set_model_version_tag(_capital_deployment_evidence_publisher_evidence.MODEL, "2", key, value)
            with _capital_deployment_evidence_publisher_patch.object(registry, '_operational_registry_PROJECT_ROOT', root):
                _capital_deployment_evidence_publisher_record_operational_review(client, "2", "synthetic-reviewer", "passed", "temporary test only")
                _capital_deployment_evidence_publisher_record_deployment_approval(client, "2", "synthetic-approver", "temporary test only")
            current = client.get_model_version(_capital_deployment_evidence_publisher_evidence.MODEL, "2")
            _capital_deployment_evidence_publisher_require_deployment_eligible("capital-bikeshare", current.tags,
                model_name=_capital_deployment_evidence_publisher_evidence.MODEL, model_version="2", client=client)
            from urllib.parse import unquote, urlsplit
            artifact_root = _capital_deployment_evidence_publisher_Path(unquote(urlsplit(client.get_run(run_id).info.artifact_uri).path))
            artifact = artifact_root / refs["capital_deployment_evidence_path"]
            self.assertTrue(artifact.is_file())
            artifact.write_bytes(b"synthetic corruption")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                _capital_deployment_evidence_publisher_require_deployment_eligible("capital-bikeshare", current.tags,
                    model_name=_capital_deployment_evidence_publisher_evidence.MODEL, model_version="2", client=client)


# ========================================================================
# test_capital_decision_http
# ========================================================================

"""Guard tests for the opt-in HTTP fixture; no MLflow server is started here."""
from pathlib import Path as _capital_decision_http_Path
import tempfile as _capital_decision_http_tempfile
from types import SimpleNamespace as _capital_decision_http_SimpleNamespace
import unittest as _capital_decision_http_unittest
from unittest.mock import patch as _capital_decision_http_patch

from scripts import runtime_checks as _capital_decision_http_fixture


class CapitalHttpFixtureSafetyTests(_capital_decision_http_unittest.TestCase):
    def test_opt_in_required_before_directory_or_server_creation(self):
        with _capital_decision_http_tempfile.TemporaryDirectory() as directory:
            output = _capital_decision_http_Path(directory) / "evidence"
            with _capital_decision_http_patch.object(_capital_decision_http_fixture.subprocess, "Popen") as launch:
                with self.assertRaises(SystemExit) as error:
                    _capital_decision_http_fixture.main(["--evidence-dir", str(output)])
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(output.exists())
                launch.assert_not_called()

    def test_no_arbitrary_registry_endpoint_argument(self):
        with _capital_decision_http_patch.object(_capital_decision_http_fixture.subprocess, "Popen") as launch:
            with self.assertRaises(SystemExit):
                _capital_decision_http_fixture.main(["--run-disposable", "--evidence-dir", "/unused",
                              "--registry-uri", "http://127.0.0.1:5000"])
            launch.assert_not_called()

    def test_evidence_cannot_overwrite_previous_trial(self):
        with _capital_decision_http_tempfile.TemporaryDirectory() as directory:
            with _capital_decision_http_patch.object(_capital_decision_http_fixture.subprocess, "Popen") as launch:
                with self.assertRaises(FileExistsError):
                    _capital_decision_http_fixture.main(["--run-disposable", "--evidence-dir", directory])
                launch.assert_not_called()

    def test_dead_or_changed_server_identity_rejects_listener(self):
        expected = {"pid": 1, "ppid": 0, "pgrp": 1, "start_ticks": 1}
        for code, current in ((1, expected), (None, dict(expected, start_ticks=2))):
            with self.subTest(code=code), _capital_decision_http_patch.object(_capital_decision_http_fixture, "process_identity", return_value=current):
                process = _capital_decision_http_SimpleNamespace(pid=1, poll=lambda: code)
                with self.assertRaisesRegex(RuntimeError, "identity"):
                    _capital_decision_http_fixture.listener_owner(process, 5000, expected)

    def test_foreign_socket_is_not_a_private_listener(self):
        # The server identity exists, but no fd in its descendants owns the port.
        with _capital_decision_http_tempfile.TemporaryDirectory() as directory:
            root = _capital_decision_http_Path(directory)
            proc = root / "proc"
            (proc / "123" / "fd").mkdir(parents=True)
            tcp = proc / "net" / "tcp"
            tcp.parent.mkdir()
            tcp.write_text("header\n0: 0100007F:1388 00000000:0000 0A 0 0 0 0 0 999\n")
            real_path = _capital_decision_http_Path
            def mapped(value):
                return proc if value == "/proc" else proc / value[6:] if value.startswith("/proc/") else real_path(value)
            identity = dict(pid=123, ppid=1, pgrp=123, start_ticks=1)
            process = _capital_decision_http_SimpleNamespace(pid=123, poll=lambda: None)
            with _capital_decision_http_patch.object(_capital_decision_http_fixture, "Path", side_effect=mapped), _capital_decision_http_patch.object(_capital_decision_http_fixture, "process_identity", return_value=identity):
                with self.assertRaisesRegex(RuntimeError, "not owned"):
                    _capital_decision_http_fixture.listener_owner(process, 5000, identity)

    def test_failure_helper_never_treats_success_as_failure(self):
        with self.assertRaises(AssertionError):
            _capital_decision_http_fixture.expect_failure(lambda: "success")
        result = _capital_decision_http_fixture.expect_failure(lambda: (_ for _ in ()).throw(ValueError("synthetic")))
        self.assertEqual(result, {"error_type": "ValueError", "message": "synthetic"})

    def test_term_unwinds_cleanup_with_nonzero_exit(self):
        with self.assertRaises(SystemExit) as error:
            _capital_decision_http_fixture.request_stop(_capital_decision_http_fixture.signal.SIGTERM, None)
        self.assertEqual(error.exception.code, 143)


if __name__ == "__main__":
    import unittest as _unittest_entry
    _unittest_entry.main()
