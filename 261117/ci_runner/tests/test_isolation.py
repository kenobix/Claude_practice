"""Opt-in real Docker probes; no mocks can satisfy these assertions."""
import errno
import os

import pytest
from isolation_support import IsolationCheck, LOG_LIMIT

pytestmark = pytest.mark.docker


@pytest.fixture(scope='module')
def isolation(request):
    if os.environ.get('CI_RUN_DOCKER_TESTS') != '1':
        pytest.skip('run scripts/verify_isolation.py to enable real Docker probes')
    checker = IsolationCheck()
    try:
        checker.setup()
        yield checker
    finally:
        checker.close(request.session.testsfailed)


def successful(result):
    assert result.record['reason'] == 'completed'
    assert result.record['exit_code'] == 0, result.record
    assert not result.record['oom_killed']
    assert result.record['removed']
    return result.json()


def test_boundary_and_rejected_operations(isolation):
    result = isolation.run('boundary')
    data = successful(result)
    assert data['uid'] == data['gid'] == 65534
    assert int(data['cap_eff'], 16) == int(data['cap_bnd'], 16) == 0
    assert data['no_new_privs'] == '1' and data['seccomp'] == '2'
    assert data['setuid_errno'] == data['raw_socket_errno'] == errno.EPERM
    assert data['readonly_errno'] == errno.EROFS and data['tmp_write']
    assert data['tmp_noexec_errno'] == errno.EACCES
    assert data['network_errno'] == errno.ENETUNREACH
    assert data['interfaces'] == ['lo']
    assert data['docker_socket_errno'] == errno.ENOENT
    assert not data['docker_socket_present'] and not data['host_mount_present']
    assert not data['host_env_inherited']
    cfg = result.record['settings']
    assert cfg['Privileged'] is False
    assert cfg['NetworkMode'] == 'none' and cfg['ReadonlyRootfs'] is True
    assert not cfg['Binds'] and not cfg['PortBindings']
    assert not cfg['Devices'] and not cfg['DeviceRequests']
    assert cfg['PidMode'] == '' and cfg['IpcMode'] == 'private'
    assert cfg['CapDrop'] == ['ALL']
    assert cfg['LogConfig']['Type'] == 'none'
    assert all(m['Type'] == 'tmpfs' and m['Destination'] == '/tmp' for m in result.record['mounts'])


def test_cpu_throttles_under_finite_load(isolation):
    data = successful(isolation.run('cpu'))
    assert data['wall_seconds'] >= 3
    assert data['cpu_ratio'] <= 0.8
    assert data['nr_throttled'] > 0 and data['throttled_usec'] > 0


def test_memory_limit_kills_bounded_allocation(isolation):
    result = isolation.run('memory')
    assert result.record['reason'] == 'completed'
    assert result.record['oom_killed'] is True
    assert result.record['exit_code'] == 137
    assert result.record['removed']


def test_pid_limit_rejects_finite_forks(isolation):
    data = successful(isolation.run('pids'))
    assert data['fork_errno'] == errno.EAGAIN
    assert 0 < data['children'] <= 63
    assert data['pids_current'] == 64 and data['limit_events'] > 0


def test_tmpfs_capacity_rejects_excess_writes(isolation):
    data = successful(isolation.run('tmpfs'))
    assert data['write_errno'] == errno.ENOSPC
    assert data['written_bytes'] <= 16 * 1024 * 1024


def test_wall_timeout_kills_sigterm_ignoring_fixture(isolation):
    result = isolation.run('timeout')
    assert result.record['reason'] == 'timeout'
    assert 10 <= result.record['elapsed_seconds'] < 18
    assert result.record['exit_code'] == 137
    assert not result.record['oom_killed'] and result.record['removed']


def test_log_limit_caps_both_streams_and_stops_container(isolation):
    result = isolation.run('logs')
    assert result.record['reason'] == 'log_limit'
    assert result.record['exit_code'] == 137 and not result.record['oom_killed']
    assert len(result.stdout) > 0 and len(result.stderr) > 0
    assert len(result.stdout) + len(result.stderr) == LOG_LIMIT
    assert sum(result.record['observed_bytes'].values()) > LOG_LIMIT
    assert result.record['settings']['LogConfig']['Type'] == 'none'
    assert result.record['removed']


def test_success_and_check_failure(isolation):
    assert successful(isolation.run('success')) == {'ok': True}
    failed = isolation.run('failure')
    assert failed.record['reason'] == 'completed'
    assert failed.record['exit_code'] == 1 and failed.stderr == b'fixed failure\n'
    assert not failed.record['oom_killed'] and failed.record['removed']


def test_unknown_fixture_is_rejected_before_docker(isolation):
    count = len(isolation.report['cases'])
    with pytest.raises(ValueError, match='fixed P0 probe'):
        isolation.run('unreviewed-command')
    assert len(isolation.report['cases']) == count


def test_preflight_mismatch_refuses_workload(isolation):
    # Stricter memory cap deliberately differs from the verified policy.
    result = isolation.run('preflight_rejection')
    assert result.record['exit_code'] == 1
    assert result.stdout == b''
    assert result.stderr == b'P0 preflight refused: memory.max\n'
    assert not result.record['oom_killed'] and result.record['removed']
