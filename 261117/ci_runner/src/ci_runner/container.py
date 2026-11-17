"""Worker-only preparation boundary. Creates but never starts a fixed fixture."""
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from uuid import UUID, uuid4

from .preflight import ExecutionPermit
from .settings import POLICY
from .worker import validate_worker_id

LABEL = 'dev.ci-runner.preparation-session'
ROOT = Path(__file__).resolve().parents[2]
TMPFS = {'/tmp': 'rw,noexec,nosuid,nodev,size=16m,mode=1777'}
MODES = frozenset(('success', 'failure', 'timeout', 'logs'))
ENTRYPOINT = ['python', '-I', '-B', '/opt/fixture.py']


class ContainerRejected(Exception):
    """Preparation or cleanup failed; no permission to start a workload."""


@dataclass(frozen=True)
class PreparedContainer:
    container_id: str
    image_id: str
    run_id: UUID
    attempt_id: UUID


def docker(*args, timeout=30, check=True):
    """No shell, input, request options, or daemon diagnostics in public errors."""
    try:
        result = subprocess.run(['docker', *args], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise ContainerRejected('Docker operation unavailable or timed out') from None
    if check and result.returncode:
        raise ContainerRejected('Docker operation failed')
    return result


def validate_configuration(info, image, labels, mode='success'):
    """Inspect settings only. Effective runtime cgroups still require verification."""
    try:
        cfg, host, state = info['Config'], info['HostConfig'], info['State']
        expected = {
            'NanoCpus': 500_000_000, 'Memory': 128 * 1024 * 1024,
            'MemorySwap': 128 * 1024 * 1024, 'PidsLimit': 64,
            'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False,
            'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges=true'],
            'Tmpfs': TMPFS, 'LogConfig': {'Type': 'none', 'Config': {}},
            'PidMode': '', 'IpcMode': 'private', 'CgroupnsMode': 'private',
            'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0},
        }
        valid = all(host.get(key) == value for key, value in expected.items())
        valid = valid and all(not host.get(key) for key in (
            'Binds', 'Mounts', 'VolumesFrom', 'Devices', 'DeviceRequests',
            'DeviceCgroupRules', 'CapAdd', 'GroupAdd', 'PortBindings',
            'PublishAllPorts', 'ExtraHosts', 'Links', 'OomKillDisable'))
        valid = valid and not info['Mounts']
        valid = valid and state['Status'] == 'created' and state['Pid'] == 0 and not state['Running']
        valid = valid and info['Image'] == image['Id']
        valid = valid and cfg['User'] == '65534:65534' and cfg['WorkingDir'] == '/tmp'
        valid = valid and cfg['Entrypoint'] == ENTRYPOINT and cfg['Cmd'] == [mode]
        valid = valid and cfg['Env'] == image['Config']['Env']
        valid = valid and not any(cfg.get(key) for key in (
            'Volumes', 'ExposedPorts', 'Healthcheck', 'Tty'))
        valid = valid and cfg['OpenStdin'] is True
        valid = valid and all(cfg['Labels'].get(key) == value for key, value in labels.items())
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise ContainerRejected('created container configuration does not match policy')


def remove_owned(name, session):
    result = docker('inspect', name, check=False)
    if result.returncode:
        if 'No such object:' in result.stderr:
            return
        raise ContainerRejected('container cleanup inspection failed')
    info = json.loads(result.stdout)[0]
    if info['Config']['Labels'].get(LABEL) != session:
        raise ContainerRejected('refusing cleanup of a container not owned by this session')
    docker('rm', '--force', '--volumes', info['Id'])


@dataclass(frozen=True)
class PreparedImage:
    image_id: str
    session: str
    tag: str


def inspect_prepared_image(prepared):
    """Check this session's pinned identity; never accept an arbitrary image/tag."""
    if (not isinstance(prepared, PreparedImage)
            or not re.fullmatch('[0-9a-f]{32}', prepared.session)
            or prepared.tag != f'ci-runner-fixed-fixture:{prepared.session}'
            or not re.fullmatch('sha256:[0-9a-f]{64}', prepared.image_id)):
        raise ContainerRejected('invalid prepared image handle')
    image = json.loads(docker('image', 'inspect', prepared.tag).stdout)[0]
    if (image['Id'] != prepared.image_id
            or image['Config']['Labels'].get(LABEL) != prepared.session):
        raise ContainerRejected('prepared image identity or ownership changed')
    return image


@contextmanager
def prepare_image():
    """Build only the local fixed source; release the owned unique tag on exit."""
    session = uuid4().hex
    tag = f'ci-runner-fixed-fixture:{session}'
    prepared = None
    try:
        docker('build', '--network=none', '--label', f'{LABEL}={session}',
               '--tag', tag, str(ROOT / 'fixtures/job'), timeout=180)
        image = json.loads(docker('image', 'inspect', tag).stdout)[0]
        prepared = PreparedImage(image['Id'], session, tag)
        inspect_prepared_image(prepared)
        yield prepared
    finally:
        result = docker('image', 'inspect', tag, check=False)
        if result.returncode == 0:
            image = json.loads(result.stdout)[0]
            if (image['Config']['Labels'].get(LABEL) != session
                    or (prepared is not None and image['Id'] != prepared.image_id)):
                raise ContainerRejected('refusing cleanup of an unowned or replaced image tag')
            docker('image', 'rm', tag)
        elif 'No such image:' not in result.stderr and 'No such object:' not in result.stderr:
            raise ContainerRejected('image cleanup inspection failed')


@contextmanager
def prepare_fixture(permit: ExecutionPermit, mode='success', *, prepared_image=None):
    """Create/inspect an unstarted fixture, borrowing an optional live image scope.

    Direct callers retain the original build/create/cleanup lifecycle. The
    one-job Worker builds before claim and keeps that image alive until cleanup.
    Neither path grants execution authority; execution must recheck the DB.
    """
    if mode not in MODES:
        raise ContainerRejected('unknown fixed fixture mode')
    validate_worker_id(permit.worker_id)
    if ((permit.repository_id, permit.ref, permit.workflow_version) !=
            (POLICY.repository_id, POLICY.ref, POLICY.workflow_version)
            or permit.attempt_no != 1 or not re.fullmatch('[0-9a-f]{40}', permit.commit_sha)
            or not isinstance(permit.run_id, UUID) or not isinstance(permit.attempt_id, UUID)):
        raise ContainerRejected('unsupported fixture preparation request')
    if prepared_image is None:
        with prepare_image() as image:
            with prepare_fixture(permit, mode, prepared_image=image) as prepared:
                yield prepared
        return
    image = inspect_prepared_image(prepared_image)
    session = uuid4().hex
    name = f'ci-runner-prepare-{session}'
    labels = {LABEL: session, 'dev.ci-runner.run': str(permit.run_id),
              'dev.ci-runner.attempt': str(permit.attempt_id)}
    attempted_create = False
    try:
        options = [item for key, value in labels.items() for item in ('--label', f'{key}={value}')]
        attempted_create = True  # Also inspect for cleanup if create response is lost.
        cid = docker('create', '--name', name, *options, '--pull=never',
                     '--interactive', '--user', '65534:65534', '--cpus', '0.5', '--memory', '128m',
                     '--memory-swap', '128m', '--pids-limit', '64', '--network', 'none',
                     '--read-only', '--tmpfs', f'/tmp:{TMPFS["/tmp"]}',
                     '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges=true',
                     '--cgroupns', 'private', '--ipc', 'private', '--restart', 'no',
                     '--log-driver', 'none', image['Id'], mode).stdout.strip()
        info = json.loads(docker('inspect', name).stdout)[0]
        validate_configuration(info, image, labels, mode)
        if cid != info['Id'] or not re.fullmatch('[0-9a-f]{64}', cid):
            raise ContainerRejected('container identity mismatch')
        yield PreparedContainer(cid, image['Id'], permit.run_id, permit.attempt_id)
    finally:
        if attempted_create:
            remove_owned(name, session)
