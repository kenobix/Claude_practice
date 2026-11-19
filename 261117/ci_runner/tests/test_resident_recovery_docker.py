from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
import json
from multiprocessing import get_context
from multiprocessing.connection import wait
import os
import signal
import subprocess
import sys
import time

import pytest

from ci_runner import commit_source, container, database, execution, finalization, orphan_cleanup, runs
from test_commit_source import HEAD
from test_finalization_input import logged_report
from test_fixture_worker_docker import resources
from test_orphan_cleanup_docker import inspect, scan_cli
from test_resident_worker_docker import resident_process, wait_for_receipt
from test_runs_postgres import ROOT, dedicated_database

pytestmark = [pytest.mark.integration, pytest.mark.docker]


def pause_resident(phase, notify, release=None):
    execute = execution.execute_fixture
    send_go = execution._release_workload
    finalize = finalization.finalize_fixture
    current = image = None

    def stop_here(result=None):
        notify.send((current, image, result))
        if release is None:
            time.sleep(90)  # The parent kills this process at the checkpoint.
            raise AssertionError('fault checkpoint timed out')
        assert release.wait(30), 'stale report check timed out'

    def track_execution(claim, *args, prepared_image, **kwargs):
        nonlocal current, image
        current, image = claim, prepared_image
        if phase == 'claim':
            stop_here()
        return execute(claim, *args, prepared_image=prepared_image, **kwargs)

    def track_release(process):
        send_go(process)
        stop_here()

    def track_finalization(claim, result, *args, **kwargs):
        stop_here(result)
        return finalize(claim, result, *args, **kwargs)

    execution.execute_fixture = track_execution
    if phase == 'running':
        execution._release_workload = track_release
    if phase == 'report':
        finalization.finalize_fixture = track_finalization


@contextmanager
def watch_expiry(tmp_path):
    path = tmp_path / 'expiry.out'
    with path.open('w') as output, (tmp_path / 'expiry.err').open('w+') as errors:
        proc = subprocess.Popen([sys.executable, '-m', 'ci_runner.expiry_monitor', '--watch'],
                                cwd=ROOT, stdout=output, stderr=errors)
        try:
            def wait_for(reason):
                deadline = time.monotonic() + 40
                while proc.poll() is None and time.monotonic() < deadline:
                    for line in path.read_text().splitlines(keepends=True):
                        if line.endswith('\n') and (event := json.loads(line))['reason'] == reason:
                            return event
                    time.sleep(0.05)
                errors.seek(0)
                pytest.fail(f'no expiry event {reason}: {errors.read()}')

            yield wait_for
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


def read_history(run_id):
    with database.connect() as conn:
        return conn.execute(
            'SELECT a.id, a.attempt_no, a.worker_id, a.state, a.reason, a.exit_code, a.started_at, '
            'a.lease_expires_at, a.finished_at, a.created_container_id, a.create_session, '
            'a.container_recovered_at, s.commit_sha, s.source_sha256, s.image_id, l.stdout '
            'FROM attempts a LEFT JOIN attempt_sources s ON s.attempt_id=a.id '
            'LEFT JOIN attempt_logs l ON l.attempt_id=a.id WHERE a.run_id=%s ORDER BY a.attempt_no',
            (run_id,),
        ).fetchall()


@pytest.mark.parametrize('phase', ['claim', 'running', 'report'])
def test_two_residents_recover_after_sigkill(dedicated_database, tmp_path, phase):
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1':
        pytest.skip('run scripts/verify_execution.py for DB/Docker recovery checks')
    run = runs.create_run(database.connect, HEAD)[0]
    before = resources()
    ctx = get_context('fork')
    resume = ctx.Event()
    replacement_output = tmp_path / 'replacement'
    old_read, old_write = ctx.Pipe(duplex=False)
    new_read, new_write = ctx.Pipe(duplex=False)
    failed_setup = partial(pause_resident, phase, old_write)
    replacement_setup = partial(pause_resident, 'report', new_write, resume)
    killed = False
    prior = None
    try:
        with old_read, old_write, new_read, new_write, watch_expiry(tmp_path) as event, resident_process(
            ctx, 'failed-resident', tmp_path / 'failed', configure=failed_setup,
            mode='timeout' if phase == 'running' else 'success',
        ) as failed:
            assert old_read.poll(35), f'Worker did not reach {phase}'
            claim, image, result = old_read.recv()
            assert claim.run_id == run.id and claim.attempt_no == 1
            stale = result if result is not None else replace(
                logged_report(claim), image_id=image.image_id, verified_source=image.verified_source)
            prior, = read_history(run.id)
            assert prior['state'] == 'running' and prior['lease_expires_at'] == claim.lease_expires_at
            orphan = prior['created_container_id']
            if phase == 'running':
                assert inspect(orphan)['State']['Running']
            elif phase == 'report':
                assert result is not None and result.reason == 'completed'
                assert orphan_cleanup._inspect(orphan) is None
            else:
                assert orphan is None and prior['create_session'] is None

            with resident_process(ctx, 'replacement', replacement_output, configure=replacement_setup) as healthy:
                try:
                    assert healthy.is_alive()
                    with database.connect() as conn:
                        stopped_at = conn.execute('SELECT clock_timestamp() AS now').fetchone()['now']
                    failed.kill()
                    killed = True
                    failed.join(timeout=5)
                    assert failed.exitcode == -signal.SIGKILL
                    if phase == 'running':
                        early = scan_cli()
                        assert early.returncode == 0 and not early.stdout and not early.stderr
                        assert inspect(orphan)['State']['Running']

                    expired = event('infra_lease_expired')
                    assert expired == {'run_id': str(run.id), 'attempt_id': str(claim.attempt_id),
                                       'state': 'failed', 'reason': 'infra_lease_expired'}
                    closed, = read_history(run.id)
                    assert closed['finished_at'] >= prior['lease_expires_at']
                    assert closed['container_recovered_at'] is None and closed['exit_code'] is None
                    if orphan is not None:
                        assert runs.get_run(database.connect, run.id).state == 'failed'
                        recovered = scan_cli()
                        assert recovered.returncode == 0 and not recovered.stderr
                        assert json.loads(recovered.stdout) == {
                            'container_id': orphan, 'run_id': str(run.id), 'attempt_id': str(claim.attempt_id)}
                        assert orphan_cleanup._inspect(orphan) is None
                        closed, = read_history(run.id)
                        assert closed['container_recovered_at'] >= closed['finished_at']

                    scheduled = event('infra_retry_scheduled')
                    assert (scheduled['run_id'], scheduled['attempt_id'], scheduled['next_attempt_no']) == (
                        str(run.id), str(claim.attempt_id), 2)
                    available_at = datetime.fromisoformat(scheduled['available_at'])
                    assert available_at >= (closed['container_recovered_at'] or closed['finished_at']) + timedelta(seconds=5)
                    # This harness wait includes backoff, the Docker build and result preparation.
                    # Recovery time is measured separately from SIGKILL to the official result.
                    ready = wait([new_read, healthy.sentinel], timeout=60)
                    assert new_read in ready, f'replacement missed report checkpoint (exit={healthy.exitcode})'
                    new_claim, new_image, new_result = new_read.recv()
                    assert new_claim.run_id == run.id and new_claim.attempt_no == 2
                    assert new_claim.started_at >= available_at
                    assert new_claim.lease_expires_at - new_claim.started_at == timedelta(seconds=30)
                    current = runs.get_run(database.connect, run.id)
                    assert current.state == 'running' and current.completed_attempt_id is None
                    with pytest.raises(finalization.FinalizationDenied, match='not current'):
                        finalization.finalize_fixture(claim, stale)
                    assert read_history(run.id)[0] == closed
                finally:
                    resume.set()

                receipt = wait_for_receipt(healthy, replacement_output)
                assert (receipt['run_id'], receipt['attempt_id'], receipt['state'], receipt['reason']) == (
                    str(run.id), str(new_claim.attempt_id), 'succeeded', 'commit_success')
                healthy.terminate()
                healthy.join(timeout=10)
                assert healthy.exitcode == 0
    finally:
        if killed and phase != 'report':
            if prior['created_container_id'] is not None:
                container.remove_owned(prior['created_container_id'], prior['create_session'].hex)
            # The production sweeper handles containers. Remove only this test's known image.
            container.inspect_prepared_image(image)
            container.docker('image', 'rm', image.tag)

    assert not (tmp_path / 'failed.out').read_text()
    assert not (tmp_path / 'failed.err').read_text()
    assert not replacement_output.with_suffix('.err').read_text()
    current = runs.get_run(database.connect, run.id)
    assert current.commit_sha == run.commit_sha and current.completed_attempt_id == new_claim.attempt_id
    with pytest.raises(finalization.FinalizationDenied, match='not current'):
        finalization.finalize_fixture(claim, stale)
    expired, official = read_history(run.id)
    assert expired == closed and expired['stdout'] is None and expired['source_sha256'] is None
    assert (official['id'], official['state'], official['worker_id'], official['exit_code']) == (
        current.completed_attempt_id, 'succeeded', 'replacement', 0)
    assert (official['commit_sha'], official['source_sha256'], official['image_id'], official['stdout']) == (
        run.commit_sha, commit_source.DIGEST, new_image.image_id, new_result.stdout)
    assert json.loads(official['stdout']) == {'ok': True}
    with database.connect() as conn:
        assert conn.execute('SELECT count(DISTINCT lease_token) AS total FROM attempts WHERE run_id=%s',
                            (run.id,)).fetchone()['total'] == 2
    assert resources() == before
    print(f'P2 {phase}: SIGKILL to official result {(official["finished_at"] - stopped_at).total_seconds():.2f}s')
