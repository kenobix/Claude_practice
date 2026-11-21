from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import replace
import threading

import psycopg
import pytest

from ci_runner import commit_source, database, finalization, runs, worker
from test_commit_source import GitHubFixture, HEAD
from test_finalization_input import logged_report
from test_runs_postgres import dedicated_database

pytestmark = pytest.mark.integration


def test_competing_verified_claims_cannot_take_a_different_run(dedicated_database):
    run, other = [runs.create_run(database.connect, char*40)[0] for char in 'ab']
    barrier = threading.Barrier(2)

    def contend(index):
        barrier.wait(timeout=5)
        return worker.claim_next(f'worker-{index}', target=(run.id, run.commit_sha))

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = [claim for claim in executor.map(contend, range(2)) if claim]
    assert [claim.run_id for claim in claims] == [run.id]
    assert worker.matches_verified_target((run.id, run.commit_sha))
    assert runs.get_run(database.connect, other.id).state == 'queued'


def test_verified_claim_refuses_sha_mismatch_without_mutation(dedicated_database):
    run = runs.create_run(database.connect, HEAD)[0]
    assert worker.claim_next('worker-1', target=(run.id, 'b'*40)) is None
    assert not worker.matches_verified_target((run.id, 'b'*40))
    assert runs.get_run(database.connect, run.id).state == 'queued'
    with database.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == 0


@pytest.mark.parametrize('version', [4, 6, 7, 8])
def test_verified_claim_requires_provenance_and_recovery_migrations(dedicated_database, version):
    run = runs.create_run(database.connect, HEAD)[0]
    with database.connect() as conn:
        # Temporarily simulate an earlier schema in this transaction only.
        conn.execute('DELETE FROM schema_migrations WHERE version=%s', (version,))
        assert worker.claim_next('worker-1', lambda: nullcontext(conn),
                                 target=(run.id, run.commit_sha)) is None
        assert not worker.matches_verified_target((run.id, run.commit_sha), lambda: nullcontext(conn))
        assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == 0
        conn.rollback()


def test_verified_miss_checks_original_policy(dedicated_database):
    run = runs.create_run(database.connect, HEAD)[0]
    with database.connect() as conn:
        conn.execute("UPDATE runs SET ref='refs/heads/other' WHERE id=%s", (run.id,))
    assert not worker.matches_verified_target((run.id, run.commit_sha))


def test_provenance_finalization_fences_sha_and_replay(dedicated_database):
    run = runs.create_run(database.connect, HEAD)[0]
    claim = worker.claim_next('worker-1', target=(run.id, run.commit_sha))
    source = commit_source._verify(run.id, HEAD, GitHubFixture(HEAD))
    report = replace(logged_report(claim), verified_source=source)
    wrong_sha = replace(report, verified_source=replace(source, commit_sha='f'*40))
    with pytest.raises(finalization.FinalizationDenied, match='SHA does not match'):
        finalization.finalize_fixture(claim, wrong_sha)
    with database.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM attempt_sources').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM attempt_logs').fetchone()['n'] == 0
    receipt = finalization.finalize_fixture(claim, report)
    assert receipt.reason == 'commit_success'
    assert finalization.finalize_fixture(claim, report).replayed
    with pytest.raises(finalization.FinalizationDenied, match='persisted source does not match'):
        finalization.finalize_fixture(claim, replace(report, image_id='sha256:'+'f'*64))


def test_failure_after_provenance_insert_rolls_back_all_result_data(dedicated_database):
    run = runs.create_run(database.connect, HEAD)[0]
    claim = worker.claim_next('worker-1', target=(run.id, run.commit_sha))
    source = commit_source._verify(run.id, HEAD, GitHubFixture(HEAD))

    @contextmanager
    def failing_transaction():
        with database.connect() as conn:
            class FailAfterInsert:
                def execute(self, sql, params):
                    cursor = conn.execute(sql, params)
                    if sql.startswith('INSERT INTO attempt_sources'):
                        raise psycopg.OperationalError('injected failure before run update')
                    return cursor
            yield FailAfterInsert()

    with pytest.raises(psycopg.OperationalError):
        finalization.finalize_fixture(
            claim, replace(logged_report(claim), verified_source=source), failing_transaction)
    with database.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM attempt_sources').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM attempt_logs').fetchone()['n'] == 0
        assert conn.execute('SELECT used_bytes FROM log_storage_budget').fetchone()['used_bytes'] == 0
        assert conn.execute('SELECT state FROM attempts WHERE id=%s', (claim.attempt_id,)).fetchone()['state'] == 'running'
    assert runs.get_run(database.connect, run.id).completed_attempt_id is None
