"""Worker-only preparation boundary. Creates but never starts a fixed fixture."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from . import commit_source
from .preflight import ExecutionPermit, record_created_container, reserve_container_create
from .settings import POLICY
from .worker import validate_worker_id

LABEL = 'dev.ci-runner.preparation-session'
RUN_LABEL = 'dev.ci-runner.run'
ATTEMPT_LABEL = 'dev.ci-runner.attempt'
NAME_PREFIX = 'ci-runner-prepare-'
ROOT = Path(__file__).resolve().parents[2]
TMPFS = {'/tmp': 'rw,noexec,nosuid,nodev,size=16m,mode=1777'}
MODES = frozenset(('success', 'failure', 'timeout', 'logs'))
ENTRYPOINT = ['python', '-I', '-B', '/opt/fixture.py']
DOCKERFILE_SHA256 = 'c6a7de570c22087ae5be90fd1d15cee425281ab10e939af526e6049abaddfecf'


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


def daemon_id():
    try:
        return UUID(docker('info', '--format', '{{.ID}}').stdout.strip())
    except ValueError:
        raise ContainerRejected('Docker daemon identity unavailable') from None


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
    result = docker('container', 'inspect', name, check=False)
    if result.returncode:
        if result.stderr.strip() == f'Error response from daemon: No such container: {name}':
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
    verified_source: commit_source.VerifiedSource | None = None


def _source_labels(source):
    if source is None:
        return {}
    return {'dev.ci-runner.source-run': str(source.run_id),
            'dev.ci-runner.source-commit': source.commit_sha,
            'dev.ci-runner.source-main': source.main_sha,
            'dev.ci-runner.source-blob': source.blob_sha,
            'dev.ci-runner.source-sha256': commit_source.DIGEST}


def inspect_prepared_image(prepared):
    """Check this session's pinned identity; never accept an arbitrary image/tag."""
    if (not isinstance(prepared, PreparedImage)
            or not re.fullmatch('[0-9a-f]{32}', prepared.session)
            or prepared.tag != f'ci-runner-fixed-fixture:{prepared.session}'
            or not re.fullmatch('sha256:[0-9a-f]{64}', prepared.image_id)):
        raise ContainerRejected('invalid prepared image handle')
    image = json.loads(docker('image', 'inspect', prepared.tag).stdout)[0]
    labels = image['Config']['Labels']
    if (image['Id'] != prepared.image_id
            or labels.get(LABEL) != prepared.session
            or any(labels.get(key) != value
                   for key, value in _source_labels(prepared.verified_source).items())):
        raise ContainerRejected('prepared image identity or ownership changed')
    return image


@contextmanager
def _build_context(source):
    if source is None:
        yield ROOT / 'fixtures/job'
        return
    commit_source.validate_verified_source(source)
    dockerfile = (ROOT / 'fixtures/job/Dockerfile').read_bytes()
    if hashlib.sha256(dockerfile).hexdigest() != DOCKERFILE_SHA256:
        raise ContainerRejected('fixed Dockerfile digest mismatch')
    with TemporaryDirectory(prefix='ci-runner-source-') as directory:
        context = Path(directory)
        (context / 'Dockerfile').write_bytes(dockerfile)
        (context / 'fixture.py').write_bytes(source.contents)
        yield context


@contextmanager
def prepare_image(*, verified_source: commit_source.VerifiedSource | None = None):
    """Build a fixed fixture; release the owned unique tag on exit."""
    session = uuid4().hex
    tag = f'ci-runner-fixed-fixture:{session}'
    prepared = None
    attempted_build = False
    try:
        with _build_context(verified_source) as context:
            labels = {LABEL: session, **_source_labels(verified_source)}
            options = [item for key, value in labels.items() for item in ('--label', f'{key}={value}')]
            attempted_build = True
            docker('build', '--network=none', *options, '--tag', tag, str(context), timeout=180)
        image = json.loads(docker('image', 'inspect', tag).stdout)[0]
        prepared = PreparedImage(image['Id'], session, tag, verified_source)
        inspect_prepared_image(prepared)
        yield prepared
    finally:
        if attempted_build:
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
def prepare_fixture(permit: ExecutionPermit, mode='success', *, prepared_image=None, connection_factory=None):
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
            with prepare_fixture(permit, mode, prepared_image=image, connection_factory=connection_factory) as prepared:
                yield prepared
        return
    image = inspect_prepared_image(prepared_image)
    source = prepared_image.verified_source
    if source is not None and (permit.run_id, permit.commit_sha) != (source.run_id, source.commit_sha):
        raise ContainerRejected('prepared source does not match the execution permit')
    reservation = uuid4()
    session = reservation.hex
    name = f'{NAME_PREFIX}{session}'
    labels = {LABEL: session, RUN_LABEL: str(permit.run_id), ATTEMPT_LABEL: str(permit.attempt_id)}
    if connection_factory is not None:
        daemon = daemon_id()
        reserve_container_create(permit, daemon, reservation, connection_factory)
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
        if connection_factory is not None:
            record_created_container(permit, daemon, reservation, cid, connection_factory)
        yield PreparedContainer(cid, image['Id'], permit.run_id, permit.attempt_id)
    finally:
        if attempted_create:
            remove_owned(name, session)
