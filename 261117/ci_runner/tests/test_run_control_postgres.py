from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

from ci_runner import commit_source, database, finalization, runs, worker
from ci_runner.expiry_monitor import expire_next
from ci_runner.retry import schedule_next
from test_commit_source import GitHubFixture, HEAD
from test_expiry_monitor import set_expiry
from test_finalization_input import report
from test_retry_postgres import make_available
from test_runs_postgres import api, dedicated_database, payload

pytestmark = pytest.mark.integration


def test_queued_cancellation_is_repeatable_and_never_claims(dedicated_database):
    with api() as (client, _):
        accepted = client.post('/runs', json=payload(HEAD))
        assert accepted.status_code == 201
        run_id = accepted.json()['id']
        cancel_path = f'/runs/{run_id}/cancel'
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(client.put, [cancel_path, cancel_path])
        assert first.status_code == second.status_code == 200
        canceled = first.json()
        assert second.json() == canceled
        assert canceled['state'] == 'canceled' and canceled['cancel_requested_at'] is not None
        assert canceled['completed_attempt_id'] is None
        assert client.get(f'/runs/{run_id}/attempts').json() == {'items': [], 'next_before': None}

        replay = client.post('/runs', json=payload(HEAD))
        assert replay.status_code == 200 and replay.json() == canceled
        missing = uuid4()
        assert client.put(f'/runs/{missing}/cancel').status_code == 404
        assert client.get(f'/runs/{missing}/attempts').status_code == 404

    assert worker.claim_next('diagnostic') is None
    assert not worker.has_queued_candidate()
    assert commit_source.verify_queued_source() is None


def test_attempt_history_pages_preserve_failure_reason_and_official_pointer(dedicated_database):
    run, _ = runs.create_run(database.connect, HEAD)
    other, _ = runs.create_run(database.connect, 'b' * 40)
    target = (run.id, run.commit_sha)
    initial = worker.claim_next('worker-1', target=target)
    set_expiry(initial, -1)
    assert expire_next().attempt_id == initial.attempt_id
    assert schedule_next().next_attempt_no == 2
    make_available(run.id)
    second = worker.claim_next('worker-2', target=target)
    source = commit_source._verify(run.id, run.commit_sha, GitHubFixture(HEAD))
    outcome = replace(report(second, 'check_failure', 1), verified_source=source)
    finalization.finalize_fixture(second, outcome)

    with api() as (client, _):
        path = f'/runs/{run.id}/attempts'
        newest = client.get(path, params={'limit': 1})
        assert newest.status_code == 200
        assert newest.json()['next_before'] == 2
        official, = newest.json()['items']
        assert (official['id'], official['attempt_no'], official['retry_kind'], official['state'],
                official['reason'], official['exit_code'], official['official']) == (
            str(second.attempt_id), 2, 'infrastructure', 'failed', 'commit_check_failure', 1, True,
        )
        older = client.get(path, params={'limit': 1, 'before': 2}).json()
        assert older['next_before'] is None
        expired, = older['items']
        assert (expired['id'], expired['attempt_no'], expired['reason'], expired['exit_code'], expired['official']) == (
            str(initial.attempt_id), 1, 'infra_lease_expired', None, False,
        )
        assert set(expired) == set(official) == {
            'id', 'attempt_no', 'retry_kind', 'state', 'started_at', 'finished_at', 'exit_code', 'reason', 'official',
        }
        assert client.get(path, params={'before': 1}).json() == {'items': [], 'next_before': None}
        assert client.get(f'/runs/{other.id}/attempts').json() == {'items': [], 'next_before': None}
        assert client.put(f'/runs/{run.id}/cancel').status_code == 409
        current = client.get(f'/runs/{run.id}').json()
        assert current['state'] == 'failed' and current['completed_attempt_id'] == official['id']
        assert current['cancel_requested_at'] is None
