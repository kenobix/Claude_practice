from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
import json
from multiprocessing import get_context
import os
import signal
import subprocess
import sys
from threading import Barrier, Event
from uuid import uuid4

import psycopg
import pytest

from ci_runner import container, database, execution, orphan_cleanup, worker
from ci_runner.expiry_monitor import expire_next
from ci_runner.finalization import FinalizationDenied, finalize_fixture
from ci_runner.preflight import authorize_execution
from ci_runner.retry import schedule_next
from test_create_reservation import create_history
from test_expiry_monitor import set_expiry
from test_finalization_input import report
from test_fixture_worker_docker import resources
from test_preflight import issue_claim
from test_runs_postgres import ROOT, dedicated_database

pytestmark = [pytest.mark.integration, pytest.mark.docker]


@pytest.fixture(autouse=True)
def opt_in():
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1':
        pytest.skip('run scripts/verify_execution.py for DB/Docker recovery checks')


def worker_at_checkpoint(claim, image, checkpoint, notify):
    prepare = container.prepare_fixture
    release = execution._release_workload

    @contextmanager
    def track(*args, **kwargs):
        with prepare(*args, **kwargs) as prepared:
            notify.send(prepared.container_id)
            if checkpoint == 'created':
                Event().wait(30)
            yield prepared

    def track_release(process):
        release(process)
        notify.send('released')
        Event().wait(30)

    container.prepare_fixture = track
    execution._release_workload = track_release
    execution.execute_fixture(claim, 'timeout', prepared_image=image)


@contextmanager
def killed_worker(claim, image, checkpoint):
    # Fork only the trusted Worker harness. The fixed workload stays in Docker.
    ctx = get_context('fork')
    receive, notify = ctx.Pipe(duplex=False)
    with receive, notify:
        worker = ctx.Process(target=worker_at_checkpoint, args=(claim, image, checkpoint, notify))
        worker.start()
        notify.close()
        container_id = None
        try:
            assert receive.poll(12), 'Worker did not create the fixed container'
            container_id = receive.recv()
            if checkpoint == 'released':
                assert receive.poll(6) and receive.recv() == 'released'
            worker.kill()
            worker.join(timeout=5)
            assert worker.exitcode == -signal.SIGKILL
            yield container_id
        finally:
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=5)
            worker.close()
            if container_id is not None:
                container.docker('container', 'rm', '--force', container_id, check=False)


def scan_cli(*, timeout=12):
    return subprocess.run([sys.executable, '-m', 'ci_runner.orphan_cleanup', '--once'],
                          cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def inspect(container_id):
    return json.loads(container.docker('container', 'inspect', container_id).stdout)[0]


def recovery_time(claim):
    with database.connect() as conn:
        return conn.execute('SELECT container_recovered_at FROM attempts WHERE id=%s',
                            (claim.attempt_id,)).fetchone()['container_recovered_at']


def test_worker_killed_after_go_is_recovered_only_after_natural_expiry(dedicated_database):
    before = resources()
    with container.prepare_image() as image:
        run, claim = issue_claim()
        with killed_worker(claim, image, 'released') as orphan:
            assert inspect(orphan)['State']['Running']
            assert expire_next() is None
            assert list(orphan_cleanup.recover()) == []
            assert inspect(orphan)['State']['Running']
            with database.connect() as conn:
                conn.execute('SET LOCAL statement_timeout = 35000')
                conn.execute('SELECT pg_sleep_until(%s)', (claim.lease_expires_at,))
            assert expire_next().reason == 'infra_lease_expired'
            assert inspect(orphan)['State']['Running']
            recovered = scan_cli()
            assert recovered.returncode == 0 and not recovered.stderr
            assert json.loads(recovered.stdout) == {
                'container_id': orphan, 'run_id': str(run.id), 'attempt_id': str(claim.attempt_id)}
            assert orphan_cleanup._inspect(orphan) is None
            confirmed_at = recovery_time(claim)
            assert confirmed_at >= claim.lease_expires_at
            again = scan_cli()
            assert again.returncode == 0 and not again.stdout and not again.stderr
            assert recovery_time(claim) == confirmed_at
            with pytest.raises(FinalizationDenied):
                finalize_fixture(claim, report(claim))
            with database.connect() as conn:
                assert conn.execute('SELECT commit_sha,state,completed_attempt_id FROM runs WHERE id=%s',
                                    (run.id,)).fetchone() == {
                    'commit_sha': run.commit_sha, 'state': 'failed', 'completed_attempt_id': None}
                assert conn.execute('SELECT count(*) AS n FROM attempt_sources').fetchone()['n'] == 0
                assert conn.execute('SELECT count(*) AS n FROM attempt_logs').fetchone()['n'] == 0
            assert container.docker('image', 'inspect', image.tag).returncode == 0
    assert resources() == before


def test_created_orphan_survives_db_stop_then_two_recovery_scans(
        dedicated_database, monkeypatch):
    project, filename = os.environ.get('CI_TEST_COMPOSE_PROJECT'), os.environ.get('CI_TEST_COMPOSE_FILE')
    assert project == 'ci-runner-261117-p0-check' and filename == str(ROOT / 'compose.yaml')
    compose = ['docker', 'compose', '--project-name', project, '-f', filename]
    before = resources()
    with container.prepare_image() as image:
        run, claim = issue_claim()
        permit = authorize_execution(claim)
        with killed_worker(claim, image, 'created') as orphan, container.prepare_fixture(
                replace(permit, attempt_id=uuid4()), prepared_image=image) as unrelated:
            assert inspect(orphan)['State']['Status'] == 'created'
            set_expiry(claim)
            # Elapsed time alone is insufficient. Require the monitor's durable closure.
            assert list(orphan_cleanup.recover()) == []
            with database.connect() as conn:
                conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (run.id,))
            assert expire_next().reason == 'canceled_lease_expired'
            try:
                subprocess.run(compose + ['stop', '--timeout', '1', 'db'],
                               check=True, capture_output=True, timeout=30)
                stopped = scan_cli()
                assert stopped.returncode == 1 and not stopped.stdout
                assert stopped.stderr == 'database unavailable; orphan cleanup stopped\n'
                assert inspect(orphan)['State']['Status'] == 'created'
            finally:
                subprocess.run(compose + ['up', '-d', '--wait', 'db'],
                               check=True, capture_output=True, timeout=60)
            ready, removed = Barrier(2), Event()
            docker = container.docker

            def race(*args, **kwargs):
                if args != ('container', 'rm', '--force', orphan):
                    return docker(*args, **kwargs)
                if ready.wait(timeout=8) == 0:
                    try:
                        return docker(*args, **kwargs)
                    finally:
                        removed.set()
                assert removed.wait(timeout=8)
                return docker(*args, **kwargs)

            with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=2) as pool:
                patch.setattr(container, 'docker', race)
                scans = [pool.submit(lambda: list(orphan_cleanup.recover())) for _ in range(2)]
                receipts = [receipt for scan in scans for receipt in scan.result(timeout=16)]
            assert receipts == [orphan_cleanup.RemovedContainer(orphan, run.id, claim.attempt_id)]
            assert orphan_cleanup._inspect(orphan) is None
            assert recovery_time(claim) is not None
            assert inspect(unrelated.container_id)['State']['Status'] == 'created'
            with database.connect() as conn:
                assert conn.execute('SELECT state FROM runs WHERE id=%s', (run.id,)).fetchone()['state'] == 'canceled'
    assert resources() == before


def test_delayed_create_is_recorded_before_docker_can_list_it(dedicated_database, monkeypatch):
    before = resources()
    docker = container.docker
    dispatched, allow_create, visible, reply = Event(), Event(), Event(), Event()
    ids = []

    def delay_create(*args, **kwargs):
        if args[0] != 'create':
            return docker(*args, **kwargs)
        dispatched.set()
        assert allow_create.wait(15)
        result = docker(*args, **kwargs)
        ids.append(result.stdout.strip())
        visible.set()
        assert reply.wait(15)
        raise container.ContainerRejected('injected create response loss')

    with container.prepare_image() as image, ThreadPoolExecutor(max_workers=1) as pool:
        run, claim = issue_claim()
        monkeypatch.setattr(container, 'docker', delay_create)
        task = pool.submit(execution.execute_fixture, claim, prepared_image=image)
        try:
            assert dispatched.wait(8)
            history = create_history(claim)
            assert history['create_tracked'] and history['create_session'] and history['create_daemon_id']
            assert history['created_container_id'] is None
            set_expiry(claim)
            assert expire_next().attempt_id == claim.attempt_id
            assert list(orphan_cleanup.recover()) == [] and create_history(claim) == history
            assert recovery_time(claim) is None
            allow_create.set()
            assert visible.wait(8)
            (container_id,) = ids
            info = inspect(container_id)
            assert info['State']['Status'] == 'created'
            assert info['Config']['Labels'][container.LABEL] == history['create_session'].hex
            owner = orphan_cleanup._owner(info)
            assert orphan_cleanup._authorize_removal(database.connect, replace(owner, session=uuid4()),
                                                      history['create_daemon_id'], container_id) is None
            with monkeypatch.context() as patch:
                other_daemon = uuid4()
                patch.setattr(container, 'daemon_id', lambda: other_daemon)
                assert list(orphan_cleanup.recover()) == []
            assert create_history(claim) == history

            authorize = orphan_cleanup._authorize_removal

            @contextmanager
            def lost_ack():
                with database.connect() as conn:
                    yield conn
                raise psycopg.OperationalError('injected lost binding commit response')

            with monkeypatch.context() as patch:
                patch.setattr(orphan_cleanup, '_authorize_removal',
                              lambda _, *args: authorize(lost_ack, *args))
                with pytest.raises(psycopg.OperationalError, match='lost binding'):
                    list(orphan_cleanup.recover())
            assert create_history(claim) == {**history, 'created_container_id': container_id}
            assert inspect(container_id)['State']['Status'] == 'created'
            assert recovery_time(claim) is None
            assert list(orphan_cleanup.recover()) == [
                orphan_cleanup.RemovedContainer(container_id, run.id, claim.attempt_id)]
            assert orphan_cleanup._inspect(container_id) is None
            assert recovery_time(claim) is not None
        finally:
            allow_create.set()
            reply.set()
        with pytest.raises(container.ContainerRejected, match='response loss'):
            task.result(timeout=8)
    assert resources() == before


@pytest.mark.parametrize('committed', [False, True], ids=['rollback', 'lost-commit-response'])
def test_recovery_restarts_after_removal_before_db_confirmation(dedicated_database, monkeypatch, committed):
    before = resources()
    confirm = orphan_cleanup._confirm_recovery

    @contextmanager
    def interrupted():
        with database.connect() as conn:
            yield conn
            if not committed:
                raise psycopg.OperationalError('injected recovery rollback')
        raise psycopg.OperationalError('injected recovery commit response loss')

    with container.prepare_image() as image:
        run, claim = issue_claim()
        with killed_worker(claim, image, 'created') as orphan:
            set_expiry(claim)
            assert expire_next().attempt_id == claim.attempt_id
            with monkeypatch.context() as patch:
                patch.setattr(orphan_cleanup, '_confirm_recovery', lambda _, *args: confirm(interrupted, *args))
                with pytest.raises(psycopg.OperationalError, match='injected recovery'):
                    list(orphan_cleanup.recover())
            assert orphan_cleanup._inspect(orphan) is None
            confirmed_at = recovery_time(claim)
            assert (confirmed_at is not None) == committed
            # The next scan must use the stored ID because Docker no longer lists it.
            expected = [] if committed else [orphan_cleanup.RemovedContainer(orphan, run.id, claim.attempt_id)]
            assert list(orphan_cleanup.recover()) == expected
            final_time = recovery_time(claim)
            assert final_time is not None
            if committed:
                assert final_time == confirmed_at
    assert resources() == before


def test_recovery_and_backoff_keep_db_order_when_clock_moves_back(dedicated_database):
    before = resources()
    with container.prepare_image() as image:
        run, claim = issue_claim()
        with killed_worker(claim, image, 'created') as orphan:
            set_expiry(claim, -1)
            assert expire_next().attempt_id == claim.attempt_id
            with database.connect() as conn:
                finished = conn.execute('SELECT finished_at FROM attempts WHERE id=%s',
                                        (claim.attempt_id,)).fetchone()['finished_at']
                future = finished + timedelta(minutes=2)
                conn.execute('UPDATE attempts SET finished_at=%s WHERE id=%s', (future, claim.attempt_id))

            assert list(orphan_cleanup.recover()) == [orphan_cleanup.RemovedContainer(orphan, run.id, claim.attempt_id)]
            assert recovery_time(claim) == future
            assert schedule_next() is None

            # Simulate a recorded recovery still ahead of the current clock after a backward step.
            with database.connect() as conn:
                conn.execute('UPDATE attempts SET finished_at=%s WHERE id=%s', (finished, claim.attempt_id))
            queued = schedule_next()
            assert (queued.attempt_id, queued.available_at) == (claim.attempt_id, future + timedelta(seconds=5))
            assert worker.claim_next('early', target=(run.id, run.commit_sha)) is None
    assert resources() == before


def test_lost_reservation_commit_ack_never_dispatches_create(dedicated_database, monkeypatch):
    before = resources()
    reserve = container.reserve_container_create
    docker = container.docker

    @contextmanager
    def lost_ack():
        with database.connect() as conn:
            yield conn
        raise psycopg.OperationalError('injected lost reservation commit response')

    def unconfirmed(permit, daemon, session, _factory):
        reserve(permit, daemon, session, lost_ack)

    def forbid_create(*args, **kwargs):
        assert args[0] != 'create', 'reservation was not acknowledged'
        return docker(*args, **kwargs)

    with container.prepare_image() as image:
        _, claim = issue_claim()
        monkeypatch.setattr(container, 'reserve_container_create', unconfirmed)
        monkeypatch.setattr(container, 'docker', forbid_create)
        with pytest.raises(psycopg.OperationalError, match='lost reservation'):
            execution.execute_fixture(claim, prepared_image=image)
        assert create_history(claim)['create_session'] is not None
        assert create_history(claim)['created_container_id'] is None
        set_expiry(claim)
        assert expire_next().attempt_id == claim.attempt_id
        assert list(orphan_cleanup.recover()) == []
        assert recovery_time(claim) is None
    assert resources() == before
