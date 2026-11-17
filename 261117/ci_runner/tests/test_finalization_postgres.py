from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from uuid import uuid4

import psycopg
import pytest

from ci_runner import database, runs
from ci_runner.finalization import FinalizationDenied, finalize_fixture
from test_finalization_input import report, logged_report
from test_preflight import issue_claim
from test_runs_postgres import dedicated_database, api, ROOT

pytestmark = pytest.mark.integration


def saved(claim):
    with database.connect() as conn:
        return conn.execute('SELECT r.state AS run_state,r.completed_attempt_id,a.state,'
                            'a.exit_code,a.reason,a.finished_at FROM runs r JOIN attempts a ON a.run_id=r.id '
                            'WHERE a.id=%s', (claim.attempt_id,)).fetchone()


@pytest.mark.parametrize('reason,code,state,stored_reason', [
    ('completed', 0, 'succeeded', 'fixture_success'),
    ('check_failure', 1, 'failed', 'fixture_check_failure'),
    ('timeout', 137, 'timed_out', 'fixture_timeout'),
    ('log_limit', 137, 'failed', 'fixture_log_limit'),
])
def test_outcomes_replay_and_conflict(dedicated_database, reason, code, state, stored_reason):
    _, claim = issue_claim()
    result = report(claim, reason, code)
    first = finalize_fixture(claim, result)
    row = saved(claim)
    assert row == dict(run_state=state, completed_attempt_id=claim.attempt_id, state=state,
                       exit_code=code, reason=stored_reason, finished_at=first.finished_at)
    assert not first.replayed and finalize_fixture(claim, result).replayed
    # Acknowledging a committed receipt does not require renewed execution rights.
    with database.connect() as conn:
        conn.execute("UPDATE attempts SET started_at=clock_timestamp()-interval '60 seconds',"
                     "lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (claim.attempt_id,))
    assert finalize_fixture(claim, result).finished_at == first.finished_at
    conflicting = report(claim, 'check_failure', 2) if code == 0 else report(claim)
    with pytest.raises(FinalizationDenied):
        finalize_fixture(claim, conflicting)
    assert saved(claim) == row
    with database.connect() as conn:
        conn.execute('SELECT id FROM runs WHERE id=%s FOR UPDATE NOWAIT', (claim.run_id,))
        conn.execute('SELECT id FROM attempts WHERE id=%s FOR UPDATE NOWAIT', (claim.attempt_id,))


@pytest.mark.parametrize('field,value', [('lease_token', uuid4()), ('worker_id', 'other'),
                                        ('attempt_no', 2), ('attempt_id', uuid4()), ('run_id', uuid4())])
def test_wrong_claim_is_denied(dedicated_database, field, value):
    _, claim = issue_claim()
    wrong = replace(claim, **{field:value})
    with pytest.raises(FinalizationDenied):
        finalize_fixture(wrong, report(wrong))
    assert saved(claim)['state'] == 'running'


@pytest.mark.parametrize('change', ['cancel', 'expired', 'run_terminal', 'attempt_terminal',
                                   'token_replaced', 'policy_changed', 'new_attempt'])
def test_stale_or_canceled_report_never_overwrites(dedicated_database, change):
    _, claim = issue_claim()
    with database.connect() as conn:
        if change == 'cancel':
            conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (claim.run_id,))
        elif change == 'expired':
            conn.execute("UPDATE attempts SET started_at=clock_timestamp()-interval '60 seconds',"
                         "lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (claim.attempt_id,))
        elif change == 'run_terminal':
            conn.execute("UPDATE runs SET state='canceled' WHERE id=%s", (claim.run_id,))
        elif change == 'policy_changed':
            conn.execute("UPDATE runs SET workflow_version='other' WHERE id=%s", (claim.run_id,))
        elif change == 'token_replaced':
            conn.execute('UPDATE attempts SET lease_token=%s WHERE id=%s', (uuid4(), claim.attempt_id))
        else:
            conn.execute("UPDATE attempts SET state='failed',finished_at=clock_timestamp() WHERE id=%s", (claim.attempt_id,))
            if change == 'new_attempt':
                conn.execute('INSERT INTO attempts(run_id,attempt_no,worker_id,lease_token,lease_expires_at,retry_kind) '
                             "VALUES (%s,2,'worker-2',%s,clock_timestamp()+interval '30 seconds','infrastructure')",
                             (claim.run_id, uuid4()))
    before = saved(claim)
    with pytest.raises(FinalizationDenied):
        finalize_fixture(claim, report(claim))
    assert saved(claim) == before


@pytest.mark.parametrize('fault', ['after_attempt', 'between_updates_expiry', 'before_commit', 'after_commit'])
def test_transaction_faults_and_commit_uncertainty(dedicated_database, fault):
    _, claim = issue_claim()
    result = logged_report(claim)
    class Connection:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, sql, args=None):
            value = self.conn.execute(sql, args)
            if sql.startswith('UPDATE attempts SET state='):
                if fault == 'after_attempt':
                    raise psycopg.OperationalError('public injected failure after attempt write')
                if fault == 'between_updates_expiry':
                    self.conn.execute("UPDATE attempts SET started_at=clock_timestamp()-interval '60 seconds',"
                                      "lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (claim.attempt_id,))
            return value
    @contextmanager
    def factory():
        with database.connect() as conn:
            yield Connection(conn)
            if fault == 'before_commit':
                raise psycopg.OperationalError('public injected precommit failure')
        if fault == 'after_commit':
            raise psycopg.OperationalError('public injected missing commit acknowledgement')
    with pytest.raises(FinalizationDenied if fault == 'between_updates_expiry' else psycopg.OperationalError):
        finalize_fixture(claim, result, factory)
    row = saved(claim)
    assert row['state'] == ('succeeded' if fault == 'after_commit' else 'running')
    assert row['run_state'] == row['state']
    with database.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM attempt_logs').fetchone()['n'] == (1 if fault == 'after_commit' else 0)
        assert conn.execute('SELECT used_bytes FROM log_storage_budget').fetchone()['used_bytes'] == (16 if fault == 'after_commit' else 0)
    assert finalize_fixture(claim, result).replayed == (fault == 'after_commit')


def wait_for_lock(pid):
    until = time.monotonic() + 3
    with database.connect() as observer:
        while time.monotonic() < until:
            row = observer.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s', (pid,)).fetchone()
            if row and row['wait_event_type'] == 'Lock':
                return
            time.sleep(0.02)
    pytest.fail('expected real PostgreSQL lock wait was not observed')


@pytest.mark.parametrize('winner', ['cancel', 'expiry'])
def test_state_and_time_rechecked_after_lock_wait(dedicated_database, winner):
    _, claim = issue_claim()
    pids = queue.Queue()
    @contextmanager
    def contender():
        with database.connect() as conn:
            pids.put(conn.info.backend_pid)
            yield conn
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.connect() as blocker:
            blocker.execute('SELECT id FROM runs WHERE id=%s FOR UPDATE', (claim.run_id,))
            future = pool.submit(finalize_fixture, claim, report(claim), contender)
            wait_for_lock(pids.get(timeout=3))
            if winner == 'cancel':
                blocker.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (claim.run_id,))
            else:
                blocker.execute("UPDATE attempts SET started_at=clock_timestamp()-interval '60 seconds',"
                                "lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (claim.attempt_id,))
        with pytest.raises(FinalizationDenied):
            future.result(timeout=5)
    assert saved(claim)['state'] == 'running'


def test_completion_wins_conditional_cancel_and_concurrent_replay(dedicated_database):
    _, claim = issue_claim()
    ready, release = threading.Event(), threading.Event()
    cancel_pid, replay_pid = queue.Queue(), queue.Queue()
    @contextmanager
    def replay_connection():
        with database.connect() as conn:
            replay_pid.put(conn.info.backend_pid)
            yield conn
    @contextmanager
    def held_commit():
        with database.connect() as conn:
            yield conn
            ready.set()
            assert release.wait(timeout=5)
    def cancel():
        with database.connect() as conn:
            cancel_pid.put(conn.info.backend_pid)
            return conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() '
                                "WHERE id=%s AND state='running' RETURNING id", (claim.run_id,)).fetchone()
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(finalize_fixture, claim, report(claim), held_commit)
        try:
            assert ready.wait(timeout=3)
            cancellation = pool.submit(cancel)
            wait_for_lock(cancel_pid.get(timeout=3))
            replay = pool.submit(finalize_fixture, claim, report(claim), replay_connection)
            wait_for_lock(replay_pid.get(timeout=3))
        finally:
            release.set()
        assert first.result(timeout=5).replayed is False
        assert cancellation.result(timeout=5) is None
        assert replay.result(timeout=5).replayed is True
    assert saved(claim)['state'] == 'succeeded'


@pytest.mark.parametrize('position', ['before_commit', 'after_commit'])
def test_process_kill_at_commit_boundary(dedicated_database, tmp_path, position):
    _, claim = issue_claim()
    marker = tmp_path / 'barrier'
    env = os.environ.copy()
    env.update(CI_FINALIZE_RUN_ID=str(claim.run_id), CI_FAULT_POSITION=position, CI_FAULT_MARKER=str(marker))
    process = subprocess.Popen([sys.executable, str(ROOT/'tests/finalization_fault.py')],
                               env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        until = time.monotonic()+8
        while not marker.exists() and time.monotonic()<until and process.poll() is None:
            time.sleep(0.02)
        assert marker.exists(), 'finalizer did not reach its commit boundary'
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    assert saved(claim)['state'] == ('running' if position == 'before_commit' else 'succeeded')
    with database.connect() as conn:
        logs = conn.execute('SELECT stdout FROM attempt_logs WHERE attempt_id=%s', (claim.attempt_id,)).fetchone()
        assert logs == (None if position == 'before_commit' else {'stdout': b'fixed commit log'})
        assert conn.execute('SELECT used_bytes FROM log_storage_budget').fetchone()['used_bytes'] == (0 if position == 'before_commit' else 16)
    assert finalize_fixture(claim, logged_report(claim)).replayed == (position == 'after_commit')


def test_persistence_after_api_and_db_restart(dedicated_database):
    project, filename = os.environ.get('CI_TEST_COMPOSE_PROJECT'), os.environ.get('CI_TEST_COMPOSE_FILE')
    if project != 'ci-runner-261117-p0-check' or filename != str(ROOT/'compose.yaml'):
        pytest.skip('restart requires the owned disposable Compose project')
    _, claim = issue_claim()
    receipt = finalize_fixture(claim, logged_report(claim))
    with api() as (client, _):
        original = client.get(f'/runs/{claim.run_id}').json()
        assert client.get(f'/runs/{claim.run_id}/logs').text == 'fixed commit log'
        assert original['state'] == 'succeeded' and original['completed_attempt_id'] == str(claim.attempt_id)
    compose = ['docker','compose','--project-name',project,'-f',filename]
    subprocess.run(compose+['restart','db'],check=True,capture_output=True,timeout=30)
    subprocess.run(compose+['up','-d','--wait','db'],check=True,capture_output=True,timeout=60)
    with api() as (client, _):
        assert client.get(f'/runs/{claim.run_id}').json() == original
        assert client.get('/runs').json()['items'][0] == original
        log_page = client.get(f'/runs/{claim.run_id}/logs')
        assert log_page.text == 'fixed commit log' and log_page.headers['x-log-attempt-id'] == str(claim.attempt_id)
    assert saved(claim)['finished_at'] == receipt.finished_at
    with database.connect() as conn:
        assert conn.execute('SELECT stdout,observed_bytes FROM attempt_logs WHERE attempt_id=%s',
                            (claim.attempt_id,)).fetchone() == {'stdout': b'fixed commit log', 'observed_bytes': 16}
        assert conn.execute('SELECT used_bytes FROM log_storage_budget').fetchone()['used_bytes'] == 16
    assert runs.get_run(database.connect, claim.run_id).state == 'succeeded'
