"""Run one queued job with the fixed fixture, then exit."""
import argparse
import json
import subprocess
import sys

import psycopg

from . import commit_source, container, database, execution, finalization, worker
from .preflight import ExecutionDenied


class OneShotFailed(Exception):
    """Expose sanitized phase/IDs only, without raw diagnostics or logs."""

    def __init__(self, stage, claim=None, *, source_error=None):
        super().__init__(f'fixture worker stopped during {stage}; inspect DB before retry')
        self.stage = stage
        self.run_id = str(claim.run_id) if claim else None
        self.attempt_id = str(claim.attempt_id) if claim else None
        self.source_error = source_error


EXPECTED_FAILURES = (psycopg.Error, database.DatabaseUnavailable, ExecutionDenied,
                     container.ContainerRejected, finalization.FinalizationDenied,
                     OSError, subprocess.SubprocessError, json.JSONDecodeError)


def run_once(worker_id, mode='success', connection_factory=None, *, verified_commit=False):
    """Claim once, execute/clean up, then finalize. Never retry any phase.

    A raised error can include an unknown COMMIT outcome. After acquisition,
    failure leaves reconciliation to the operator/future recovery process.
    """
    worker.validate_worker_id(worker_id)
    if mode not in container.MODES:
        raise ValueError('unknown fixed fixture mode')
    claim = None
    stage = 'queue'
    try:
        source = None
        if verified_commit:
            stage = 'source'
            source = commit_source.verify_queued_source(connection_factory)
            if source is None:
                return None
        elif not worker.has_queued_candidate(connection_factory):
            return None
        stage = 'image'
        image_options = {} if source is None else {'verified_source': source}
        with container.prepare_image(**image_options) as image:
            stage = 'claim'
            claim_options = {} if source is None else {'target': (source.run_id, source.commit_sha)}
            claim = worker.claim_next(worker_id, connection_factory, **claim_options)
            if source is not None and claim is None:
                raise commit_source.SourceRejected('verified run is no longer claimable')
            if claim is not None:
                stage = 'execution'
                result = execution.execute_fixture(claim, mode, connection_factory, prepared_image=image)
            stage = 'image_cleanup'
        # Both container and unique image tag must be released before persistence.
    except commit_source.SourceRejected as exc:
        # SourceRejected messages are fixed descriptions, never raw remote data.
        raise OneShotFailed(stage, claim, source_error=str(exc)) from None
    except EXPECTED_FAILURES:
        raise OneShotFailed(stage, claim) from None
    if claim is None:
        return None
    try:
        # execute_fixture returns only after its resource context has exited.
        return finalization.finalize_fixture(claim, result, connection_factory)
    except EXPECTED_FAILURES:
        raise OneShotFailed('finalization', claim) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-id', required=True, type=worker.validate_worker_id)
    parser.add_argument('--run-once', required=True, action='store_true')
    parser.add_argument('--verified-commit', action='store_true',
                        help='fetch and run only the approved fixture at the queued commit')
    parser.add_argument('--fixture-mode', choices=sorted(container.MODES), default='success',
                        help='operator-selected fixed fixture outcome')
    args = parser.parse_args(argv)
    try:
        options = {'verified_commit': True} if args.verified_commit else {}
        receipt = run_once(args.worker_id, args.fixture_mode, **options)
    except OneShotFailed as exc:
        # Claim/finalization COMMIT may have succeeded despite a lost response.
        error = {'status': 'incomplete', 'stage': exc.stage,
                 'detail': 'inspect DB state; no automatic retry'}
        if exc.run_id:
            error.update(run_id=exc.run_id, attempt_id=exc.attempt_id)
        if exc.source_error:
            error['source_error'] = exc.source_error
        print(json.dumps(error), file=sys.stderr)
        return 1
    output = {'claimed': receipt is not None}
    if receipt:
        output.update(run_id=str(receipt.run_id), attempt_id=str(receipt.attempt_id),
                      state=receipt.state, exit_code=receipt.exit_code, reason=receipt.reason,
                      finished_at=receipt.finished_at.isoformat(), replayed=receipt.replayed)
    print(json.dumps(output))
    # A recorded check failure/timeout is a completed Worker operation. The
    # job's outcome is the explicit state/exit_code, not this process exit code.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
