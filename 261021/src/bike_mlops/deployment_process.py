"""Bound the complete CLI deployment in an owned process group, without pickle.

A timeout is an unknown outcome: daemon/HTTP side effects may have completed.
Only finite JSON is returned. Credentials remain in an explicit child environment.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from bike_mlops.deployment_progress import DeploymentTimeout

OPERATION_SECONDS = 900
RECOVERY_SECONDS = 300
TERM_SECONDS = 5
KILL_SECONDS = 2
ENV_KEYS = {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT',
    'DATA_PROFILE_ID', 'DATABASE_URL', 'MLFLOW_TRACKING_URI', 'MLFLOW_REGISTRY_URI',
    'MLFLOW_TRACKING_USERNAME', 'MLFLOW_TRACKING_PASSWORD', 'MLFLOW_TRACKING_TOKEN',
    'MLFLOW_TRACKING_SERVER_CERT_PATH', 'MLFLOW_MODEL_NAME', 'FEATURE_CONTRACT_VERSION',
    'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
    'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'MLFLOW_DISABLE_AGENT_HINT'}


def strict_result(raw):
    def invalid(_):
        raise ValueError('Nonfinite deployment result')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate deployment result key')
            result[key] = value
        return result
    def finite_float(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError('Nonfinite deployment result')
        return value
    value = json.loads(raw, parse_constant=invalid, parse_float=finite_float, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError('Deployment result must be an object')
    return value


def remaining_seconds(cap):
    raw = os.getenv('BIKE_DEPLOYMENT_DEADLINE_NS')
    if raw is None:
        return cap
    remaining = (int(raw) - time.monotonic_ns()) / 1e9
    if remaining <= 0:
        raise DeploymentTimeout('Deployment deadline expired; outcome unconfirmed; inspect before recovery')
    return min(cap, remaining)


def _start_identity(pid):
    # /proc starttime distinguishes PID reuse. The unreaped leader owns its PGID.
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return None


def _signal_owned(process, identity, signum):
    if identity is None or _start_identity(process.pid) != identity:
        raise RuntimeError('Deployment process ownership unavailable; cleanup unconfirmed')
    if os.getpgid(process.pid) != process.pid:
        raise RuntimeError('Deployment process group differs; cleanup unconfirmed')
    os.killpg(process.pid, signum)


def _group_alive(group):
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry/'stat').read_text().rsplit(')', 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if fields[0] not in {'Z', 'X'} and fields[2] == str(group) and fields[3] == str(group):
            return True
    return False


def stop_owned(process, identity):
    # Do not reap the leader before signalling the whole group: its /proc start
    # identity remains available even if TERM exits the leader before descendants.
    _signal_owned(process, identity, signal.SIGTERM)
    until = time.monotonic() + TERM_SECONDS
    while time.monotonic() < until:
        ended = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if ended is not None:
            break
        time.sleep(.02)
    _signal_owned(process, identity, signal.SIGKILL)
    try:
        process.wait(timeout=KILL_SECONDS)
    except subprocess.TimeoutExpired:
        raise DeploymentTimeout('Deployment child did not exit; cleanup unconfirmed') from None
    until = time.monotonic() + KILL_SECONDS
    while _group_alive(process.pid):
        if time.monotonic() >= until:
            raise DeploymentTimeout('Deployment descendants did not exit; cleanup unconfirmed')
        time.sleep(.02)


def run_operation(payload, *, timeout_seconds=None, command=None, env_keys=ENV_KEYS):
    """command/env_keys overrides are only used by disposable subprocess tests."""
    mode = payload.get('mode')
    if mode not in {'promote', 'recover'}:
        raise ValueError('Invalid deployment operation')
    budget = timeout_seconds if timeout_seconds is not None else (OPERATION_SECONDS if mode == 'promote' else RECOVERY_SECONDS)
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError('Invalid deployment deadline')
    began = time.monotonic_ns()
    deadline = began + int(budget * 1e9)
    env = {key: value for key, value in os.environ.items() if key in env_keys}
    env['BIKE_DEPLOYMENT_DEADLINE_NS'] = str(deadline)
    env['BIKE_DEPLOYMENT_SUPERVISOR_PID'] = str(os.getpid())
    env['MLFLOW_DISABLE_AGENT_HINT'] = '1'
    command = command or [sys.executable, '-m', 'bike_mlops.deployment_process']
    # Temporary bytes include user declarations, so private mode + automatic cleanup.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as request:
        request.write(json.dumps(payload, allow_nan=False).encode())
        request.seek(0)
        process = subprocess.Popen(command, stdin=request, stdout=output, stderr=None,
                                   env=env, start_new_session=True, close_fds=True)
        identity = _start_identity(process.pid)
        try:
            process.wait(timeout=max(.001, (deadline-time.monotonic_ns())/1e9))
        except BaseException:
            stop_owned(process, identity)
            raise DeploymentTimeout('Deployment deadline or interruption; outcome unconfirmed; run deploy inspect/recover') from None
        if process.returncode:
            raise RuntimeError(f'Deployment worker failed (exit {process.returncode}); run deploy inspect/recover')
        output.seek(0, 2)
        if output.tell() > 1024*1024:
            raise ValueError('Deployment result exceeds limit; outcome unconfirmed')
        output.seek(0)
        return strict_result(output.read().decode('utf-8'))


@contextmanager
def interruption_guard():
    def interrupted(signum, _frame):
        raise DeploymentTimeout('Deployment interrupted; outcome unconfirmed; inspect before recovery')
    previous = {s: signal.signal(s, interrupted) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def _bind_supervisor():
    # Linux/WSL process-local setting; no host clock/system configuration changes.
    import ctypes
    expected = int(os.environ['BIKE_DEPLOYMENT_SUPERVISOR_PID'])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != expected:
        raise DeploymentTimeout('Deployment supervisor disappeared; outcome unconfirmed')


class _SafeStderr:
    """Only our structured progress reaches the inherited diagnostic stream."""
    def __init__(self, stream):
        self.stream, self.buffer = stream, ''
    def write(self, value):
        self.buffer += value
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            try:
                event = strict_result(line)
                if event.get('kind') in {'deployment_progress', 'deployment_worker_failure'}:
                    # These events contain code-controlled stage/type/time labels only.
                    allowed = {'kind','stage','status','outcome','observed_at_utc','monotonic_ns',
                               'elapsed_seconds','process_id','log_reference','deployment_id',
                               'remaining_seconds','error_type','action'}
                    if set(event) <= allowed:
                        self.stream.write(json.dumps(event) + '\n')
                        self.stream.flush()
            except (ValueError, TypeError):
                pass
        if len(self.buffer) > 8192:
            self.buffer = ''
        return len(value)
    def flush(self):
        self.stream.flush()
    def isatty(self):
        return False


def main():
    # Discard native fd2 output too; keep a private copy only for safe JSON events.
    diagnostic = os.fdopen(os.dup(2), 'w', buffering=1)
    with open(os.devnull, 'w') as null:
        os.dup2(null.fileno(), 2)
    sys.stderr = _SafeStderr(diagnostic)
    # No traceback/native error/credential is sent to the caller.
    with interruption_guard():
        try:
            _bind_supervisor()
            payload = strict_result(sys.stdin.buffer.read(65537).decode())
            from bike_mlops import cli
            remaining_seconds(OPERATION_SECONDS)
            if payload['mode'] == 'promote':
                result = cli._promote(payload['version'], payload['approver'], payload['reason'],
                                      payload['rollback'], payload['diagnostic'])
            elif payload['mode'] == 'recover':
                with cli._deployment_lock():
                    from bike_mlops.deployment_progress import deployment_stage
                    with deployment_stage(cli.RUNTIME_DIR, 'recover'):
                        result = cli._recover_deployment()
            else:
                raise ValueError('Invalid deployment operation')
            print(json.dumps(result, allow_nan=False), flush=True)
        except BaseException as error:
            print(json.dumps(dict(kind='deployment_worker_failure', error_type=type(error).__name__,
                                  outcome='unconfirmed', action='deploy inspect/recover')), file=sys.stderr, flush=True)
            raise SystemExit(1)


if __name__ == '__main__':
    main()
