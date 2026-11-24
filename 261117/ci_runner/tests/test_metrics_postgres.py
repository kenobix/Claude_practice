import pytest

from ci_runner import database, finalization, runs, worker
from ci_runner.expiry_monitor import expire_next
from test_expiry_monitor import set_expiry
from test_finalization_input import report
from test_runs_postgres import api, dedicated_database

pytestmark = pytest.mark.integration


def test_metric_counts_and_missing_durations(dedicated_database):
    with api() as (client, _):
        empty = client.get('/metrics/operations').json()
        assert empty['finished_attempts'] == 0
        assert empty['queue_mean_ms'] is empty['execution_p95_ms'] is empty['infrastructure_error_rate'] is None
        assert '計測値なし' in client.get('/ui/metrics').text
        assert client.get('/metrics/operations', headers={'Host': 'remote.invalid'}).status_code == 403

        claims = []
        for key in 'abcdef':
            run, _ = runs.create_run(database.connect, key * 40)
            claim = worker.claim_next('observer')
            claims.append(claim)
            with database.connect() as conn:
                row = conn.execute(
                    'SELECT a.claimable_at=r.available_at AS recorded FROM attempts a JOIN runs r ON r.id=a.run_id '
                    'WHERE a.id=%s', (claim.attempt_id,),
                ).fetchone()
                assert row['recorded'] and claim.run_id == run.id
            if key in 'ab':
                if key == 'b':
                    runs.cancel_run(database.connect, run.id)
                set_expiry(claim, -1)
                assert expire_next().reason == ('infra_lease_expired' if key == 'a' else 'canceled_lease_expired')
            elif key == 'c':
                finalization.finalize_fixture(claim, report(claim, 'check_failure', 1))
            else:
                finalization.finalize_fixture(claim, report(claim))

        expired, canceled, failed, legacy, aged, foreign = claims
        # Fix sample timestamps to independently verify the aggregates.
        with database.connect() as conn:
            for claim, seconds in [(expired, 1), (canceled, 2), (failed, 3)]:
                conn.execute("UPDATE attempts SET claimable_at=started_at-%s * interval '1 second' WHERE id=%s",
                             (seconds, claim.attempt_id))
            conn.execute('UPDATE attempts SET claimable_at=NULL, execution_ms=NULL WHERE id=%s', (legacy.attempt_id,))
            conn.execute("UPDATE attempts SET finished_at=clock_timestamp()-interval '25 hours' WHERE id=%s",
                         (aged.attempt_id,))
            conn.execute("UPDATE runs SET workflow_version='separate-workflow' WHERE id=%s", (foreign.run_id,))
        response = client.get('/metrics/operations')
        summary = response.json()
        assert response.headers['cache-control'] == 'no-store'
        assert (summary['finished_attempts'], summary['infrastructure_failures'], summary['lease_expirations']) == (4, 1, 2)
        assert summary['infrastructure_error_rate'] == 0.25
        assert (summary['queue_samples'], summary['queue_mean_ms'], summary['queue_p95_ms']) == (3, 2000, 2900)
        assert (summary['execution_samples'], summary['execution_mean_ms'], summary['execution_p95_ms']) == (1, 1000, 1000)
        page = client.get('/ui/metrics')
        assert page.status_code == 200 and all(text in page.text for text in ('直近24時間', '25.0%', '2,900.0 ms'))
        assert '運用指標' in client.get('/ui').text


def test_metrics_sample_is_bounded_and_uses_latest_finishes(dedicated_database):
    run, _ = runs.create_run(database.connect, 'a'*40)
    with database.connect() as conn:
        conn.execute(
            'INSERT INTO attempts(run_id,attempt_no,worker_id,lease_token,started_at,lease_expires_at,'
            'finished_at,state,retry_kind,reason) '
            "SELECT %s, n, 'sample', gen_random_uuid(), now()-interval '1 hour', now()-interval '59 minutes', "
            "now()-n * interval '1 second', 'failed', 'initial', "
            "CASE WHEN n=1001 THEN 'infra_lease_expired' ELSE 'fixture_check_failure' END "
            'FROM generate_series(1,1001) AS n', (run.id,),
        )
    with api() as (client, _):
        summary = client.get('/metrics/operations').json()
        assert summary['finished_attempts'] == summary['sample_limit'] == 1000
        assert summary['infrastructure_failures'] == summary['lease_expirations'] == 0
