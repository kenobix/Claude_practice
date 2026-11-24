from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import psycopg
import pytest

from ci_runner import container, execution, finalization, fixture_worker, worker
from test_finalization_input import report


@pytest.fixture(autouse=True)
def image_scope(monkeypatch):
    image = object()
    monkeypatch.setattr(container, 'prepare_image', lambda: nullcontext(image))
    monkeypatch.setattr(worker, 'has_queued_candidate', lambda *a: True)
    return image


def claim():
    now = datetime.now(timezone.utc)
    return worker.Claim(uuid4(), uuid4(), 1, 'worker-1', uuid4(), now, now + timedelta(seconds=30))


def test_empty_queue_never_executes_or_finalizes(monkeypatch):
    monkeypatch.setattr(worker, 'has_queued_candidate', lambda *a: False)
    monkeypatch.setattr(container, 'prepare_image', lambda: pytest.fail('empty queue built an image'))
    monkeypatch.setattr(worker, 'claim_next', lambda *a: None)
    def forbidden(*args, **kwargs):
        pytest.fail('empty queue advanced beyond acquisition')
    monkeypatch.setattr(execution, 'execute_fixture', forbidden)
    monkeypatch.setattr(finalization, 'finalize_fixture', forbidden)
    assert fixture_worker.run_once('worker-1') is None


@pytest.mark.parametrize('args', [[], ['--run-once', '--fixture-mode', 'arbitrary-command']])
def test_cli_requires_explicit_opt_in_and_fixed_mode(monkeypatch, args):
    monkeypatch.setattr(fixture_worker, 'run_once', lambda *a: pytest.fail('invalid CLI started work'))
    with pytest.raises(SystemExit) as error:
        fixture_worker.main(['--worker-id', 'worker-1', *args])
    assert error.value.code == 2


@pytest.mark.parametrize('worker_id,mode', [('bad/id', 'success'), ('worker-1', 'arbitrary')])
def test_invalid_operator_input_never_acquires(monkeypatch, worker_id, mode):
    monkeypatch.setattr(worker, 'claim_next', lambda *a: pytest.fail('invalid input acquired a job'))
    with pytest.raises(ValueError):
        fixture_worker.run_once(worker_id, mode)


@pytest.mark.parametrize('stage', ['claim', 'execution', 'finalization'])
def test_each_phase_failure_stops_without_retry_or_leaking(monkeypatch, capsys, stage):
    current = claim()
    events = []
    def phase(name, value):
        def call(*args, **kwargs):
            events.append(name)
            if name == stage:
                raise psycopg.OperationalError('private-driver-canary ' + str(current.lease_token))
            return value
        return call
    monkeypatch.setattr(worker, 'claim_next', phase('claim', current))
    monkeypatch.setattr(execution, 'execute_fixture', phase('execution', report(current)))
    monkeypatch.setattr(finalization, 'finalize_fixture', phase('finalization', None))
    assert fixture_worker.main(['--worker-id', 'worker-1', '--run-once']) == 1
    output = capsys.readouterr()
    assert output.out == ''
    error = json.loads(output.err)
    assert error['stage'] == stage and error['status'] == 'incomplete'
    assert 'private-driver-canary' not in output.err and str(current.lease_token) not in output.err
    assert events == ['claim', 'execution', 'finalization'][:['claim', 'execution', 'finalization'].index(stage) + 1]
    assert error.get('run_id') == (None if stage == 'claim' else str(current.run_id))


def test_cleaned_execution_result_and_factory_reach_finalizer_once(monkeypatch, capsys):
    current = claim()
    result = report(current, 'check_failure', 1)
    receipt = finalization.FinalizationReceipt(current.run_id, current.attempt_id, 'failed', 1,
                                              'fixture_check_failure', current.started_at, False)
    factory = object()
    calls = []
    def acquire(worker_id, connection_factory):
        assert worker_id == 'worker-1' and connection_factory is factory
        calls.append('acquired')
        return current
    def execute(actual, mode, connection_factory, *, prepared_image):
        assert actual is current and mode == 'failure' and connection_factory is factory
        calls.append('cleaned')
        return result
    def finalize(actual, outcome, connection_factory):
        assert actual is current and outcome is result and connection_factory is factory
        assert calls == ['acquired', 'cleaned']
        calls.append('committed')
        return receipt
    monkeypatch.setattr(worker, 'claim_next', acquire)
    monkeypatch.setattr(execution, 'execute_fixture', execute)
    monkeypatch.setattr(finalization, 'finalize_fixture', finalize)
    assert fixture_worker.run_once('worker-1', 'failure', factory) is receipt
    monkeypatch.setattr(fixture_worker, 'run_once', lambda *a: receipt)
    assert fixture_worker.main(['--worker-id', 'worker-1', '--run-once', '--fixture-mode', 'failure']) == 0
    output = capsys.readouterr()
    assert output.err == ''
    assert json.loads(output.out)['state'] == 'failed'
    assert str(current.lease_token) not in output.out and 'stdout' not in output.out


@pytest.mark.parametrize('reason,code', [('lease_expired', 137), ('runtime_preflight_rejected', 78)])
def test_unfinalizable_result_is_incomplete(monkeypatch, reason, code):
    current = claim()
    monkeypatch.setattr(worker, 'claim_next', lambda *a: current)
    monkeypatch.setattr(execution, 'execute_fixture', lambda *a, **k: report(current, reason, code))
    with pytest.raises(fixture_worker.OneShotFailed) as error:
        fixture_worker.run_once('worker-1')
    assert error.value.stage == 'finalization'


def test_cleanup_failure_never_sends_result(monkeypatch):
    current = claim()
    monkeypatch.setattr(worker, 'claim_next', lambda *a: current)
    def cleanup_failed(*args, **kwargs):
        raise container.ContainerRejected('cleanup failed')
    monkeypatch.setattr(execution, 'execute_fixture', cleanup_failed)
    monkeypatch.setattr(finalization, 'finalize_fixture', lambda *a: pytest.fail('cleanup failure finalized'))
    with pytest.raises(fixture_worker.OneShotFailed, match='execution'):
        fixture_worker.run_once('worker-1')


def test_image_failure_never_claims(monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def failed():
        raise container.ContainerRejected('private build canary')
        yield
    monkeypatch.setattr(container, 'prepare_image', failed)
    monkeypatch.setattr(worker, 'claim_next', lambda *a: pytest.fail('failed build acquired a job'))
    with pytest.raises(fixture_worker.OneShotFailed) as error:
        fixture_worker.run_once('worker-1')
    assert error.value.stage == 'image' and error.value.run_id is None


@pytest.mark.parametrize('has_claim,cleanup_fails', [(False, False), (True, False), (True, True)])
def test_image_lifetime_and_racing_empty_queue(monkeypatch, has_claim, cleanup_fails):
    from contextlib import contextmanager
    current = claim()
    image = object()
    events = []
    @contextmanager
    def prepared():
        events.append('built')
        yield image
        events.append('image_removed')
        if cleanup_fails:
            raise container.ContainerRejected('public injected image cleanup error')
    def acquire(*args):
        assert events == ['built']
        events.append('claimed' if has_claim else 'no_claim')
        return current if has_claim else None
    def execute(actual, mode, factory, *, prepared_image):
        assert actual is current and prepared_image is image
        assert events == ['built', 'claimed']
        events.append('container_removed')
        return report(current)
    def finalize(*args):
        assert events == ['built', 'claimed', 'container_removed', 'image_removed']
        events.append('finalized')
        return 'receipt'
    monkeypatch.setattr(container, 'prepare_image', prepared)
    monkeypatch.setattr(worker, 'claim_next', acquire)
    monkeypatch.setattr(execution, 'execute_fixture', execute)
    monkeypatch.setattr(finalization, 'finalize_fixture', finalize)
    if cleanup_fails:
        with pytest.raises(fixture_worker.OneShotFailed, match='image_cleanup'):
            fixture_worker.run_once('worker-1')
        assert 'finalized' not in events
    else:
        assert fixture_worker.run_once('worker-1') == ('receipt' if has_claim else None)
        if not has_claim:
            assert events == ['built', 'no_claim', 'image_removed']
