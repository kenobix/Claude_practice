import base64
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from ci_runner import commit_source as source

HEAD = 'a' * 40
OLDER = 'b' * 40
BLOB = 'a2352427ae73ed9337e933d1dcefd49968b3fe8b'
FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/job/fixture.py'


class GitHubFixture:
    def __init__(self, sha):
        parts = source.PATH.split('/')
        trees = [str(i) * 40 for i in range(len(parts))]
        self.responses = {
            source.REF_ENDPOINT: {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': HEAD}},
            f'/compare/{sha}...{HEAD}?per_page=1': {
                'base_commit': {'sha': sha}, 'merge_base_commit': {'sha': sha},
                'status': 'ahead', 'behind_by': 0,
            },
            f'/git/commits/{sha}': {'sha': sha, 'tree': {'sha': trees[0]}},
            f'/git/blobs/{BLOB}': {
                'sha': BLOB, 'size': 2151, 'encoding': 'base64',
                'content': base64.encodebytes(FIXTURE.read_bytes()).decode(),
            },
        }
        for index, (oid, part) in enumerate(zip(trees, parts)):
            entry = {'path': part, 'mode': '040000', 'type': 'tree'}
            if index == len(parts) - 1:
                entry.update(mode='100644', type='blob', sha=BLOB, size=2151)
                self.leaf = entry
            else:
                entry['sha'] = trees[index + 1]
            self.responses[f'/git/trees/{oid}'] = {'sha': oid, 'truncated': False, 'tree': [entry]}
        self.calls = []

    def get(self, endpoint):
        self.calls.append(endpoint)
        return self.responses[endpoint]


@pytest.mark.parametrize('sha', [HEAD, OLDER])
def test_pinned_main_or_ancestor_returns_exact_approved_bytes(sha):
    github = GitHubFixture(sha)
    run_id = uuid4()
    result = source._verify(run_id, sha, github)
    assert (result.run_id, result.commit_sha, result.main_sha, result.blob_sha) == (run_id, sha, HEAD, BLOB)
    assert result.contents == FIXTURE.read_bytes()
    assert github.calls.count(source.REF_ENDPOINT) == 1
    assert f'/git/commits/{sha}' in github.calls
    if sha != HEAD:
        assert f'/git/commits/{HEAD}' not in github.calls


@pytest.mark.parametrize('key,value,error', [
    ('ref', 'refs/heads/other', 'unexpected main ref'),
    ('merge_base_commit', {'sha': HEAD}, 'outside the main snapshot'),
    ('status', 'diverged', 'outside the main snapshot'),
    ('sha', HEAD, 'commit SHA mismatch'),
])
def test_rejects_other_ref_commit_or_mismatched_commit_identity(key, value, error):
    github = GitHubFixture(OLDER)
    endpoint = {'ref': source.REF_ENDPOINT, 'sha': f'/git/commits/{OLDER}'}.get(
        key, f'/compare/{OLDER}...{HEAD}?per_page=1')
    github.responses[endpoint][key] = value
    with pytest.raises(source.SourceRejected, match=error):
        source._verify(uuid4(), OLDER, github)
    assert f'/git/blobs/{BLOB}' not in github.calls


@pytest.mark.parametrize('changes', [
    {'mode': '120000'}, {'mode': '160000', 'type': 'commit'},
    {'path': 'other.py'}, {'size': 2152},
])
def test_rejects_invalid_path_type_or_size_before_fetching_blob(changes):
    github = GitHubFixture(HEAD)
    github.leaf.update(changes)
    with pytest.raises(source.SourceRejected):
        source._verify(uuid4(), HEAD, github)
    assert f'/git/blobs/{BLOB}' not in github.calls


@pytest.mark.parametrize('tamper,error', [
    ('bytes', 'approved bytes'), ('size', 'source size'), ('encoding', 'source encoding'),
    ('identity', 'blob SHA mismatch'),
])
def test_rejects_blob_tampering(tamper, error):
    github = GitHubFixture(HEAD)
    blob = github.responses[f'/git/blobs/{BLOB}']
    if tamper == 'bytes':
        blob['content'] = base64.b64encode(b'X' + FIXTURE.read_bytes()[1:]).decode()
    elif tamper == 'size':
        blob['content'] = 'A' * 4097
    elif tamper == 'encoding':
        blob['content'] = '!'
    else:
        oid = 'f' * 40
        blob['sha'] = github.leaf['sha'] = oid
        github.responses[f'/git/blobs/{oid}'] = blob
    with pytest.raises(source.SourceRejected, match=error):
        source._verify(uuid4(), HEAD, github)


@pytest.mark.parametrize('response,error', [
    (b'{}\n302', 'status'), (b'X' * (source.MAX_RESPONSE + 1) + b'\n200', 'size'),
    (b'not json\n200', 'JSON'), (subprocess.TimeoutExpired('curl', 5), 'timed out'),
    (subprocess.CalledProcessError(7, 'curl'), 'transport failed'),
    (subprocess.CalledProcessError(63, 'curl'), 'response limit'),
])
def test_transport_bounds_and_fail_closed(monkeypatch, response, error):
    def run(args, **kwargs):
        assert args[:2] == ['curl', '-q']
        if args[-1] == '--version':
            return subprocess.CompletedProcess(args, 0, b'curl 8.4.0')
        assert args[-1] == source.API + source.REF_ENDPOINT
        assert '--location' not in args and '--compressed' not in args
        assert args[args.index('--max-filesize') + 1] == str(source.MAX_RESPONSE)
        assert 0 < kwargs['timeout'] <= source.REQUEST_TIMEOUT
        if isinstance(response, Exception):
            raise response
        return subprocess.CompletedProcess(args, 0, response)

    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(source.SourceRejected, match=error):
        source._GitHub().get(source.REF_ENDPOINT)


def test_total_deadline_stops_before_next_request(monkeypatch):
    monkeypatch.setattr(source, '_curl', lambda *args: b'curl 8.5.0')
    github = source._GitHub()
    github.deadline = source.time.monotonic() - 1
    monkeypatch.setattr(source, '_curl', lambda *args: pytest.fail('expired request sent'))
    with pytest.raises(source.SourceRejected, match='timed out'):
        github.get(source.REF_ENDPOINT)


@pytest.mark.parametrize('change', [None, 'id', 'sha', 'missing'])
def test_queued_binding_rechecks_db_after_transport(monkeypatch, change, capsys):
    run_id = uuid4()
    initial = {'id': run_id, 'commit_sha': HEAD}
    next_row = {'id': uuid4() if change == 'id' else run_id,
                'commit_sha': OLDER if change == 'sha' else HEAD}
    rows = iter([initial, None if change == 'missing' else next_row])
    opened = False

    @contextmanager
    def factory():
        nonlocal opened
        opened = True
        try:
            class Connection:
                def execute(self, sql, params):
                    assert params == ('kenobix/Claude_practice', 'refs/heads/main', 'fixture-v1')
                    return self

                def fetchone(self):
                    return next(rows)

            yield Connection()
        finally:
            opened = False

    def github():
        assert not opened
        return GitHubFixture(HEAD)

    monkeypatch.setattr(source, '_GitHub', github)
    monkeypatch.setattr(source.database, 'connect', factory)
    assert source.main([]) == (1 if change else 0)
    output = capsys.readouterr()
    if change:
        assert 'run or SHA changed' in output.err and not output.out
    else:
        receipt = json.loads(output.out)
        assert receipt['run_id'] == str(run_id) and receipt['commit_sha'] == HEAD
        assert 'contents' not in receipt
