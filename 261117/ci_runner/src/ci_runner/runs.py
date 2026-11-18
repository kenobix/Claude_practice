"""Persistence only: no clone, shell, Docker, Worker or webhook execution."""
import base64
import binascii
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from .database import DatabaseUnavailable
from .models import Attempt, AttemptPage, Cursor, Run, RunPage
from .settings import POLICY

COLUMNS = ('id, repository_id, ref, commit_sha, workflow_version, state, created_at, '
           'available_at, cancel_requested_at, completed_attempt_id')


def decode_cursor(value: str | None) -> Cursor | None:
    if value is None:
        return None
    try:
        raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        return Cursor.model_validate_json(raw)
    except (ValueError, binascii.Error, ValidationError):
        raise HTTPException(422, 'invalid cursor') from None


def create_run(connection_factory, commit_sha: str) -> tuple[Run, bool]:
    with connection_factory() as conn:
        result = upsert_run(conn, commit_sha)
    # Context manager must finish COMMIT before the HTTP handler can return success.
    return result


def upsert_run(conn, commit_sha: str) -> tuple[Run, bool]:
    """Use the caller's transaction for the same business key as manual intake."""
    key = (POLICY.repository_id, POLICY.ref, commit_sha, POLICY.workflow_version)
    row = conn.execute(
        f'INSERT INTO runs(repository_id, ref, commit_sha, workflow_version) '
        f'VALUES (%s, %s, %s, %s) ON CONFLICT '
        f'(repository_id, ref, commit_sha, workflow_version) DO NOTHING RETURNING {COLUMNS}',
        key).fetchone()
    if row is not None:
        return Run.model_validate(row), True
    # Separate READ COMMITTED statement sees the winner after INSERT's wait.
    row = conn.execute(
        f'SELECT {COLUMNS} FROM runs WHERE repository_id=%s AND ref=%s '
        'AND commit_sha=%s AND workflow_version=%s', key).fetchone()
    if row is None:
        raise DatabaseUnavailable()
    return Run.model_validate(row), False


def get_run(connection_factory, run_id: UUID) -> Run:
    with connection_factory() as conn:
        row = conn.execute(f'SELECT {COLUMNS} FROM runs WHERE id=%s', (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'run not found')
    return Run.model_validate(row)


def cancel_run(connection_factory, run_id: UUID) -> Run:
    """Serialize the cancellation with claim, renewal, expiry and completion."""
    with connection_factory() as conn:
        row = conn.execute(
            f'SELECT {COLUMNS} FROM runs WHERE id=%s AND repository_id=%s AND ref=%s '
            'AND workflow_version=%s FOR UPDATE', (run_id, POLICY.repository_id, POLICY.ref, POLICY.workflow_version),
        ).fetchone()
        if row is None:
            raise HTTPException(404, 'run not found')
        if row['state'] not in ('queued', 'running', 'canceled'):
            raise HTTPException(409, 'run already finished')
        if row['state'] == 'queued' or (row['state'] == 'running' and row['cancel_requested_at'] is None):
            row = conn.execute(
                "UPDATE runs SET state=CASE WHEN state='queued' THEN 'canceled' ELSE state END, "
                'cancel_requested_at=COALESCE(cancel_requested_at, clock_timestamp()) '
                f'WHERE id=%s RETURNING {COLUMNS}', (run_id,),
            ).fetchone()
        result = Run.model_validate(row)
    return result


def list_attempts(connection_factory, run_id: UUID, limit: int, before: int | None) -> AttemptPage:
    with connection_factory() as conn:
        # One snapshot distinguishes a missing run from an empty history and pins official status.
        rows = conn.execute(
            'SELECT page.*, COALESCE(page.id=r.completed_attempt_id, false) AS official '
            'FROM runs r LEFT JOIN LATERAL ( '
            '  SELECT a.id, a.attempt_no, a.retry_kind, a.state, a.started_at, a.finished_at, a.exit_code, a.reason '
            '  FROM attempts a WHERE a.run_id=r.id AND (%s::integer IS NULL OR a.attempt_no < %s) '
            '  ORDER BY a.attempt_no DESC LIMIT %s '
            ') AS page ON true WHERE r.id=%s ORDER BY page.attempt_no DESC',
            (before, before, limit + 1, run_id),
        ).fetchall()
    if not rows:
        raise HTTPException(404, 'run not found')
    items = [Attempt.model_validate(row) for row in rows[:limit] if row['id'] is not None]
    return AttemptPage(items=items, next_before=items[-1].attempt_no if len(rows) > limit else None)


def list_runs(connection_factory, limit: int, cursor: Cursor | None) -> RunPage:
    with connection_factory() as conn:
        if cursor:
            rows = conn.execute(
                f'SELECT {COLUMNS} FROM runs WHERE (created_at, id) < (%s, %s) '
                'ORDER BY created_at DESC, id DESC LIMIT %s',
                (cursor.created_at, cursor.id, limit + 1)).fetchall()
        else:
            rows = conn.execute(f'SELECT {COLUMNS} FROM runs '
                                'ORDER BY created_at DESC, id DESC LIMIT %s', (limit + 1,)).fetchall()
    items = [Run.model_validate(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        raw = Cursor(created_at=last.created_at, id=last.id).model_dump_json().encode()
        next_cursor = base64.urlsafe_b64encode(raw).decode().rstrip('=')
    return RunPage(items=items, next_cursor=next_cursor)
