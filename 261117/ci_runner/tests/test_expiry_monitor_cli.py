from datetime import datetime, timezone
import json
from uuid import uuid4

import psycopg

from ci_runner import expiry_monitor
from ci_runner.retry import ScheduledRetry


def test_watch_sleeps_between_batches_and_stops_on_lost_commit(monkeypatch, capsys):
    receipt = expiry_monitor.ExpiredAttempt(uuid4(), uuid4(), 'failed', 'infra_lease_expired')
    results = iter([receipt, None])
    sleeps = []

    def scan():
        try:
            return next(results)
        except StopIteration:
            raise psycopg.OperationalError('injected connection detail') from None

    monkeypatch.setattr(expiry_monitor, 'expire_next', scan)
    monkeypatch.setattr(expiry_monitor, 'schedule_next', lambda: None)
    monkeypatch.setattr(expiry_monitor.time, 'sleep', sleeps.append)
    assert expiry_monitor.main(['--watch']) == 1
    assert sleeps == [5]
    output = capsys.readouterr()
    assert json.loads(output.out)['attempt_id'] == str(receipt.attempt_id)
    assert output.err == 'database unavailable; expiry outcome may be unknown\n'


def test_once_is_bounded(monkeypatch, capsys):
    receipt = expiry_monitor.ExpiredAttempt(uuid4(), uuid4(), 'failed', 'infra_lease_expired')
    retry = ScheduledRetry(receipt.run_id, receipt.attempt_id, 2, datetime.now(timezone.utc))
    monkeypatch.setattr(expiry_monitor, 'expire_next', lambda: receipt)
    monkeypatch.setattr(expiry_monitor, 'schedule_next', lambda: retry)
    assert expiry_monitor.main(['--once']) == 0
    lines = capsys.readouterr().out.splitlines()
    states = [json.loads(line)['state'] for line in lines]
    assert states == ['failed'] * expiry_monitor.MAX_PER_POLL + ['queued'] * expiry_monitor.MAX_PER_POLL
