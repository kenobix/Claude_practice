from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from ci_runner import database, runs
from ci_runner.preflight import ExecutionDenied, authorize_execution
from ci_runner.worker import claim_next
from test_runs_postgres import dedicated_database

pytestmark = pytest.mark.integration


def issue_claim():
    run = runs.create_run(database.connect, 'a' * 40)[0]
    # Authorization tests require an unambiguously eligible run; availability
    # scheduling itself is covered by the existing Worker claim tests.
    with database.connect() as conn:
        conn.execute("UPDATE runs SET available_at=clock_timestamp()-interval '1 day' WHERE id=%s", (run.id,))
    claim = claim_next('worker-1')
    if claim is None:
        with database.connect() as conn:
            row = conn.execute('SELECT state,available_at,clock_timestamp() AS db_now, '
                               '(SELECT count(*) FROM attempts WHERE run_id=%s) AS attempts '
                               'FROM runs WHERE id=%s', (run.id, run.id)).fetchone()
        raise AssertionError(f'preflight fixture could not acquire its eligible run: {row}')
    return run, claim


def test_current_claim_returns_db_snapshot_without_locks_or_writes(dedicated_database):
    run, claim = issue_claim()
    permit = authorize_execution(claim)
    assert permit.run_id == run.id and permit.attempt_id == claim.attempt_id
    assert 0 < permit.remaining_lease_seconds <= 30
    assert permit.attempt_no == 1 and permit.worker_id == claim.worker_id
    assert permit.lease_token == claim.lease_token
    assert permit.lease_expires_at == claim.lease_expires_at
    assert (permit.repository_id, permit.ref, permit.commit_sha, permit.workflow_version) == (
        'kenobix/Claude_practice', 'refs/heads/main', 'a' * 40, 'fixture-v1')
    assert str(permit.lease_token) not in repr(permit)
    with psycopg.connect(dedicated_database.dsn) as conn:
        assert conn.execute('SELECT state FROM runs WHERE id=%s FOR UPDATE NOWAIT', (run.id,)).fetchone() == ('running',)
        assert conn.execute('SELECT state, lease_expires_at FROM attempts WHERE id=%s FOR UPDATE NOWAIT',
                            (claim.attempt_id,)).fetchone() == ('running', claim.lease_expires_at)
        assert conn.execute('SELECT count(*) FROM attempts').fetchone()[0] == 1


@pytest.mark.parametrize('field,value', [
    ('run_id', uuid4()), ('attempt_id', uuid4()), ('lease_token', uuid4()),
    ('worker_id', 'other-worker'), ('attempt_no', 2),
])
def test_mismatched_claim_is_denied(dedicated_database, field, value):
    _, claim = issue_claim()
    with pytest.raises(ExecutionDenied, match='not currently valid'):
        authorize_execution(replace(claim, **{field: value}))


@pytest.mark.parametrize('change', [
    'cancel', 'run_terminal', 'attempt_terminal', 'token_replaced',
    'repository_changed', 'ref_changed', 'workflow_changed', 'result_already_set',
])
def test_db_state_changes_invalidate_cached_claim(dedicated_database, change):
    run, claim = issue_claim()
    with psycopg.connect(dedicated_database.dsn) as conn:
        if change == 'cancel':
            conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (run.id,))
        elif change == 'run_terminal':
            conn.execute("UPDATE runs SET state='canceled' WHERE id=%s", (run.id,))
        elif change == 'attempt_terminal':
            conn.execute("UPDATE attempts SET state='failed',finished_at=clock_timestamp() WHERE id=%s", (claim.attempt_id,))
        elif change == 'token_replaced':
            conn.execute('UPDATE attempts SET lease_token=%s WHERE id=%s', (uuid4(), claim.attempt_id))
        elif change in ('repository_changed', 'ref_changed', 'workflow_changed'):
            # Only these static test SQL statements may select a column name.
            queries = {
                'repository_changed': "UPDATE runs SET repository_id='other/repository' WHERE id=%s",
                'ref_changed': "UPDATE runs SET ref='refs/heads/other' WHERE id=%s",
                'workflow_changed': "UPDATE runs SET workflow_version='other-v1' WHERE id=%s",
            }
            conn.execute(queries[change], (run.id,))
        else:
            conn.execute('UPDATE runs SET completed_attempt_id=%s WHERE id=%s', (claim.attempt_id, run.id))
    with pytest.raises(ExecutionDenied):
        authorize_execution(claim)


def test_db_expiry_overrides_a_future_cached_lease(dedicated_database):
    _, claim = issue_claim()
    with psycopg.connect(dedicated_database.dsn) as conn:
        # Keep lease > started_at as required by schema while moving both into the past.
        conn.execute("UPDATE attempts SET started_at=clock_timestamp()-interval '60 seconds', "
                     "lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (claim.attempt_id,))
    forged = replace(claim, lease_expires_at=claim.lease_expires_at + timedelta(days=1))
    with pytest.raises(ExecutionDenied):
        authorize_execution(forged)


def test_db_lease_overrides_an_expired_cached_timestamp(dedicated_database):
    _, claim = issue_claim()
    stale = replace(claim, lease_expires_at=claim.started_at - timedelta(days=1))
    permit = authorize_execution(stale)
    assert permit.lease_expires_at == claim.lease_expires_at


def test_old_claim_cannot_authorize_a_new_attempt(dedicated_database):
    run, claim = issue_claim()
    with psycopg.connect(dedicated_database.dsn) as conn:
        conn.execute("UPDATE attempts SET state='failed',finished_at=clock_timestamp() WHERE id=%s", (claim.attempt_id,))
        conn.execute('INSERT INTO attempts(run_id,attempt_no,worker_id,lease_token,started_at,'
                     "lease_expires_at,retry_kind) VALUES (%s,2,'worker-2',%s,statement_timestamp(),"
                     "statement_timestamp()+interval '30 seconds','infrastructure')", (run.id, uuid4()))
    with pytest.raises(ExecutionDenied):
        authorize_execution(claim)


def test_unavailable_database_and_context_exit_error_never_return_permit(dedicated_database, monkeypatch):
    _, claim = issue_claim()
    @contextmanager
    def lost_connection():
        with database.connect() as conn:
            yield conn
            raise psycopg.OperationalError('public injected connection loss')
    with pytest.raises(psycopg.OperationalError):
        authorize_execution(claim, lost_connection)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(database.DatabaseUnavailable):
        authorize_execution(claim)


def test_later_cancellation_requires_rechecking_the_snapshot(dedicated_database):
    run, claim = issue_claim()
    authorize_execution(claim)
    with psycopg.connect(dedicated_database.dsn) as conn:
        conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (run.id,))
    with pytest.raises(ExecutionDenied):
        authorize_execution(claim)
