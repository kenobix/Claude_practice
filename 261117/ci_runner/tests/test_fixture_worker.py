from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import psycopg
import pytest

from ci_runner import container, execution, finalization, fixture_worker, worker
from test_finalization_input import report


def claim():
    now = datetime.now(timezone.utc)
    return worker.Claim(uuid4(), uuid4(), 1, 'worker-1', uuid4(), now, now + timedelta(seconds=30))


def test_empty_queue_never_executes_or_finalizes(monkeypatch):
    monkeypatch.setattr(worker, 'claim_next', lambda *a: None)
    def forbidden(*args):
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
        def call(*args):
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
    def execute(actual, mode, connection_factory):
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
    monkeypatch.setattr(execution, 'execute_fixture', lambda *a: report(current, reason, code))
    with pytest.raises(fixture_worker.OneShotFailed) as error:
        fixture_worker.run_once('worker-1')
    assert error.value.stage == 'finalization'


def test_cleanup_failure_never_sends_result(monkeypatch):
    current = claim()
    monkeypatch.setattr(worker, 'claim_next', lambda *a: current)
    def cleanup_failed(*args):
        raise container.ContainerRejected('cleanup failed')
    monkeypatch.setattr(execution, 'execute_fixture', cleanup_failed)
    monkeypatch.setattr(finalization, 'finalize_fixture', lambda *a: pytest.fail('cleanup failure finalized'))
    with pytest.raises(fixture_worker.OneShotFailed, match='execution'):
        fixture_worker.run_once('worker-1')
