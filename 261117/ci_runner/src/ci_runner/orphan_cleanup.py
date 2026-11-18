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


POLICY_PARAMS = (POLICY.repository_id, POLICY.ref, POLICY.workflow_version)
CLOSED_ATTEMPT = (
    'r.id=a.run_id AND r.repository_id=%s AND r.ref=%s AND r.workflow_version=%s '
    "AND a.attempt_no=1 AND a.retry_kind='initial' "
    'AND a.container_recovered_at IS NULL AND r.completed_attempt_id IS NULL AND a.exit_code IS NULL '
    'AND a.lease_expires_at <= statement_timestamp() AND a.finished_at >= a.lease_expires_at '
    "AND ((r.state='failed' AND a.state='failed' AND a.reason='infra_lease_expired' "
    'AND r.cancel_requested_at IS NULL) '
    "OR (r.state='canceled' AND a.state='canceled' AND a.reason='canceled_lease_expired' "
    'AND r.cancel_requested_at IS NOT NULL))'
)


@dataclass(frozen=True)
class ContainerOwner:
    run_id: UUID
    attempt_id: UUID
    session: UUID


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


def _owner(info):
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
        return ContainerOwner(run_id, attempt_id, UUID(hex=session))
    return None


def _known_containers(factory, daemon):
    # Include known IDs even when Docker's list is empty after a crash following rm.
    with factory() as conn:
        rows = conn.execute(
            'SELECT a.created_container_id, a.run_id, a.id, a.create_session '
            'FROM attempts a JOIN runs r ON r.id=a.run_id '
            f'WHERE {CLOSED_ATTEMPT} AND a.create_daemon_id=%s AND a.created_container_id IS NOT NULL',
            (*POLICY_PARAMS, daemon),
        ).fetchall()
    return {row['created_container_id']: ContainerOwner(row['run_id'], row['id'], row['create_session'])
            for row in rows}


def _authorize_removal(factory, owner, daemon, container_id):
    """Return tracked/legacy authorization only after a successful transaction."""
    with factory() as conn:
        row = conn.execute(
            'SELECT a.create_tracked, a.create_daemon_id, a.create_session, a.created_container_id '
            'FROM attempts a JOIN runs r ON r.id=a.run_id '
            f'WHERE {CLOSED_ATTEMPT} AND r.id=%s AND a.id=%s FOR UPDATE OF r',
            (*POLICY_PARAMS, owner.run_id, owner.attempt_id),
        ).fetchone()
        if row is None:
            return None
        if not row['create_tracked']:
            return 'legacy'
        if (row['create_daemon_id'], row['create_session']) != (daemon, owner.session):
            return None
        if row['created_container_id'] not in (None, container_id):
            return None
        if row['created_container_id'] is None:
            # The Worker can concurrently record the same ID even after lease expiry.
            bound = conn.execute(
                'UPDATE attempts SET created_container_id=%s WHERE id=%s AND container_recovered_at IS NULL '
                'AND (created_container_id IS NULL OR created_container_id=%s) RETURNING id',
                (container_id, owner.attempt_id, container_id),
            ).fetchone()
            if bound is None:
                return None
    return 'tracked'


def _confirm_recovery(factory, owner, daemon, container_id):
    if container.daemon_id() != daemon:
        raise container.ContainerRejected('Docker daemon changed during orphan cleanup')
    with factory() as conn:
        row = conn.execute(
            'UPDATE attempts AS a SET container_recovered_at=clock_timestamp() FROM runs AS r '
            f'WHERE {CLOSED_ATTEMPT} AND r.id=%s AND a.id=%s AND a.create_daemon_id=%s '
            'AND a.create_session=%s AND a.created_container_id=%s RETURNING a.id',
            (*POLICY_PARAMS, owner.run_id, owner.attempt_id, daemon, owner.session, container_id),
        ).fetchone()
    return row is not None


def recover(connection_factory=None):
    """Scan once; yield newly confirmed recoveries and acknowledged legacy removals."""
    factory = connection_factory or database.connect
    daemon = container.daemon_id()
    known = _known_containers(factory, daemon)
    candidates = container.docker('container', 'ls', '--all', '--quiet', '--no-trunc',
                                  '--filter', f'label={container.LABEL}').stdout.splitlines()
    for container_id in dict.fromkeys([*known, *candidates]):
        if not re.fullmatch('[0-9a-f]{64}', container_id):
            raise container.ContainerRejected('unexpected orphan cleanup candidate identity')
        recorded_owner = known.get(container_id)
        info = _inspect(container_id)
        if info is None:
            if recorded_owner and _confirm_recovery(factory, recorded_owner, daemon, container_id):
                yield RemovedContainer(container_id, recorded_owner.run_id, recorded_owner.attempt_id)
            continue
        owner = _owner(info)
        if recorded_owner and owner != recorded_owner:
            raise container.ContainerRejected('recorded container ownership mismatch')
        if owner is None:
            continue
        if container.daemon_id() != daemon:
            raise container.ContainerRejected('Docker daemon changed during orphan cleanup')
        authorization = _authorize_removal(factory, owner, daemon, container_id)
        if authorization is None:
            continue
        result = container.docker('container', 'rm', '--force', container_id, check=False)
        acknowledged = result.returncode == 0 and result.stdout.strip() == container_id
        if authorization == 'legacy' and acknowledged:
            yield RemovedContainer(container_id, owner.run_id, owner.attempt_id)
            continue
        # Tracked recovery requires exact absence even when rm acknowledged the ID.
        if _inspect(container_id) is not None:
            raise container.ContainerRejected('orphan removal did not complete')
        if authorization == 'tracked' and _confirm_recovery(factory, owner, daemon, container_id):
            yield RemovedContainer(container_id, owner.run_id, owner.attempt_id)


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
