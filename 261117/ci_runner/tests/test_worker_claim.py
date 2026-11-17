from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
import pytest

from ci_runner import database, runs
from ci_runner.app import app
from ci_runner.worker import claim_next
# Reuse the explicitly disposable DB fixture; never touch an existing development DB.
from test_runs_postgres import dedicated_database

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


def seed(sha='a' * 40):
    return runs.create_run(database.connect, sha)[0]


def worker_process(worker_id):
    return subprocess.run([sys.executable, '-m', 'ci_runner.worker', '--claim-once',
                           '--worker-id', worker_id], capture_output=True, text=True,
                          timeout=10, cwd=ROOT)


def test_separate_worker_commits_initial_attempt_and_api_observes_it(dedicated_database):
    run = seed()
    result = worker_process('worker-1')
    assert result.returncode == 0 and result.stderr == ''
    output = json.loads(result.stdout)
    assert output['claimed'] and output['run_id'] == str(run.id) and output['attempt_no'] == 1
    assert 'lease_token' not in output
    with psycopg.connect(dedicated_database.dsn) as conn:
        row = conn.execute('SELECT state FROM runs WHERE id=%s FOR UPDATE NOWAIT', (run.id,)).fetchone()
        assert row == ('running',)  # no row lock remains after worker process exit
        attempt = conn.execute('SELECT worker_id, attempt_no, retry_kind, '
                               'lease_expires_at-started_at, lease_token, '
                               'clock_timestamp() < lease_expires_at FROM attempts').fetchone()
        assert attempt[:4] == ('worker-1', 1, 'initial', timedelta(seconds=30))
        assert attempt[4] is not None and attempt[5]
        assert str(attempt[4]) not in result.stdout + result.stderr
    client = TestClient(app)
    assert client.get('/runs/' + str(run.id)).json()['state'] == 'running'
    replay = client.post('/runs', json={'repository_id': run.repository_id, 'commit_sha': run.commit_sha})
    assert replay.status_code == 200 and replay.json()['state'] == 'running'
    assert claim_next('worker-1') is None  # never reacquire a running run


def test_empty_queue_creates_nothing(dedicated_database):
    result = worker_process('worker-1')
    assert result.returncode == 0 and json.loads(result.stdout) == {'claimed': False}
    with psycopg.connect(dedicated_database.dsn) as conn:
        assert conn.execute('SELECT count(*) FROM attempts').fetchone()[0] == 0


def test_ineligible_runs_are_skipped(dedicated_database):
    pending, canceled, unsupported, terminal, retried, allowed = [seed(c * 40) for c in 'abcdef']
    with psycopg.connect(dedicated_database.dsn) as conn:
        conn.execute("UPDATE runs SET available_at=clock_timestamp()+interval '1 day' WHERE id=%s", (pending.id,))
        conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (canceled.id,))
        conn.execute("UPDATE runs SET workflow_version='unsupported' WHERE id=%s", (unsupported.id,))
        conn.execute("UPDATE runs SET state='failed' WHERE id=%s", (terminal.id,))
        conn.execute(
            'INSERT INTO attempts(run_id,attempt_no,worker_id,lease_token,started_at,lease_expires_at,'
            'finished_at,state,retry_kind) VALUES (%s,1,%s,%s,now(), '
            "now()+interval '30 seconds',now(),'failed','initial')", (retried.id, 'previous', uuid4()))
    claim = claim_next('worker-1')
    assert claim.run_id == allowed.id
    assert claim_next('worker-1') is None  # requeued attempts await explicit retry implementation


def test_locked_head_is_skipped_without_waiting(dedicated_database):
    head, next_run = seed('a' * 40), seed('b' * 40)
    with psycopg.connect(dedicated_database.dsn) as owner:
        owner.execute('SELECT id FROM runs WHERE id=%s FOR UPDATE', (head.id,))
        # Another process can claim the next row while the queue head is locked.
        result = worker_process('worker-1')
        assert result.returncode == 0 and json.loads(result.stdout)['run_id'] == str(next_run.id)
    claim = claim_next('worker-1')
    assert claim.run_id == head.id
    assert str(claim.lease_token) not in repr(claim)
    with psycopg.connect(dedicated_database.dsn) as conn:
        assert conn.execute('SELECT count(DISTINCT lease_token) FROM attempts').fetchone()[0] == 2


def test_claim_contenders_never_publish_two_attempts(dedicated_database):
    # Limited acquisition contention check; not a full two-Worker execution/recovery system.
    run = seed()
    barrier = threading.Barrier(2)
    def contend(index):
        barrier.wait(timeout=5)
        return claim_next(f'claimant-{index}')
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(contend, range(2)))
    claims = [r for r in results if r is not None]
    assert len(claims) == 1 and claims[0].run_id == run.id
    with psycopg.connect(dedicated_database.dsn) as conn:
        assert conn.execute('SELECT count(*) FROM attempts').fetchone()[0] == 1


@pytest.mark.parametrize('failure_point', ['after_insert', 'before_commit'])
def test_failure_rolls_back_run_and_attempt(dedicated_database, failure_point):
    run = seed()
    class InjectedFailure(Exception):
        pass
    class FailingConnection:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, query, parameters=None):
            result = self.conn.execute(query, parameters)
            if failure_point == 'after_insert' and query.startswith('INSERT INTO attempts'):
                raise InjectedFailure()
            return result
    @contextmanager
    def failing_factory():
        with database.connect() as conn:
            yield FailingConnection(conn)
            raise InjectedFailure()
    with pytest.raises(InjectedFailure):
        claim_next('worker-1', failing_factory)
    with psycopg.connect(dedicated_database.dsn) as conn:
        assert conn.execute('SELECT state FROM runs WHERE id=%s FOR UPDATE NOWAIT', (run.id,)).fetchone() == ('queued',)
        assert conn.execute('SELECT count(*) FROM attempts').fetchone()[0] == 0
    assert claim_next('worker-1').attempt_no == 1


def test_database_failure_has_no_success_ack_or_secret_output(dedicated_database, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    result = worker_process('worker-1')
    assert result.returncode == 1 and result.stdout == ''
    assert result.stderr == 'database unavailable; claim outcome may be unknown\n'
    with pytest.raises(database.DatabaseUnavailable):
        claim_next('worker-1')


def test_invalid_worker_id_never_connects(dedicated_database):
    def forbidden_connection():
        raise AssertionError('must validate before database access')
    for worker_id in ('', '../worker', 'x' * 65, 'worker\n1'):
        with pytest.raises(ValueError):
            claim_next(worker_id, forbidden_connection)
