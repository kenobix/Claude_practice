import asyncio
import base64
import json

from fastapi.testclient import TestClient
import pytest

from ci_runner.app import create_app
from ci_runner.body_limit import JsonBodyLimit


def no_database():
    raise AssertionError('invalid input must never open a database connection')


client = TestClient(create_app(no_database))
VALID = {'repository_id': 'kenobix/Claude_practice', 'commit_sha': 'a' * 40}


@pytest.mark.parametrize('update', [
    {'repository_id': 'https://example.invalid/repo.git'}, {'repository_id': 'other/repo'},
    {'commit_sha': 'A' * 40}, {'commit_sha': 'abc'}, {'commit_sha': 123},
    {'url': 'https://example.invalid'}, {'command': 'echo x'}, {'yaml': 'steps: []'},
    {'ref': 'refs/heads/other'}, {'workflow_version': 'arbitrary'},
])
def test_invalid_or_unallowed_input_is_rejected_before_db(update):
    response = client.post('/runs', json=VALID | update)
    assert response.status_code == 422


@pytest.mark.parametrize('path', ['/runs/not-a-uuid', '/runs?limit=0', '/runs?limit=101',
                                 '/runs?cursor=bad!', '/runs?cursor=', '/runs?cursor=' + 'a' * 257])
def test_invalid_identifiers_and_paging_are_rejected_before_db(path):
    assert client.get(path).status_code == 422


def test_content_types_sizes_and_malformed_json():
    assert client.post('/runs', content='steps: []', headers={'Content-Type': 'application/yaml'}).status_code == 415
    assert client.post('/runs', content=b'x' * 4097, headers={'Content-Type': 'application/json'}).status_code == 413
    assert client.post('/runs', content='{', headers={'Content-Type': 'application/json'}).status_code == 422
    response = client.post('/runs', json=VALID | {'command': 'public-canary-never-reflect'})
    assert 'public-canary-never-reflect' not in response.text


@pytest.mark.parametrize('chunks,headers,expected', [
    ([b'x' * 2048, b'x' * 2049], [], 413),
    ([b'x' * 4096], [], 204),
    ([b'{}'], [(b'content-length', b'4097')], 413),
    ([b'{}'], [(b'content-length', b'0')], 400),
    ([b'{}'], [(b'content-length', b'bad')], 400),
    ([b'{}'], [(b'content-length', b'2'), (b'content-length', b'2')], 400),
])
def test_streamed_body_limits_without_trusting_content_length(chunks, headers, expected):
    async def exercise():
        sent = []
        called = False
        messages = iter({'type': 'http.request', 'body': chunk, 'more_body': i < len(chunks) - 1}
                        for i, chunk in enumerate(chunks))
        async def receive():
            return next(messages)
        async def send(value):
            sent.append(value)
        async def downstream(scope, receive, send):
            nonlocal called
            called = True
            assert len((await receive())['body']) <= 4096
            await send({'type': 'http.response.start', 'status': 204, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})
        await JsonBodyLimit(downstream, path='/runs', limit=4096)(
            {'type': 'http', 'method': 'POST', 'path': '/runs',
             'headers': [(b'content-type', b'application/json'), *headers]}, receive, send)
        assert sent[0]['status'] == expected
        assert called == (expected == 204)
    asyncio.run(exercise())


@pytest.mark.parametrize('cursor_data', [
    {}, {'created_at': '2000-01-01T00:00:00', 'id': '00000000-0000-0000-0000-000000000001'},
    {'created_at': '2000-01-01T00:00:00Z', 'id': 'invalid'},
    {'created_at': '2000-01-01T00:00:00Z', 'id': '00000000-0000-0000-0000-000000000001', 'extra': 1},
])
def test_structurally_invalid_cursors_are_rejected(cursor_data):
    value = base64.urlsafe_b64encode(json.dumps(cursor_data).encode()).decode().rstrip('=')
    assert client.get('/runs', params={'cursor': value}).status_code == 422
