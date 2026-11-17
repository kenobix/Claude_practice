"""Opt-in create/inspect/cleanup only: never start the job fixture."""
import json
import os

import pytest

from ci_runner import container
from test_container import permit

pytestmark = pytest.mark.docker


@pytest.fixture(autouse=True)
def opt_in():
    if os.environ.get('CI_RUN_PREPARATION_TESTS') != '1':
        pytest.skip('run scripts/verify_preparation.py for Docker preparation checks')


@pytest.mark.parametrize('failure', ['none', 'body', 'mismatch', 'lost_create_response'])
def test_owned_lifecycle_and_failure_cleanup(monkeypatch, failure):
    real_docker = container.docker
    names, tags = [], []
    def observe(*args, **kwargs):
        if args[0] == 'build':
            tags.append(args[args.index('--tag') + 1])
        if args[0] == 'create':
            names.append(args[args.index('--name') + 1])
        result = real_docker(*args, **kwargs)
        if args[0] == 'create' and failure == 'lost_create_response':
            raise container.ContainerRejected('injected response loss after create')
        return result
    monkeypatch.setattr(container, 'docker', observe)
    if failure == 'mismatch':
        validate = container.validate_configuration
        def reject(info, image, labels, mode):
            info['HostConfig']['NetworkMode'] = 'bridge'
            validate(info, image, labels, mode)
        monkeypatch.setattr(container, 'validate_configuration', reject)
    job_permit = permit()
    def exercise():
        with container.prepare_fixture(job_permit) as prepared:
            info = json.loads(real_docker('inspect', prepared.container_id).stdout)[0]
            assert info['State']['Status'] == 'created' and info['State']['Pid'] == 0
            assert info['Image'] == prepared.image_id
            assert str(job_permit.lease_token) not in json.dumps(info['Config']['Labels'])
            assert info['Config']['Labels']['dev.ci-runner.run'] == str(job_permit.run_id)
            assert info['Config']['Labels']['dev.ci-runner.attempt'] == str(job_permit.attempt_id)
            if failure == 'body':
                raise RuntimeError('injected caller failure')
    try:
        if failure == 'none':
            exercise()
        else:
            expected = RuntimeError if failure == 'body' else container.ContainerRejected
            with pytest.raises(expected):
                exercise()
    finally:
        assert len(names) == len(tags) == 1
        for name in names:
            assert real_docker('inspect', name, check=False).returncode != 0
        for tag in tags:
            assert real_docker('image', 'inspect', tag, check=False).returncode != 0
