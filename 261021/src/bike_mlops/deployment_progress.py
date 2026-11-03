"""Process-local elapsed time and conservative Docker timeout diagnostics."""
from __future__ import annotations

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
