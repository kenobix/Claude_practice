from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from queue import Queue
import subprocess
import sys
from threading import Barrier

import psycopg
import pytest

from ci_runner import database
from ci_runner.expiry_monitor import ExpiredAttempt, expire_next
from ci_runner.finalization import FinalizationDenied, finalize_fixture
from ci_runner.worker import LeaseRenewalDenied, claim_next, renew_lease
from test_finalization_input import report
from test_finalization_postgres import saved, wait_for_lock
from test_preflight import issue_claim
from test_runs_postgres import ROOT, dedicated_database

pytestmark = pytest.mark.integration


def set_expiry(claim, seconds=0):
    with database.connect() as conn:
        return conn.execute(
            "UPDATE attempts SET started_at=clock_timestamp()-interval '1 minute', "
            "lease_expires_at=clock_timestamp()+%s*interval '1 second' WHERE id=%s RETURNING lease_expires_at",
            (seconds, claim.attempt_id),
        ).fetchone()['lease_expires_at']


@pytest.mark.parametrize('cancel', [False, True])
def test_expiry_records_only_the_db_outcome(dedicated_database, cancel):
    _, claim = issue_claim()
    if cancel:
        with database.connect() as conn:
            conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (claim.run_id,))
    assert expire_next() is None  # Even a pending cancellation waits for lease expiry.
    deadline = set_expiry(claim)
    before = saved(claim)
    assert before['state'] == before['run_state'] == 'running'
    state, reason = ('canceled', 'canceled_lease_expired') if cancel else ('failed', 'infra_lease_expired')
    assert expire_next() == ExpiredAttempt(claim.run_id, claim.attempt_id, state, reason)
    row = saved(claim)
    assert row['state'] == row['run_state'] == state
    assert row['reason'] == reason and row['finished_at'] >= deadline
    assert row['exit_code'] is None and row['completed_attempt_id'] is None
    with database.connect() as conn:
        assert conn.execute(
            'SELECT lease_token=%s AND lease_expires_at=%s AS same_right, attempt_no FROM attempts WHERE id=%s',
            (claim.lease_token, deadline, claim.attempt_id),
        ).fetchone() == {'same_right': True, 'attempt_no': 1}
        for table in ('attempt_logs', 'attempt_sources'):
            assert conn.execute(f'SELECT count(*) AS count FROM {table}').fetchone()['count'] == 0
    assert expire_next() is None and claim_next('other-worker') is None
    assert saved(claim) == row


@pytest.mark.parametrize('winner', ['renewal', 'finalization'])
def test_locked_run_is_skipped_and_committed_authority_wins(dedicated_database, winner):
    _, claim = issue_claim()
    old_deadline = set_expiry(claim, 2)

    @contextmanager
    def hold_commit():
        with database.connect() as conn:
            yield conn
            # Another connection now sees the old lease as elapsed, with the parent still locked.
            conn.execute('SELECT pg_sleep_until(%s)', (old_deadline,))
            assert expire_next() is None

    if winner == 'renewal':
        assert renew_lease(claim, hold_commit) > old_deadline
        assert saved(claim)['state'] == 'running'
    else:
        assert finalize_fixture(claim, report(claim), hold_commit).state == 'succeeded'
        assert saved(claim)['completed_attempt_id'] == claim.attempt_id
    assert expire_next() is None


def test_expiry_wins_against_two_monitors_and_late_worker_reports(dedicated_database):
    _, claim = issue_claim()
    set_expiry(claim)
    pids = Queue()
    reports = []

    @contextmanager
    def contender():
        with database.connect() as conn:
            pids.put(conn.info.backend_pid)
            yield conn

    with ThreadPoolExecutor(max_workers=2) as pool:
        @contextmanager
        def hold_commit():
            with database.connect() as conn:
                yield conn
                assert expire_next() is None
                reports.append(pool.submit(renew_lease, claim, contender))
                reports.append(pool.submit(finalize_fixture, claim, report(claim), contender))
                for _ in range(2):
                    wait_for_lock(pids.get(timeout=5))

        assert expire_next(hold_commit).state == 'failed'
        renewal, finalization = reports
        with pytest.raises(LeaseRenewalDenied):
            renewal.result(timeout=5)
        with pytest.raises(FinalizationDenied):
            finalization.result(timeout=5)

    row = saved(claim)
    assert row['state'] == row['run_state'] == 'failed' and row['completed_attempt_id'] is None
    assert expire_next() is None


def test_two_simultaneous_monitors_report_one_expiry(dedicated_database):
    _, claim = issue_claim()
    set_expiry(claim)
    start = Barrier(2)

    def check():
        start.wait(timeout=5)
        return expire_next()

    with ThreadPoolExecutor(max_workers=2) as pool:
        contenders = [pool.submit(check) for _ in range(2)]
        receipts = [receipt for task in contenders if (receipt := task.result(timeout=5)) is not None]
    assert receipts == [ExpiredAttempt(claim.run_id, claim.attempt_id, 'failed', 'infra_lease_expired')]


@pytest.mark.parametrize('committed', [False, True])
def test_commit_fault_does_not_acknowledge_or_partially_close(dedicated_database, committed):
    _, claim = issue_claim()
    set_expiry(claim, -1)

    @contextmanager
    def faulty_connection():
        with database.connect() as conn:
            yield conn
            assert conn.execute('SELECT state FROM attempts WHERE id=%s',
                                (claim.attempt_id,)).fetchone()['state'] == 'failed'
            if not committed:
                raise psycopg.OperationalError('injected precommit disconnect')
        raise psycopg.OperationalError('injected missing commit acknowledgement')

    fault = 'missing commit acknowledgement' if committed else 'precommit disconnect'
    with pytest.raises(psycopg.OperationalError, match=f'injected {fault}'):
        expire_next(faulty_connection)
    row = saved(claim)
    assert row['state'] == row['run_state'] == ('failed' if committed else 'running')
    receipt = expire_next()
    assert (receipt is None) == committed


def test_monitor_cli_acknowledges_only_committed_expiry_without_token(dedicated_database):
    _, claim = issue_claim()
    set_expiry(claim)
    cmd = [sys.executable, '-m', 'ci_runner.expiry_monitor', '--once']
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and not result.stderr
    expiry, retry = [json.loads(line) for line in result.stdout.splitlines()]
    identity = {'run_id': str(claim.run_id), 'attempt_id': str(claim.attempt_id)}
    assert expiry == {**identity, 'state': 'failed', 'reason': 'infra_lease_expired'}
    with database.connect() as conn:
        run = conn.execute('SELECT state, available_at FROM runs WHERE id=%s', (claim.run_id,)).fetchone()
    assert run['state'] == 'queued'
    assert retry == {**identity, 'state': 'queued', 'reason': 'infra_retry_scheduled',
                     'next_attempt_no': 2, 'available_at': run['available_at'].isoformat()}
    empty = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert empty.returncode == 0 and not empty.stdout and not empty.stderr
