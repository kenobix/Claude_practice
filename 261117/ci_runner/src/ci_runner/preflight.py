"""DB authorization snapshot before execution preparation; does not start a job."""
from dataclasses import dataclass, field
from datetime import datetime
import re
from uuid import UUID

from . import database
from .settings import POLICY
from .worker import Claim, validate_worker_id


class ExecutionDenied(Exception):
    """Claim is stale, canceled, expired, unsupported, or no longer current."""


@dataclass(frozen=True)
class ExecutionPermit:
    run_id: UUID
    attempt_id: UUID
    attempt_no: int
    worker_id: str
    lease_token: UUID = field(repr=False)
    lease_expires_at: datetime
    repository_id: str
    ref: str
    commit_sha: str
    workflow_version: str
    remaining_lease_seconds: float = 0.0


def authorize_execution(claim: Claim, connection_factory=None) -> ExecutionPermit:
    """Re-read current authority from PostgreSQL, never trust a cached lease time.

    Raises ExecutionDenied or a DB error on failure. A returned permit is a
    snapshot, not a lease extension or a reservation across an external start.
    """
    validate_worker_id(claim.worker_id)
    factory = connection_factory or database.connect
    with factory() as conn:
        row = conn.execute(
            'SELECT r.id AS run_id, a.id AS attempt_id, a.attempt_no, a.worker_id, '
            'a.lease_token, a.lease_expires_at, r.repository_id, r.ref, '
            'r.commit_sha, r.workflow_version, '
            'EXTRACT(EPOCH FROM (a.lease_expires_at-clock_timestamp())) AS remaining_lease_seconds '
            'FROM runs r JOIN attempts a ON a.run_id=r.id '
            "WHERE r.id=%s AND a.id=%s AND a.lease_token=%s AND a.worker_id=%s "
            'AND a.attempt_no=%s AND a.attempt_no=1 '
            "AND a.retry_kind='initial' AND r.state='running' AND a.state='running' "
            'AND a.finished_at IS NULL AND r.cancel_requested_at IS NULL '
            'AND r.completed_attempt_id IS NULL '
            'AND a.lease_expires_at > clock_timestamp() '
            'AND r.repository_id=%s AND r.ref=%s AND r.workflow_version=%s',
            (claim.run_id, claim.attempt_id, claim.lease_token, claim.worker_id,
             claim.attempt_no, POLICY.repository_id, POLICY.ref, POLICY.workflow_version)
        ).fetchone()
        if row is None or not re.fullmatch(r'[0-9a-f]{40}', row['commit_sha']):
            raise ExecutionDenied('execution claim is not currently valid')
        row['remaining_lease_seconds'] = float(row['remaining_lease_seconds'])
        permit = ExecutionPermit(**row)
    # DB errors during context exit must also prevent a successful return.
    return permit


def reserve_container_create(permit: ExecutionPermit, daemon_id: UUID, session: UUID, connection_factory):
    """Record exactly one create request before any request goes to Docker."""
    denied = 'container create cannot be reserved for this attempt'
    with connection_factory() as conn:
        # Expiry, cancellation and completion take the parent lock first.
        run = conn.execute(
            "SELECT id FROM runs WHERE id=%s AND state='running' AND cancel_requested_at IS NULL "
            'AND completed_attempt_id IS NULL AND repository_id=%s AND ref=%s AND workflow_version=%s '
            'AND commit_sha=%s FOR UPDATE',
            (permit.run_id, POLICY.repository_id, POLICY.ref, POLICY.workflow_version, permit.commit_sha),
        ).fetchone()
        if run is None:
            raise ExecutionDenied(denied)
        attempt = conn.execute(
            "SELECT id FROM attempts WHERE run_id=%s AND id=%s AND state='running' "
            "AND attempt_no=1 AND attempt_no=%s AND retry_kind='initial' "
            'AND worker_id=%s AND lease_token=%s FOR UPDATE',
            (permit.run_id, permit.attempt_id, permit.attempt_no, permit.worker_id, permit.lease_token),
        ).fetchone()
        if attempt is None:
            raise ExecutionDenied(denied)
        changed = conn.execute(
            'UPDATE attempts SET create_daemon_id=%s, create_session=%s '
            'WHERE id=%s AND create_tracked AND create_session IS NULL '
            'AND lease_expires_at > clock_timestamp() RETURNING id',
            (daemon_id, session, permit.attempt_id),
        ).fetchone()
        if changed is None:
            raise ExecutionDenied(denied)


def record_created_container(permit: ExecutionPermit, daemon_id: UUID, session: UUID,
                             container_id: str, connection_factory):
    """Record observation of the verified Docker ID, including after lease loss."""
    with connection_factory() as conn:
        changed = conn.execute(
            'UPDATE attempts AS a SET created_container_id=%s FROM runs AS r '
            'WHERE r.id=a.run_id AND r.id=%s AND r.commit_sha=%s '
            'AND a.id=%s AND a.worker_id=%s AND a.lease_token=%s '
            'AND a.create_daemon_id=%s AND a.create_session=%s '
            'AND a.created_container_id IS NULL RETURNING a.id',
            (container_id, permit.run_id, permit.commit_sha, permit.attempt_id, permit.worker_id,
             permit.lease_token, daemon_id, session),
        ).fetchone()
        if changed is None:
            raise ExecutionDenied('created container does not match the reserved attempt')
