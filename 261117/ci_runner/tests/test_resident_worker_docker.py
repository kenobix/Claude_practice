from contextlib import contextmanager
from datetime import timedelta
import json
import os
import subprocess
import sys
from threading import Event
import time

import psycopg
import pytest

from ci_runner import commit_source, database, execution, fixture_worker, runs
from test_commit_source import GitHubFixture, HEAD
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
        lease = conn.execute('SELECT lease_expires_at-started_at AS lease FROM attempts').fetchone()['lease']
        assert lease >= timedelta(seconds=35)
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


@pytest.mark.parametrize('fault', ['cancel', 'db_stop', 'unknown_commit', 'no_response'])
def test_heartbeat_fault_stops_resident_and_reclaims_running_fixture(dedicated_database, monkeypatch, capsys, fault):
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1':
        pytest.skip('explicit Docker execution opt-in required')
    if fault == 'db_stop' and (os.environ.get('CI_TEST_COMPOSE_PROJECT'), os.environ.get('CI_TEST_COMPOSE_FILE')) != (
            'ci-runner-261117-p0-check', str(ROOT / 'compose.yaml')):
        pytest.skip('database-stop requires the disposable verify_execution.py Compose project')
    compose = ['docker', 'compose', '--project-name', 'ci-runner-261117-p0-check', '-f', str(ROOT / 'compose.yaml')]
    monkeypatch.setattr(commit_source, '_GitHub', lambda: GitHubFixture(HEAD))
    first = runs.create_run(database.connect, HEAD)[0]
    pending = runs.create_run(database.connect, 'f'*40)[0]
    before = resources()
    renew = execution.renew_lease
    active = []
    unblock = Event()
    finished = Event()
    db_stopped = Event()

    @contextmanager
    def commit_without_ack():
        with database.connect() as conn:
            yield conn
        raise psycopg.OperationalError('injected loss after real COMMIT')

    def renew_with_fault(claim, factory):
        try:
            active.extend(subprocess.check_output(
                ['docker', 'ps', '-q', '--filter', f'label=dev.ci-runner.attempt={claim.attempt_id}'],
                text=True, timeout=3).splitlines())
            if fault == 'no_response':
                unblock.wait(timeout=25)
                raise psycopg.OperationalError('injected missing response')
            if fault == 'db_stop':
                subprocess.run(compose + ['stop', '--timeout', '1', 'db'],
                               check=True, capture_output=True, timeout=15)
                db_stopped.set()
            elif fault == 'cancel':
                with database.connect() as conn:
                    conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (claim.run_id,))
            elif fault == 'unknown_commit':
                factory = commit_without_ack
            return renew(claim, factory)
        finally:
            finished.set()

    monkeypatch.setattr(execution, 'renew_lease', renew_with_fault)
    try:
        code = fixture_worker.main(['--serve', '--worker-id', 'heartbeat-integration', '--fixture-mode', 'timeout'])
        if fault == 'no_response':
            assert not finished.is_set()  # Worker exited while DB work was still pending.
    finally:
        unblock.set()
        finished.wait(timeout=20)
        if fault == 'db_stop':
            subprocess.run(compose + ['up', '-d', '--wait', 'db'], check=True, capture_output=True, timeout=60)
    output = capsys.readouterr()
    assert code == 1 and output.out == '', (output.out, output.err)
    diagnostic = json.loads(output.err)
    assert diagnostic['stage'] == 'heartbeat' and diagnostic['run_id'] == str(first.id), diagnostic
    assert finished.is_set() and len(active) == 1
    if fault == 'db_stop':
        assert db_stopped.is_set(), 'verification database did not stop'
    assert resources() == before
    with database.connect() as conn:
        row = conn.execute('SELECT r.state,r.completed_attempt_id,a.id,a.lease_expires_at-a.started_at AS lease '
                           'FROM runs r JOIN attempts a ON a.run_id=r.id WHERE r.id=%s', (first.id,)).fetchone()
        assert row['state'] == 'running' and row['completed_attempt_id'] is None
        assert str(row['id']) == diagnostic['attempt_id']
        if fault == 'unknown_commit':
            assert row['lease'] >= timedelta(seconds=35)
        else:
            assert row['lease'] == timedelta(seconds=30)
        assert conn.execute('SELECT count(*) AS n FROM attempt_logs').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM attempt_sources').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == 1
    assert runs.get_run(database.connect, pending.id).state == 'queued'
