from dataclasses import replace

import pytest

from ci_runner import container, execution
from ci_runner.preflight import ExecutionDenied
from test_container import permit


def test_unknown_mode_never_contacts_database_or_docker(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid mode contacted a dependency')
    monkeypatch.setattr(execution, 'authorize_execution', forbidden)
    with pytest.raises(container.ContainerRejected, match='unknown fixed'):
        execution.execute_fixture(None, 'arbitrary-command')
    with pytest.raises(container.ContainerRejected, match='unknown fixed'):
        with container.prepare_fixture(permit(), 'arbitrary-command'):
            pytest.fail('invalid mode yielded')


def test_denied_claim_never_prepares_container(monkeypatch):
    def deny(*args):
        raise ExecutionDenied('public injected denial')
    def forbidden(*args):
        pytest.fail('denied claim reached container preparation')
    monkeypatch.setattr(execution, 'authorize_execution', deny)
    monkeypatch.setattr(container, 'prepare_fixture', forbidden)
    with pytest.raises(ExecutionDenied):
        execution.execute_fixture(None)


def test_db_round_trip_consumes_local_lease_budget(monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def prepared(*args, **kwargs):
        yield object()
    times = iter([100.0, 103.0])
    monkeypatch.setattr(execution.time, 'monotonic', lambda: next(times))
    monkeypatch.setattr(execution, 'authorize_execution', lambda *a: replace(permit(), remaining_lease_seconds=2.0))
    monkeypatch.setattr(container, 'prepare_fixture', prepared)
    monkeypatch.setattr(execution, '_run', lambda *a: pytest.fail('expired authority started a process'))
    with pytest.raises(ExecutionDenied, match='expired during authorization'):
        execution.execute_fixture(None)


@pytest.fixture
def heartbeat_clock(monkeypatch):
    calls = []
    clock = [100.0]

    class DeferredThread:
        def __init__(self, *, target, args, daemon):
            assert daemon
            self.call = lambda: target(*args)

        def start(self):
            calls.append(self.call)

    monkeypatch.setattr(execution.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(execution, 'Thread', DeferredThread)
    return clock, calls


def test_heartbeat_anchors_budget_to_dispatch_and_never_overlaps(monkeypatch, heartbeat_clock):
    clock, calls = heartbeat_clock
    claim, factory = object(), object()
    updated = []
    monkeypatch.setattr(execution, 'renew_lease', lambda *args: updated.append(args))
    heartbeat = execution._Heartbeat(claim, factory)
    clock[0] = 104.999
    assert heartbeat.poll(112, running=True) == 112 and not calls
    clock[0] = 105.0
    assert heartbeat.poll(112, running=True) == 112 and heartbeat.pending
    clock[0] = 108.0
    assert heartbeat.poll(112, running=True) == 112 and len(calls) == 1
    calls[0]()
    assert updated == [(claim, factory)]
    assert heartbeat.poll(112, running=True) == 135.0 and not heartbeat.pending
    clock[0] = 110.0
    assert heartbeat.poll(135, running=False) == 135 and len(calls) == 1


@pytest.mark.parametrize('lease_deadline,now', [(108, 108), (132, 110)])
def test_late_heartbeat_acknowledgement_cannot_restore_authority(monkeypatch, heartbeat_clock, lease_deadline, now):
    clock, calls = heartbeat_clock
    monkeypatch.setattr(execution, 'renew_lease', lambda *args: None)
    heartbeat = execution._Heartbeat(object(), None)
    clock[0] = 105.0
    assert heartbeat.poll(lease_deadline, running=True) == lease_deadline
    clock[0] = now
    calls[0]()
    with pytest.raises(execution.HeartbeatFailed, match='not confirmed in time'):
        heartbeat.poll(lease_deadline, running=True)
