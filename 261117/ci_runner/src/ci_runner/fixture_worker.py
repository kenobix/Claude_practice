"""Run at most one queued job using only a reviewed local fixture, then exit.

This is not a submitted-commit checker, resident Worker, or recovery loop.
"""
import argparse
import json
import subprocess
import sys

import psycopg

from . import container, database, execution, finalization, worker
from .preflight import ExecutionDenied


class OneShotFailed(Exception):
    """Expose sanitized phase/IDs only, without raw diagnostics or logs."""

    def __init__(self, stage, claim=None):
        super().__init__(f'fixture worker stopped during {stage}; inspect DB before retry')
        self.stage = stage
        self.run_id = str(claim.run_id) if claim else None
        self.attempt_id = str(claim.attempt_id) if claim else None


EXPECTED_FAILURES = (psycopg.Error, database.DatabaseUnavailable, ExecutionDenied,
                     container.ContainerRejected, finalization.FinalizationDenied,
                     OSError, subprocess.SubprocessError, json.JSONDecodeError)


def run_once(worker_id, mode='success', connection_factory=None):
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
        if not worker.has_queued_candidate(connection_factory):
            return None
        stage = 'image'
        with container.prepare_image() as image:
            stage = 'claim'
            claim = worker.claim_next(worker_id, connection_factory)
            if claim is not None:
                stage = 'execution'
                result = execution.execute_fixture(claim, mode, connection_factory, prepared_image=image)
            stage = 'image_cleanup'
        # Both container and unique image tag must be released before persistence.
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
    parser.add_argument('--fixture-mode', choices=sorted(container.MODES), default='success',
                        help='operator-selected local fixture; never fetched from the submitted SHA')
    args = parser.parse_args(argv)
    try:
        receipt = run_once(args.worker_id, args.fixture_mode)
    except OneShotFailed as exc:
        # Claim/finalization COMMIT may have succeeded despite a lost response.
        error = {'status': 'incomplete', 'stage': exc.stage,
                 'detail': 'inspect DB state; no automatic retry'}
        if exc.run_id:
            error.update(run_id=exc.run_id, attempt_id=exc.attempt_id)
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
