from dataclasses import replace
from uuid import uuid4

import pytest

from ci_runner import commit_source, container, execution, fixture_worker
from ci_runner.preflight import ExecutionDenied
from test_commit_source import GitHubFixture, HEAD
from test_container import permit


def test_source_failure_never_falls_back_to_local_workload(monkeypatch, capsys):
    def reject(*args):
        raise commit_source.SourceRejected('fixed source rejection')

    monkeypatch.setattr(commit_source, 'verify_queued_source', reject)
    monkeypatch.setattr(container, 'prepare_image', lambda **kw: pytest.fail('source failure reached Docker'))
    assert fixture_worker.main(['--worker-id', 'worker-1', '--run-once', '--verified-commit']) == 1
    output = capsys.readouterr()
    assert output.out == '' and 'fixed source rejection' in output.err


def test_private_context_contains_only_approved_dockerfile_and_fetched_bytes():
    source = commit_source._verify(uuid4(), HEAD, GitHubFixture(HEAD))
    with container._build_context(source) as context:
        assert {p.name for p in context.iterdir()} == {'Dockerfile', 'fixture.py'}
        assert (context / 'Dockerfile').read_bytes() == (container.ROOT / 'fixtures/job/Dockerfile').read_bytes()
        assert (context / 'fixture.py').read_bytes() == source.contents
    assert not context.exists()


@pytest.mark.parametrize('tamper', ['source', 'Dockerfile'])
def test_modified_build_input_never_calls_docker(monkeypatch, tmp_path, tamper):
    source = commit_source._verify(uuid4(), HEAD, GitHubFixture(HEAD))
    if tamper == 'source':
        source = replace(source, contents=b'X' + source.contents[1:])
        error = commit_source.SourceRejected
    else:
        target = tmp_path / 'fixtures/job/Dockerfile'
        target.parent.mkdir(parents=True)
        target.write_text('FROM scratch\n')
        monkeypatch.setattr(container, 'ROOT', tmp_path)
        error = container.ContainerRejected

    monkeypatch.setattr(container, 'docker', lambda *a, **k: pytest.fail('invalid input contacted Docker'))
    with pytest.raises(error, match='approved bytes|Dockerfile digest'):
        with container.prepare_image(verified_source=source):
            pytest.fail('invalid input built an image')


def test_execution_rejects_changed_commit_before_container_preparation(monkeypatch):
    current = permit()
    source = commit_source._verify(current.run_id, HEAD, GitHubFixture(HEAD))
    image = container.PreparedImage('sha256:' + 'a'*64, 'b'*32,
                                    'ci-runner-fixed-fixture:' + 'b'*32, source)
    monkeypatch.setattr(execution, 'authorize_execution', lambda *a: replace(current, commit_sha='c'*40))
    monkeypatch.setattr(container, 'prepare_fixture', lambda *a, **k: pytest.fail('changed SHA reached Docker'))
    with pytest.raises(ExecutionDenied, match='no longer matches'):
        execution.execute_fixture(current, prepared_image=image)
