"""Test-only subprocess killed at a finalization commit barrier."""
from contextlib import contextmanager
import os
from pathlib import Path
import time

from ci_runner import database
from ci_runner.finalization import finalize_fixture
from ci_runner.worker import Claim
from test_finalization_input import report


def barrier(position):
    if os.environ['CI_FAULT_POSITION'] == position:
        Path(os.environ['CI_FAULT_MARKER']).touch()
        while True:
            time.sleep(0.02)


@contextmanager
def fault_connection():
    with database.connect() as conn:
        yield conn
        barrier('before_commit')
    barrier('after_commit')


if __name__ == '__main__':
    with database.connect() as conn:
        row = conn.execute('SELECT id AS attempt_id,run_id,attempt_no,worker_id,lease_token,'
                           'started_at,lease_expires_at FROM attempts WHERE run_id=%s',
                           (os.environ['CI_FINALIZE_RUN_ID'],)).fetchone()
    claim = Claim(**row)
    finalize_fixture(claim, report(claim), fault_connection)
