"""Claim and renewal operations; the CLI only claims initial attempts."""
import argparse
from dataclasses import dataclass, field
from datetime import datetime
import json
import re
import sys
from uuid import UUID

import psycopg

from . import database
from .settings import POLICY, SCHEMA_VERSION

LEASE_SECONDS = 30

ELIGIBLE_SQL = (
    "SELECT r.id, r.commit_sha FROM runs r WHERE r.state='queued' "
    'AND r.cancel_requested_at IS NULL AND r.available_at <= clock_timestamp() '
    'AND r.repository_id=%s AND r.ref=%s AND r.workflow_version=%s '
    # Retry cause/budget is not implemented: never mislabel a retry as initial.
    'AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.run_id=r.id) '
)
QUEUE_ORDER = (
    'ORDER BY r.available_at, r.created_at, r.id '
    'LIMIT 1'
)
CANDIDATE_SQL = ELIGIBLE_SQL + QUEUE_ORDER


@dataclass(frozen=True)
class Claim:
    attempt_id: UUID
    run_id: UUID
    attempt_no: int
    worker_id: str
    lease_token: UUID = field(repr=False)
    started_at: datetime
    lease_expires_at: datetime


class LeaseRenewalDenied(Exception):
    """The claim is no longer eligible for renewal."""


def validate_worker_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}', value):
        raise ValueError('worker ID must be 1-64 ASCII identifier characters')
    return value


def has_queued_candidate(connection_factory=None):
    """Read-only hint, not a reservation or authorization; closes before build."""
    with (connection_factory or database.connect)() as conn:
        present = conn.execute(CANDIDATE_SQL, (POLICY.repository_id, POLICY.ref,
                                              POLICY.workflow_version)).fetchone() is not None
    return present


def claim_next(worker_id: str, connection_factory=None, *, target: tuple[UUID, str] | None = None) -> Claim | None:
    """Claim the next candidate, or only the exact verified run and SHA."""
    validate_worker_id(worker_id)
    factory = connection_factory or database.connect
    params = (POLICY.repository_id, POLICY.ref, POLICY.workflow_version)
    eligible = ELIGIBLE_SQL + 'AND EXISTS (SELECT 1 FROM schema_migrations WHERE version=%s) '
    params += (SCHEMA_VERSION,)
    sql = eligible + QUEUE_ORDER
    if target is not None:
        # This path must be able to persist the provenance before we grant a lease.
        sql = (eligible + 'AND r.id=%s AND r.commit_sha=%s '
               'AND EXISTS (SELECT 1 FROM schema_migrations WHERE version=4)')
        params += target
    with factory() as conn:
        run = conn.execute(
            sql + ' FOR UPDATE OF r SKIP LOCKED', params).fetchone()
        if run is None:
            return None
        row = conn.execute(
            'INSERT INTO attempts(run_id, attempt_no, worker_id, lease_token, '
            'started_at, lease_expires_at, retry_kind, create_tracked) '
            "VALUES (%s, 1, %s, gen_random_uuid(), statement_timestamp(), "
            "statement_timestamp() + %s * interval '1 second', 'initial', true) "
            'RETURNING id AS attempt_id, run_id, attempt_no, worker_id, '
            'lease_token, started_at, lease_expires_at',
            (run['id'], worker_id, LEASE_SECONDS)).fetchone()
        changed = conn.execute(
            "UPDATE runs SET state='running' WHERE id=%s AND state='queued' "
            'AND cancel_requested_at IS NULL RETURNING id', (run['id'],)).fetchone()
        if changed is None:
            raise database.DatabaseUnavailable()
        result = Claim(**row)
    # Success means both changes committed; no execution-time lock is retained.
    return result


def renew_lease(claim: Claim, connection_factory=None) -> datetime:
    """Return the new DB deadline after commit, or raise on rejection or DB failure."""
    validate_worker_id(claim.worker_id)
    factory = connection_factory or database.connect
    denied = 'lease is not currently renewable'
    with factory() as conn:
        # Use the same lock order as finalization and cancellation.
        run = conn.execute(
            "SELECT id FROM runs WHERE id=%s AND state='running' "
            'AND cancel_requested_at IS NULL AND completed_attempt_id IS NULL '
            'AND repository_id=%s AND ref=%s AND workflow_version=%s FOR UPDATE',
            (claim.run_id, POLICY.repository_id, POLICY.ref, POLICY.workflow_version),
        ).fetchone()
        if run is None:
            raise LeaseRenewalDenied(denied)
        attempt = conn.execute(
            'SELECT id FROM attempts WHERE run_id=%s AND id=%s AND worker_id=%s '
            'AND lease_token=%s AND attempt_no=%s AND attempt_no=1 '
            "AND retry_kind='initial' AND state='running' AND finished_at IS NULL FOR UPDATE",
            (claim.run_id, claim.attempt_id, claim.worker_id, claim.lease_token, claim.attempt_no),
        ).fetchone()
        if attempt is None:
            raise LeaseRenewalDenied(denied)
        # Sample once after both locks. A snapshot taken before waiting can revive an expired lease.
        row = conn.execute(
            'WITH renewal_time AS MATERIALIZED (SELECT clock_timestamp() AS measured_at) '
            "UPDATE attempts AS a SET lease_expires_at=t.measured_at + %s * interval '1 second' "
            'FROM renewal_time AS t WHERE a.id=%s AND a.lease_expires_at > t.measured_at '
            'RETURNING a.lease_expires_at',
            (LEASE_SECONDS, attempt['id']),
        ).fetchone()
        if row is None:
            raise LeaseRenewalDenied(denied)
    # A failed or unknown commit must never publish a new deadline to the caller.
    return row['lease_expires_at']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-id', required=True, type=validate_worker_id)
    parser.add_argument('--claim-once', required=True, action='store_true',
                        help='claim one initial attempt and exit; it stays running without recovery')
    args = parser.parse_args()
    try:
        claim = claim_next(args.worker_id)
    except (psycopg.Error, database.DatabaseUnavailable):
        print('database unavailable; claim outcome may be unknown', file=sys.stderr)
        return 1
    output = {'claimed': claim is not None}
    if claim:
        # The execution-right token must not be emitted into routine logs.
        output.update(run_id=str(claim.run_id), attempt_id=str(claim.attempt_id),
                      attempt_no=claim.attempt_no, lease_expires_at=claim.lease_expires_at.isoformat())
    print(json.dumps(output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
