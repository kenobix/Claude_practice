from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest

from ci_runner import artifacts, commit_source, database, finalization, runs, worker
from test_commit_source import GitHubFixture
from test_finalization_input import logged_report, report
from test_preflight import issue_claim
from test_retry_postgres import parallel_calls
from test_runs_postgres import api, dedicated_database

pytestmark = pytest.mark.integration


def test_result_download_is_pinned_to_the_current_attempt(dedicated_database):
    run, first = issue_claim()
    target = (run.id, run.commit_sha)
    source = commit_source._verify(*target, GitHubFixture(run.commit_sha))
    failure = replace(logged_report(first), reason='check_failure', exit_code=1, verified_source=source)
    finalization.finalize_fixture(first, failure)
    assert finalization.finalize_fixture(first, failure).replayed
    with pytest.raises(finalization.FinalizationDenied, match='artifact does not match'):
        finalization.finalize_fixture(first, replace(failure, elapsed_seconds=2))
    url = f'/runs/{run.id}/artifacts/result'
    old = {'attempt_id': str(first.attempt_id)}
    with api() as (client, _):
        download = client.get(url, params=old)
        assert download.status_code == 200 and download.headers['cache-control'] == 'no-store'
        assert download.headers['content-disposition'] == 'attachment; filename="result.json"'
        assert download.headers['x-artifact-sha256'] == sha256(download.content).hexdigest()
        payload = download.json()
        assert (payload['run_id'], payload['attempt_id'], payload['reason'], payload['execution_ms']) == (
            str(run.id), str(first.attempt_id), 'commit_check_failure', 1000,
        )
        assert payload['source']['source_sha256'] == commit_source.DIGEST
        assert str(first.lease_token) not in download.text and failure.stdout not in download.content
        assert '結果JSONを保存' in client.get(f'/ui/runs/{run.id}').text
        assert client.get(url, params=old, headers={'Host': 'example.invalid'}).status_code == 403
        assert client.get(url, params={'attempt_id': '../result.json'}).status_code == 422
        assert client.get(f'/runs/{uuid4()}/artifacts/result', params=old).status_code == 404
        assert client.put(f'/runs/{run.id}/retry/{first.attempt_id}').status_code == 202
        assert client.get(url, params=old).status_code == 409
        retry = worker.claim_next('manual', target=target)
        finalization.finalize_fixture(retry, replace(report(retry), verified_source=source))
        assert client.get(url, params={'attempt_id': str(retry.attempt_id)}).json()['attempt_no'] == 2
        assert client.get(url, params=old).status_code == 409
    with database.connect() as conn:
        saved = conn.execute('SELECT payload FROM artifacts WHERE attempt_id=%s', (first.attempt_id,)).fetchone()
        assert saved['payload'] == download.content


def test_artifact_limits_and_atomic_budget_contention(dedicated_database):
    run, first = issue_claim()
    other, _ = runs.create_run(database.connect, 'b'*40)
    second = worker.claim_next('second', target=(other.id, other.commit_sha))
    payload = artifacts.result_document(first, run.commit_sha, 'succeeded', 'fixture_success', 0, 1000, None)
    with database.connect() as conn:
        conn.execute('UPDATE artifact_storage_budget SET capacity_bytes=%s', (len(payload),))
    for body, digest in [(b'x' * 4097, sha256(b'x' * 4097).hexdigest()), (b'x', 'f'*64)]:
        with pytest.raises(psycopg.errors.CheckViolation, match='artifacts_result_payload'):
            with database.connect() as conn:
                conn.execute(
                    'INSERT INTO artifacts(attempt_id,relative_name,storage_key,size_bytes,sha256,payload) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (first.attempt_id, artifacts.NAME, f'{first.attempt_id}/{artifacts.NAME}', len(body), digest, body),
                )

    def complete(i):
        claim = (first, second)[i]
        try:
            finalization.finalize_fixture(claim, logged_report(claim))
            return claim
        except psycopg.errors.CheckViolation as exc:
            assert 'result artifact budget exhausted' in str(exc)
            return None

    winner, = parallel_calls(complete)
    assert finalization.finalize_fixture(winner, logged_report(winner)).replayed
    with database.connect() as conn:
        stored = conn.execute('SELECT attempt_id, size_bytes FROM artifacts').fetchall()
        assert stored == [{'attempt_id': winner.attempt_id, 'size_bytes': len(payload)}]
        assert conn.execute('SELECT used_bytes FROM artifact_storage_budget').fetchone()['used_bytes'] == len(payload)
        assert conn.execute('SELECT used_bytes FROM log_storage_budget').fetchone()['used_bytes'] == 16
        assert conn.execute('SELECT attempt_id FROM attempt_logs').fetchall() == [{'attempt_id': winner.attempt_id}]
        assert conn.execute("SELECT count(*) AS n FROM attempts WHERE state='running' AND execution_ms IS NULL").fetchone()['n'] == 1
