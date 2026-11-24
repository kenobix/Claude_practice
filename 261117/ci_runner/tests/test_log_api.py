from contextlib import contextmanager
from html.parser import HTMLParser
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
import pytest

from ci_runner.app import create_app
from ci_runner.logs import MAX_PAGE_BYTES


def no_database():
    pytest.fail('invalid log request accessed DB')


@pytest.mark.parametrize('suffix', [
    '/runs/bad/logs', '/runs/{id}/logs?stream=private-canary',
    '/runs/{id}/logs?format=private-canary', '/runs/{id}/logs?offset=-1',
    '/runs/{id}/logs?offset=1048577', '/runs/{id}/logs?limit=0',
    '/runs/{id}/logs?limit=16385', '/runs/{id}/logs?offset=1.5',
])
def test_invalid_log_request_never_accesses_db(suffix):
    response = TestClient(create_app(no_database), base_url='http://127.0.0.1').get(suffix.format(id=uuid4()))
    assert response.status_code == 422 and 'private-canary' not in response.text


def client_with_page(chunk, total=None):
    attempt_id = uuid4()
    @contextmanager
    def factory():
        def execute(sql, params):
            assert 'substring(l.stdout FROM %s FOR %s)' in sql
            assert params[:2] == (1, MAX_PAGE_BYTES)
            row = {'state': 'succeeded', 'attempt_state': 'succeeded',
                   'completed_attempt_id': attempt_id, 'log_attempt_id': attempt_id,
                   'chunk': chunk, 'total_bytes': len(chunk) if total is None else total,
                   'retained_bytes': len(chunk), 'observed_bytes': len(chunk)+10}
            return SimpleNamespace(fetchone=lambda: row)
        yield SimpleNamespace(execute=execute)
    return TestClient(create_app(factory), base_url='http://127.0.0.1'), attempt_id


@pytest.mark.parametrize('format', ['text', 'html'])
def test_safe_rendering_and_metadata(format):
    raw = b'</pre><script>alert(1)</script><img src=x onerror=x> & "\'\x00\x1b\xff\r\n\t' + '\u202e'.encode()
    client, attempt_id = client_with_page(raw, len(raw)+2)
    response = client.get(f'/runs/{uuid4()}/logs?format={format}&limit={MAX_PAGE_BYTES}')
    assert response.status_code == 200
    assert response.headers['content-type'] == f'text/{"html" if format == "html" else "plain"}; charset=utf-8'
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert "default-src 'none'" in response.headers['content-security-policy']
    assert 'sandbox' in response.headers['content-security-policy']
    assert response.headers['x-log-attempt-id'] == str(attempt_id)
    assert response.headers['x-log-next-offset'] == str(len(raw))
    assert response.headers['x-log-truncated'] == 'true'
    assert response.headers['x-log-observed-bytes'] == str(len(raw)+10)
    assert '\\u0000\\u001b\ufffd\\u000d\n\t\\u202e' in response.text
    if format == 'html':
        class Tags(HTMLParser):
            def __init__(self):
                super().__init__()
                self.names = []
            def handle_starttag(self, tag, attrs):
                self.names.append(tag)
        parsed = Tags()
        parsed.feed(response.text)
        assert parsed.names == ['pre']
        assert '&lt;script&gt;' in response.text and '&quot;' in response.text and '&#x27;' in response.text
    else:
        assert '<script>' in response.text  # Literal text, with an explicit safe media type.


@pytest.mark.parametrize('raw,format', [(b"'"*MAX_PAGE_BYTES, 'html'), (b'\0'*MAX_PAGE_BYTES, 'text')])
def test_maximum_page_has_bounded_rendered_response(raw, format):
    client, _ = client_with_page(raw)
    response = client.get(f'/runs/{uuid4()}/logs?format={format}&limit={MAX_PAGE_BYTES}')
    assert response.status_code == 200 and len(response.content) <= MAX_PAGE_BYTES*6+12
    assert len(response.content) == MAX_PAGE_BYTES*6+(12 if format == 'html' else 0)
    assert response.headers['x-log-bytes'] == str(MAX_PAGE_BYTES)
    assert 'x-log-next-offset' not in response.headers


def test_db_commit_failure_does_not_return_log_content():
    @contextmanager
    def unavailable():
        yield SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchone=lambda: None))
        raise psycopg.OperationalError('private-log-db-canary')
    response = TestClient(create_app(unavailable), base_url='http://127.0.0.1').get(f'/runs/{uuid4()}/logs')
    assert response.status_code == 503 and response.headers['retry-after'] == '1'
    assert response.json() == {'detail': 'database unavailable'}
