import json
import os
import subprocess
import sys
import time

import pytest

from ci_runner import commit_source, database, runs
from test_fixture_worker_docker import resources
from test_runs_postgres import ROOT, api, dedicated_database, payload
from test_verified_execution_docker import PUBLISHED

pytestmark = [pytest.mark.integration, pytest.mark.docker]


def test_resident_sigterm_drains_real_verified_commit_and_leaves_next_run_queued(dedicated_database):
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1' or os.environ.get('CI_RUN_LIVE_COMMIT_SOURCE') != '1':
        pytest.skip('explicit Docker and anonymous GitHub acquisition opt-in required')

    before = resources()
    with api() as (client, _):
        accepted = client.post('/runs', json=payload(PUBLISHED))
        assert accepted.status_code == 201
        run_id = accepted.json()['id']

    proc = subprocess.Popen(
        [sys.executable, '-m', 'ci_runner.fixture_worker', '--serve',
         '--worker-id', 'resident-integration', '--fixture-mode', 'timeout'],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    observed_running = False
    try:
        limit = time.monotonic() + 100
        while proc.poll() is None and time.monotonic() < limit:
            listing = subprocess.check_output(
                ['docker', 'ps', '-q', '--filter', f'label=dev.ci-runner.run={run_id}'],
                text=True, timeout=5)
            if listing.strip():
                observed_running = True
                break
            time.sleep(0.1)
        if observed_running:
            pending = runs.create_run(database.connect, 'f'*40)[0]
            proc.terminate()
        else:
            # Stop only this test's Worker if the expected container never starts.
            if proc.poll() is None:
                proc.terminate()
        stdout, stderr = proc.communicate(timeout=40)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)
    assert observed_running, stderr
    assert proc.returncode == 0 and not stderr, stderr
    lines = stdout.splitlines()
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert result['run_id'] == run_id
    assert (result['state'], result['exit_code'], result['reason']) == ('timed_out', 137, 'commit_timeout')
    with database.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == 1
        saved = conn.execute('SELECT commit_sha, source_sha256, image_id FROM attempt_sources WHERE run_id=%s',
                             (run_id,)).fetchone()
        assert saved['commit_sha'] == PUBLISHED and saved['source_sha256'] == commit_source.DIGEST
        assert saved['image_id'].startswith('sha256:')
    assert runs.get_run(database.connect, pending.id).state == 'queued'
    with api() as (client, _):
        detail = client.get(f'/runs/{run_id}').json()
        assert detail['completed_attempt_id'] == result['attempt_id'] and detail['state'] == 'timed_out'
        assert client.get(f'/runs/{run_id}/logs?format=html').status_code == 200
    assert resources() == before
