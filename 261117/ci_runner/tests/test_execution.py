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
    def prepared(*args):
        yield object()
    times = iter([100.0, 103.0])
    monkeypatch.setattr(execution.time, 'monotonic', lambda: next(times))
    monkeypatch.setattr(execution, 'authorize_execution', lambda *a: replace(permit(), remaining_lease_seconds=2.0))
    monkeypatch.setattr(container, 'prepare_fixture', prepared)
    monkeypatch.setattr(execution, '_run', lambda *a: pytest.fail('expired authority started a process'))
    with pytest.raises(ExecutionDenied, match='expired during authorization'):
        execution.execute_fixture(None)
