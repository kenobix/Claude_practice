"""Read and verify a queued run's fixed source without claiming or executing it."""
import base64
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from uuid import UUID

import psycopg

from . import database, worker
from .settings import POLICY

PATH = '261117/ci_runner/fixtures/job/fixture.py'
SIZE = 2151
DIGEST = '9c567875e01f89cc15eb23d1b3d2d40c2c23239cc414ff12a1b1c3902dd3f7ff'
API = f'https://api.github.com/repos/{POLICY.repository_id}'
REF_ENDPOINT = f'/git/ref/{POLICY.ref.removeprefix("refs/")}'
MAX_RESPONSE = 1024 * 1024
TOTAL_TIMEOUT = 30
REQUEST_TIMEOUT = 5


class SourceRejected(Exception):
    """Verification failed. Messages contain only fixed descriptions."""


@dataclass(frozen=True)
class VerifiedSource:
    run_id: UUID
    commit_sha: str
    main_sha: str
    blob_sha: str
    contents: bytes = field(repr=False)


def _oid(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise SourceRejected('invalid object ID')
    return value


def _object(value):
    if not isinstance(value, dict):
        raise SourceRejected('invalid GitHub response')
    return value


def _curl(args, timeout):
    try:
        return subprocess.run(['curl', '-q', *args], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=timeout, check=True).stdout
    except subprocess.TimeoutExpired:
        raise SourceRejected('GitHub acquisition timed out') from None
    except (OSError, subprocess.CalledProcessError):
        raise SourceRejected('GitHub transport failed or exceeded response limit') from None


class _GitHub:
    def __init__(self):
        self.deadline = time.monotonic() + TOTAL_TIMEOUT
        version = re.match(rb'curl (\d+)\.(\d+)\.(\d+)\b', _curl(['--version'], REQUEST_TIMEOUT))
        # Older curl does not enforce --max-filesize on unknown-length bodies.
        if not version or tuple(map(int, version.groups())) < (8, 4, 0):
            raise SourceRejected('curl 8.4 or newer is required')

    def get(self, endpoint):
        remaining = min(REQUEST_TIMEOUT, self.deadline - time.monotonic())
        if remaining < 0.01:
            raise SourceRejected('GitHub acquisition timed out')
        response = _curl([
            '--silent', '--fail', '--proto', '=https', '--globoff',
            '--max-time', f'{remaining:.3f}', '--max-filesize', str(MAX_RESPONSE),
            '--header', 'Accept: application/vnd.github+json',
            '--header', 'Accept-Encoding: identity',
            '--header', 'X-GitHub-Api-Version: 2022-11-28',
            '--write-out', '\n%{http_code}', '--url', API + endpoint,
        ], remaining)
        body, _, status = response.rpartition(b'\n')
        if status != b'200' or len(body) > MAX_RESPONSE:
            raise SourceRejected('unexpected GitHub status or response size')
        try:
            return _object(json.loads(body))
        except (ValueError, RecursionError):
            raise SourceRejected('invalid GitHub JSON') from None


def _verify(run_id, commit_sha, github):
    commit_sha = _oid(commit_sha)
    ref = github.get(REF_ENDPOINT)
    target = _object(ref.get('object'))
    if ref.get('ref') != POLICY.ref or target.get('type') != 'commit':
        raise SourceRejected('unexpected main ref')
    main_sha = _oid(target.get('sha'))

    if commit_sha != main_sha:
        comparison = github.get(f'/compare/{commit_sha}...{main_sha}?per_page=1')
        base = _object(comparison.get('base_commit'))
        common = _object(comparison.get('merge_base_commit'))
        if (base.get('sha') != commit_sha or common.get('sha') != commit_sha
                or comparison.get('status') != 'ahead'
                or comparison.get('behind_by') != 0):
            raise SourceRejected('commit is outside the main snapshot')

    commit = github.get(f'/git/commits/{commit_sha}')
    if commit.get('sha') != commit_sha:
        raise SourceRejected('commit SHA mismatch')
    oid = _oid(_object(commit.get('tree')).get('sha'))
    parts = PATH.split('/')
    for index, component in enumerate(parts):
        tree = github.get(f'/git/trees/{oid}')
        entries = tree.get('tree')
        if tree.get('sha') != oid or tree.get('truncated') is not False or not isinstance(entries, list):
            raise SourceRejected('unexpected or truncated Git tree')
        matches = [entry for entry in entries if _object(entry).get('path') == component]
        if len(matches) != 1:
            raise SourceRejected('fixed source path is missing or ambiguous')
        entry = matches[0]
        expected = ('100644', 'blob') if index == len(parts) - 1 else ('040000', 'tree')
        if (entry.get('mode'), entry.get('type')) != expected:
            raise SourceRejected('unexpected Git path entry type')
        oid = _oid(entry.get('sha'))

    if entry.get('size') != SIZE:
        raise SourceRejected('unexpected source size in Git tree')
    blob = github.get(f'/git/blobs/{oid}')
    encoded = blob.get('content')
    if (blob.get('size') != SIZE
            or blob.get('sha') != oid or blob.get('encoding') != 'base64'
            or not isinstance(encoded, str) or len(encoded) > 4096):
        raise SourceRejected('unexpected source size or blob metadata')
    try:
        contents = base64.b64decode(encoded.replace('\n', ''), validate=True)
    except ValueError:
        raise SourceRejected('invalid source encoding') from None
    if len(contents) != SIZE or hashlib.sha256(contents).hexdigest() != DIGEST:
        raise SourceRejected('source does not match approved bytes')
    git_header = f'blob {SIZE}\0'.encode('ascii')
    if hashlib.sha1(git_header + contents, usedforsecurity=False).hexdigest() != oid:
        raise SourceRejected('Git blob SHA mismatch')
    return VerifiedSource(run_id, commit_sha, main_sha, oid, contents)


def _candidate(factory):
    with factory() as conn:
        row = conn.execute(worker.CANDIDATE_SQL, (
            POLICY.repository_id, POLICY.ref, POLICY.workflow_version)).fetchone()
    return (row['id'], row['commit_sha']) if row else None


def verify_queued_source(connection_factory=None):
    """Read the candidate again after I/O. The result is a snapshot, not a claim."""
    factory = connection_factory or database.connect
    candidate = _candidate(factory)
    if candidate is None:
        return None
    verified = _verify(*candidate, _GitHub())
    if (verified.run_id, verified.commit_sha) != candidate or _candidate(factory) != candidate:
        raise SourceRejected('queued run or SHA changed during acquisition')
    return verified


def main(argv=None):
    import argparse

    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        verified = verify_queued_source()
    except SourceRejected as exc:
        print(f'source verification refused: {exc}', file=sys.stderr)
        return 1
    except (psycopg.Error, database.DatabaseUnavailable):
        print('source verification unavailable: database error', file=sys.stderr)
        return 1
    if verified is None:
        print(json.dumps({'verified': False}))
    else:
        print(json.dumps({'verified': True, 'run_id': str(verified.run_id),
                          'commit_sha': verified.commit_sha, 'main_sha': verified.main_sha,
                          'blob_sha': verified.blob_sha, 'sha256': DIGEST}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
