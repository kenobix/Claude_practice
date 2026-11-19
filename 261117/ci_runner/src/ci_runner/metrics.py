"""Recent operational metrics for the configured CI workflow."""
from .settings import POLICY

WINDOW_HOURS = 24
SAMPLE_LIMIT = 1000


def recent(factory):
    with factory() as conn:
        row = conn.execute(
            'WITH sample AS ('
            ' SELECT a.reason, a.execution_ms, CASE WHEN a.started_at >= a.claimable_at THEN '
            ' (1000 * EXTRACT(EPOCH FROM (a.started_at-a.claimable_at)))::double precision END AS queue_ms '
            ' FROM attempts a JOIN runs r ON r.id=a.run_id '
            ' WHERE a.finished_at BETWEEN statement_timestamp() - %s * interval \'1 hour\' AND statement_timestamp() '
            ' AND r.repository_id=%s AND r.ref=%s AND r.workflow_version=%s '
            ' ORDER BY a.finished_at DESC, a.id DESC LIMIT %s'
            ') '
            'SELECT statement_timestamp() AS measured_at, count(*) AS finished_attempts, '
            "count(*) FILTER (WHERE reason IN ('infra_lease_expired','infra_exhausted')) AS infrastructure_failures, "
            "count(*) FILTER (WHERE reason IN ('infra_lease_expired','infra_exhausted','canceled_lease_expired')) "
            ' AS lease_expirations, count(queue_ms) AS queue_samples, count(execution_ms) AS execution_samples, '
            'avg(queue_ms) AS queue_mean_ms, percentile_cont(0.95) WITHIN GROUP (ORDER BY queue_ms) AS queue_p95_ms, '
            'avg(execution_ms)::double precision AS execution_mean_ms, '
            'percentile_cont(0.95) WITHIN GROUP (ORDER BY execution_ms) AS execution_p95_ms '
            'FROM sample',
            (WINDOW_HOURS, POLICY.repository_id, POLICY.ref, POLICY.workflow_version, SAMPLE_LIMIT),
        ).fetchone()
    count = row['finished_attempts']
    return {
        **row, 'window_hours': WINDOW_HOURS, 'sample_limit': SAMPLE_LIMIT,
        'infrastructure_error_rate': row['infrastructure_failures'] / count if count else None,
    }
