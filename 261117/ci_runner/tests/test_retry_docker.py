from dataclasses import replace
import os

import pytest

from ci_runner import commit_source, container, database, fixture_worker, orphan_cleanup, worker
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
from test_runs_postgres import dedicated_database

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
