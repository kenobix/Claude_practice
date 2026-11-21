from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import threading
import time
from datetime import timedelta
from uuid import UUID

import pytest

from ci_runner import container, database, fixture_worker
from test_fixture_worker_postgres import invoke, seed_pair
from test_runs_postgres import ROOT, api, dedicated_database, payload

pytestmark = [pytest.mark.integration, pytest.mark.docker]


def resources():
    return {kind: sorted(set(subprocess.check_output(args, text=True).splitlines()))
            for kind, args in (
                ('containers', ['docker', 'ps', '-aq', '--filter', f'label={container.LABEL}']),
                ('images', ['docker', 'image', 'ls', '-q', '--filter', f'label={container.LABEL}']))}


@pytest.fixture(scope='module')
def evidence(request):
    data = {'cases': []}
    yield data
    sources = [ROOT/'src/ci_runner'/name for name in
               ('fixture_worker.py', 'worker.py', 'execution.py', 'container.py', 'preflight.py', 'finalization.py', 'app.py', 'logs.py')]
    sources += [*sorted((ROOT/'fixtures/job').iterdir()), ROOT/'tests/test_fixture_worker_docker.py',
                ROOT/'tests/test_fixture_worker_postgres.py']
    data.update(finished_at=datetime.now(timezone.utc).isoformat(),
                git_head_at_execution=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                pytest_failures=request.session.testsfailed,
                source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sources if p.is_file()})
    path = ROOT/'artifacts/fixture-worker-latest.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')


@pytest.mark.parametrize('mode,state,reason,code', [
    ('success', 'succeeded', 'fixture_success', 0),
    ('failure', 'failed', 'fixture_check_failure', 1),
    ('timeout', 'timed_out', 'fixture_timeout', 137),
    ('logs', 'failed', 'fixture_log_limit', 137)])
def test_api_to_separate_worker_to_persisted_result(dedicated_database, mode, state, reason, code, request):
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1':
        pytest.skip('run scripts/verify_execution.py for one-job Worker integration')
    evidence = request.getfixturevalue('evidence')
    before = resources()
    with api() as (client, _):
        first = client.post('/runs', json=payload()).json()
        second = client.post('/runs', json=payload('b'*40)).json()
    with database.connect() as conn:
        conn.execute("UPDATE runs SET available_at=statement_timestamp() - interval '1 day' "
                     "- CASE WHEN id=%s THEN interval '1 second' ELSE interval '0 seconds' END WHERE id IN (%s, %s)",
                     (first['id'], first['id'], second['id']))
    result = invoke(mode)
    assert result.returncode == 0 and result.stderr == ''
    output = json.loads(result.stdout)
    assert output['claimed'] and output['run_id'] == first['id']
    assert (output['state'], output['reason'], output['exit_code']) == (state, reason, code)
    assert not output['replayed']
    with database.connect() as conn:
        saved = conn.execute('SELECT state,reason,exit_code,lease_token,finished_at FROM attempts '
                             'WHERE id=%s FOR UPDATE NOWAIT', (UUID(output['attempt_id']),)).fetchone()
        assert (saved['state'], saved['reason'], saved['exit_code']) == (state, reason, code)
        assert saved['finished_at'].isoformat() == output['finished_at']
        assert str(saved['lease_token']) not in result.stdout + result.stderr
        assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == 1
        logs = conn.execute('SELECT stdout,stderr,observed_bytes FROM attempt_logs WHERE attempt_id=%s',
                            (UUID(output['attempt_id']),)).fetchone()
        retained = len(logs['stdout'])+len(logs['stderr'])
        assert retained <= 1048576 and logs['observed_bytes'] >= retained
        if mode == 'success':
            assert json.loads(logs['stdout']) == {'ok': True}
        elif mode == 'failure':
            assert logs['stderr'] == b'fixed failure\n'
        elif mode == 'logs':
            assert retained == 1048576 and logs['observed_bytes'] > retained
    # A fresh API process observes the Worker commit; the next run remains queued.
    with api() as (client, _):
        detail = client.get('/runs/' + first['id']).json()
        assert detail['state'] == state and detail['completed_attempt_id'] == output['attempt_id']
        assert client.get('/runs/' + second['id']).json()['state'] == 'queued'
        assert client.post('/runs', json=payload()).json() == detail
        for stream in ('stdout', 'stderr'):
            log_page = client.get('/runs/' + first['id'] + '/logs', params={'stream': stream})
            assert log_page.status_code == 200 and log_page.headers['x-log-attempt-id'] == output['attempt_id']
            assert int(log_page.headers['x-log-bytes']) == min(4096, len(logs[stream]))
            assert log_page.headers['x-log-truncated'] == str(logs['observed_bytes'] > retained).lower()
        assert client.get('/runs/' + first['id'] + '/logs', params={'format': 'html'}).text.startswith('<pre>\n')
    after = resources()
    assert before == after
    evidence['cases'].append({'fixture': mode, 'receipt': output,
                              'persisted_log_bytes': retained, 'observed_bytes': logs['observed_bytes'],
                              'owned_resources_before': before, 'owned_resources_after': after,
                              'api_restarted': True, 'next_run_state': 'queued'})


def test_full_log_budget_has_no_success_receipt(dedicated_database, request):
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1':
        pytest.skip('run scripts/verify_execution.py for one-job Worker integration')
    evidence = request.getfixturevalue('evidence')
    first, second = seed_pair()
    with database.connect() as conn:
        conn.execute('UPDATE log_storage_budget SET capacity_bytes=0')
    before = resources()
    result = invoke()
    assert result.returncode == 1 and result.stdout == ''
    assert json.loads(result.stderr)['stage'] == 'finalization'
    with database.connect() as conn:
        assert conn.execute('SELECT state,completed_attempt_id FROM runs WHERE id=%s', (first.id,)).fetchone() == {
            'state': 'running', 'completed_attempt_id': None}
        assert conn.execute('SELECT state FROM runs WHERE id=%s', (second.id,)).fetchone()['state'] == 'queued'
        assert conn.execute('SELECT count(*) AS n FROM attempt_logs').fetchone()['n'] == 0
        assert conn.execute('SELECT used_bytes FROM log_storage_budget').fetchone()['used_bytes'] == 0
    after = resources()
    assert before == after
    evidence['cases'].append({'fixture': 'log_budget_full', 'worker_exit': result.returncode,
                              'success_receipt': False, 'state': 'running',
                              'owned_resources_before': before, 'owned_resources_after': after})


def test_build_longer_than_lease_precedes_claim(dedicated_database, monkeypatch, request):
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1':
        pytest.skip('run scripts/verify_execution.py for one-job Worker integration')
    evidence = request.getfixturevalue('evidence')
    first, second = seed_pair()
    before = resources()
    real = container.docker
    built, created, identities = [], [], []
    build_ready_at = None
    def observe(*args, **kwargs):
        nonlocal build_ready_at
        result = real(*args, **kwargs)
        if args[0] == 'build':
            built.append(args[args.index('--tag')+1])
            with database.connect() as conn:
                # The read-only queue hint must have released its transaction.
                assert conn.execute('SELECT id FROM runs FOR UPDATE NOWAIT').fetchall()
                assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == 0
            started = time.monotonic()
            # Inject latency, not untrusted workload or daemon/host settings.
            threading.Event().wait(31)
            assert time.monotonic()-started >= 31
            with database.connect() as conn:
                assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == 0
                assert conn.execute('SELECT state FROM runs WHERE id=%s', (first.id,)).fetchone()['state'] == 'queued'
                build_ready_at = conn.execute('SELECT clock_timestamp() AS t').fetchone()['t']
        if args[:2] == ('image', 'inspect') and result.returncode == 0:
            identities.append(json.loads(result.stdout)[0]['Id'])
        if args[0] == 'create':
            created.append(args[-2])
        return result
    monkeypatch.setattr(container, 'docker', observe)
    receipt = fixture_worker.run_once('worker-1')
    assert receipt.state == 'succeeded' and receipt.run_id == first.id
    assert len(built) == len(created) == 1 and set(identities) == set(created)
    with database.connect() as conn:
        attempt = conn.execute('SELECT started_at,lease_expires_at FROM attempts').fetchone()
        assert attempt['started_at'] >= build_ready_at
        assert attempt['lease_expires_at']-attempt['started_at'] == timedelta(seconds=30)
        assert conn.execute('SELECT state FROM runs WHERE id=%s', (second.id,)).fetchone()['state'] == 'queued'
    after = resources()
    assert before == after
    evidence['cases'].append({'fixture': 'delayed_build', 'injected_delay_seconds': 31,
                              'attempts_during_build': 0, 'build_count': len(built),
                              'created_image_id': created[0], 'lease_seconds': 30,
                              'claim_after_build': True, 'state': receipt.state,
                              'owned_resources_before': before, 'owned_resources_after': after})
