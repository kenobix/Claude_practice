"""Initial-attempt claiming only. Does not execute jobs or recover expired leases."""
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
CANDIDATE_SQL = ELIGIBLE_SQL + (
    'ORDER BY r.available_at, r.created_at, r.id '
    'LIMIT 1'
)


@dataclass(frozen=True)
class Claim:
    attempt_id: UUID
    run_id: UUID
    attempt_no: int
    worker_id: str
    lease_token: UUID = field(repr=False)
    started_at: datetime
    lease_expires_at: datetime


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
    sql = CANDIDATE_SQL
    if target is not None:
        # This path must be able to persist the provenance before we grant a lease.
        sql = (ELIGIBLE_SQL + 'AND r.id=%s AND r.commit_sha=%s '
               'AND EXISTS (SELECT 1 FROM schema_migrations WHERE version=%s)')
        params += (*target, SCHEMA_VERSION)
    with factory() as conn:
        run = conn.execute(
            sql + ' FOR UPDATE OF r SKIP LOCKED', params).fetchone()
        if run is None:
            return None
        row = conn.execute(
            'INSERT INTO attempts(run_id, attempt_no, worker_id, lease_token, '
            'started_at, lease_expires_at, retry_kind) '
            "VALUES (%s, 1, %s, gen_random_uuid(), statement_timestamp(), "
            "statement_timestamp() + %s * interval '1 second', 'initial') "
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
