"""Bounded fixed-fixture execution with database authorization."""
from dataclasses import dataclass
import json
import os
from queue import Empty, SimpleQueue
import selectors
import subprocess
from threading import Thread
import time
from uuid import UUID

from . import container
from .commit_source import VerifiedSource
from .preflight import ExecutionDenied, authorize_execution
from .worker import Claim, LEASE_SECONDS, renew_lease

LOG_LIMIT = 1024 * 1024
TIMEOUT = 10
HEARTBEAT_INTERVAL = 5
EXPECTED_LIMITS = {'cpu.max': '50000 100000', 'memory.max': '134217728',
                   'memory.swap.max': '0', 'pids.max': '64'}


class HeartbeatFailed(ExecutionDenied):
    """The execution loop could not confirm continued DB authority."""


def _renew_in_background(claim, connection_factory, response):
    try:
        renew_lease(claim, connection_factory)
    except Exception:
        # DB and transport diagnostics can contain secrets. Fail closed on any error.
        response.put(False)
    else:
        response.put(True)


class _Heartbeat:
    def __init__(self, claim, connection_factory):
        self._claim = claim
        self._factory = connection_factory
        self._next_at = time.monotonic() + HEARTBEAT_INTERVAL
        self._response = None

    @property
    def pending(self):
        return self._response is not None

    def poll(self, lease_deadline, *, running):
        now = time.monotonic()
        if self._response is not None:
            if now >= min(lease_deadline, self._next_at):
                raise HeartbeatFailed('lease renewal was not confirmed in time')
            try:
                confirmed = self._response.get_nowait()
            except Empty:
                return lease_deadline
            if not confirmed:
                raise HeartbeatFailed('lease renewal was rejected or unconfirmed')
            self._response = None
            # Anchor to request dispatch so DB and delivery latency consume the budget.
            return self._next_at - HEARTBEAT_INTERVAL + LEASE_SECONDS
        if running and now >= self._next_at and now < lease_deadline:
            self._response = SimpleQueue()
            self._next_at = now + HEARTBEAT_INTERVAL
            # A black-holed DB connection cannot block the watchdog or worker exit.
            Thread(target=_renew_in_background, args=(self._claim, self._factory, self._response),
                   daemon=True).start()
        return lease_deadline


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
    verified_source: VerifiedSource | None = None

    @property
    def truncated(self):
        return self.observed_bytes > len(self.stdout) + len(self.stderr)


def _authorize(claim, connection_factory, source):
    permit = authorize_execution(claim, connection_factory)
    if source is not None and (permit.run_id, permit.commit_sha) != (source.run_id, source.commit_sha):
        raise ExecutionDenied('verified source no longer matches the claimed run')
    return permit


def execute_fixture(claim: Claim, mode='success', connection_factory=None, *, prepared_image=None):
    """Run only a fixed fixture. Returns after owned container cleanup.

    Starts a trusted guard, then rechecks DB before releasing fixed workload.
    Heartbeat starts after release. No repository fetch, DB finalization, or public API.
    """
    if mode not in container.MODES:
        raise container.ContainerRejected('unknown fixed fixture mode')
    if prepared_image is not None and not isinstance(prepared_image, container.PreparedImage):
        raise container.ContainerRejected('invalid prepared image handle')
    source = prepared_image.verified_source if prepared_image is not None else None
    permit = _authorize(claim, connection_factory, source)
    options = {} if prepared_image is None else {'prepared_image': prepared_image}
    with container.prepare_fixture(permit, mode, **options) as prepared:
        # Budget starts before the DB round trip: query/commit/network delay may
        # shorten local authority but must never extend the DB-issued lease.
        measured = time.monotonic()
        permit = _authorize(claim, connection_factory, source)
        lease_deadline = measured + permit.remaining_lease_seconds
        if lease_deadline <= time.monotonic():
            raise ExecutionDenied('execution lease expired during authorization')
        return _run(prepared, claim, connection_factory, lease_deadline, source)


def _run(prepared, claim, connection_factory, lease_deadline, source):
    process = stop = None
    heartbeat = None
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
        while True:
            now = time.monotonic()
            running = process.poll() is None
            if heartbeat is not None:
                lease_deadline = heartbeat.poll(lease_deadline, running=(
                    running and reason == 'completed' and now < wall_deadline))
            if not selector.get_map() and not running and (heartbeat is None or not heartbeat.pending):
                break
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
                    permit = _authorize(claim, connection_factory, source)
                    lease_deadline = min(lease_deadline, measured + permit.remaining_lease_seconds)
                    if time.monotonic() >= min(wall_deadline, lease_deadline) or reason != 'completed':
                        raise ExecutionDenied('execution budget expired before release')
                    _release_workload(process)
                    released = True
                    heartbeat = _Heartbeat(claim, connection_factory)
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
                               prepared.run_id, prepared.attempt_id, source)
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
