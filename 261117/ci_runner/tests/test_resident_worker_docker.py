from contextlib import contextmanager, nullcontext, redirect_stderr, redirect_stdout
from datetime import timedelta
import json
from multiprocessing import get_context
import os
import signal
import subprocess
import sys
from threading import Event
import time

import psycopg
import pytest

from ci_runner import commit_source, database, execution, finalization, fixture_worker, runs, worker
from ci_runner.expiry_monitor import expire_next
from test_commit_source import GitHubFixture, HEAD, OLDER
from test_finalization_input import report
from test_fixture_worker_docker import resources
from test_runs_postgres import ROOT, api, dedicated_database, payload
from test_verified_execution_docker import PUBLISHED

pytestmark = [pytest.mark.integration, pytest.mark.docker]


def resident_at_queue_checkpoint(worker_id, output, pause=None, *, configure=None, mode='success'):
    # Replace only the GitHub transport with fixed responses; DB and Docker are real.
    verify = commit_source._verify
    claim_next = worker.claim_next

    def verify_fixture(run_id, sha, _transport):
        source = verify(run_id, sha, GitHubFixture(sha))
        if pause and pause[0] == 'source':
            wait_at_checkpoint()
        return source

    def claim(*args, **kwargs):
        if pause and pause[0] == 'claim':
            wait_at_checkpoint()
        return claim_next(*args, **kwargs)

    def wait_at_checkpoint():
        _, reached, release, _ = pause
        if not reached.is_set():
            reached.set()
            assert release.wait(60), 'test did not release contending Worker'

    commit_source._verify = verify_fixture
    worker.claim_next = claim
    if pause:
        matches_target = worker.matches_verified_target

        def confirm(target):
            matches = matches_target(target)
            assert matches
            pause[3].set()
            return matches

        worker.matches_verified_target = confirm
    if configure is not None:
        configure()
    with (output.with_suffix('.out').open('w') as stdout, output.with_suffix('.err').open('w') as stderr,
          redirect_stdout(stdout), redirect_stderr(stderr)):
        raise SystemExit(fixture_worker.main(['--serve', '--worker-id', worker_id, '--fixture-mode', mode]))


@contextmanager
def resident_process(ctx, worker_id, output, pause=None, **options):
    proc = ctx.Process(target=resident_at_queue_checkpoint, args=(worker_id, output, pause), kwargs=options)
    proc.start()
    try:
        yield proc
    finally:
        if pause:
            pause[2].set()
        if proc.is_alive():
            # Drain a possible in-flight claim before the disposable DB is truncated.
            proc.terminate()
            proc.join(timeout=30)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=5)
        proc.close()


def wait_for_receipt(proc, output):
    stdout, stderr = output.with_suffix('.out'), output.with_suffix('.err')
    deadline = time.monotonic() + 45
    while proc.is_alive() and time.monotonic() < deadline:
        if stdout.exists() and (line := stdout.read_text()).endswith('\n'):
            return json.loads(line)
        time.sleep(0.05)
    pytest.fail(f'no success receipt from {proc.name}: {stderr.read_text() if stderr.exists() else ""}')


@pytest.mark.parametrize('phase', ['source', 'claim'])
def test_two_residents_continue_after_contending_for_same_run(dedicated_database, tmp_path, phase):
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1':
        pytest.skip('explicit Docker execution opt-in required')
    first = runs.create_run(database.connect, HEAD)[0]
    before = resources()
    ctx = get_context('fork')
    reached, release, confirmed = ctx.Event(), ctx.Event(), ctx.Event()
    loser_output, winner_output = tmp_path / 'loser', tmp_path / 'winner'

    with resident_process(ctx, 'contender-loser', loser_output, (phase, reached, release, confirmed)) as loser:
        assert reached.wait(35), f'loser did not reach {phase} checkpoint'
        with resident_process(ctx, 'contender-winner', winner_output) as winner:
            first_receipt = wait_for_receipt(winner, winner_output)
            assert first_receipt['run_id'] == str(first.id) and first_receipt['state'] == 'succeeded'
            winner.terminate()
            winner.join(timeout=15)
            assert winner.exitcode == 0
        release.set()
        assert confirmed.wait(12), 'loser did not confirm the original target after losing the race'
        assert loser.is_alive()
        assert resources() == before  # A losing build is gone before the next poll.
        second = runs.create_run(database.connect, OLDER)[0]
        second_receipt = wait_for_receipt(loser, loser_output)
        assert second_receipt['run_id'] == str(second.id) and second_receipt['state'] == 'succeeded'
        loser.terminate()
        loser.join(timeout=15)
        assert loser.exitcode == 0

    for output in (winner_output, loser_output):
        assert output.with_suffix('.err').read_text() == ''
        assert len(output.with_suffix('.out').read_text().splitlines()) == 1
    with database.connect() as conn:
        recorded = conn.execute(
            'SELECT r.id, r.commit_sha, r.completed_attempt_id, a.id AS attempt_id, a.attempt_no, a.worker_id, '
            's.commit_sha AS source_commit_sha, s.source_sha256, s.image_id, l.attempt_id AS logs_attempt_id '
            'FROM runs r JOIN attempts a ON a.run_id=r.id LEFT JOIN attempt_sources s ON s.attempt_id=a.id '
            'LEFT JOIN attempt_logs l ON l.attempt_id=a.id WHERE r.id IN (%s, %s) ORDER BY r.created_at',
            (first.id, second.id),
        ).fetchall()
    assert len(recorded) == 2
    for row, run, receipt, owner in zip(recorded, (first, second), (first_receipt, second_receipt),
                                        ('contender-winner', 'contender-loser')):
        assert row['id'] == run.id and row['worker_id'] == owner and row['attempt_no'] == 1
        assert row['commit_sha'] == row['source_commit_sha'] == run.commit_sha
        assert row['source_sha256'] == commit_source.DIGEST and row['image_id'].startswith('sha256:')
        assert row['completed_attempt_id'] == row['attempt_id'] == row['logs_attempt_id']
        assert str(row['attempt_id']) == receipt['attempt_id']
    assert resources() == before


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
    cancellation = []
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
                path = f'/runs/{claim.run_id}/cancel'
                response = client.put(path)
                assert response.status_code == 202
                requested = response.json()
                assert requested['state'] == 'running' and requested['cancel_requested_at']
                repeat = client.put(path)
                assert repeat.status_code == 202 and repeat.json() == requested
                cancellation.append((claim, requested))
            elif fault == 'unknown_commit':
                factory = commit_without_ack
            return renew(claim, factory)
        finally:
            finished.set()

    monkeypatch.setattr(execution, 'renew_lease', renew_with_fault)
    with (api() if fault == 'cancel' else nullcontext((None, None))) as (client, _):
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
    if fault == 'cancel':
        (claim, requested), = cancellation
        with pytest.raises(finalization.FinalizationDenied):
            finalization.finalize_fixture(claim, report(claim))
        with database.connect() as conn:
            conn.execute('SET LOCAL statement_timeout = 35000')
            conn.execute('SELECT pg_sleep_until(%s)', (claim.lease_expires_at,))
        closed = expire_next()
        assert (closed.attempt_id, closed.state, closed.reason) == (
            claim.attempt_id, 'canceled', 'canceled_lease_expired',
        )
        with api() as (client, _):
            canceled = client.put(f'/runs/{first.id}/cancel')
            assert canceled.status_code == 200
            assert canceled.json()['state'] == 'canceled'
            assert canceled.json()['cancel_requested_at'] == requested['cancel_requested_at']
            attempt, = client.get(f'/runs/{first.id}/attempts').json()['items']
            assert (attempt['state'], attempt['reason'], attempt['official']) == (
                'canceled', 'canceled_lease_expired', False,
            )
