"""Close expired attempts and schedule safe retries for the configured workflow."""
import argparse
from dataclasses import dataclass
import json
import sys
import time
from uuid import UUID

import psycopg

from . import database
from .retry import MAX_ATTEMPTS, schedule_next
from .settings import POLICY

POLL_SECONDS = 5
MAX_PER_POLL = 16


@dataclass(frozen=True)
class ExpiredAttempt:
    run_id: UUID
    attempt_id: UUID
    state: str
    reason: str


def expire_next(connection_factory=None) -> ExpiredAttempt | None:
    """Close a single expired attempt and its run in one transaction."""
    with (connection_factory or database.connect)() as conn:
        # Discover expiry without locking attempts before their parent runs.
        candidate = conn.execute(
            'SELECT r.id AS run_id, a.id AS attempt_id, r.cancel_requested_at '
            'FROM attempts a JOIN runs r ON r.id=a.run_id '
            "WHERE r.state='running' AND r.completed_attempt_id IS NULL AND a.state='running' "
            'AND a.lease_expires_at <= statement_timestamp() '
            'AND r.repository_id=%s AND r.ref=%s AND r.workflow_version=%s '
            'ORDER BY a.lease_expires_at, a.id LIMIT 1 FOR UPDATE OF r SKIP LOCKED',
            (POLICY.repository_id, POLICY.ref, POLICY.workflow_version),
        ).fetchone()
        if candidate is None:
            return None
        run_id, attempt_id = candidate['run_id'], candidate['attempt_id']
        locked = conn.execute(
            "SELECT attempt_no, retry_kind FROM attempts WHERE run_id=%s AND id=%s AND state='running' FOR UPDATE",
            (run_id, attempt_id),
        ).fetchone()
        if locked is None:
            return None
        if candidate['cancel_requested_at'] is not None:
            state, reason = 'canceled', 'canceled_lease_expired'
        elif (locked['attempt_no'], locked['retry_kind']) == (MAX_ATTEMPTS, 'infrastructure'):
            state, reason = 'failed', 'infra_exhausted'
        else:
            state, reason = 'failed', 'infra_lease_expired'
        expired = conn.execute(
            'WITH expiry_time AS MATERIALIZED (SELECT clock_timestamp() AS measured_at) '
            'UPDATE attempts AS a SET state=%s, reason=%s, exit_code=NULL, finished_at=t.measured_at '
            'FROM expiry_time AS t WHERE a.id=%s AND a.lease_expires_at <= t.measured_at RETURNING a.id',
            (state, reason, attempt_id),
        ).fetchone()
        if expired is None:
            return None
        # Expiry records an infrastructure failure or cancellation, without promoting logs.
        changed = conn.execute('UPDATE runs SET state=%s WHERE id=%s RETURNING id', (state, run_id)).fetchone()
        if changed is None:
            raise database.DatabaseUnavailable()
        result = ExpiredAttempt(run_id, attempt_id, state, reason)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--once', action='store_true', help='process bounded expiry and retry batches and exit')
    mode.add_argument('--watch', action='store_true', help='repeat the scan after a five-second delay')
    args = parser.parse_args(argv)
    try:
        while True:
            for _ in range(MAX_PER_POLL):
                result = expire_next()
                if result is None:
                    break
                print(json.dumps({'run_id': str(result.run_id), 'attempt_id': str(result.attempt_id),
                                  'state': result.state, 'reason': result.reason}), flush=True)
            for _ in range(MAX_PER_POLL):
                retry = schedule_next()
                if retry is None:
                    break
                print(json.dumps({'run_id': str(retry.run_id), 'attempt_id': str(retry.attempt_id),
                                  'state': 'queued', 'reason': 'infra_retry_scheduled',
                                  'next_attempt_no': retry.next_attempt_no,
                                  'available_at': retry.available_at.isoformat()}), flush=True)
            if args.once:
                return 0
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        return 0
    except (psycopg.Error, database.DatabaseUnavailable):
        print('database unavailable; expiry outcome may be unknown', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
