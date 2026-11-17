from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ci_runner import container
from ci_runner.preflight import ExecutionPermit


def permit():
    return ExecutionPermit(uuid4(), uuid4(), 1, 'worker-1', uuid4(),
                           datetime.now(timezone.utc), 'kenobix/Claude_practice',
                           'refs/heads/main', 'a' * 40, 'fixture-v1')


def configuration():
    image = {'Id': 'sha256:' + 'b' * 64, 'Config': {'Env': ['PATH=/usr/bin']}}
    labels = {container.LABEL: 'session'}
    info = {
        'Id': 'c' * 64, 'Image': image['Id'], 'Mounts': [],
        'State': {'Status': 'created', 'Pid': 0, 'Running': False},
        'Config': {'User': '65534:65534', 'WorkingDir': '/tmp',
                   'Entrypoint': container.ENTRYPOINT.copy(), 'Cmd': None,
                   'Env': image['Config']['Env'].copy(), 'Labels': labels.copy()},
        'HostConfig': {'NanoCpus': 500_000_000, 'Memory': 134217728,
                       'MemorySwap': 134217728, 'PidsLimit': 64, 'NetworkMode': 'none',
                       'ReadonlyRootfs': True, 'Privileged': False, 'CapDrop': ['ALL'],
                       'SecurityOpt': ['no-new-privileges=true'], 'Tmpfs': container.TMPFS.copy(),
                       'LogConfig': {'Type': 'none', 'Config': {}}, 'PidMode': '',
                       'IpcMode': 'private', 'CgroupnsMode': 'private',
                       'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}},
    }
    return info, image, labels


@pytest.mark.parametrize('section,key,value', [
    ('HostConfig', 'NanoCpus', 0), ('HostConfig', 'Memory', 0),
    ('HostConfig', 'MemorySwap', -1), ('HostConfig', 'PidsLimit', -1),
    ('HostConfig', 'NetworkMode', 'bridge'), ('HostConfig', 'ReadonlyRootfs', False),
    ('HostConfig', 'Privileged', True), ('HostConfig', 'CapDrop', []),
    ('HostConfig', 'CapAdd', ['SYS_ADMIN']), ('HostConfig', 'SecurityOpt', []),
    ('HostConfig', 'Tmpfs', {'/tmp': 'rw,size=1g'}),
    ('HostConfig', 'Binds', ['/var/run/docker.sock:/var/run/docker.sock']),
    ('HostConfig', 'Devices', [{'PathOnHost': '/dev/sda'}]),
    ('HostConfig', 'PortBindings', {'80/tcp': [{'HostPort': '8080'}]}),
    ('HostConfig', 'PidMode', 'host'), ('HostConfig', 'IpcMode', 'host'),
    ('HostConfig', 'CgroupnsMode', 'host'),
    ('HostConfig', 'LogConfig', {'Type': 'json-file', 'Config': {}}),
    ('HostConfig', 'RestartPolicy', {'Name': 'always', 'MaximumRetryCount': 0}),
    ('Config', 'User', '0'), ('Config', 'Entrypoint', ['/bin/sh']),
    ('Config', 'Cmd', ['unreviewed']), ('Config', 'Env', ['SECRET=public-test-canary']),
    ('Config', 'Volumes', {'/host': {}}), ('Config', 'Labels', {}),
    ('State', 'Status', 'running'), ('State', 'Pid', 1),
])
def test_dangerous_or_missing_configuration_is_rejected(section, key, value):
    info, image, labels = configuration()
    container.validate_configuration(info, image, labels)
    info[section][key] = value
    with pytest.raises(container.ContainerRejected):
        container.validate_configuration(info, image, labels)


def test_unknown_image_mounts_and_incomplete_inspection_are_rejected():
    info, image, labels = configuration()
    for changed in ({**info, 'Image': 'sha256:' + 'd' * 64},
                    {**info, 'Mounts': [{'Type': 'bind'}]}, {}):
        with pytest.raises(container.ContainerRejected):
            container.validate_configuration(changed, image, labels)


@pytest.mark.parametrize('field,value', [('repository_id', 'other/repo'),
    ('ref', 'refs/heads/other'), ('workflow_version', 'arbitrary'),
    ('commit_sha', 'https://example.invalid/repo'), ('attempt_no', 2)])
def test_unsupported_permit_does_not_contact_docker(monkeypatch, field, value):
    def forbidden(*args, **kwargs):
        pytest.fail('unsupported input reached Docker')
    monkeypatch.setattr(container, 'docker', forbidden)
    with pytest.raises(container.ContainerRejected):
        with container.prepare_fixture(replace(permit(), **{field: value})):
            pytest.fail('invalid permit yielded')


def test_docker_errors_do_not_expose_diagnostics(monkeypatch):
    monkeypatch.setattr(container.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=1, stdout='', stderr='SECRET=public-test-canary'))
    with pytest.raises(container.ContainerRejected) as error:
        container.docker('info')
    assert 'public-test-canary' not in str(error.value)


def test_cleanup_refuses_unowned_container(monkeypatch):
    import json
    calls = []
    info, _, _ = configuration()
    def fake(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps([info]))
    monkeypatch.setattr(container, 'docker', fake)
    with pytest.raises(container.ContainerRejected, match='not owned'):
        container.remove_owned('name', 'different-session')
    assert len(calls) == 1


@pytest.mark.parametrize('error', [FileNotFoundError('public injected missing CLI'),
    container.subprocess.TimeoutExpired(['docker'], 30, output='public-test-canary')])
def test_unavailable_or_timed_out_docker_is_rejected(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(container.subprocess, 'run', fail)
    with pytest.raises(container.ContainerRejected, match='unavailable or timed out') as caught:
        container.docker('info')
    assert 'public-test-canary' not in str(caught.value)
