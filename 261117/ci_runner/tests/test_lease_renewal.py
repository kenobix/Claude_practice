from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from queue import Queue
from uuid import uuid4

import psycopg
import pytest

from ci_runner import database
from ci_runner.finalization import finalize_fixture
from ci_runner.preflight import authorize_execution
from ci_runner.worker import LEASE_SECONDS, LeaseRenewalDenied, renew_lease
from test_finalization_input import report
from test_finalization_postgres import wait_for_lock
from test_preflight import issue_claim
from test_runs_postgres import dedicated_database

pytestmark = pytest.mark.integration


def test_renewal_uses_db_time_and_preserves_attempt_identity(dedicated_database):
    _, claim = issue_claim()
    with database.connect() as observer:
        before = observer.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        # The cached deadline is not part of the authority held in the database.
        deadline = renew_lease(replace(claim, lease_expires_at=claim.started_at))
        renewed = renew_lease(claim)
        after = observer.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        lease = timedelta(seconds=LEASE_SECONDS)
        assert before + lease <= deadline <= renewed <= after + lease
        stored = observer.execute(
            'SELECT id,run_id,attempt_no,worker_id,lease_token=%s AS same_token,started_at,lease_expires_at,state '
            'FROM attempts FOR UPDATE NOWAIT', (claim.lease_token,),
        ).fetchall()
        assert stored == [dict(id=claim.attempt_id, run_id=claim.run_id, attempt_no=claim.attempt_no,
                               worker_id=claim.worker_id, same_token=True, started_at=claim.started_at,
                               lease_expires_at=renewed, state='running')]
    assert authorize_execution(claim).lease_expires_at == renewed
    assert not finalize_fixture(claim, report(claim)).replayed
    with pytest.raises(LeaseRenewalDenied):
        renew_lease(claim)


@pytest.mark.parametrize('field,value', [('run_id', uuid4()), ('attempt_id', uuid4()),
                                        ('worker_id', 'other'), ('attempt_no', 2), ('lease_token', uuid4())])
def test_mismatched_claim_cannot_renew(dedicated_database, field, value):
    _, claim = issue_claim()
    with pytest.raises(LeaseRenewalDenied, match='not currently renewable'):
        renew_lease(replace(claim, **{field: value}))
    assert authorize_execution(claim).lease_expires_at == claim.lease_expires_at


def test_old_attempt_cannot_renew_even_with_a_current_attempt_for_same_run(dedicated_database):
    _, claim = issue_claim()
    with database.connect() as conn:
        conn.execute("UPDATE attempts SET state='failed',finished_at=clock_timestamp() WHERE id=%s", (claim.attempt_id,))
        conn.execute('INSERT INTO attempts(run_id,attempt_no,worker_id,lease_token,lease_expires_at,retry_kind) '
                     "VALUES (%s,2,%s,%s,clock_timestamp()+interval '30 seconds','infrastructure')",
                     (claim.run_id, claim.worker_id, uuid4()))
        sql = 'SELECT attempt_no,lease_expires_at FROM attempts WHERE run_id=%s ORDER BY attempt_no'
        before = conn.execute(sql, (claim.run_id,)).fetchall()
    with pytest.raises(LeaseRenewalDenied):
        renew_lease(claim)
    with database.connect() as conn:
        assert conn.execute(sql, (claim.run_id,)).fetchall() == before


def test_run_outside_policy_cannot_renew(dedicated_database):
    _, claim = issue_claim()
    with database.connect() as conn:
        conn.execute("UPDATE runs SET workflow_version='unsupported' WHERE id=%s", (claim.run_id,))
    with pytest.raises(LeaseRenewalDenied):
        renew_lease(claim)


def test_cancellation_committed_while_renewal_waits_wins(dedicated_database):
    _, claim = issue_claim()
    pids = Queue()

    @contextmanager
    def contender():
        with database.connect() as conn:
            pids.put(conn.info.backend_pid)
            yield conn

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.connect() as blocker:
            blocker.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (claim.run_id,))
            renewal = pool.submit(renew_lease, claim, contender)
            wait_for_lock(pids.get(timeout=3))
        with pytest.raises(LeaseRenewalDenied):
            renewal.result(timeout=5)
    with database.connect() as conn:
        assert conn.execute('SELECT lease_expires_at FROM attempts WHERE id=%s', (claim.attempt_id,)).fetchone() == {
            'lease_expires_at': claim.lease_expires_at}


def test_lease_that_expires_while_waiting_for_attempt_lock_is_not_revived(dedicated_database):
    _, claim = issue_claim()
    with database.connect() as conn:
        expires = conn.execute("UPDATE attempts SET lease_expires_at=clock_timestamp()+interval '2 seconds' "
                               'WHERE id=%s RETURNING lease_expires_at', (claim.attempt_id,)).fetchone()['lease_expires_at']
    pids = Queue()

    @contextmanager
    def contender():
        with database.connect() as conn:
            pids.put(conn.info.backend_pid)
            yield conn

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.connect() as blocker:
            blocker.execute('SELECT id FROM attempts WHERE id=%s FOR UPDATE', (claim.attempt_id,))
            renewal = pool.submit(renew_lease, claim, contender)
            wait_for_lock(pids.get(timeout=3))
            assert blocker.execute('SELECT clock_timestamp() < %s AS live', (expires,)).fetchone()['live']
            blocker.execute('SELECT pg_sleep_until(%s)', (expires,))
        with pytest.raises(LeaseRenewalDenied):
            renewal.result(timeout=5)
    with database.connect() as conn:
        assert conn.execute('SELECT lease_expires_at FROM attempts WHERE id=%s', (claim.attempt_id,)).fetchone() == {
            'lease_expires_at': expires}


@pytest.mark.parametrize('committed', [False, True])
def test_commit_error_never_returns_deadline(dedicated_database, committed):
    _, claim = issue_claim()

    @contextmanager
    def connection_with_fault():
        with database.connect() as conn:
            yield conn
            if not committed:
                raise psycopg.OperationalError('injected connection loss before commit')
        raise psycopg.OperationalError('injected loss of commit acknowledgement')

    with pytest.raises(psycopg.OperationalError):
        renew_lease(claim, connection_with_fault)
    expires = authorize_execution(claim).lease_expires_at
    if committed:
        assert expires > claim.lease_expires_at
    else:
        assert expires == claim.lease_expires_at
