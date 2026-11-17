from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4
import threading

import pytest

from ci_runner import database, runs
from ci_runner.finalization import finalize_fixture
from test_logs_postgres import logged, ledger
from test_preflight import issue_claim
from test_runs_postgres import api, dedicated_database

pytestmark = pytest.mark.integration


def test_official_streams_paging_and_read_only_accounting(dedicated_database):
    run, claim = issue_claim()
    result = logged(claim, b'hello & <script>', b'err\n')
    finalize_fixture(claim, result)
    before = ledger()
    with api() as (client, _):
        path = f'/runs/{run.id}/logs'
        first = client.get(path, params={'limit': 2})
        assert first.status_code == 200 and first.text == 'he'
        assert first.headers['x-log-next-offset'] == '2'
        assert first.headers['x-log-attempt-id'] == str(claim.attempt_id)
        assert first.headers['x-log-total-bytes'] == str(len(result.stdout))
        assert first.headers['x-log-retained-bytes'] == str(len(result.stdout)+len(result.stderr))
        assert first.headers['x-log-truncated'] == 'false'
        remaining = client.get(path, params={'offset': 2})
        assert first.text+remaining.text == result.stdout.decode()
        assert 'x-log-next-offset' not in remaining.headers
        error_stream = client.get(path, params={'stream': 'stderr', 'offset': 1, 'limit': 2})
        assert error_stream.text == 'rr' and error_stream.headers['x-log-next-offset'] == '3'
        view = client.get(path, params={'format': 'html'})
        assert view.text == '<pre>\nhello &amp; &lt;script&gt;</pre>'
    assert ledger() == before


def test_page_boundaries_empty_stream_and_eof(dedicated_database):
    run, claim = issue_claim()
    finalize_fixture(claim, logged(claim, b'x'*1048576, b''))
    with api() as (client, _):
        path = f'/runs/{run.id}/logs'
        page = client.get(path, params={'limit': 16384})
        assert len(page.content) == 16384 and page.headers['x-log-next-offset'] == '16384'
        last = client.get(path, params={'offset': 1048573, 'limit': 16384})
        assert last.text == 'xxx' and last.headers['x-log-bytes'] == '3'
        eof = client.get(path, params={'offset': 1048576})
        assert eof.status_code == 200 and eof.content == b'' and 'x-log-next-offset' not in eof.headers
        empty = client.get(path, params={'stream': 'stderr'})
        assert empty.status_code == 200 and empty.content == b''
        invalid = client.get(path, params={'stream': 'stderr', 'offset': 1})
        assert invalid.status_code == 416 and invalid.headers['x-log-total-bytes'] == '0'
        assert invalid.headers['cache-control'] == 'no-store'


def test_utf8_byte_boundaries_and_binary_display_are_lossy_only_in_response(dedicated_database):
    run, claim = issue_claim()
    raw = b'\xc3\xa9\0\xff\x1b'
    finalize_fixture(claim, logged(claim, raw, b''))
    with api() as (client, _):
        path = f'/runs/{run.id}/logs'
        assert client.get(path).text == 'é\\u0000\ufffd\\u001b'
        assert client.get(path, params={'limit': 1}).text == '\ufffd'
        assert client.get(path, params={'offset': 1, 'limit': 1}).text == '\ufffd'
        assert client.get(path, params={'limit': 2}).text == 'é'
    with database.connect() as conn:
        assert conn.execute('SELECT stdout FROM attempt_logs WHERE attempt_id=%s',
                            (claim.attempt_id,)).fetchone()['stdout'] == raw


def test_unfinalized_staged_and_missing_logs_are_not_returned(dedicated_database):
    run = runs.create_run(database.connect, 'a'*40)[0]
    with api() as (client, _):
        path = f'/runs/{run.id}/logs'
        assert client.get(f'/runs/{uuid4()}/logs').status_code == 404
        assert client.get(path).status_code == 409
        _, claim = issue_claim()
        with database.connect() as conn:
            # Deliberately staged row, not an officially committed result.
            conn.execute('INSERT INTO attempt_logs(attempt_id,stdout,stderr,observed_bytes) VALUES (%s,%s,%s,7)',
                         (claim.attempt_id, b'private', b''))
        response = client.get(path)
        assert response.status_code == 409 and 'private' not in response.text
        # Simulate the old schema's terminal result without persisted logs.
        with database.connect() as conn:
            other = runs.create_run(database.connect, 'b'*40)[0]
            conn.execute("UPDATE runs SET state='failed' WHERE id=%s", (other.id,))
        assert client.get(f'/runs/{other.id}/logs').status_code == 409  # No official attempt reference.
        with database.connect() as conn:
            second = conn.execute("INSERT INTO attempts(run_id,attempt_no,worker_id,lease_token,lease_expires_at,"
                                  "finished_at,state,retry_kind) VALUES (%s,1,'old',gen_random_uuid(),"
                                  "now()+interval '30 seconds',now(),'failed','initial') RETURNING id", (other.id,)).fetchone()
            conn.execute('UPDATE runs SET completed_attempt_id=%s WHERE id=%s', (second['id'], other.id))
        missing = client.get(f'/runs/{other.id}/logs')
        assert missing.status_code == 404 and missing.json() == {'detail': 'logs not found'}


def test_only_official_attempt_is_read_even_with_other_history(dedicated_database):
    run, claim = issue_claim()
    finalize_fixture(claim, logged(claim, b'old hidden', b''))
    with database.connect() as conn:
        # Model history directly; this does not implement or prove Worker retries.
        second = conn.execute("INSERT INTO attempts(run_id,attempt_no,worker_id,lease_token,lease_expires_at,"
                              "finished_at,state,retry_kind) VALUES (%s,2,'new',gen_random_uuid(),"
                              "now()+interval '30 seconds',now(),'failed','manual') RETURNING id", (run.id,)).fetchone()
        conn.execute('INSERT INTO attempt_logs(attempt_id,stdout,stderr,observed_bytes) VALUES (%s,%s,%s,8)',
                     (second['id'], b'official', b''))
        conn.execute("UPDATE runs SET state='failed',completed_attempt_id=%s WHERE id=%s", (second['id'], run.id))
    with api() as (client, _):
        response = client.get(f'/runs/{run.id}/logs')
        assert response.text == 'official' and response.headers['x-log-attempt-id'] == str(second['id'])
        with database.connect() as conn:
            conn.execute("UPDATE runs SET state='running' WHERE id=%s", (run.id,))
        assert client.get(f'/runs/{run.id}/logs').status_code == 409


def test_reader_sees_no_partial_result_before_commit(dedicated_database):
    run, claim = issue_claim()
    ready, release = threading.Event(), threading.Event()
    @contextmanager
    def held_commit():
        with database.connect() as conn:
            yield conn
            ready.set()
            assert release.wait(timeout=5)
    with api() as (client, _), ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(finalize_fixture, claim, logged(claim, b'committed', b''), held_commit)
        try:
            assert ready.wait(timeout=3)
            assert client.get(f'/runs/{run.id}/logs').status_code == 409
        finally:
            release.set()
        assert pending.result(timeout=5).state == 'succeeded'
        assert client.get(f'/runs/{run.id}/logs').text == 'committed'
