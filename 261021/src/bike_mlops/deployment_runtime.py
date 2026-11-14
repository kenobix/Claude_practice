"""Product components: deployment_runtime."""
from __future__ import annotations


# ========================================================================
# deployment_progress
# ========================================================================

"""Process-local elapsed time and conservative Docker timeout diagnostics."""


from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import stat
import sys
import threading
import time


_operation = ContextVar("deployment_operation", default=None)


class DeploymentTimeout(TimeoutError):
    """The Docker client stopped waiting; daemon-side completion is unknown."""


@contextmanager
def deployment_stage(runtime: Path, stage: str, *, interval=15, operation_id=None):
    """Emit safe stage labels, never native command output or credentials.

    This journal is diagnostic only, not an approval or deployment authority.
    Heartbeats use MONOTONIC; UTC is recorded separately and may regress.
    """
    if stage not in {"compose_build_start", "compose_proxy", "candidate_ready",
                     "artifact_load_and_probe", "registry_check", "recover",
                     "model_registry", "artifact_download", "artifact_hash", "deserialize",
                     "probe_http", "state_finalize", "ledger_finalize"} or interval <= 0:
        raise ValueError("Invalid deployment diagnostic stage")
    operation_id = operation_id or _operation.get()
    if operation_id is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", operation_id):
        raise ValueError("Invalid deployment diagnostic operation identity")
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / "deployment-progress.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("Deployment progress journal must be a regular file")
    began = time.monotonic_ns()
    stopped, lock = threading.Event(), threading.Lock()
    failures = []
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        def emit(status, outcome):
            event = dict(kind="deployment_progress", stage=stage, status=status, outcome=outcome,
                         observed_at_utc=datetime.now(timezone.utc).isoformat(),
                         monotonic_ns=time.monotonic_ns(), elapsed_seconds=(time.monotonic_ns()-began)/1e9,
                         process_id=os.getpid(), log_reference=path.name)
            event["deployment_id"] = operation_id
            deadline = os.getenv("BIKE_DEPLOYMENT_DEADLINE_NS")
            event["remaining_seconds"] = max(0, (int(deadline)-time.monotonic_ns())/1e9) if deadline else None
            line = json.dumps(event, sort_keys=True)
            with lock:
                stream.write(line + "\n")
                stream.flush()
                print(line, file=sys.stderr, flush=True)

        def heartbeat():
            while not stopped.wait(interval):
                try:
                    emit("in_progress", "unconfirmed")
                except Exception as error:
                    failures.append(error)
                    return

        emit("started", "unconfirmed")
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        token = _operation.set(operation_id)
        try:
            yield
        except BaseException:
            stopped.set()
            thread.join()
            emit("interrupted_or_failed", "unconfirmed")
            raise
        else:
            stopped.set()
            thread.join()
            if failures:
                raise RuntimeError("Deployment progress journal failed; outcome unconfirmed") from failures[0]
            emit("stage_completed", "stage_only_not_deployment_approval")
        finally:
            _operation.reset(token)


# ========================================================================
# deployment_process
# ========================================================================

"""Bound the complete CLI deployment in an owned process group, without pickle.

A timeout is an unknown outcome: daemon/HTTP side effects may have completed.
Only finite JSON is returned. Credentials remain in an explicit child environment.
"""


from contextlib import contextmanager as _deployment_process_contextmanager
import json as _deployment_process_json
import math
import os as _deployment_process_os
from pathlib import Path as _deployment_process_Path
import signal
import subprocess
import sys as _deployment_process_sys
import tempfile
import time as _deployment_process_time

_deployment_process_DeploymentTimeout = DeploymentTimeout

OPERATION_SECONDS = 900
RECOVERY_SECONDS = 300
TERM_SECONDS = 5
KILL_SECONDS = 2
ENV_KEYS = {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT',
    'DATA_PROFILE_ID', 'DATABASE_URL', 'MLFLOW_TRACKING_URI', 'MLFLOW_REGISTRY_URI',
    'APP_DB_PASSWORD', 'MLFLOW_DB_PASSWORD', 'POSTGRES_ADMIN_PASSWORD', 'APP_DB_HOST_PORT',
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
    value = _deployment_process_json.loads(raw, parse_constant=invalid, parse_float=finite_float, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError('Deployment result must be an object')
    return value


def remaining_seconds(cap):
    raw = _deployment_process_os.getenv('BIKE_DEPLOYMENT_DEADLINE_NS')
    if raw is None:
        return cap
    remaining = (int(raw) - _deployment_process_time.monotonic_ns()) / 1e9
    if remaining <= 0:
        raise _deployment_process_DeploymentTimeout('Deployment deadline expired; outcome unconfirmed; inspect before recovery')
    return min(cap, remaining)


def _start_identity(pid):
    # /proc starttime distinguishes PID reuse. The unreaped leader owns its PGID.
    try:
        return _deployment_process_Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return None


def _signal_owned(process, identity, signum):
    if identity is None or _start_identity(process.pid) != identity:
        raise RuntimeError('Deployment process ownership unavailable; cleanup unconfirmed')
    if _deployment_process_os.getpgid(process.pid) != process.pid:
        raise RuntimeError('Deployment process group differs; cleanup unconfirmed')
    _deployment_process_os.killpg(process.pid, signum)


def _group_alive(group):
    for entry in _deployment_process_Path('/proc').iterdir():
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
    until = _deployment_process_time.monotonic() + TERM_SECONDS
    while _deployment_process_time.monotonic() < until:
        ended = _deployment_process_os.waitid(_deployment_process_os.P_PID, process.pid, _deployment_process_os.WEXITED | _deployment_process_os.WNOHANG | _deployment_process_os.WNOWAIT)
        if ended is not None:
            break
        _deployment_process_time.sleep(.02)
    _signal_owned(process, identity, signal.SIGKILL)
    try:
        process.wait(timeout=KILL_SECONDS)
    except subprocess.TimeoutExpired:
        raise _deployment_process_DeploymentTimeout('Deployment child did not exit; cleanup unconfirmed') from None
    until = _deployment_process_time.monotonic() + KILL_SECONDS
    while _group_alive(process.pid):
        if _deployment_process_time.monotonic() >= until:
            raise _deployment_process_DeploymentTimeout('Deployment descendants did not exit; cleanup unconfirmed')
        _deployment_process_time.sleep(.02)


def run_operation(payload, *, timeout_seconds=None, command=None, env_keys=ENV_KEYS):
    """command/env_keys overrides are only used by disposable subprocess tests."""
    mode = payload.get('mode')
    if mode not in {'promote', 'recover'}:
        raise ValueError('Invalid deployment operation')
    budget = timeout_seconds if timeout_seconds is not None else (OPERATION_SECONDS if mode == 'promote' else RECOVERY_SECONDS)
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError('Invalid deployment deadline')
    began = _deployment_process_time.monotonic_ns()
    deadline = began + int(budget * 1e9)
    env = {key: value for key, value in _deployment_process_os.environ.items() if key in env_keys}
    env['BIKE_DEPLOYMENT_DEADLINE_NS'] = str(deadline)
    env['BIKE_DEPLOYMENT_SUPERVISOR_PID'] = str(_deployment_process_os.getpid())
    env['MLFLOW_DISABLE_AGENT_HINT'] = '1'
    command = command or [_deployment_process_sys.executable, '-m', 'bike_mlops.deployment_runtime']
    # Temporary bytes include user declarations, so private mode + automatic cleanup.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as request:
        request.write(_deployment_process_json.dumps(payload, allow_nan=False).encode())
        request.seek(0)
        process = subprocess.Popen(command, stdin=request, stdout=output, stderr=None,
                                   env=env, start_new_session=True, close_fds=True)
        identity = _start_identity(process.pid)
        try:
            process.wait(timeout=max(.001, (deadline-_deployment_process_time.monotonic_ns())/1e9))
        except BaseException:
            stop_owned(process, identity)
            raise _deployment_process_DeploymentTimeout('Deployment deadline or interruption; outcome unconfirmed; run deploy inspect/recover') from None
        if process.returncode:
            raise RuntimeError(f'Deployment worker failed (exit {process.returncode}); run deploy inspect/recover')
        output.seek(0, 2)
        if output.tell() > 1024*1024:
            raise ValueError('Deployment result exceeds limit; outcome unconfirmed')
        output.seek(0)
        return strict_result(output.read().decode('utf-8'))


@_deployment_process_contextmanager
def interruption_guard():
    def interrupted(signum, _frame):
        raise _deployment_process_DeploymentTimeout('Deployment interrupted; outcome unconfirmed; inspect before recovery')
    previous = {s: signal.signal(s, interrupted) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def _bind_supervisor():
    # Linux/WSL process-local setting; no host clock/system configuration changes.
    import ctypes
    expected = int(_deployment_process_os.environ['BIKE_DEPLOYMENT_SUPERVISOR_PID'])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or _deployment_process_os.getppid() != expected:
        raise _deployment_process_DeploymentTimeout('Deployment supervisor disappeared; outcome unconfirmed')


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
                        self.stream.write(_deployment_process_json.dumps(event) + '\n')
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
    diagnostic = _deployment_process_os.fdopen(_deployment_process_os.dup(2), 'w', buffering=1)
    with open(_deployment_process_os.devnull, 'w') as null:
        _deployment_process_os.dup2(null.fileno(), 2)
    _deployment_process_sys.stderr = _SafeStderr(diagnostic)
    # No traceback/native error/credential is sent to the caller.
    with interruption_guard():
        try:
            _bind_supervisor()
            payload = strict_result(_deployment_process_sys.stdin.buffer.read(65537).decode())
            from bike_mlops import cli
            remaining_seconds(OPERATION_SECONDS)
            if payload['mode'] == 'promote':
                result = cli._promote(payload['version'], payload['approver'], payload['reason'],
                                      payload['rollback'], payload['diagnostic'])
            elif payload['mode'] == 'recover':
                with cli._deployment_lock():
                    from bike_mlops.deployment_runtime import deployment_stage
                    with deployment_stage(cli.RUNTIME_DIR, 'recover'):
                        result = cli._recover_deployment()
            else:
                raise ValueError('Invalid deployment operation')
            print(_deployment_process_json.dumps(result, allow_nan=False), flush=True)
        except BaseException as error:
            print(_deployment_process_json.dumps(dict(kind='deployment_worker_failure', error_type=type(error).__name__,
                                  outcome='unconfirmed', action='deploy inspect/recover')), file=_deployment_process_sys.stderr, flush=True)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
