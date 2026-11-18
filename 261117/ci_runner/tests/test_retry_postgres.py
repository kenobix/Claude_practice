from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from ci_runner import commit_source, database, runs, worker
from ci_runner.expiry_monitor import expire_next
from ci_runner.finalization import FinalizationDenied, finalize_fixture
from ci_runner.preflight import ExecutionDenied, authorize_execution, reserve_container_create
from ci_runner.retry import schedule_next
from test_expiry_monitor import set_expiry
from test_finalization_input import report
from test_preflight import issue_claim
from test_runs_postgres import dedicated_database

pytestmark = pytest.mark.integration


def make_available(run_id):
    with database.connect() as conn:
        conn.execute("UPDATE runs SET available_at=clock_timestamp()-interval '1 second' WHERE id=%s", (run_id,))


def db_now():
    with database.connect() as conn:
        return conn.execute('SELECT clock_timestamp() AS now').fetchone()['now']


def parallel_calls(call):
    barrier = Barrier(2)

    def contend(index):
        barrier.wait(timeout=5)
        return call(index)

    with ThreadPoolExecutor(max_workers=2) as pool:
        return [result for result in pool.map(contend, range(2)) if result is not None]


def test_two_backoffs_and_three_attempts_with_fenced_reacquisition(dedicated_database):
    run, first = issue_claim()
    target = (run.id, run.commit_sha)
    claims = [first]
    for number, seconds in enumerate((5, 10), start=2):
        old = claims[-1]
        stale_permit = authorize_execution(old)
        set_expiry(old, -1)
        assert expire_next().reason == 'infra_lease_expired'
        before = db_now()
        queued, = parallel_calls(lambda _: schedule_next())
        after = db_now()
        assert (queued.run_id, queued.attempt_id, queued.next_attempt_no) == (run.id, old.attempt_id, number)
        assert before + timedelta(seconds=seconds) <= queued.available_at <= after + timedelta(seconds=seconds)
        assert schedule_next() is None
        assert worker.claim_next('early', target=target) is None
        assert commit_source._candidate(database.connect) is None

        make_available(run.id)
        assert commit_source._candidate(database.connect) == target
        assert not worker.has_queued_candidate()
        assert worker.claim_next('diagnostic') is None
        assert worker.claim_next('wrong-sha', target=(run.id, 'f'*40)) is None
        current, = parallel_calls(lambda index: worker.claim_next(f'contender-{index}', target=target))
        assert current.attempt_no == number and current.run_id == run.id
        assert current.lease_token != old.lease_token and current.attempt_id != old.attempt_id
        assert authorize_execution(current).commit_sha == run.commit_sha
        shortened = set_expiry(current, 15)
        assert worker.renew_lease(current) > shortened
        with pytest.raises(ExecutionDenied):
            authorize_execution(old)
        with pytest.raises(ExecutionDenied):
            reserve_container_create(stale_permit, uuid4(), uuid4(), database.connect)
        with pytest.raises(worker.LeaseRenewalDenied):
            worker.renew_lease(old)
        with pytest.raises(FinalizationDenied):
            finalize_fixture(old, report(old))
        claims.append(current)

    set_expiry(claims[-1], -1)
    exhausted = expire_next()
    assert (exhausted.attempt_id, exhausted.state, exhausted.reason) == (
        claims[-1].attempt_id, 'failed', 'infra_exhausted')
    assert schedule_next() is None and worker.claim_next('fourth', target=target) is None
    replay, created = runs.create_run(database.connect, run.commit_sha)
    assert not created and (replay.id, replay.state, replay.completed_attempt_id) == (run.id, 'failed', None)
    with database.connect() as conn:
        rows = conn.execute('SELECT attempt_no, retry_kind, reason, create_tracked FROM attempts '
                            'WHERE run_id=%s ORDER BY attempt_no', (run.id,)).fetchall()
        assert [(r['attempt_no'], r['retry_kind'], r['reason'], r['create_tracked']) for r in rows] == [
            (1, 'initial', 'infra_lease_expired', True), (2, 'infrastructure', 'infra_lease_expired', True),
            (3, 'infrastructure', 'infra_exhausted', True),
        ]
        assert conn.execute('SELECT count(DISTINCT lease_token) AS n FROM attempts').fetchone()['n'] == 3
        assert conn.execute('SELECT count(*) AS n FROM attempt_logs').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM attempt_sources').fetchone()['n'] == 0


@pytest.mark.parametrize('status', ['unknown_create', 'unrecovered_id', 'legacy', 'cancel_requested',
                                    'canceled', 'check_failure', 'timeout', 'log_limit'])
def test_retries_deny_unsafe_or_ordinary_outcomes(dedicated_database, status):
    run, claim = issue_claim()
    if status in ('check_failure', 'timeout', 'log_limit'):
        finalize_fixture(claim, report(claim, status, 1 if status == 'check_failure' else 137))
    else:
        with database.connect() as conn:
            if status in ('unknown_create', 'unrecovered_id'):
                conn.execute('UPDATE attempts SET create_session=%s,create_daemon_id=%s,created_container_id=%s '
                             'WHERE id=%s', (uuid4(), uuid4(), 'b'*64 if status == 'unrecovered_id' else None,
                                             claim.attempt_id))
            if status == 'legacy':
                conn.execute('UPDATE attempts SET create_tracked=false WHERE id=%s', (claim.attempt_id,))
            if status == 'canceled':
                conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (run.id,))
        set_expiry(claim, -1)
        assert expire_next().state == ('canceled' if status == 'canceled' else 'failed')
        if status == 'cancel_requested':
            with database.connect() as conn:
                conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (run.id,))

    assert schedule_next() is None
    with database.connect() as conn:
        conn.execute("UPDATE runs SET state='queued',available_at=clock_timestamp()-interval '1 second' WHERE id=%s",
                     (run.id,))
    assert commit_source._candidate(database.connect) is None
    assert worker.claim_next('denied', target=(run.id, run.commit_sha)) is None


@pytest.mark.parametrize('committed', [False, True], ids=['rollback', 'lost-commit-response'])
def test_retry_schedule_resumes_after_database_failure(dedicated_database, committed):
    run, claim = issue_claim()
    set_expiry(claim, -1)
    assert expire_next().attempt_id == claim.attempt_id

    @contextmanager
    def interrupted():
        with database.connect() as conn:
            yield conn
            assert conn.execute('SELECT state FROM runs WHERE id=%s', (run.id,)).fetchone()['state'] == 'queued'
            if not committed:
                raise psycopg.OperationalError('injected rollback')
        raise psycopg.OperationalError('injected lost commit response')

    with pytest.raises(psycopg.OperationalError, match='injected'):
        schedule_next(interrupted)
    current = runs.get_run(database.connect, run.id)
    assert current.state == ('queued' if committed else 'failed')
    recovered = schedule_next()
    if committed:
        assert recovered is None and runs.get_run(database.connect, run.id).available_at == current.available_at
    else:
        assert recovered.run_id == run.id and recovered.attempt_id == claim.attempt_id
