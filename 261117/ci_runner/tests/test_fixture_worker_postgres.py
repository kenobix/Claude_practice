from contextlib import contextmanager
import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient
import psycopg
import pytest

from ci_runner import container, database, execution, fixture_worker, runs
from ci_runner.app import app
from test_finalization_input import report
from test_runs_postgres import ROOT, dedicated_database

pytestmark = pytest.mark.integration


def invoke(mode='success', env=None):
    return subprocess.run([sys.executable, '-m', 'ci_runner.fixture_worker', '--run-once',
                           '--worker-id', 'fixture-worker-1', '--fixture-mode', mode],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)


def seed_pair():
    first, second = [runs.create_run(database.connect, c * 40)[0] for c in 'ab']
    with database.connect() as conn:
        # Acquisition eligibility is independently covered by Worker tests.
        conn.execute("UPDATE runs SET available_at=clock_timestamp()-interval '1 day'")
    return first, second


def test_empty_process_with_no_docker_on_path(dedicated_database):
    result = invoke(env=os.environ | {'PATH': ''})
    assert result.returncode == 0 and result.stderr == ''
    assert json.loads(result.stdout) == {'claimed': False}
    with database.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == 0


def test_missing_db_process_never_reports_success(dedicated_database):
    env = os.environ.copy()
    env.pop('DATABASE_URL', None)
    result = invoke(env=env)
    assert result.returncode == 1 and result.stdout == ''
    assert json.loads(result.stderr) == {'status': 'incomplete', 'stage': 'claim',
                                       'detail': 'inspect DB state; no automatic retry'}


def test_execution_failure_retains_running_attempt_and_next_queued_run(dedicated_database, monkeypatch):
    first, second = seed_pair()
    def refused(*args):
        raise container.ContainerRejected('public injected cleanup failure')
    monkeypatch.setattr(execution, 'execute_fixture', refused)
    with pytest.raises(fixture_worker.OneShotFailed) as error:
        fixture_worker.run_once('worker-1')
    assert error.value.stage == 'execution' and error.value.run_id == str(first.id)
    with database.connect() as conn:
        assert conn.execute('SELECT state,completed_attempt_id FROM runs WHERE id=%s',
                            (first.id,)).fetchone() == {'state': 'running', 'completed_attempt_id': None}
        assert conn.execute('SELECT state FROM runs WHERE id=%s', (second.id,)).fetchone()['state'] == 'queued'
        assert conn.execute('SELECT state,finished_at FROM attempts').fetchone() == {'state': 'running', 'finished_at': None}


def test_lost_claim_commit_response_never_starts_execution(dedicated_database, monkeypatch):
    first, second = seed_pair()
    @contextmanager
    def factory():
        with database.connect() as conn:
            yield conn
        raise psycopg.OperationalError('public injected lost claim commit response')
    monkeypatch.setattr(execution, 'execute_fixture', lambda *a: pytest.fail('unknown claim was executed'))
    with pytest.raises(fixture_worker.OneShotFailed) as error:
        fixture_worker.run_once('worker-1', connection_factory=factory)
    assert error.value.stage == 'claim' and error.value.run_id is None
    with database.connect() as conn:
        assert conn.execute('SELECT state FROM runs WHERE id=%s', (first.id,)).fetchone()['state'] == 'running'
        assert conn.execute('SELECT state FROM runs WHERE id=%s', (second.id,)).fetchone()['state'] == 'queued'
        assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == 1


def test_cancel_after_claim_is_denied_before_docker(dedicated_database, monkeypatch):
    first, second = seed_pair()
    acquire = fixture_worker.worker.claim_next
    def canceled(*args):
        claim = acquire(*args)
        with database.connect() as conn:
            conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (claim.run_id,))
        return claim
    monkeypatch.setattr(fixture_worker.worker, 'claim_next', canceled)
    monkeypatch.setattr(container, 'prepare_fixture', lambda *a: pytest.fail('canceled claim reached Docker'))
    with pytest.raises(fixture_worker.OneShotFailed) as error:
        fixture_worker.run_once('worker-1')
    assert error.value.stage == 'execution' and error.value.run_id == str(first.id)
    assert runs.get_run(database.connect, second.id).state == 'queued'


@pytest.mark.parametrize('position,state', [('before', 'running'), ('after', 'succeeded')])
def test_finalization_commit_fault_never_acquires_another_job(dedicated_database, monkeypatch, position, state):
    first, second = seed_pair()
    calls = 0
    @contextmanager
    def factory():
        nonlocal calls
        calls += 1
        with database.connect() as conn:
            yield conn
            if calls == 2 and position == 'before':
                raise psycopg.OperationalError('public injected pre-commit disconnect')
        if calls == 2 and position == 'after':
            raise psycopg.OperationalError('public injected lost commit response')
    # Test DB orchestration only; this is not evidence of Docker execution.
    monkeypatch.setattr(execution, 'execute_fixture', lambda claim, *a: report(claim))
    with pytest.raises(fixture_worker.OneShotFailed) as error:
        fixture_worker.run_once('worker-1', connection_factory=factory)
    assert calls == 2 and error.value.stage == 'finalization'
    client = TestClient(app)
    detail = client.get('/runs/' + str(first.id)).json()
    assert detail['state'] == state
    assert (detail['completed_attempt_id'] is not None) == (position == 'after')
    assert client.get('/runs/' + str(second.id)).json()['state'] == 'queued'
    with database.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == 1
