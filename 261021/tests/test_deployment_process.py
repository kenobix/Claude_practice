"""Real child processes prove timeout, interruption, and JSON/env boundaries."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from bike_mlops import cli
from bike_mlops.deployment_process import run_operation, remaining_seconds, strict_result, interruption_guard
from bike_mlops.deployment_progress import DeploymentTimeout


class DeploymentProcessTests(unittest.TestCase):
    def test_success_is_json_only_and_environment_is_explicit(self):
        code = "import json,os,sys; p=json.load(sys.stdin); print(json.dumps({'mode':p['mode'],'secret_present':'UNRELATED_SECRET' in os.environ,'deadline':os.environ['BIKE_DEPLOYMENT_DEADLINE_NS']}))"
        with patch.dict(os.environ, UNRELATED_SECRET='must not propagate'):
            result = run_operation({'mode':'recover'}, command=[sys.executable, '-c', code], timeout_seconds=3)
        self.assertFalse(result['secret_present'])
        self.assertGreater(int(result['deadline']), 0)

    def test_deadline_kills_a_term_ignoring_child_without_wait_extension(self):
        with tempfile.TemporaryDirectory() as d:
            pid = Path(d)/'pid'
            code = f"import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); open({str(pid)!r},'w').write(str(os.getpid())); time.sleep(60)"
            began=time.monotonic()
            with patch('bike_mlops.deployment_process.TERM_SECONDS', .1), self.assertRaisesRegex(DeploymentTimeout, 'unconfirmed'):
                run_operation({'mode':'recover'}, command=[sys.executable,'-c',code], timeout_seconds=.3)
            self.assertLess(time.monotonic()-began, 3)
            self.assertFalse(Path('/proc/'+pid.read_text()).exists())

    def test_response_lost_after_side_effect_is_unknown_and_not_retried(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d)/'ledger'
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
        with patch.dict(os.environ, BIKE_DEPLOYMENT_DEADLINE_NS='2000000000'), patch('time.monotonic_ns', return_value=1500000000):
            self.assertEqual(remaining_seconds(30), .5)
        with patch.dict(os.environ, BIKE_DEPLOYMENT_DEADLINE_NS='2000000000'), patch('time.monotonic_ns', return_value=2000000001):
            with self.assertRaises(DeploymentTimeout): remaining_seconds(30)

    def test_term_during_registry_wait_is_recorded_and_child_exits(self):
        with tempfile.TemporaryDirectory() as d:
            ready = Path(d)/'ready'
            code = f"from bike_mlops.deployment_process import interruption_guard,run_operation; from pathlib import Path; import sys; Path({str(ready)!r}).touch();\nwith interruption_guard(): run_operation({{'mode':'recover'}}, command=[sys.executable,'-c','import time; time.sleep(30)'], timeout_seconds=20)"
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
        with patch.object(cli,'run_operation',return_value={'status':'unconfirmed'}) as run, patch.object(cli,'_print'):
            cli.command_deploy_promote(SimpleNamespace(approver='declared',version=3,reason='test',rollback=False,diagnostic=False))
            self.assertEqual(run.call_args.args[0]['mode'],'promote')
            cli.command_deploy_recover(SimpleNamespace())
            self.assertEqual(run.call_args.args[0], {'mode':'recover'})

    def test_term_of_leader_also_kills_term_ignoring_descendant(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'descendant'
            child=f"import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); open({str(path)!r},'w').write(str(os.getpid())); time.sleep(60)"
            code=f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
            with self.assertRaises(DeploymentTimeout):
                run_operation({'mode':'recover'},command=[sys.executable,'-c',code],timeout_seconds=.5)
            pid=path.read_text()
            stat=Path('/proc/'+pid+'/stat')
            # A short-lived zombie awaiting system reaping is not a running child.
            self.assertTrue(not stat.exists() or stat.read_text().rsplit(')',1)[1].split()[0]=='Z')

    def test_phase_interrupts_keep_prepared_operation_pending_and_do_not_compensate(self):
        for phase in ('registry', 'artifact', 'proxy', 'state'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as d:
                result=Path(d)/'outcome.json'
                code='''import sys,json,time,os,signal
from pathlib import Path
sys.path.insert(0, 'tests')
from test_deployment_recovery import DeploymentRecoveryTests
from bike_mlops.deployment_process import interruption_guard
x=DeploymentRecoveryTests(); x.setUp()
phase=PHASE
x.live=x.version('2')
x.http.side_effect=lambda *a,**kw: x.version('2') if not x.journal() else x.current_candidate()
def hang(*a,**kw): os.kill(os.getpid(), signal.SIGTERM); raise AssertionError("TERM ignored")
if phase=='registry': x.registry.get_model_version.side_effect=hang
elif phase=='artifact': x.probe.side_effect=hang
else:
 original=x.compose.side_effect
 def compose(*a,**kw):
  if phase=='proxy' and a[0]=='exec': hang()
 x.compose.side_effect=compose
 if phase=='state':
  original_write=__import__('bike_mlops.cli',fromlist=['_write_atomic'])._write_atomic
  def write(path, content):
   original_write(path,content)
   if path.name=='deployment-state.json': hang()
  x.stack.enter_context(__import__('unittest.mock',fromlist=['patch']).patch('bike_mlops.cli._write_atomic',side_effect=write))
try:
 with interruption_guard(): x.result=__import__('bike_mlops.cli',fromlist=['_promote'])._promote('3','operator','test',False,False)
except BaseException:
 Path(OUT).write_text(json.dumps({'rows':x.journal(),'state':x.read_state(),'compose_count':x.compose.call_count}))
 raise SystemExit(1)
'''.replace('PHASE',repr(phase)).replace('OUT',repr(str(result)))
                with self.assertRaises(RuntimeError):
                    run_operation({'mode':'promote'},command=[sys.executable,'-c',code],timeout_seconds=15)
                outcome=json.loads(result.read_text())
                if phase=='registry':
                    self.assertEqual(outcome['rows'],[])
                    self.assertEqual(outcome['compose_count'],0)
                else:
                    self.assertEqual(outcome['rows'][0]['status'],'in_progress')
                    self.assertEqual(outcome['rows'][0]['phase'],'recovery_required')
                    if phase=='state': self.assertEqual(outcome['state']['active_version'],'3')

    def test_safe_diagnostics_drop_native_output_and_invalid_json(self):
        import io
        from bike_mlops.deployment_process import _SafeStderr
        stream=io.StringIO(); safe=_SafeStderr(stream)
        safe.write('postgresql://secret:password@host/db\n')
        safe.write('{"kind":"deployment_progress","argv":"secret"}\n')
        safe.write('{"kind":"deployment_worker_failure","error_type":"TimeoutError","outcome":"unconfirmed"}\n')
        self.assertNotIn('secret',stream.getvalue())
        self.assertEqual(len(stream.getvalue().splitlines()),1)

    def test_cleanup_failure_is_reported_as_unconfirmed(self):
        with patch('bike_mlops.deployment_process._group_alive',return_value=True), \
             patch('bike_mlops.deployment_process.KILL_SECONDS',.05):
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
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
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
        with tempfile.TemporaryDirectory() as d:
            ready=Path(d)/'ready'; stopped=Path(d)/'stopped'
            worker=f"import signal,time; from pathlib import Path; from bike_mlops.deployment_process import _bind_supervisor; signal.signal(signal.SIGTERM,lambda *a:(Path({str(stopped)!r}).write_text('term'),exit(1))); _bind_supervisor(); Path({str(ready)!r}).touch(); time.sleep(30)"
            supervisor=f"import sys; from bike_mlops.deployment_process import run_operation; run_operation({{'mode':'recover'}},command=[sys.executable,'-c',{worker!r}],timeout_seconds=20)"
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
        code="""from unittest.mock import patch
import sys
from bike_mlops import cli
from bike_mlops.deployment_process import main
def promote(*args):
 sys.stderr.write('native postgresql://secret:password@host/db\\n')
 return {'model_version': args[0], 'prediction': 1.25}
with patch.object(cli,'_promote',side_effect=promote): main()
"""
        result=run_operation(dict(mode='promote',version='3',approver='declared',reason='fixture',rollback=False,diagnostic=False),
                             command=[sys.executable,'-c',code],timeout_seconds=15)
        self.assertEqual(result,dict(model_version='3',prediction=1.25))
