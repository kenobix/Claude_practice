"""Dedicated PostgreSQL plus real restricted Docker fixtures, explicit opt-in."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import selectors

import psycopg
import pytest

from ci_runner import container, database, execution
from ci_runner.preflight import ExecutionDenied
from test_preflight import issue_claim
from test_runs_postgres import dedicated_database

pytestmark = [pytest.mark.integration, pytest.mark.docker]
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module', autouse=True)
def evidence(request):
    report = {'cases': []}
    yield report
    if os.environ.get('CI_RUN_EXECUTION_TESTS') == '1':
        sources = [ROOT / 'src/ci_runner' / name for name in
                   ('execution.py', 'container.py', 'preflight.py')]
        sources += [*sorted((ROOT / 'fixtures/job').iterdir()), Path(__file__),
                    ROOT / 'scripts/verify_execution.py']
        report.update(finished_at=datetime.now(timezone.utc).isoformat(),
                      git_head_at_execution=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                      source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in sources if p.is_file()},
                      pytest_failures=request.session.testsfailed)
        path = ROOT / 'artifacts/execution-latest.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + '\n')


def record_result(evidence, case, result):
    evidence['cases'].append({
        'case': case, 'reason': result.reason, 'exit_code': result.exit_code,
        'released': result.released, 'elapsed_seconds': result.elapsed_seconds,
        'retained_stdout': len(result.stdout), 'retained_stderr': len(result.stderr),
        'observed_bytes': result.observed_bytes, 'truncated': result.truncated,
        'verified_limits': result.verified_limits, 'image_id': result.image_id})


@pytest.fixture(autouse=True)
def runtime_opt_in():
    if os.environ.get('CI_RUN_EXECUTION_TESTS') != '1':
        pytest.skip('run scripts/verify_execution.py for DB/Docker execution checks')


@pytest.fixture
def owned_resources(monkeypatch, evidence):
    real = container.docker
    names, tags = [], []
    def observe(*args, **kwargs):
        if args[0] == 'build':
            tags.append(args[args.index('--tag') + 1])
        if args[0] == 'create':
            names.append(args[args.index('--name') + 1])
        return real(*args, **kwargs)
    monkeypatch.setattr(container, 'docker', observe)
    yield names
    assert names and tags
    for name in names:
        assert real('inspect', name, check=False).returncode != 0
    for tag in tags:
        assert real('image', 'inspect', tag, check=False).returncode != 0
    evidence.setdefault('cleanup_checks', []).append({'containers_removed': len(names), 'tags_removed': len(tags)})


@pytest.mark.parametrize('mode,reason,exit_code', [
    ('success', 'completed', 0), ('failure', 'check_failure', 1),
    ('timeout', 'timeout', 137), ('logs', 'log_limit', 137)])
def test_four_bounded_fixtures(dedicated_database, owned_resources, evidence, mode, reason, exit_code):
    _, claim = issue_claim()
    try:
        result = execution.execute_fixture(claim, mode)
    except ExecutionDenied as exc:
        with database.connect() as conn:
            diagnostic = conn.execute(
                'SELECT r.state AS run_state,a.state AS attempt_state,'
                'r.cancel_requested_at IS NOT NULL AS canceled,'
                'a.lease_expires_at,clock_timestamp() AS db_now,'
                'a.lease_token=%s AS token_matches FROM runs r '
                'JOIN attempts a ON a.run_id=r.id WHERE a.id=%s',
                (claim.lease_token, claim.attempt_id)).fetchone()
        raise AssertionError(f"fixture authorization denied: {diagnostic}") from exc
    record_result(evidence, mode, result)
    assert result.reason == reason and result.exit_code == exit_code
    assert result.released and result.verified_limits == execution.EXPECTED_LIMITS
    assert len(result.stdout) + len(result.stderr) <= execution.LOG_LIMIT
    if mode == 'success':
        assert json.loads(result.stdout) == {'ok': True} and not result.stderr
    elif mode == 'failure':
        assert result.stderr == b'fixed failure\n' and not result.stdout
    elif mode == 'timeout':
        assert 10 <= result.elapsed_seconds < 18
    else:
        assert result.stdout and result.stderr and result.truncated
        assert len(result.stdout) + len(result.stderr) == execution.LOG_LIMIT
    # Execution does not pretend that the DB result was finalized.
    with database.connect() as conn:
        assert conn.execute('SELECT state FROM attempts WHERE id=%s', (claim.attempt_id,)).fetchone()['state'] == 'running'


@pytest.mark.parametrize('fault', ['cancel', 'db_error', 'expired', 'db_stop'])
def test_after_guard_db_recheck_refuses_release(dedicated_database, owned_resources, evidence, monkeypatch, fault):
    run, claim = issue_claim()
    calls = 0
    compose = None
    if fault == 'db_stop':
        project, filename = os.environ.get('CI_TEST_COMPOSE_PROJECT'), os.environ.get('CI_TEST_COMPOSE_FILE')
        assert project == 'ci-runner-261117-p0-check' and filename == str(ROOT / 'compose.yaml')
        compose = ['docker', 'compose', '--project-name', project, '-f', filename]
    @contextmanager
    def factory():
        nonlocal calls
        calls += 1
        if calls == 3:
            if fault == 'db_error':
                raise psycopg.OperationalError('public injected DB disconnect after guard')
            if fault == 'db_stop':
                subprocess.run(compose + ['stop', 'db'], check=True, capture_output=True, timeout=30)
            else:
                with database.connect() as conn:
                    if fault == 'cancel':
                        conn.execute('UPDATE runs SET cancel_requested_at=clock_timestamp() WHERE id=%s', (run.id,))
                    else:
                        conn.execute("UPDATE attempts SET started_at=clock_timestamp()-interval '1 minute', "
                                 "lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (claim.attempt_id,))
        with database.connect() as conn:
            yield conn
    monkeypatch.setattr(execution, '_release_workload', lambda p: pytest.fail('invalid authority released workload'))
    try:
        with pytest.raises(psycopg.OperationalError if fault in ('db_error', 'db_stop') else ExecutionDenied):
            execution.execute_fixture(claim, connection_factory=factory)
        assert calls == 3
        evidence['cases'].append({'case': fault, 'released': False, 'db_checks': calls})
    finally:
        if compose:
            subprocess.run(compose + ['up', '-d', '--wait', 'db'], check=True, capture_output=True, timeout=60)


def test_actual_cgroup_mismatch_never_releases_workload(dedicated_database, owned_resources, evidence, monkeypatch):
    _, claim = issue_claim()
    real_docker = container.docker
    validate = container.validate_configuration
    def smaller_memory(*args, **kwargs):
        if args[0] == 'create':
            args = list(args)
            for option in ('--memory', '--memory-swap'):
                args[args.index(option) + 1] = '64m'
        return real_docker(*args, **kwargs)
    def bypass_inspect_memory_only(info, image, labels, mode):
        # Deliberately bypass just the inspect check to exercise the independent
        # in-container cgroup guard. Actual kernel memory.max remains 64MiB.
        copy = {**info, 'HostConfig': {**info['HostConfig'], 'Memory': 134217728, 'MemorySwap': 134217728}}
        validate(copy, image, labels, mode)
    monkeypatch.setattr(container, 'docker', smaller_memory)
    monkeypatch.setattr(container, 'validate_configuration', bypass_inspect_memory_only)
    monkeypatch.setattr(execution, '_release_workload', lambda p: pytest.fail('mismatched cgroup released workload'))
    result = execution.execute_fixture(claim)
    record_result(evidence, 'actual_memory_mismatch', result)
    assert not result.released and not result.verified_limits
    assert result.reason == 'runtime_preflight_rejected' and result.exit_code == 78
    assert result.stdout == b'' and result.stderr == b'runtime preflight refused\n'


def test_db_remaining_lease_caps_local_execution(dedicated_database, owned_resources, evidence):
    _, claim = issue_claim()
    calls = 0
    @contextmanager
    def short_lease():
        nonlocal calls
        calls += 1
        if calls == 2:
            with database.connect() as conn:
                conn.execute("UPDATE attempts SET lease_expires_at=clock_timestamp()+interval '3 seconds' WHERE id=%s", (claim.attempt_id,))
        with database.connect() as conn:
            yield conn
    result = execution.execute_fixture(claim, 'timeout', short_lease)
    record_result(evidence, 'short_db_lease', result)
    assert result.released and result.reason == 'lease_expired' and result.exit_code == 137
    assert 2 <= result.elapsed_seconds < 8


def test_guard_wait_is_bounded_without_go(dedicated_database, owned_resources, evidence):
    from test_container import permit
    with container.prepare_fixture(permit()) as prepared:
        process = subprocess.Popen(['docker', 'start', '--attach', '--interactive', prepared.container_id],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            # An open host stdin may keep the interactive CLI alive even after
            # container exit. Inspect daemon state before closing that stdin.
            # Synchronize on the guard acknowledgement: Docker wait on a
            # not-yet-started container can return its initial exit code 0.
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                assert ready.select(timeout=5), 'guard acknowledgement missing'
                acknowledgement = process.stdout.readline(4096)
            assert json.loads(acknowledgement)['limits'] == execution.EXPECTED_LIMITS
            assert container.docker('wait', prepared.container_id, timeout=18).stdout.strip() == '124'
            info = json.loads(container.docker('inspect', prepared.container_id).stdout)[0]
            assert info['State']['ExitCode'] == 124 and not info['State']['Running']
            assert info['State']['Pid'] == 0
            process.stdin.close()
            process.wait(timeout=5)
            assert process.stdout.read() == b''
            assert process.stderr.read() == b''
            evidence['cases'].append({'case': 'guard_without_go', 'exit_code': 124, 'released': False})
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
