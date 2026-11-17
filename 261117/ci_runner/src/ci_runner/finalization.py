"""Atomic, fenced outcome persistence for trusted local fixture reports only."""
from dataclasses import dataclass
from datetime import datetime
import re
from uuid import UUID

from . import database
from .execution import ExecutionResult, EXPECTED_LIMITS
from .settings import POLICY
from .worker import Claim, validate_worker_id


class FinalizationDenied(Exception):
    """Report is invalid, stale, canceled, conflicting, or no longer authorized."""


@dataclass(frozen=True)
class FinalizationReceipt:
    run_id: UUID
    attempt_id: UUID
    state: str
    exit_code: int
    reason: str
    finished_at: datetime
    replayed: bool


def outcome(claim: Claim, result: ExecutionResult):
    validate_worker_id(claim.worker_id)
    if (result.run_id != claim.run_id or result.attempt_id != claim.attempt_id
            or result.released is not True or result.verified_limits != EXPECTED_LIMITS
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', result.image_id)
            or type(result.exit_code) is not int):
        raise FinalizationDenied('invalid fixture result')
    if result.reason == 'completed' and result.exit_code == 0:
        return 'succeeded', 'fixture_success'
    if result.reason == 'check_failure' and 1 <= result.exit_code <= 255:
        return 'failed', 'fixture_check_failure'
    if result.reason == 'timeout' and result.exit_code == 137:
        return 'timed_out', 'fixture_timeout'
    if result.reason == 'log_limit' and result.exit_code == 137:
        return 'failed', 'fixture_log_limit'
    # Expiry/bootstrap/infra failures belong to recovery, not a successful finish.
    raise FinalizationDenied('fixture result is not finalizable')


def finalize_fixture(claim: Claim, result: ExecutionResult, connection_factory=None):
    """Persist a report after execution/cleanup; no Docker calls or log storage.

    Lock run then attempt. Fresh DB-clock guards fence both updates. Identical
    committed reports can be acknowledged after expiry without writing again.
    Exceptions during commit leave the outcome unknown; resend the same report.
    This is a trusted internal API, not a cryptographic proof of execution.
    """
    state, reason = outcome(claim, result)
    factory = connection_factory or database.connect
    with factory() as conn:
        run = conn.execute('SELECT * FROM runs WHERE id=%s FOR UPDATE', (claim.run_id,)).fetchone()
        attempt = conn.execute('SELECT * FROM attempts WHERE id=%s AND run_id=%s FOR UPDATE',
                               (claim.attempt_id, claim.run_id)).fetchone()
        if (run is None or attempt is None
                or (run['repository_id'], run['ref'], run['workflow_version']) !=
                (POLICY.repository_id, POLICY.ref, POLICY.workflow_version)
                or attempt['lease_token'] != claim.lease_token
                or attempt['worker_id'] != claim.worker_id
                or attempt['attempt_no'] != claim.attempt_no or claim.attempt_no != 1
                or attempt['retry_kind'] != 'initial'):
            raise FinalizationDenied('fixture report is not current')
        replayed = (run['completed_attempt_id'] == claim.attempt_id
                    and run['state'] == attempt['state'] == state
                    and attempt['exit_code'] == result.exit_code and attempt['reason'] == reason
                    and attempt['finished_at'] is not None)
        if replayed:
            finished_at = attempt['finished_at']
        else:
            if (run['state'] != 'running' or run['cancel_requested_at'] is not None
                    or run['completed_attempt_id'] is not None or attempt['state'] != 'running'
                    or attempt['finished_at'] is not None):
                raise FinalizationDenied('fixture report is not current')
            saved = conn.execute(
                'UPDATE attempts SET state=%s,exit_code=%s,reason=%s,finished_at=clock_timestamp() '
                "WHERE id=%s AND lease_token=%s AND state='running' AND finished_at IS NULL "
                'AND lease_expires_at>clock_timestamp() RETURNING finished_at',
                (state, result.exit_code, reason, claim.attempt_id, claim.lease_token)).fetchone()
            if saved is None:
                raise FinalizationDenied('execution lease is expired')
            changed = conn.execute(
                'UPDATE runs SET state=%s,completed_attempt_id=%s '
                "WHERE id=%s AND state='running' AND cancel_requested_at IS NULL "
                'AND completed_attempt_id IS NULL AND EXISTS '
                '(SELECT 1 FROM attempts WHERE id=%s AND run_id=runs.id '
                'AND lease_token=%s AND lease_expires_at>clock_timestamp()) RETURNING id',
                (state, claim.attempt_id, claim.run_id, claim.attempt_id, claim.lease_token)).fetchone()
            if changed is None:
                # Includes expiry between UPDATEs: roll the attempt change back.
                raise FinalizationDenied('execution authority changed before finalization')
            finished_at = saved['finished_at']
        receipt = FinalizationReceipt(claim.run_id, claim.attempt_id, state,
                                      result.exit_code, reason, finished_at, replayed)
    return receipt  # The connection context must finish COMMIT first.
