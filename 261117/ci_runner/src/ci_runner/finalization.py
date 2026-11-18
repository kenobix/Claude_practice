"""Atomic, fenced outcome persistence for trusted fixed fixture reports."""
from dataclasses import dataclass
from datetime import datetime
import re
from uuid import UUID

from . import commit_source, database
from .execution import ExecutionResult, EXPECTED_LIMITS, LOG_LIMIT
from .retry import attempt_kind
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
    if (type(result.stdout) is not bytes or type(result.stderr) is not bytes
            or type(result.observed_bytes) is not int
            or not 0 <= result.observed_bytes <= 9223372036854775807
            or len(result.stdout) + len(result.stderr) > LOG_LIMIT
            or len(result.stdout) + len(result.stderr) > result.observed_bytes):
        raise FinalizationDenied('invalid retained logs')
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
    """Persist a report and bounded opaque logs after execution/cleanup.

    Lock run then attempt. Fresh DB-clock guards fence both updates. Identical
    committed reports can be acknowledged after expiry without writing again.
    Exceptions during commit leave the outcome unknown; resend the same report.
    This is a trusted internal API, not a cryptographic proof of execution.
    """
    state, reason = outcome(claim, result)
    source = result.verified_source
    kind = attempt_kind(claim.attempt_no)
    if kind is None or (kind == 'infrastructure' and source is None):
        raise FinalizationDenied('fixture report is not current')
    provenance = None
    if source is not None:
        try:
            commit_source.validate_verified_source(source)
        except commit_source.SourceRejected:
            raise FinalizationDenied('invalid verified source in result') from None
        if source.run_id != claim.run_id:
            raise FinalizationDenied('source does not match the claimed run')
        reason = 'commit_' + reason.removeprefix('fixture_')
        provenance = {'commit_sha': source.commit_sha, 'main_sha': source.main_sha,
                      'blob_sha': source.blob_sha, 'source_sha256': commit_source.DIGEST,
                      'image_id': result.image_id}
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
                or attempt['attempt_no'] != claim.attempt_no or attempt['retry_kind'] != kind):
            raise FinalizationDenied('fixture report is not current')
        if source is not None and run['commit_sha'] != source.commit_sha:
            raise FinalizationDenied('source SHA does not match the claimed run')
        replayed = (run['completed_attempt_id'] == claim.attempt_id
                    and run['state'] == attempt['state'] == state
                    and attempt['exit_code'] == result.exit_code and attempt['reason'] == reason
                    and attempt['finished_at'] is not None)
        if replayed:
            logs = conn.execute('SELECT stdout,stderr,observed_bytes FROM attempt_logs WHERE attempt_id=%s',
                                (claim.attempt_id,)).fetchone()
            if logs is None or (logs['stdout'], logs['stderr'], logs['observed_bytes']) != (
                    result.stdout, result.stderr, result.observed_bytes):
                raise FinalizationDenied('persisted logs do not match the report')
            if provenance is not None:
                saved_source = conn.execute(
                    'SELECT commit_sha,main_sha,blob_sha,source_sha256,image_id '
                    'FROM attempt_sources WHERE attempt_id=%s AND run_id=%s',
                    (claim.attempt_id, claim.run_id)).fetchone()
                if saved_source != provenance:
                    raise FinalizationDenied('persisted source does not match the report')
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
            conn.execute('INSERT INTO attempt_logs(attempt_id,stdout,stderr,observed_bytes) VALUES (%s,%s,%s,%s)',
                         (claim.attempt_id, result.stdout, result.stderr, result.observed_bytes))
            if provenance is not None:
                conn.execute(
                    'INSERT INTO attempt_sources(attempt_id,run_id,commit_sha,main_sha,blob_sha,source_sha256,image_id) '
                    'VALUES (%(attempt_id)s,%(run_id)s,%(commit_sha)s,%(main_sha)s,%(blob_sha)s,%(source_sha256)s,%(image_id)s)',
                    {**provenance, 'attempt_id': claim.attempt_id, 'run_id': claim.run_id})
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
