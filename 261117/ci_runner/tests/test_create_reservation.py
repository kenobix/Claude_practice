from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from queue import Queue
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from ci_runner import database
from ci_runner.expiry_monitor import expire_next
from ci_runner.preflight import (ExecutionDenied, authorize_execution, record_created_container,
                                 reserve_container_create)
from test_expiry_monitor import set_expiry
from test_finalization_postgres import wait_for_lock
from test_preflight import issue_claim
from test_runs_postgres import dedicated_database

pytestmark = pytest.mark.integration


def create_history(claim):
    with database.connect() as conn:
        return conn.execute(
            'SELECT create_tracked, create_session, create_daemon_id, created_container_id '
            'FROM attempts WHERE id=%s', (claim.attempt_id,),
        ).fetchone()


@pytest.mark.parametrize('fault', ['legacy', 'stale_token', 'wrong_sha', 'cancel', 'expired'])
def test_reservation_refuses_invalid_or_legacy_attempts(dedicated_database, fault):
    _, claim = issue_claim()
    permit = authorize_execution(claim)
    if fault == 'legacy':
        with database.connect() as conn:
            conn.execute('UPDATE attempts SET create_tracked=false WHERE id=%s', (claim.attempt_id,))
    elif fault == 'stale_token':
        permit = replace(permit, lease_token=uuid4())
    elif fault == 'wrong_sha':
        permit = replace(permit, commit_sha='f'*40)
    elif fault == 'cancel':
        with database.connect() as conn:
            conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (claim.run_id,))
    else:
        set_expiry(claim)
    with pytest.raises(ExecutionDenied, match='cannot be reserved'):
        reserve_container_create(permit, uuid4(), uuid4(), database.connect)
    assert create_history(claim) == {'create_tracked': fault != 'legacy', 'create_session': None,
                                     'create_daemon_id': None, 'created_container_id': None}


def test_one_reservation_wins_and_only_its_docker_id_can_be_recorded(dedicated_database):
    _, claim = issue_claim()
    permit = authorize_execution(claim)
    daemon = uuid4()
    ready = Barrier(2)

    def create(session):
        ready.wait(timeout=5)
        try:
            reserve_container_create(permit, daemon, session, database.connect)
        except ExecutionDenied:
            return None
        return session

    sessions = [uuid4(), uuid4()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        (winner,) = [session for session in pool.map(create, sessions) if session is not None]
    assert create_history(claim) == {'create_tracked': True, 'create_session': winner,
                                     'create_daemon_id': daemon, 'created_container_id': None}
    set_expiry(claim)
    assert expire_next().attempt_id == claim.attempt_id
    with pytest.raises(ExecutionDenied):
        record_created_container(permit, uuid4(), winner, 'c'*64, database.connect)
    with pytest.raises(ExecutionDenied):
        record_created_container(permit, daemon, uuid4(), 'c'*64, database.connect)
    record_created_container(permit, daemon, winner, 'a'*64, database.connect)
    with pytest.raises(ExecutionDenied):
        record_created_container(permit, daemon, winner, 'b'*64, database.connect)
    assert create_history(claim)['created_container_id'] == 'a'*64


@pytest.mark.parametrize('committed', [False, True])
def test_reservation_commit_fault_distinguishes_no_dispatch_and_uncertainty(dedicated_database, committed):
    _, claim = issue_claim()
    permit = authorize_execution(claim)
    daemon, session = uuid4(), uuid4()

    @contextmanager
    def faulty_connection():
        with database.connect() as conn:
            yield conn
            if not committed:
                raise psycopg.OperationalError('injected precommit disconnect')
        raise psycopg.OperationalError('injected commit acknowledgement loss')

    with pytest.raises(psycopg.OperationalError):
        reserve_container_create(permit, daemon, session, faulty_connection)
    assert create_history(claim) == {'create_tracked': True, 'create_session': session if committed else None,
                                     'create_daemon_id': daemon if committed else None, 'created_container_id': None}


def test_expiry_during_attempt_row_lock_rejects_create(dedicated_database):
    _, claim = issue_claim()
    permit = authorize_execution(claim)
    deadline = set_expiry(claim, 2)
    backend = Queue()

    @contextmanager
    def contender():
        with database.connect() as conn:
            backend.put(conn.info.backend_pid)
            yield conn

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.connect() as conn:
            conn.execute('SELECT id FROM attempts WHERE id=%s FOR UPDATE', (claim.attempt_id,))
            pending = pool.submit(reserve_container_create, permit, uuid4(), uuid4(), contender)
            wait_for_lock(backend.get(timeout=5))
            conn.execute('SELECT pg_sleep_until(%s)', (deadline,))
        with pytest.raises(ExecutionDenied):
            pending.result(timeout=5)
    assert expire_next().attempt_id == claim.attempt_id
    assert create_history(claim)['create_session'] is None
