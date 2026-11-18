"""Recover containers for lease-expired attempts closed by the DB monitor."""
import argparse
from dataclasses import dataclass
import json
import re
import sys
from uuid import UUID

import psycopg

from . import container, database
from .settings import POLICY


@dataclass(frozen=True)
class RemovedContainer:
    container_id: str
    run_id: UUID
    attempt_id: UUID


def _inspect(container_id):
    result = container.docker('container', 'inspect', container_id, check=False)
    if result.returncode:
        if result.stderr.strip() == f'Error response from daemon: No such container: {container_id}':
            return None
        raise container.ContainerRejected('orphan cleanup inspection failed')
    try:
        (info,) = json.loads(result.stdout)
        if info['Id'] == container_id:
            return info
    except (KeyError, TypeError, ValueError):
        pass
    raise container.ContainerRejected('orphan cleanup inspection identity mismatch')


def _attempt_ids(info):
    cfg = info.get('Config')
    labels = cfg.get('Labels') if isinstance(cfg, dict) else None
    if not isinstance(labels, dict):
        return None
    session = labels.get(container.LABEL)
    if (not isinstance(session, str) or not re.fullmatch('[0-9a-f]{32}', session)
            or info.get('Name') != f'/{container.NAME_PREFIX}{session}'):
        return None
    try:
        run_label, attempt_label = labels[container.RUN_LABEL], labels[container.ATTEMPT_LABEL]
        run_id, attempt_id = UUID(run_label), UUID(attempt_label)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if (run_label, attempt_label) == (str(run_id), str(attempt_id)):
        return run_id, attempt_id
    return None


def _closed_by_expiry(factory, run_id, attempt_id):
    with factory() as conn:
        row = conn.execute(
            'SELECT 1 FROM runs r JOIN attempts a ON a.run_id=r.id '
            'WHERE r.id=%s AND a.id=%s AND r.repository_id=%s AND r.ref=%s AND r.workflow_version=%s '
            "AND a.attempt_no=1 AND a.retry_kind='initial' "
            'AND r.completed_attempt_id IS NULL AND a.exit_code IS NULL '
            'AND a.lease_expires_at <= statement_timestamp() AND a.finished_at >= a.lease_expires_at '
            "AND ((r.state='failed' AND a.state='failed' AND a.reason='infra_lease_expired' "
            'AND r.cancel_requested_at IS NULL) '
            "OR (r.state='canceled' AND a.state='canceled' AND a.reason='canceled_lease_expired' "
            'AND r.cancel_requested_at IS NOT NULL))',
            (run_id, attempt_id, POLICY.repository_id, POLICY.ref, POLICY.workflow_version),
        ).fetchone()
    # Never issue a Docker removal before a successful DB round trip.
    return row is not None


def recover(connection_factory=None):
    """Scan once; yield removals that Docker acknowledged."""
    factory = connection_factory or database.connect
    candidates = container.docker('container', 'ls', '--all', '--quiet', '--no-trunc',
                                  '--filter', f'label={container.LABEL}').stdout.splitlines()
    for container_id in dict.fromkeys(candidates):
        if not re.fullmatch('[0-9a-f]{64}', container_id):
            raise container.ContainerRejected('unexpected orphan cleanup candidate identity')
        info = _inspect(container_id)
        if info is None:
            continue
        ids = _attempt_ids(info)
        if ids is None or not _closed_by_expiry(factory, *ids):
            continue
        result = container.docker('container', 'rm', '--force', container_id, check=False)
        if result.returncode == 0 and result.stdout.strip() == container_id:
            yield RemovedContainer(container_id, *ids)
        elif _inspect(container_id) is not None:
            raise container.ContainerRejected('orphan removal did not complete')
        # Forced removal of an absent ID can return success with empty stdout.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', required=True, help='scan this Docker daemon once')
    parser.parse_args(argv)
    try:
        for removed in recover():
            print(json.dumps({'container_id': removed.container_id, 'run_id': str(removed.run_id),
                              'attempt_id': str(removed.attempt_id)}), flush=True)
    except (psycopg.Error, database.DatabaseUnavailable):
        print('database unavailable; orphan cleanup stopped', file=sys.stderr)
        return 1
    except container.ContainerRejected:
        print('Docker operation failed; orphan cleanup may be incomplete', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
