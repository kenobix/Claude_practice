"""Test-only phase/clock traces; print evidence on failure and retain it when requested."""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
import tempfile
from unittest.mock import patch


class FailureTrace:
    def __init__(self, directory: Path, name: str):
        self.path = directory / (name + ".jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Each test invocation gets a unique directory. Never overwrite evidence.
        self.path.touch(exist_ok=False)

    def emit(self, phase: str, **values):
        row = {"phase": phase, "utc": datetime.now(timezone.utc).isoformat(),
               "monotonic_ns": time.monotonic_ns(), "pid": os.getpid(), **values}
        with self.path.open("a") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")

    def rows(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def publish(self, failed: bool):
        if failed:
            print("TEST_DIAGNOSTICS " + self.path.name + "\n" + self.path.read_text(), file=sys.stderr)


def trace_directory(fallback: Path, name: str) -> Path:
    base = Path(os.environ.get("BIKE_MLOPS_TEST_DIAGNOSTICS_DIR", str(fallback)))
    path = base / (name + "-" + uuid.uuid4().hex)
    path.mkdir(parents=True, exist_ok=False)
    return path


@contextmanager
def trace_decision_clocks(trace: FailureTrace):
    """Record the exact clocks used by the real generator and future-time validator."""
    from bike_mlops import capital_deployment_decisions as decisions
    from bike_mlops import capital_deployment_evidence as evidence

    original_check, original_decision = evidence.utc_time, decisions._decision_time

    def observed_datetime(role):
        class ObservedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = datetime.now(tz)
                trace.emit(role, value=value.isoformat())
                return value
        return ObservedDatetime

    def checked(value):
        trace.emit("timestamp_validation_enter", value=value)
        try:
            result = original_check(value)
        except ValueError as error:
            trace.emit("timestamp_validation_rejected", value=value,
                       notes=getattr(error, "__notes__", []))
            raise
        trace.emit("timestamp_validation_passed", value=value)
        return result

    def generated(tags, key):
        trace.emit("decision_time_enter", key=key, reused=key in tags)
        result = original_decision(tags, key)
        trace.emit("decision_time_returned", key=key, value=result)
        return result

    with (patch.object(evidence, "datetime", observed_datetime("validation_clock_now")),
          patch.object(decisions, "datetime", observed_datetime("generation_clock_now")),
          patch.object(evidence, "utc_time", checked), patch.object(decisions, "utc_time", checked),
          patch.object(decisions, "_decision_time", generated)):
        yield


def diagnose_decision_test(test):
    @wraps(test)
    def observed(*args, **kwargs):
        with tempfile.TemporaryDirectory(prefix="decision-clock-diagnostic-") as fallback:
            directory = trace_directory(Path(fallback), test.__name__)
            trace = FailureTrace(directory, "decision")
            failed = True
            try:
                trace.emit("test_enter", test=test.__name__)
                with trace_decision_clocks(trace):
                    result = test(*args, **kwargs)
                trace.emit("test_passed")
                failed = False
                return result
            except BaseException as error:
                trace.emit("test_raised", error_type=type(error).__name__,
                           notes=getattr(error, "__notes__", []))
                raise
            finally:
                trace.publish(failed)
    return observed
