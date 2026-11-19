from dataclasses import replace
import os

import pytest

from ci_runner import commit_source, container, database, fixture_worker, orphan_cleanup, runs, worker
from ci_runner.expiry_monitor import expire_next
from ci_runner.finalization import FinalizationDenied, finalize_fixture
from ci_runner.preflight import authorize_execution
from ci_runner.retry import schedule_next
from test_commit_source import GitHubFixture, HEAD
from test_expiry_monitor import set_expiry
from test_finalization_input import report
from test_fixture_worker_docker import resources
from test_orphan_cleanup_docker import killed_worker, recovery_time
from test_preflight import issue_claim
from test_retry_postgres import make_available
from test_runs_postgres import api, dedicated_database

pytestmark = [pytest.mark.integration, pytest.mark.docker]


def test_verified_retry_recovers_second_attempt_before_running_third(dedicated_database, monkeypatch):
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1':
        pytest.skip('run scripts/verify_execution.py for DB/Docker retry checks')

    before = resources()
    monkeypatch.setattr(commit_source, '_GitHub', lambda: GitHubFixture(HEAD))
    run, first = issue_claim()
    assert run.commit_sha == HEAD
    set_expiry(first, -1)
    assert expire_next().attempt_id == first.attempt_id
    assert schedule_next().next_attempt_no == 2
    make_available(run.id)

    source = commit_source.verify_queued_source()
    assert (source.run_id, source.commit_sha) == (run.id, run.commit_sha)
    with container.prepare_image(verified_source=source) as image:
        second = worker.claim_next('retry-2', target=(source.run_id, source.commit_sha))
        assert second.attempt_no == 2
        with pytest.raises(container.ContainerRejected, match='verified source'), container.prepare_fixture(
                authorize_execution(second)):
            pytest.fail('local image prepared a retry')
        with pytest.raises(FinalizationDenied):
            finalize_fixture(second, report(second))
        with killed_worker(second, image, 'created') as orphan:
            set_expiry(second, -1)
            assert expire_next().attempt_id == second.attempt_id
            assert schedule_next() is None
            assert list(orphan_cleanup.recover()) == [
                orphan_cleanup.RemovedContainer(orphan, run.id, second.attempt_id)]
            assert recovery_time(second) is not None
            assert orphan_cleanup._inspect(orphan) is None
            queued = schedule_next()
            assert queued.next_attempt_no == 3 and queued.attempt_id == second.attempt_id

    make_available(run.id)
    result = fixture_worker.run_once('retry-3', verified_commit=True)
    assert (result.run_id, result.state, result.reason) == (run.id, 'succeeded', 'commit_success')
    with pytest.raises(FinalizationDenied):
        finalize_fixture(second, replace(report(second), verified_source=source))
    with database.connect() as conn:
        official = conn.execute(
            'SELECT a.attempt_no, s.commit_sha, s.source_sha256, s.image_id, l.observed_bytes '
            'FROM runs r JOIN attempts a ON a.id=r.completed_attempt_id '
            'JOIN attempt_sources s ON s.attempt_id=a.id JOIN attempt_logs l ON l.attempt_id=a.id '
            'WHERE r.id=%s AND a.id=%s', (run.id, result.attempt_id),
        ).fetchone()
        assert (official['attempt_no'], official['commit_sha'], official['source_sha256']) == (
            3, run.commit_sha, commit_source.DIGEST)
        assert official['image_id'].startswith('sha256:') and official['observed_bytes'] > 0
        assert conn.execute('SELECT count(*) AS n FROM attempt_sources').fetchone()['n'] == 1
    assert schedule_next() is None and resources() == before


def test_manual_retry_recovers_orphan_and_promotes_only_new_result(dedicated_database, monkeypatch):
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1':
        pytest.skip('run scripts/verify_execution.py for DB/Docker retry checks')

    before = resources()
    monkeypatch.setattr(commit_source, '_GitHub', lambda: GitHubFixture(HEAD))
    run, _ = runs.create_run(database.connect, HEAD)
    first = fixture_worker.run_once('original', mode='failure', verified_commit=True)
    assert (first.run_id, first.state, first.reason) == (run.id, 'failed', 'commit_check_failure')

    with api() as (client, _):
        retry = client.put(f'/runs/{run.id}/retry/{first.attempt_id}')
        assert retry.status_code == 202
        source = commit_source.verify_queued_source()
        assert (source.run_id, source.commit_sha) == (run.id, run.commit_sha)
        with container.prepare_image(verified_source=source) as image:
            manual = worker.claim_next('manual', target=(source.run_id, source.commit_sha))
            with pytest.raises(container.ContainerRejected, match='verified source'), container.prepare_fixture(
                    authorize_execution(manual)):
                pytest.fail('local image prepared a manual retry')
            with killed_worker(manual, image, 'created') as orphan:
                set_expiry(manual, -1)
                assert expire_next().reason == 'infra_lease_expired'
                assert client.put(f'/runs/{run.id}/retry/{manual.attempt_id}').status_code == 409
                assert schedule_next() is None
                assert list(orphan_cleanup.recover()) == [
                    orphan_cleanup.RemovedContainer(orphan, run.id, manual.attempt_id)]
                assert recovery_time(manual) is not None and orphan_cleanup._inspect(orphan) is None
                scheduled = schedule_next()
                assert scheduled.next_attempt_no == 3 and scheduled.attempt_id == manual.attempt_id

        make_available(run.id)
        last = fixture_worker.run_once('replacement', verified_commit=True)
        assert (last.run_id, last.state, last.reason) == (run.id, 'succeeded', 'commit_success')
        with pytest.raises(FinalizationDenied):
            finalize_fixture(manual, replace(report(manual), verified_source=source))
        assert client.put(f'/runs/{run.id}/retry/{first.attempt_id}').json() == retry.json()
        assert client.get(f'/runs/{run.id}/logs').headers['x-log-attempt-id'] == str(last.attempt_id)
        attempts = client.get(f'/runs/{run.id}/attempts').json()['items']
        assert [(row['retry_kind'], row['reason'], row['official']) for row in attempts] == [
            ('infrastructure', 'commit_success', True), ('manual', 'infra_lease_expired', False),
            ('initial', 'commit_check_failure', False),
        ]

    with database.connect() as conn:
        rows = conn.execute(
            'SELECT s.attempt_id, s.commit_sha, s.source_sha256, s.image_id, l.observed_bytes '
            'FROM attempt_sources s JOIN attempt_logs l ON l.attempt_id=s.attempt_id WHERE s.run_id=%s', (run.id,),
        ).fetchall()
    assert {row['attempt_id'] for row in rows} == {first.attempt_id, last.attempt_id}
    assert all(row['commit_sha'] == run.commit_sha and row['source_sha256'] == commit_source.DIGEST
               and row['image_id'].startswith('sha256:') and row['observed_bytes'] > 0 for row in rows)
    assert resources() == before
