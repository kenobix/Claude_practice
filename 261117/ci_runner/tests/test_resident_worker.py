from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import signal
from uuid import uuid4

import pytest

from ci_runner import commit_source, container, finalization, fixture_worker, worker
from test_commit_source import GitHubFixture, HEAD

ARGS = ['--worker-id', 'resident-1', '--serve']


@pytest.mark.parametrize('flags', [['--run-once'], ['--fixture-mode', 'unapproved']])
def test_invalid_serve_arguments_do_not_acquire(monkeypatch, flags):
    monkeypatch.setattr(fixture_worker, 'run_once', lambda *a, **k: pytest.fail('invalid CLI acquired work'))
    with pytest.raises(SystemExit) as error:
        fixture_worker.main([*ARGS, *flags])
    assert error.value.code == 2


def test_serve_waits_on_empty_queue_and_drains_serially(monkeypatch, capsys):
    results = [finalization.FinalizationReceipt(uuid4(), uuid4(), 'failed', 1,
                                                 'commit_check_failure', datetime.now(timezone.utc), False)
               for _ in range(2)]
    calls = []
    old = signal.getsignal(signal.SIGTERM)

    def run_once(worker_id, mode, *, verified_commit, stopping):
        assert verified_commit and not stopping()
        calls.append('run')
        if len(calls) == 1:
            return None
        if len(calls) == 4:
            os.kill(os.getpid(), signal.SIGTERM)
            assert stopping()
        return results.pop(0)

    def wait(seconds):
        assert seconds == fixture_worker.IDLE_POLL_SECONDS
        calls.append('wait')

    monkeypatch.setattr(fixture_worker, 'run_once', run_once)
    monkeypatch.setattr(fixture_worker.time, 'sleep', wait)
    assert fixture_worker.main(ARGS) == 0
    assert calls == ['run', 'wait', 'run', 'run']
    assert signal.getsignal(signal.SIGTERM) == old
    output = capsys.readouterr()
    assert not output.err
    assert [json.loads(line)['reason'] for line in output.out.splitlines()] == ['commit_check_failure'] * 2


def test_idle_sigint_stops_without_another_poll(monkeypatch, capsys):
    calls = []
    old = signal.getsignal(signal.SIGINT)

    def run_once(*args, **kwargs):
        calls.append('run')
        return None

    def wait(_seconds):
        calls.append('wait')
        os.kill(os.getpid(), signal.SIGINT)

    monkeypatch.setattr(fixture_worker, 'run_once', run_once)
    monkeypatch.setattr(fixture_worker.time, 'sleep', wait)
    assert fixture_worker.main(ARGS) == 0
    assert calls == ['run', 'wait']
    assert signal.getsignal(signal.SIGINT) == old
    assert not capsys.readouterr().out


@pytest.mark.parametrize('stage', ['source', 'claim', 'finalization'])
def test_serve_stops_instead_of_retrying_incomplete_work(monkeypatch, capsys, stage):
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(stage)
        raise fixture_worker.OneShotFailed(stage)

    monkeypatch.setattr(fixture_worker, 'run_once', fail)
    assert fixture_worker.main(ARGS) == 1
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)['stage'] == stage
    assert attempts == [stage]


@pytest.mark.parametrize('phase', ['source', 'image'])
def test_stop_before_claim_does_not_issue_lease_and_releases_image(monkeypatch, phase):
    source = commit_source._verify(uuid4(), HEAD, GitHubFixture(HEAD))
    events = []
    stopped = False

    def verify(_factory):
        nonlocal stopped
        events.append('source')
        stopped = phase == 'source'
        return source

    @contextmanager
    def prepare(*, verified_source):
        nonlocal stopped
        assert verified_source is source
        events.append('image')
        stopped = True
        try:
            yield object()
        finally:
            events.append('cleanup')

    monkeypatch.setattr(commit_source, 'verify_queued_source', verify)
    monkeypatch.setattr(container, 'prepare_image', prepare)
    monkeypatch.setattr(worker, 'claim_next', lambda *a, **k: pytest.fail('shutdown issued a lease'))
    assert fixture_worker.run_once('resident-1', verified_commit=True, stopping=lambda: stopped) is None
    assert events == (['source'] if phase == 'source' else ['source', 'image', 'cleanup'])
