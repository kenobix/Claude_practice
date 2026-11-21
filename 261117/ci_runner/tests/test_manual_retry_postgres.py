from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import time
from uuid import uuid4

import httpx
import pytest

from ci_runner import commit_source, database, finalization, runs, worker
from ci_runner.expiry_monitor import expire_next
from ci_runner.preflight import ExecutionDenied, authorize_execution
from ci_runner.retry import schedule_next
from test_commit_source import GitHubFixture
from test_expiry_monitor import set_expiry
from test_finalization_input import report
from test_preflight import issue_claim
from test_retry_postgres import db_now, make_available, parallel_calls
from test_runs_postgres import api, dedicated_database

pytestmark = pytest.mark.integration


def retry_path(claim):
    return f'/runs/{claim.run_id}/retry/{claim.attempt_id}'


def test_retry_uses_the_named_failure_and_keeps_old_history(dedicated_database):
    run, first = issue_claim()
    target = (run.id, run.commit_sha)
    source = commit_source._verify(*target, GitHubFixture(run.commit_sha))
    failure = replace(report(first, 'check_failure', 1), stdout=b'first failure', observed_bytes=13,
                      verified_source=source)
    with api() as (client, _):
        assert client.put(retry_path(first)).status_code == 409  # still running
        assert client.put(f'/runs/{uuid4()}/retry/{first.attempt_id}').status_code == 404
        finalization.finalize_fixture(first, failure)
        assert client.put(f'/runs/{run.id}/retry/{uuid4()}').status_code == 409
        before = db_now()
        first_response, second_response = parallel_calls(lambda _: client.put(retry_path(first)))
        assert first_response.status_code == second_response.status_code == 202
        receipt = first_response.json()
        assert second_response.json() == receipt
        assert receipt['run_id'] == str(run.id) and receipt['previous_attempt_id'] == str(first.attempt_id)
        assert before <= datetime.fromisoformat(receipt['requested_at']) <= db_now()
        queued = client.get(f'/runs/{run.id}').json()
        assert queued['state'] == 'queued' and queued['completed_attempt_id'] is None
        assert queued['available_at'] == receipt['requested_at'] and queued['commit_sha'] == run.commit_sha
        assert client.get(f'/runs/{run.id}/logs').status_code == 409
        assert worker.claim_next('diagnostic') is None
        assert worker.claim_next('wrong-sha', target=(run.id, 'f'*40)) is None
        assert commit_source._candidate(database.connect) == target
        second, = parallel_calls(lambda index: worker.claim_next(f'manual-{index}', target=target))
        assert second.attempt_no == 2 and second.lease_token != first.lease_token
        assert authorize_execution(second).retry_start_no == 2
        with pytest.raises(worker.LeaseRenewalDenied):
            worker.renew_lease(first)
        with pytest.raises(ExecutionDenied):
            authorize_execution(first)
        with pytest.raises(finalization.FinalizationDenied):
            finalization.finalize_fixture(second, report(second))
        with pytest.raises(finalization.FinalizationDenied):
            finalization.finalize_fixture(first, failure)
        finalization.finalize_fixture(second, replace(report(second, 'timeout', 137), verified_source=source))
        assert client.put(retry_path(first)).json() == receipt  # delayed replay never requeues the timeout
        detail = client.get(f'/runs/{run.id}').json()
        assert detail['state'] == 'timed_out' and detail['completed_attempt_id'] == str(second.attempt_id)
        assert client.get(f'/runs/{run.id}/logs').headers['x-log-attempt-id'] == str(second.attempt_id)
        history = client.get(f'/runs/{run.id}/attempts').json()['items']
        assert [(a['attempt_no'], a['retry_kind'], a['reason'], a['official']) for a in history] == [
            (2, 'manual', 'commit_timeout', True), (1, 'initial', 'commit_check_failure', False),
        ]
        assert client.put(retry_path(second)).status_code == 202
        third = worker.claim_next('manual-3', target=target)
        assert third.attempt_no == 3 and authorize_execution(third).retry_start_no == 3
        finalization.finalize_fixture(third, replace(report(third), verified_source=source))
        assert client.put(retry_path(third)).status_code == 409

    with database.connect() as conn:
        rows = conn.execute(
            'SELECT a.attempt_no, s.commit_sha, s.source_sha256, l.stdout FROM attempts a '
            'JOIN attempt_logs l ON l.attempt_id=a.id JOIN attempt_sources s ON s.attempt_id=a.id '
            'WHERE a.run_id=%s ORDER BY a.attempt_no', (run.id,),
        ).fetchall()
    assert [(row['attempt_no'], row['commit_sha'], row['source_sha256'], row['stdout']) for row in rows] == [
        (1, run.commit_sha, commit_source.DIGEST, b'first failure'), (2, run.commit_sha, commit_source.DIGEST, b''),
        (3, run.commit_sha, commit_source.DIGEST, b''),
    ]


def test_manual_retry_gets_at_most_three_attempts_and_waits_for_safe_recovery(dedicated_database):
    run, first = issue_claim()
    target = (run.id, run.commit_sha)
    with database.connect() as conn:
        conn.execute('UPDATE attempts SET create_session=%s, create_daemon_id=%s, created_container_id=%s '
                     'WHERE id=%s', (uuid4(), uuid4(), 'a'*64, first.attempt_id))
    set_expiry(first, -1)
    assert expire_next().reason == 'infra_lease_expired'
    with api() as (client, _):
        assert client.put(retry_path(first)).status_code == 409
        assert schedule_next() is None and worker.claim_next('unrecovered', target=target) is None
        # The Docker recovery round trip is tested separately. Model its durable DB evidence here.
        with database.connect() as conn:
            conn.execute('UPDATE attempts SET container_recovered_at=clock_timestamp() WHERE id=%s',
                         (first.attempt_id,))
        assert client.put(retry_path(first)).status_code == 202
        assert schedule_next() is None
        current = worker.claim_next('manual-start', target=target)
        assert current.attempt_no == 2
        for number, seconds in [(3, 5), (4, 10)]:
            set_expiry(current, -1)
            assert expire_next().reason == 'infra_lease_expired'
            before = db_now()
            scheduled = schedule_next()
            assert scheduled.next_attempt_no == number
            assert before + timedelta(seconds=seconds) <= scheduled.available_at <= db_now() + timedelta(seconds=seconds)
            assert worker.claim_next('too-early', target=target) is None
            assert client.put(retry_path(current)).status_code == 409  # automatic requeue already won
            make_available(run.id)
            current = worker.claim_next(f'infra-{number}', target=target)
            assert authorize_execution(current).retry_start_no == 2
        set_expiry(current, -1)
        assert expire_next().reason == 'infra_exhausted'
        assert schedule_next() is None and worker.claim_next('exhausted', target=target) is None
        assert client.put(retry_path(current)).status_code == 202
        assert client.put(f'/runs/{run.id}/cancel').json()['state'] == 'canceled'
        assert client.put(retry_path(first)).status_code == 202  # historical receipt only
        assert client.put(f'/runs/{run.id}/retry/{uuid4()}').status_code == 409
        assert worker.claim_next('canceled', target=target) is None


@pytest.mark.parametrize('position,committed', [('before_commit', False), ('after_commit', True)])
def test_manual_retry_recovers_from_api_loss_at_commit(dedicated_database, tmp_path, position, committed):
    run, claim = issue_claim()
    finalization.finalize_fixture(claim, report(claim, 'timeout', 137))
    marker = tmp_path / 'retry-fault'
    with api(position, marker) as (client, process):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.put, retry_path(claim))
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline and not pending.done():
                time.sleep(0.02)
            assert marker.exists()
            process.kill()
            process.wait(timeout=5)
            with pytest.raises(httpx.TransportError):
                pending.result(timeout=5)
    saved = runs.get_run(database.connect, run.id)
    assert saved.state == ('queued' if committed else 'timed_out')
    assert saved.completed_attempt_id == (None if committed else claim.attempt_id)
    with database.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM manual_retries').fetchone()['n'] == int(committed)
    with api() as (client, _):
        response = client.put(retry_path(claim))
        assert response.status_code == 202
        assert client.put(retry_path(claim)).json() == response.json()
        if committed:
            assert datetime.fromisoformat(response.json()['requested_at']) == saved.available_at
        history = client.get(f'/runs/{run.id}/attempts').json()['items']
        assert [item['id'] for item in history] == [str(claim.attempt_id)]
