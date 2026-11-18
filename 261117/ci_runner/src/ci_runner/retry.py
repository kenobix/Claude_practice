"""Schedule bounded retries after lease expiry and safe container recovery."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from . import database
from .settings import POLICY

BACKOFF_SECONDS = (5, 10)
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1
ATTEMPT_KIND_SQL = (
    "((a.attempt_no=1 AND a.retry_kind='initial') OR "
    f"(a.attempt_no BETWEEN 2 AND {MAX_ATTEMPTS} AND a.retry_kind='infrastructure'))"
)
LATEST_ATTEMPT_JOIN = (
    'LEFT JOIN LATERAL (SELECT * FROM attempts WHERE run_id=r.id '
    'ORDER BY attempt_no DESC LIMIT 1) AS a ON true '
)
RETRYABLE_ATTEMPT_SQL = (
    f'{ATTEMPT_KIND_SQL} AND a.attempt_no<{MAX_ATTEMPTS} '
    "AND a.state='failed' AND a.reason='infra_lease_expired' AND a.exit_code IS NULL "
    'AND a.finished_at >= a.lease_expires_at AND a.finished_at <= statement_timestamp() '
    'AND a.create_tracked AND (a.create_session IS NULL OR a.container_recovered_at IS NOT NULL)'
)
FAILED_RUN_FROM = (
    f'FROM runs r {LATEST_ATTEMPT_JOIN}'
    "WHERE r.state='failed' AND r.completed_attempt_id IS NULL AND r.cancel_requested_at IS NULL "
    'AND r.repository_id=%s AND r.ref=%s AND r.workflow_version=%s '
    f'AND {RETRYABLE_ATTEMPT_SQL} '
)


def attempt_kind(number: int) -> str | None:
    if type(number) is not int:
        return None
    if number == 1:
        return 'initial'
    if 2 <= number <= MAX_ATTEMPTS:
        return 'infrastructure'
    return None


@dataclass(frozen=True)
class ScheduledRetry:
    run_id: UUID
    attempt_id: UUID
    next_attempt_no: int
    available_at: datetime


def schedule_next(connection_factory=None) -> ScheduledRetry | None:
    """Queue one eligible run; only a later verified claim creates its next attempt."""
    params = (POLICY.repository_id, POLICY.ref, POLICY.workflow_version)
    with (connection_factory or database.connect)() as conn:
        candidate = conn.execute(
            f'SELECT r.id {FAILED_RUN_FROM}'
            'ORDER BY a.finished_at, r.id LIMIT 1 FOR UPDATE OF r SKIP LOCKED', params,
        ).fetchone()
        if candidate is None:
            return None
        # Read the latest attempt again under the run lock using a fresh snapshot.
        previous = conn.execute(
            f'SELECT a.id, a.attempt_no {FAILED_RUN_FROM}AND r.id=%s', (*params, candidate['id']),
        ).fetchone()
        if previous is None:
            return None
        number = previous['attempt_no']
        queued = conn.execute(
            "UPDATE runs SET state='queued', available_at=clock_timestamp() + %s * interval '1 second' "
            'WHERE id=%s RETURNING available_at', (BACKOFF_SECONDS[number - 1], candidate['id']),
        ).fetchone()
        if queued is None:
            raise database.DatabaseUnavailable()
        receipt = ScheduledRetry(candidate['id'], previous['id'], number + 1, queued['available_at'])
    return receipt
