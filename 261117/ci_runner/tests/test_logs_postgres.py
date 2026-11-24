from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import threading

import psycopg
import pytest

from ci_runner import database, runs
from ci_runner.finalization import FinalizationDenied, finalize_fixture
from ci_runner.worker import claim_next
from test_finalization_input import report, logged_report
from test_preflight import issue_claim
from test_runs_postgres import dedicated_database

pytestmark = pytest.mark.integration


def ledger():
    with database.connect() as conn:
        budget = conn.execute('SELECT used_bytes,capacity_bytes FROM log_storage_budget').fetchone()
        count = conn.execute('SELECT count(*) AS n,coalesce(sum(octet_length(stdout)+octet_length(stderr)),0) AS bytes '
                             'FROM attempt_logs').fetchone()
    assert budget['used_bytes'] == count['bytes']
    return budget['used_bytes'], count['n']


def logged(claim, stdout=b'out\x00\xff<script>', stderr=b'err\n'):
    return replace(report(claim), stdout=stdout, stderr=stderr, observed_bytes=len(stdout)+len(stderr))


def test_bytes_replay_conflict_and_immutability(dedicated_database):
    _, claim = issue_claim()
    result = logged(claim)
    receipt = finalize_fixture(claim, result)
    assert ledger() == (len(result.stdout)+len(result.stderr), 1)
    with database.connect() as conn:
        before = conn.execute('SELECT * FROM attempt_logs WHERE attempt_id=%s', (claim.attempt_id,)).fetchone()
    assert before['stdout'] == result.stdout and before['stderr'] == result.stderr
    assert finalize_fixture(claim, result).replayed
    for changed in (replace(result, stdout=b'x'*len(result.stdout)),
                    replace(result, observed_bytes=result.observed_bytes+1)):
        with pytest.raises(FinalizationDenied, match='logs do not match'):
            finalize_fixture(claim, changed)
    for sql in ('UPDATE attempt_logs SET stdout=stdout WHERE attempt_id=%s',
                'DELETE FROM attempt_logs WHERE attempt_id=%s'):
        with pytest.raises(psycopg.errors.CheckViolation, match='immutable'):
            with database.connect() as conn:
                conn.execute(sql, (claim.attempt_id,))
    with database.connect() as conn:
        assert conn.execute('SELECT * FROM attempt_logs WHERE attempt_id=%s', (claim.attempt_id,)).fetchone() == before
        assert conn.execute('SELECT finished_at FROM attempts WHERE id=%s', (claim.attempt_id,)).fetchone()['finished_at'] == receipt.finished_at
    assert ledger() == (len(result.stdout)+len(result.stderr), 1)


@pytest.mark.parametrize('size', [0, 1048576])
def test_log_size_boundaries(dedicated_database, size):
    _, claim = issue_claim()
    finalize_fixture(claim, logged(claim, b'x'*size, b''))
    assert ledger() == (size, 1)


def test_sql_constraints_cannot_bypass_payload_limits(dedicated_database):
    _, claim = issue_claim()
    for payload, observed in [(b'x'*1048577, 1048577), (b'x', 0)]:
        with pytest.raises(psycopg.errors.CheckViolation):
            with database.connect() as conn:
                conn.execute('INSERT INTO attempt_logs(attempt_id,stdout,stderr,observed_bytes) VALUES (%s,%s,%s,%s)',
                             (claim.attempt_id, payload, b'', observed))
        assert ledger() == (0, 0)


def test_global_budget_serializes_competing_results(dedicated_database):
    for c in 'ab':
        runs.create_run(database.connect, c*40)
    with database.connect() as conn:
        conn.execute("UPDATE runs SET available_at=statement_timestamp()-interval '1 day'")
        conn.execute('UPDATE log_storage_budget SET capacity_bytes=4')
    claims = [claim_next(f'worker-{i}') for i in range(2)]
    barrier = threading.Barrier(2)
    def complete(claim):
        barrier.wait(timeout=3)
        try:
            return finalize_fixture(claim, logged(claim, b'abcd', b''))
        except psycopg.errors.CheckViolation:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(complete, claims))
    assert sum(r is not None for r in receipts) == 1
    assert ledger() == (4, 1)
    with database.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM runs WHERE state='succeeded'").fetchone()['n'] == 1
        assert conn.execute("SELECT count(*) AS n FROM attempts WHERE state='running' AND finished_at IS NULL").fetchone()['n'] == 1
    # Replay does not reserve quota a second time, even when full.
    winner = next(c for c, r in zip(claims, receipts) if r is not None)
    assert finalize_fixture(winner, logged(winner, b'abcd', b'')).replayed
    assert ledger() == (4, 1)


@pytest.mark.parametrize('fault', ['after_logs', 'before_commit', 'after_commit'])
def test_logs_and_quota_follow_commit_outcome(dedicated_database, fault):
    _, claim = issue_claim()
    class Connection:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, sql, args=None):
            value = self.conn.execute(sql, args)
            if fault == 'after_logs' and sql.startswith('INSERT INTO attempt_logs'):
                raise psycopg.OperationalError('public injected log-write disconnect')
            return value
    @contextmanager
    def factory():
        with database.connect() as conn:
            yield Connection(conn)
            if fault == 'before_commit':
                raise psycopg.OperationalError('public injected precommit disconnect')
        if fault == 'after_commit':
            raise psycopg.OperationalError('public injected lost commit response')
    with pytest.raises(psycopg.OperationalError):
        finalize_fixture(claim, logged_report(claim), factory)
    assert ledger() == ((16, 1) if fault == 'after_commit' else (0, 0))
    with database.connect() as conn:
        assert conn.execute('SELECT state FROM runs WHERE id=%s', (claim.run_id,)).fetchone()['state'] == ('succeeded' if fault == 'after_commit' else 'running')
    assert finalize_fixture(claim, logged_report(claim)).replayed == (fault == 'after_commit')
    assert ledger() == (16, 1)


def test_expired_report_cannot_reserve_log_budget(dedicated_database):
    _, claim = issue_claim()
    with database.connect() as conn:
        conn.execute("UPDATE attempts SET started_at=clock_timestamp()-interval '1 minute',"
                     "lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (claim.attempt_id,))
    with pytest.raises(FinalizationDenied):
        finalize_fixture(claim, logged_report(claim))
    assert ledger() == (0, 0)


def test_migration_preserves_preexisting_terminal_runs(dedicated_database):
    root = Path(__file__).resolve().parents[1]
    with database.connect() as conn:
        # Transaction-local schema in the disposable DB; rollback removes it.
        conn.execute('CREATE SCHEMA log_upgrade_check')
        conn.execute('SET LOCAL search_path TO log_upgrade_check')
        for name in ('001_initial.sql', '002_run_history.sql'):
            conn.execute((root/'migrations'/name).read_text())
        row = conn.execute("INSERT INTO runs(repository_id,ref,commit_sha,workflow_version,state) "
                           "VALUES ('old','old',%s,'old','succeeded') RETURNING id", ('a'*40,)).fetchone()
        attempt = conn.execute("INSERT INTO attempts(run_id,attempt_no,worker_id,lease_token,"
                               "lease_expires_at,finished_at,state,exit_code,retry_kind) "
                               "VALUES (%s,1,'old',gen_random_uuid(),now()+interval '30 seconds',"
                               "now(),'succeeded',0,'initial') RETURNING id", (row['id'],)).fetchone()
        conn.execute('UPDATE runs SET completed_attempt_id=%s WHERE id=%s', (attempt['id'], row['id']))
        conn.execute((root/'migrations/003_attempt_logs.sql').read_text())
        assert conn.execute('SELECT state,completed_attempt_id FROM runs WHERE id=%s', (row['id'],)).fetchone() == {
            'state': 'succeeded', 'completed_attempt_id': attempt['id']}
        assert conn.execute('SELECT count(*) AS n FROM attempt_logs').fetchone()['n'] == 0
        conn.rollback()


def test_legacy_result_without_logs_is_not_silently_backfilled(dedicated_database):
    _, claim = issue_claim()
    with database.connect() as conn:
        conn.execute("UPDATE attempts SET state='succeeded',exit_code=0,reason='fixture_success',"
                     'finished_at=clock_timestamp() WHERE id=%s', (claim.attempt_id,))
        conn.execute("UPDATE runs SET state='succeeded',completed_attempt_id=%s WHERE id=%s",
                     (claim.attempt_id, claim.run_id))
    with pytest.raises(FinalizationDenied, match='logs do not match'):
        finalize_fixture(claim, logged_report(claim))
    assert ledger() == (0, 0)
