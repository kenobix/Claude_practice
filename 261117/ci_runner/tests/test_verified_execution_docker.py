import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ci_runner import commit_source, container, database, fixture_worker, runs, worker
from test_commit_source import GitHubFixture, HEAD
from test_fixture_worker_docker import resources
from test_runs_postgres import ROOT, api, dedicated_database, payload

pytestmark = [pytest.mark.integration, pytest.mark.docker]
PUBLISHED = '3eb342a89fc0eb990c0a48a79dbea42e8be44283'


@pytest.fixture(autouse=True)
def opt_in_before_work():
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1':
        pytest.skip('run scripts/verify_execution.py for real Docker verification')


@pytest.mark.parametrize('mode,state,reason,code', [
    ('success', 'succeeded', 'commit_success', 0),
    ('failure', 'failed', 'commit_check_failure', 1),
    ('timeout', 'timed_out', 'commit_timeout', 137),
    ('logs', 'failed', 'commit_log_limit', 137),
])
def test_live_verified_commit_runs_through_normal_cli(
    dedicated_database, monkeypatch, capsys, mode, state, reason, code,
):
    transport = os.environ.get('CI_RUN_LIVE_COMMIT_SOURCE')
    if transport not in ('1', 'gh'):
        pytest.skip('explicit live GitHub acquisition opt-in required')
    before = resources()
    with api() as (client, _):
        response = client.post('/runs', json=payload(PUBLISHED))
        assert response.status_code == 201
        run_id = response.json()['id']
    args = ['--run-once', '--verified-commit', '--worker-id', 'verified-worker', '--fixture-mode', mode]
    if transport == 'gh':
        # Authenticated GitHub traffic for tests when the public API quota is exhausted.
        # Only the HTTP transport changes: validate the real responses and run the same CLI.
        def authenticated_get(self, endpoint):
            try:
                response = subprocess.check_output(
                    ['gh', 'api', '--hostname', 'github.com', f'repos/kenobix/Claude_practice{endpoint}'],
                    stderr=subprocess.DEVNULL, timeout=commit_source.REQUEST_TIMEOUT)
            except subprocess.TimeoutExpired:
                pytest.fail('authenticated gh request exceeded the source timeout', pytrace=False)
            except subprocess.CalledProcessError as exc:
                pytest.fail(f'authenticated gh request exited with status {exc.returncode}', pytrace=False)
            assert len(response) <= commit_source.MAX_RESPONSE
            return json.loads(response)

        monkeypatch.setattr(commit_source._GitHub, 'get', authenticated_get)
        exit_code = fixture_worker.main(args)
        output = capsys.readouterr()
        assert exit_code == 0 and output.err == '', output.err
        stdout = output.out
    else:
        output = subprocess.run([sys.executable, '-m', 'ci_runner.fixture_worker', *args],
                                cwd=ROOT, capture_output=True, text=True, timeout=120)
        assert output.returncode == 0 and output.stderr == '', output.stderr
        stdout = output.stdout
    receipt = json.loads(stdout)
    assert receipt['run_id'] == run_id
    assert (receipt['state'], receipt['reason'], receipt['exit_code']) == (state, reason, code)
    with database.connect() as conn:
        source = conn.execute('SELECT * FROM attempt_sources WHERE attempt_id=%s',
                              (receipt['attempt_id'],)).fetchone()
        assert str(source['run_id']) == run_id and source['commit_sha'] == PUBLISHED
        assert source['blob_sha'] == 'a2352427ae73ed9337e933d1dcefd49968b3fe8b'
        assert source['source_sha256'] == commit_source.DIGEST
        assert source['image_id'].startswith('sha256:') and len(source['image_id']) == 71
        logs = conn.execute('SELECT stdout,stderr,observed_bytes FROM attempt_logs WHERE attempt_id=%s',
                            (receipt['attempt_id'],)).fetchone()
        retained = len(logs['stdout']) + len(logs['stderr'])
        assert retained <= 1048576
        if mode == 'success':
            assert json.loads(logs['stdout']) == {'ok': True}
        elif mode == 'failure':
            assert logs['stderr'] == b'fixed failure\n'
        elif mode == 'logs':
            assert retained == 1048576 and logs['observed_bytes'] > retained
    with api() as (client, _):
        detail = client.get('/runs/' + run_id).json()
        assert detail['state'] == state and detail['completed_attempt_id'] == receipt['attempt_id']
        assert client.get('/runs/' + run_id + '/logs').status_code == 200
    assert resources() == before


@pytest.mark.parametrize('fault', ['competitor', 'changed_sha', 'db_stop'])
def test_verified_image_recovers_after_claim_or_preflight_denial(dedicated_database, monkeypatch, fault):
    # Keep control-plane fault injection independent of the live GitHub test.
    monkeypatch.setattr(commit_source, '_GitHub', lambda: GitHubFixture(HEAD))
    first, other = [runs.create_run(database.connect, char*40)[0] for char in 'ab']
    before = resources()
    docker = container.docker
    context_path = None
    stopped = False
    compose = ['docker', 'compose', '--project-name', 'ci-runner-261117-p0-check',
               '-f', str(ROOT/'compose.yaml')]
    if fault == 'db_stop' and (
        os.environ.get('CI_TEST_COMPOSE_PROJECT'), os.environ.get('CI_TEST_COMPOSE_FILE')
    ) != ('ci-runner-261117-p0-check', str(ROOT/'compose.yaml')):
        pytest.skip('DB stop requires the owned verify_execution.py project')

    def observe(*args, **kwargs):
        nonlocal stopped, context_path
        if args[0] == 'build':
            context_path = Path(args[-1])
            assert '--network=none' in args
            assert {p.name for p in context_path.iterdir()} == {'Dockerfile', 'fixture.py'}
            with database.connect() as conn:
                assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == 0
        result = docker(*args, **kwargs)
        if args[0] == 'build':
            if fault == 'competitor':
                assert worker.claim_next('competitor', target=(first.id, HEAD)).run_id == first.id
            elif fault == 'changed_sha':
                with database.connect() as conn:
                    conn.execute('UPDATE runs SET commit_sha=%s WHERE id=%s', ('f'*40, first.id))
        if args[0] == 'create' and fault == 'db_stop':
            stopped = True
            subprocess.run(compose + ['stop', 'db'], check=True, capture_output=True, timeout=30)
        return result

    monkeypatch.setattr(container, 'docker', observe)
    monkeypatch.setattr(fixture_worker.execution, '_run',
                        lambda *a: pytest.fail('denied attempt started a container'))
    try:
        with pytest.raises(fixture_worker.OneShotFailed, match='execution' if fault == 'db_stop' else 'claim'):
            fixture_worker.run_once('verified-worker', verified_commit=True)
    finally:
        if stopped:
            subprocess.run(compose + ['up', '-d', '--wait', 'db'], check=True, capture_output=True, timeout=60)
    assert context_path is not None and not context_path.exists()
    assert resources() == before
    assert runs.get_run(database.connect, other.id).state == 'queued'
    with database.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == int(fault != 'changed_sha')
        assert conn.execute('SELECT count(*) AS n FROM attempt_sources').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM attempt_logs').fetchone()['n'] == 0
