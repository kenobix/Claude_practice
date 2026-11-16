"""Test-only Uvicorn factory. Not imported by production code or configurable over HTTP."""
from contextlib import contextmanager
import os
from pathlib import Path
import time

from ci_runner.app import create_app
from ci_runner.database import connect


def make_app():
    @contextmanager
    def fault_connection():
        def barrier(position):
            if os.environ['CI_FAULT_POSITION'] == position:
                Path(os.environ['CI_FAULT_MARKER']).touch()
                while True:
                    time.sleep(0.05)  # test sends SIGKILL to the entire API process
        with connect() as conn:
            yield conn
            barrier('before_commit')
        barrier('after_commit')
    return create_app(fault_connection)
