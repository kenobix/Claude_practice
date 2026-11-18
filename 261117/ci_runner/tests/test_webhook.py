import asyncio
import hashlib
import hmac
import json
import secrets
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from ci_runner import webhook
from ci_runner.body_limit import JsonBodyLimit
from ci_runner.settings import GITHUB_REPOSITORY_ID, POLICY

HOOK = '123456'


@pytest.fixture
def webhook_secret(monkeypatch):
    value = secrets.token_hex(32)
    monkeypatch.setenv('CI_GITHUB_WEBHOOK_HOOK_ID', HOOK)
    monkeypatch.setenv('CI_GITHUB_WEBHOOK_SECRET', value)
    return value.encode()


def push_body(**updates):
    payload = {'repository': {'id': GITHUB_REPOSITORY_ID, 'full_name': POLICY.repository_id},
               'ref': POLICY.ref, 'deleted': False, 'after': 'a' * 40}
    return json.dumps(payload | updates, ensure_ascii=False, indent=2).encode('utf-8')


def signed_headers(secret, raw, *, delivery=None, event='push'):
    return {'Content-Type': 'application/json', 'X-GitHub-Hook-ID': HOOK,
            'X-GitHub-Delivery': delivery or str(uuid4()), 'X-GitHub-Event': event,
            'X-Hub-Signature-256': 'sha256=' + hmac.new(secret, raw, 'sha256').hexdigest()}


def no_db():
    pytest.fail('request unexpectedly reached PostgreSQL')


def test_raw_utf8_body_and_success_receipt(webhook_secret, monkeypatch):
    seen = []
    run_id = str(uuid4())

    def save(_factory, hook, delivery, digest, sha):
        seen.append((hook, delivery, digest, sha))
        return {'status': 'accepted', 'run_id': run_id}

    monkeypatch.setattr(webhook, '_save_delivery', save)
    raw = push_body(commits=[{'message': '検証'}])
    headers = signed_headers(webhook_secret, raw)
    with TestClient(webhook.create_app(no_db)) as client:
        # Equivalent JSON with different byte representation fails the original signature.
        assert client.post(webhook.PATH, content=json.dumps(json.loads(raw)), headers=headers).status_code == 403
        response = client.post(webhook.PATH, content=raw, headers=headers)
        assert client.get('/runs').status_code == 404
        assert client.get('/openapi.json').status_code == 404
        assert client.post(webhook.PATH + '/', content=raw, headers=headers).status_code == 404
    assert response.status_code == 202
    assert response.json() == {'status': 'accepted', 'run_id': run_id}
    assert seen == [(HOOK, headers['X-GitHub-Delivery'], hashlib.sha256(raw).hexdigest(), 'a' * 40)]


@pytest.mark.parametrize('header,value,status', [
    ('X-Hub-Signature-256', None, 403),
    ('X-Hub-Signature-256', 'sha1=' + 'f' * 40, 403),
    ('X-Hub-Signature-256', 'sha256=' + 'f' * 64, 403),
    ('X-GitHub-Hook-ID', '9876', 403),
    ('X-GitHub-Delivery', 'wrong', 400),
    ('X-GitHub-Event', None, 400),
    ('Content-Type', 'application/x-www-form-urlencoded', 415),
])
def test_invalid_headers_never_touch_database(webhook_secret, header, value, status):
    raw = push_body()
    headers = signed_headers(webhook_secret, raw)
    if value is None:
        del headers[header]
    else:
        headers[header] = value
    with TestClient(webhook.create_app(no_db)) as client:
        assert client.post(webhook.PATH, content=raw, headers=headers).status_code == status


def test_duplicate_signature_or_delivery_header_is_rejected(webhook_secret):
    raw = push_body()
    headers = signed_headers(webhook_secret, raw)
    with TestClient(webhook.create_app(no_db)) as client:
        for key, status in [('X-Hub-Signature-256', 403), ('X-GitHub-Delivery', 400)]:
            duplicated = [*headers.items(), (key, headers[key])]
            assert client.post(webhook.PATH, content=raw, headers=duplicated).status_code == status


@pytest.mark.parametrize('raw', [
    b'{broken', b'{"n":"\xff"}', b'[]', b'{"ref":"one","ref":"two"}', b'{"n":NaN}',
    push_body(after='0' * 40), push_body(after='A' * 40), push_body(deleted='false'),
    push_body(repository={'id': True, 'full_name': POLICY.repository_id}),
])
def test_malformed_signed_payloads_return_generic_error(webhook_secret, raw):
    headers = signed_headers(webhook_secret, raw)
    with TestClient(webhook.create_app(no_db)) as client:
        response = client.post(webhook.PATH, content=raw, headers=headers)
    assert response.status_code == 400
    assert raw.decode('utf-8', errors='replace') not in response.text


@pytest.mark.parametrize('event,updates', [
    ('ping', {}), ('push', {'deleted': True}), ('push', {'ref': 'refs/heads/other'}),
    ('push', {'repository': {'id': 1, 'full_name': POLICY.repository_id}}),
    ('push', {'repository': {'id': GITHUB_REPOSITORY_ID, 'full_name': 'other/repo'}}),
])
def test_signed_out_of_scope_payloads_cannot_schedule_a_run(webhook_secret, monkeypatch, event, updates):
    def save(_factory, _hook, _delivery, _digest, sha):
        assert sha is None
        return {'status': 'ignored', 'run_id': None}

    monkeypatch.setattr(webhook, '_save_delivery', save)
    raw = push_body(**updates)
    with TestClient(webhook.create_app(no_db)) as client:
        response = client.post(webhook.PATH, content=raw, headers=signed_headers(webhook_secret, raw, event=event))
    assert response.status_code == 200
    assert response.json() == {'status': 'ignored', 'run_id': None}


def test_disabled_without_configuration(monkeypatch):
    monkeypatch.delenv('CI_GITHUB_WEBHOOK_SECRET', raising=False)
    with TestClient(webhook.create_app(no_db)) as client:
        response = client.post(webhook.PATH, json={})
    assert response.status_code == 503


def test_body_boundary_and_chunked_overflow(webhook_secret, monkeypatch):
    monkeypatch.setattr(webhook, '_save_delivery', lambda *args: {'status': 'accepted', 'run_id': None})
    raw = push_body().ljust(webhook.BODY_LIMIT, b' ')
    headers = signed_headers(webhook_secret, raw)
    with TestClient(webhook.create_app(no_db)) as client:
        assert client.post(webhook.PATH, content=raw, headers=headers).status_code == 202
        assert client.post(webhook.PATH, content=raw + b'x', headers=headers).status_code == 413
        chunks = (chunk for chunk in (raw[:1024], raw[1024:], b'x'))
        assert client.post(webhook.PATH, content=chunks, headers=headers).status_code == 413


def test_slow_body_expires_before_downstream_application():
    async def slow():
        await asyncio.sleep(1)
        pytest.fail('slow body was not bounded')

    async def downstream(*args):
        pytest.fail('slow body reached downstream')

    sent = []

    async def send(message):
        sent.append(message)

    scope = {'type': 'http', 'method': 'POST', 'path': webhook.PATH,
             'headers': [(b'content-type', b'application/json')]}
    app = JsonBodyLimit(downstream, path=webhook.PATH, limit=webhook.BODY_LIMIT, read_timeout=0.01)
    asyncio.run(app(scope, slow, send))
    assert sent[0]['status'] == 408
