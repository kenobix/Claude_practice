from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
from uuid import UUID

import pytest

from ci_runner import container, database
from test_fixture_worker_postgres import invoke
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
               ('fixture_worker.py', 'worker.py', 'execution.py', 'container.py', 'preflight.py', 'finalization.py')]
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
        conn.execute("UPDATE runs SET available_at=clock_timestamp()-interval '1 day'")
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
    # A fresh API process observes the Worker commit; the next run remains queued.
    with api() as (client, _):
        detail = client.get('/runs/' + first['id']).json()
        assert detail['state'] == state and detail['completed_attempt_id'] == output['attempt_id']
        assert client.get('/runs/' + second['id']).json()['state'] == 'queued'
        assert client.post('/runs', json=payload()).json() == detail
    after = resources()
    assert before == after
    evidence['cases'].append({'fixture': mode, 'receipt': output,
                              'owned_resources_before': before, 'owned_resources_after': after,
                              'api_restarted': True, 'next_run_state': 'queued'})
