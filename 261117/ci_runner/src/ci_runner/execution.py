"""Bounded local-fixture execution, not a Worker loop or DB result finalizer."""
from dataclasses import dataclass
import json
import os
import selectors
import subprocess
import time
from uuid import UUID

from . import container
from .preflight import ExecutionDenied, authorize_execution
from .worker import Claim

LOG_LIMIT = 1024 * 1024
TIMEOUT = 10
EXPECTED_LIMITS = {'cpu.max': '50000 100000', 'memory.max': '134217728',
                   'memory.swap.max': '0', 'pids.max': '64'}


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    reason: str
    stdout: bytes
    stderr: bytes
    observed_bytes: int
    elapsed_seconds: float
    released: bool
    verified_limits: dict
    image_id: str
    run_id: UUID
    attempt_id: UUID

    @property
    def truncated(self):
        return self.observed_bytes > len(self.stdout) + len(self.stderr)


def execute_fixture(claim: Claim, mode='success', connection_factory=None, *, prepared_image=None):
    """Run only server-owned local fixtures. Returns after owned-resource cleanup.

    Starts a trusted guard, then rechecks DB before releasing fixed workload.
    No polling, heartbeat, repository fetch, DB finalization, or public API.
    """
    if mode not in container.MODES:
        raise container.ContainerRejected('unknown fixed fixture mode')
    permit = authorize_execution(claim, connection_factory)
    options = {} if prepared_image is None else {'prepared_image': prepared_image}
    with container.prepare_fixture(permit, mode, **options) as prepared:
        # Budget starts before the DB round trip: query/commit/network delay may
        # shorten local authority but must never extend the DB-issued lease.
        measured = time.monotonic()
        permit = authorize_execution(claim, connection_factory)
        lease_deadline = measured + permit.remaining_lease_seconds
        if lease_deadline <= time.monotonic():
            raise ExecutionDenied('execution lease expired during authorization')
        return _run(prepared, claim, connection_factory, lease_deadline)


def _run(prepared, claim, connection_factory, lease_deadline):
    process = stop = None
    selector = selectors.DefaultSelector()
    out, err, handshake = bytearray(), bytearray(), bytearray()
    observed = 0
    reason, released, limits = 'completed', False, {}
    started = time.monotonic()
    wall_deadline = started + TIMEOUT
    stopped_at = None
    try:
        process = subprocess.Popen(['docker', 'start', '--attach', '--interactive', prepared.container_id],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for stream, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if reason == 'completed' and now >= min(wall_deadline, lease_deadline):
                reason = 'lease_expired' if lease_deadline <= wall_deadline else 'timeout'
            if reason != 'completed' and stop is None:
                # Keep draining attach while kill is processed: synchronous kill
                # can deadlock against daemon-side output backpressure.
                stop = subprocess.Popen(['docker', 'kill', prepared.container_id],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
                stopped_at = now
            if stopped_at is not None and now - stopped_at > 10:
                raise container.ContainerRejected('container did not stop after deadline')
            for key, _ in selector.select(timeout=0.05):
                chunk = os.read(key.fileobj.fileno(), 16384)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == 'stdout' and not released:
                    handshake.extend(chunk)
                    if len(handshake) > 4096:
                        raise container.ContainerRejected('invalid runtime acknowledgement')
                    if b'\n' not in handshake:
                        continue
                    line, rest = bytes(handshake).split(b'\n', 1)
                    try:
                        acknowledgement = json.loads(line)
                    except (ValueError, UnicodeError):
                        raise container.ContainerRejected('invalid runtime acknowledgement') from None
                    if acknowledgement != {'ready': 1, 'limits': EXPECTED_LIMITS} or rest:
                        raise container.ContainerRejected('runtime restrictions do not match')
                    limits = acknowledgement['limits']
                    measured = time.monotonic()
                    permit = authorize_execution(claim, connection_factory)
                    lease_deadline = min(lease_deadline, measured + permit.remaining_lease_seconds)
                    if time.monotonic() >= min(wall_deadline, lease_deadline) or reason != 'completed':
                        raise ExecutionDenied('execution budget expired before release')
                    _release_workload(process)
                    released = True
                    handshake.clear()
                    continue
                observed += len(chunk)
                target = out if key.data == 'stdout' else err
                target.extend(chunk[:max(0, LOG_LIMIT - len(out) - len(err))])
                if reason == 'completed' and observed > LOG_LIMIT:
                    reason = 'log_limit'
        process.wait(timeout=5)
        if stop:
            # kill may race with natural exit. Inspect below is the stopping proof.
            stop.wait(timeout=5)
        info = json.loads(container.docker('inspect', prepared.container_id).stdout)[0]
        state = info['State']
        if state['Running'] or state['Pid'] != 0 or state['Status'] != 'exited':
            raise container.ContainerRejected('container exit not confirmed')
        if not released:
            reason = 'runtime_preflight_rejected' if reason == 'completed' else reason
        elif reason == 'completed' and state['ExitCode'] != 0:
            reason = 'check_failure'
        return ExecutionResult(state['ExitCode'], reason, bytes(out), bytes(err), observed,
                               time.monotonic() - started, released, limits, prepared.image_id,
                               prepared.run_id, prepared.attempt_id)
    finally:
        selector.close()
        # Free attach pipes before the preparation context issues blocking rm.
        # rm --force handles the container on exceptions, even before release.
        for child in (process, stop):
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()


def _release_workload(process):
    process.stdin.write(b'GO\n')
    process.stdin.flush()
    process.stdin.close()
