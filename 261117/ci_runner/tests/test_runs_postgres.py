"""Real PostgreSQL + real HTTP processes. Destructive tests require a disposable DB."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

import httpx
import psycopg
import pytest

from ci_runner.expiry_monitor import expire_next
from ci_runner.finalization import finalize_fixture
from test_finalization_input import report
from ci_runner.preflight import authorize_execution
from ci_runner.worker import claim_next, renew_lease

@dataclass
class DisposableDatabase:
    dsn: str = field(repr=False)  # pytest failure output must never show credentials


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'kenobix/Claude_practice'
pytestmark = pytest.mark.integration


def payload(sha='a' * 40):
    return {'repository_id': REPOSITORY, 'commit_sha': sha}


@pytest.fixture(autouse=True)
def dedicated_database(monkeypatch):
    dsn = os.environ.get('CI_TEST_DATABASE_URL')
    if not dsn or os.environ.get('CI_TEST_DISPOSABLE') != '1':
        pytest.skip('use verify_db.py: disposable dedicated PostgreSQL required')
    monkeypatch.setenv('DATABASE_URL', dsn)
    with psycopg.connect(dsn) as conn:
        # Allowed only under the explicitly disposable test DB contract above.
        conn.execute('TRUNCATE runs CASCADE')
        conn.execute('UPDATE log_storage_budget SET capacity_bytes=67108864 WHERE singleton')
    yield DisposableDatabase(dsn)
    with psycopg.connect(dsn) as conn:
        conn.execute('TRUNCATE runs CASCADE')
        conn.execute('UPDATE log_storage_budget SET capacity_bytes=67108864 WHERE singleton')


@contextmanager
def api(fault_position=None, marker=None):
    env = os.environ.copy()
    args = [sys.executable, '-m', 'uvicorn']
    if fault_position:
        env.update(CI_FAULT_POSITION=fault_position, CI_FAULT_MARKER=str(marker))
        args += ['p1_fault_server:make_app', '--factory', '--app-dir', str(ROOT / 'tests')]
    else:
        args += ['ci_runner.app:app']
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        args += ['--fd', str(listener.fileno()), '--no-access-log', '--log-level', 'critical']
        process = subprocess.Popen(args, env=env, cwd=ROOT, pass_fds=(listener.fileno(),),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=10, trust_env=False) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise AssertionError('API process exited before startup')
                    try:
                        if client.get('/health/live').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise AssertionError('API startup timed out')
                yield client, process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            process.wait(timeout=5)


def row_count(db):
    with psycopg.connect(db.dsn) as conn:
        return conn.execute('SELECT count(*) FROM runs').fetchone()[0]


def test_manual_creation_serial_replay_and_status(dedicated_database):
    with api() as (client, _):
        assert client.get('/runs').json() == {'items': [], 'next_cursor': None}
        first = client.post('/runs', json=payload())
        assert first.status_code == 201
        run = first.json()
        assert run['ref'] == 'refs/heads/main' and run['workflow_version'] == 'fixture-v1'
        assert run['state'] == 'queued' and run['completed_attempt_id'] is None
        assert first.headers['location'] == '/runs/' + run['id']
        for _ in range(10):
            replay = client.post('/runs', json=payload())
            assert replay.status_code == 200 and replay.json() == run
        assert row_count(dedicated_database) == 1
        assert client.get('/runs/' + run['id']).json() == run
        assert client.get('/runs/' + str(uuid4())).status_code == 404
        assert client.post('/runs', json=payload('b' * 40)).status_code == 201
        with psycopg.connect(dedicated_database.dsn) as conn:
            conn.execute("UPDATE runs SET state='failed' WHERE id=%s", (run['id'],))
        replay = client.post('/runs', json=payload()).json()
        assert replay['id'] == run['id'] and replay['state'] == 'failed'
        assert row_count(dedicated_database) == 2


def test_concurrent_same_key_creates_one_run(dedicated_database):
    with api() as (client, _):
        base_url = str(client.base_url)
        barrier = threading.Barrier(10)
        def submit(_):
            with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as worker:
                barrier.wait(timeout=10)
                return worker.post('/runs', json=payload())
        with ThreadPoolExecutor(max_workers=10) as executor:
            responses = list(executor.map(submit, range(10)))
        assert sorted(r.status_code for r in responses) == [200] * 9 + [201]
        assert len({r.json()['id'] for r in responses}) == 1
        assert row_count(dedicated_database) == 1


def test_cursor_pagination_with_tied_timestamps_and_new_inserts(dedicated_database):
    with api() as (client, _):
        ids = []
        for sha in ('a', 'b', 'c'):
            ids.append(client.post('/runs', json=payload(sha * 40)).json()['id'])
        with psycopg.connect(dedicated_database.dsn) as conn:
            conn.execute("UPDATE runs SET created_at='2000-01-01T00:00:00Z'")
        first = client.get('/runs', params={'limit': 1}).json()
        assert first['items'][0]['id'] == sorted(ids, reverse=True)[0]
        # A new row above the cursor must not duplicate or displace old-page results.
        client.post('/runs', json=payload('d' * 40))
        found = [first['items'][0]['id']]
        cursor = first['next_cursor']
        while cursor:
            page = client.get('/runs', params={'limit': 1, 'cursor': cursor}).json()
            found.extend(r['id'] for r in page['items'])
            cursor = page['next_cursor']
        assert found == sorted(ids, reverse=True)
        assert len(client.get('/runs?limit=100').json()['items']) == 4


def test_api_process_restart_keeps_run(dedicated_database):
    with api() as (client, _):
        saved = client.post('/runs', json=payload()).json()
    with api() as (client, _):
        assert client.get('/runs/' + saved['id']).json() == saved
        response = client.post('/runs', json=payload())
        assert response.status_code == 200 and response.json() == saved


@pytest.mark.parametrize('position,count_after_crash,replay_status', [
    ('before_commit', 0, 201), ('after_commit', 1, 200),
])
def test_api_sigkill_at_commit_boundary(dedicated_database, position, count_after_crash, replay_status):
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / 'artifacts', prefix='fault-') as directory:
        marker = Path(directory) / 'reached'
        with api(position, marker) as (client, process):
            with ThreadPoolExecutor(max_workers=1) as executor:
                request = executor.submit(client.post, '/runs', json=payload())
                deadline = time.monotonic() + 8
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert marker.exists(), 'transaction boundary was not reached'
                process.kill()
                process.wait(timeout=5)
                with pytest.raises(httpx.TransportError):
                    request.result(timeout=5)
        assert row_count(dedicated_database) == count_after_crash
        with psycopg.connect(dedicated_database.dsn) as conn:
            saved = conn.execute('SELECT id FROM runs').fetchone()
        with api() as (client, _):
            replay = client.post('/runs', json=payload())
            assert replay.status_code == replay_status
            if saved:
                assert replay.json()['id'] == str(saved[0])
            assert row_count(dedicated_database) == 1


def test_actual_database_stop_returns_503_and_recovers(dedicated_database):
    project = os.environ.get('CI_TEST_COMPOSE_PROJECT')
    filename = os.environ.get('CI_TEST_COMPOSE_FILE')
    if project != 'ci-runner-261117-p0-check' or filename != str(ROOT / 'compose.yaml'):
        pytest.skip('database-stop experiment requires the owned verify_db.py Compose project')
    compose = ['docker', 'compose', '--project-name', project, '-f', filename]
    with api() as (client, _):
        saved = client.post('/runs', json=payload()).json()
        claim = claim_next('db-stop-check')
        assert authorize_execution(claim).run_id == claim.run_id
        saved = client.get('/runs/' + saved['id']).json()
        try:
            subprocess.run(compose + ['stop', 'db'], check=True, capture_output=True, timeout=30)
            for method, path, kwargs in [
                ('POST', '/runs', {'json': payload('b' * 40)}),
                ('GET', '/runs', {}), ('GET', '/runs/' + saved['id'], {}),
                ('GET', '/runs/' + saved['id'] + '/logs', {}),
            ]:
                response = client.request(method, path, **kwargs)
                assert response.status_code == 503
                assert response.json() == {'detail': 'database unavailable'}
                assert response.headers['retry-after'] == '1'
            assert client.get('/health/ready').status_code == 503
            with pytest.raises(psycopg.OperationalError):
                authorize_execution(claim)
            with pytest.raises(psycopg.OperationalError):
                renew_lease(claim)
            with pytest.raises(psycopg.OperationalError):
                expire_next()
            with pytest.raises(psycopg.OperationalError):
                finalize_fixture(claim, report(claim))
            worker = subprocess.run(
                [sys.executable, '-m', 'ci_runner.worker', '--claim-once', '--worker-id', 'db-stop-check'],
                capture_output=True, text=True, timeout=5, cwd=ROOT)
            assert worker.returncode == 1 and worker.stdout == ''
            assert worker.stderr == 'database unavailable; claim outcome may be unknown\n'
        finally:
            subprocess.run(compose + ['up', '-d', '--wait', 'db'], check=True, capture_output=True, timeout=60)
        assert client.get('/health/ready').status_code == 200
        assert client.get('/runs/' + saved['id']).json() == saved
        assert row_count(dedicated_database) == 1
        with psycopg.connect(dedicated_database.dsn) as conn:
            assert conn.execute('SELECT lease_expires_at FROM attempts WHERE id=%s',
                                (claim.attempt_id,)).fetchone() == (claim.lease_expires_at,)


def test_unique_conflict_wait_then_reads_committed_winner(dedicated_database):
    with api() as (client, _), psycopg.connect(dedicated_database.dsn) as owner:
        saved_id = owner.execute(
            "INSERT INTO runs(repository_id,ref,commit_sha,workflow_version) "
            "VALUES (%s,'refs/heads/main',%s,'fixture-v1') RETURNING id",
            (REPOSITORY, 'a' * 40)).fetchone()[0]
        with ThreadPoolExecutor(max_workers=1) as executor:
            request = executor.submit(client.post, '/runs', json=payload())
            try:
                deadline = time.monotonic() + 2
                lock_seen = False
                with psycopg.connect(dedicated_database.dsn, autocommit=True) as observer:
                    while time.monotonic() < deadline:
                        lock_seen = observer.execute(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name='ci-runner-intake' AND wait_event_type='Lock')"
                        ).fetchone()[0]
                        if lock_seen:
                            break
                        time.sleep(0.02)
                assert lock_seen, 'API INSERT did not wait on the uncommitted unique key'
            finally:
                owner.commit()  # release the winner, even on a failed assertion
            response = request.result(timeout=5)
        assert response.status_code == 200 and response.json()['id'] == str(saved_id)
        assert row_count(dedicated_database) == 1
