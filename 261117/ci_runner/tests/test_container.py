from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from stat import S_IMODE
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ci_runner import commit_source, container
from ci_runner.preflight import ExecutionPermit
from test_commit_source import GitHubFixture, HEAD


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
                   'Entrypoint': container.ENTRYPOINT.copy(), 'Cmd': ['success'], 'OpenStdin': True,
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


@pytest.mark.parametrize('reply', ['valid', 'malformed', 'curl_error', 'ssh', 'tls'])
def test_daemon_id_uses_current_context_and_preserves_cli_fallback(monkeypatch, reply):
    for var in ('DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CUSTOM_HEADERS', 'DOCKER_API_VERSION'):
        monkeypatch.delenv(var, raising=False)
    identity = uuid4()
    path = '/run/user/1234/docker.sock'
    host = 'ssh://test' if reply == 'ssh' else f'unix://{path}'
    fallback = []

    def docker(*args, **kwargs):
        if args == ('context', 'inspect', '--format', '{{json .}}'):
            context = {'Endpoints': {'docker': {'Host': host}},
                       'TLSMaterial': {'docker': ['ca.pem']} if reply == 'tls' else None}
            return SimpleNamespace(stdout=json.dumps(context))
        assert args == ('info', '--format', '{{.ID}}') and 0 < kwargs['timeout'] <= 30
        fallback.append(True)
        return SimpleNamespace(stdout=str(identity))

    def curl(args, **kwargs):
        assert reply not in ('ssh', 'tls')
        assert args[:2] == ['curl', '-q']
        assert args[args.index('--unix-socket') + 1] == path
        assert args[args.index('--noproxy') + 1] == '*' and args[-1] == 'http://localhost/info'
        assert 0 < kwargs['timeout'] < 30
        return SimpleNamespace(returncode=int(reply == 'curl_error'),
                               stdout=json.dumps({'ID': 'nonpublic daemon reply' if reply == 'malformed' else str(identity)}))

    monkeypatch.setattr(container, 'docker', docker)
    monkeypatch.setattr(container.subprocess, 'run', curl)
    if reply == 'malformed':
        with pytest.raises(container.ContainerRejected, match=r'^Docker daemon identity unavailable$'):
            container.daemon_id()
    else:
        assert container.daemon_id() == identity
    assert bool(fallback) == (reply in ('curl_error', 'ssh', 'tls'))


def test_cleanup_refuses_unowned_container(monkeypatch):
    calls = []
    info, _, _ = configuration()
    def fake(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps([info]))
    monkeypatch.setattr(container, 'docker', fake)
    with pytest.raises(container.ContainerRejected, match='not owned'):
        container.remove_owned('name', 'different-session')
    assert len(calls) == 1


@pytest.mark.parametrize('missing', ['owned-name', 'different-name'])
def test_owned_cleanup_accepts_only_its_exact_absence(monkeypatch, missing):
    def inspect(*args, **kwargs):
        assert args == ('container', 'inspect', 'owned-name') and kwargs == {'check': False}
        return SimpleNamespace(returncode=1, stderr=f'Error response from daemon: No such container: {missing}')

    monkeypatch.setattr(container, 'docker', inspect)
    if missing == 'owned-name':
        container.remove_owned('owned-name', 'session')
    else:
        with pytest.raises(container.ContainerRejected, match='cleanup inspection failed'):
            container.remove_owned('owned-name', 'session')


@pytest.mark.parametrize('error', [FileNotFoundError('public injected missing CLI'),
    container.subprocess.TimeoutExpired(['docker'], 30, output='public-test-canary')])
def test_unavailable_or_timed_out_docker_is_rejected(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(container.subprocess, 'run', fail)
    with pytest.raises(container.ContainerRejected, match='unavailable or timed out') as caught:
        container.docker('info')
    assert 'public-test-canary' not in str(caught.value)


@pytest.mark.parametrize('change', [{'image_id': 'unreviewed:latest'}, {'session': '../host'},
                                   {'tag': 'external:latest'}])
def test_invalid_image_handle_never_contacts_docker(monkeypatch, change):
    image = container.PreparedImage('sha256:'+'a'*64, 'b'*32, 'ci-runner-fixed-fixture:'+'b'*32)
    monkeypatch.setattr(container, 'docker', lambda *a, **k: pytest.fail('invalid handle contacted Docker'))
    with pytest.raises(container.ContainerRejected):
        container.inspect_prepared_image(replace(image, **change))


@pytest.mark.parametrize('changed', ['identity', 'owner'])
def test_rebound_or_unowned_image_never_creates_container(monkeypatch, changed):
    prepared = container.PreparedImage('sha256:'+'a'*64, 'b'*32, 'ci-runner-fixed-fixture:'+'b'*32)
    image = {'Id': prepared.image_id, 'Config': {'Labels': {container.LABEL: prepared.session}}}
    if changed == 'identity':
        image['Id'] = 'sha256:'+'c'*64
    else:
        image['Config']['Labels'] = {}
    def inspect(*args, **kwargs):
        assert args == ('image', 'inspect', prepared.tag)
        return SimpleNamespace(stdout=json.dumps([image]))
    monkeypatch.setattr(container, 'docker', inspect)
    with pytest.raises(container.ContainerRejected, match='identity or ownership'):
        with container.prepare_fixture(permit(), prepared_image=prepared):
            pytest.fail('changed image yielded a container')


def test_cleanup_never_removes_a_rebound_image_tag(monkeypatch):
    rebound = False
    def fake(*args, **kwargs):
        if args[0] == 'build':
            return SimpleNamespace(returncode=0, stdout='')
        assert args[:2] == ('image', 'inspect'), 'rebound tag must never be removed'
        tag = args[2]
        session = tag.split(':')[1]
        image = {'Id': 'sha256:'+('b' if rebound else 'a')*64,
                 'Config': {'Labels': {container.LABEL: session}}}
        return SimpleNamespace(returncode=0, stdout=json.dumps([image]))
    monkeypatch.setattr(container, 'docker', fake)
    with pytest.raises(container.ContainerRejected, match='replaced image tag'):
        with container.prepare_image():
            rebound = True


def test_verified_fixture_is_readable_with_restrictive_umask():
    source = commit_source._verify(uuid4(), HEAD, GitHubFixture(HEAD))
    previous_umask = os.umask(0o077)
    try:
        with container._build_context(source) as context:
            fixture = context / 'fixture.py'
            assert S_IMODE(context.stat().st_mode) == 0o700
            assert S_IMODE(fixture.stat().st_mode) == 0o644
            assert fixture.read_bytes() == source.contents
    finally:
        os.umask(previous_umask)
    assert not context.exists()
