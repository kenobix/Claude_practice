from contextlib import contextmanager
import json
from subprocess import CompletedProcess
from uuid import uuid4

import psycopg
import pytest

from ci_runner import container, orphan_cleanup
from ci_runner.settings import POLICY

CID = 'a' * 64
SESSION = 'b' * 32
LIST = ('container', 'ls', '--all', '--quiet', '--no-trunc', '--filter', f'label={container.LABEL}')
INSPECT = ('container', 'inspect', CID)
REMOVE = ('container', 'rm', '--force', CID)


@pytest.fixture
def owned():
    return {'Id': CID, 'Name': f'/{container.NAME_PREFIX}{SESSION}', 'Config': {'Labels': {
        container.LABEL: SESSION, container.RUN_LABEL: str(uuid4()), container.ATTEMPT_LABEL: str(uuid4())}}}


@pytest.fixture
def mock_scan(monkeypatch, owned):
    def install(*steps):
        calls = []
        answers = iter(steps)

        def docker(*args, **kwargs):
            calls.append(args)
            expected, code, output = next(answers)
            assert args == expected
            if not code and args != LIST and args != REMOVE:
                output = json.dumps([owned]) if output is None else output
            return CompletedProcess(args, code, output if not code else '', output if code else '')

        monkeypatch.setattr(container, 'docker', docker)
        return calls
    return install


@pytest.mark.parametrize('race', [
    'none', 'before_inspect', 'force_absent', 'rm_error_absent', 'force_unconfirmed', 'rm_error_present',
])
def test_only_full_id_is_removed_and_missing_is_idempotent(mock_scan, owned, race, capsys, monkeypatch):
    absent = f'Error response from daemon: No such container: {CID}'
    steps = [(LIST, 0, f'{CID}\n{CID}\n')]
    if race == 'before_inspect':
        steps.append((INSPECT, 1, absent))
    else:
        steps.append((INSPECT, 0, None))
        if race == 'none':
            steps.append((REMOVE, 0, CID))
        else:
            steps.append((REMOVE, 0, '') if race.startswith('force_') else (REMOVE, 1, 'nonpublic daemon detail'))
            steps.append((INSPECT, 1, absent) if race.endswith('_absent') else (INSPECT, 0, None))
    calls = mock_scan(*steps)
    labels = owned['Config']['Labels']
    authorizations = []

    def closed(factory, run, attempt):
        assert (str(run), str(attempt)) == (labels[container.RUN_LABEL], labels[container.ATTEMPT_LABEL])
        authorizations.append(attempt)
        return True

    monkeypatch.setattr(orphan_cleanup, '_closed_by_expiry', closed)
    failed = race in ('force_unconfirmed', 'rm_error_present')
    assert orphan_cleanup.main(['--once']) == int(failed)
    assert calls == [step[0] for step in steps]
    assert len(authorizations) == (0 if race == 'before_inspect' else 1)
    output = capsys.readouterr()
    if race == 'none':
        assert json.loads(output.out) == {'container_id': CID, 'run_id': labels[container.RUN_LABEL],
                                          'attempt_id': labels[container.ATTEMPT_LABEL]}
    else:
        assert not output.out
    assert output.err == ('Docker operation failed; orphan cleanup may be incomplete\n' if failed else '')


@pytest.mark.parametrize('field,value', [
    ('Name', '/some-other-project'), ('Config', {'Labels': None}),
    (container.LABEL, '../elsewhere'), (container.RUN_LABEL, 'bad'),
    (container.ATTEMPT_LABEL, '-abc'), (container.ATTEMPT_LABEL, '0'*32),
])
def test_unowned_or_noncanonical_identity_never_queries_db(mock_scan, owned, field, value):
    target = owned if field in owned else owned['Config']['Labels']
    target[field] = value
    calls = mock_scan((LIST, 0, CID), (INSPECT, 0, None))
    assert list(orphan_cleanup.recover(lambda: pytest.fail('unexpected DB access'))) == []
    assert calls == [LIST, INSPECT]


@pytest.mark.parametrize('fault', ['short_id', 'option', 'changed_id', 'bad_inspect', 'unavailable', 'wrong_missing_id'])
def test_ambiguous_daemon_response_stops_before_db_or_removal(mock_scan, owned, fault, capsys):
    steps = {
        'short_id': [(LIST, 0, CID[:12])],
        'option': [(LIST, 0, '--all')],
        'changed_id': [(LIST, 0, CID), (INSPECT, 0, None)],
        'bad_inspect': [(LIST, 0, CID), (INSPECT, 0, '[invalid-json')],
        'unavailable': [(LIST, 0, CID), (INSPECT, 1, 'daemon unavailable')],
        'wrong_missing_id': [(LIST, 0, CID), (INSPECT, 1, 'Error response from daemon: No such container: other')],
    }[fault]
    owned['Id'] = 'c' * 64
    calls = mock_scan(*steps)
    assert orphan_cleanup.main(['--once']) == 1
    assert calls == [step[0] for step in steps]
    output = capsys.readouterr()
    assert not output.out and output.err == 'Docker operation failed; orphan cleanup may be incomplete\n'


def test_db_error_on_exit_prevents_removal(mock_scan, owned, monkeypatch, capsys):
    labels = owned['Config']['Labels']
    calls = mock_scan((LIST, 0, CID), (INSPECT, 0, None))

    @contextmanager
    def unreadable_connection():
        class Connection:
            def execute(self, query, values):
                assert tuple(map(str, values)) == (labels[container.RUN_LABEL], labels[container.ATTEMPT_LABEL],
                                                  POLICY.repository_id, POLICY.ref, POLICY.workflow_version)
                return self

            def fetchone(self):
                return (1,)

        yield Connection()
        raise psycopg.OperationalError('nonpublic connection details')

    monkeypatch.setattr(orphan_cleanup.database, 'connect', unreadable_connection)
    assert orphan_cleanup.main(['--once']) == 1
    assert calls == [LIST, INSPECT]
    output = capsys.readouterr()
    assert not output.out and output.err == 'database unavailable; orphan cleanup stopped\n'
