from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
import shutil
import subprocess
import time
from uuid import UUID

import httpx
import psycopg
import pytest

from ci_runner.webhook import PATH
from test_runs_postgres import ROOT, api, dedicated_database, payload
from test_webhook import HOOK, push_body, signed_headers, webhook_secret

pytestmark = pytest.mark.integration


@pytest.fixture
def tls_files(tmp_path):
    openssl = shutil.which('openssl')
    if not openssl:
        pytest.skip('the loopback HTTPS test requires OpenSSL')
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1',
                    '-out', str(cert), '-keyout', str(key)], check=True, capture_output=True, timeout=15)
    return cert, key


def saved_deliveries(db):
    with psycopg.connect(db.dsn) as conn:
        return conn.execute('SELECT hook_id, delivery_id, body_sha256, result, run_id FROM deliveries '
                            'ORDER BY delivery_id').fetchall()


def run_ids(db):
    with psycopg.connect(db.dsn) as conn:
        return [row[0] for row in conn.execute('SELECT id FROM runs ORDER BY id')]


def test_https_signed_delivery_and_ten_redeliveries(dedicated_database, webhook_secret, tls_files):
    raw = push_body()
    headers = signed_headers(webhook_secret, raw)
    with api(webhook=True, tls=tls_files) as (client, _):
        invalid = headers | {'X-Hub-Signature-256': 'sha256=' + '0' * 64}
        assert client.post(PATH, content=raw, headers=invalid).status_code == 403
        assert saved_deliveries(dedicated_database) == run_ids(dedicated_database) == []

        accepted = client.post(PATH, content=raw, headers=headers)
        assert accepted.status_code == 202
        run_id = UUID(accepted.json()['run_id'])
        assert run_ids(dedicated_database) == [run_id]

        def deliver(request_headers):
            return client.post(PATH, content=raw, headers=request_headers)

        with ThreadPoolExecutor(max_workers=10) as pool:
            replays = list(pool.map(deliver, [headers] * 10))
        assert all(r.status_code == 202 and r.json() == accepted.json() for r in replays)
        longest = max(r.elapsed.total_seconds() for r in [accepted, *replays])
        assert longest < 10
        print(f'longest loopback HTTPS accepted response: {longest:.3f}s')
        assert saved_deliveries(dedicated_database) == [
            (HOOK, headers['X-GitHub-Delivery'], hashlib.sha256(raw).hexdigest(), 'accepted', run_id)]

        changed = push_body(after='b' * 40)
        changed_headers = signed_headers(webhook_secret, changed, delivery=headers['X-GitHub-Delivery'])
        assert client.post(PATH, content=changed, headers=changed_headers).status_code == 409
        assert run_ids(dedicated_database) == [run_id]
        assert client.get('/runs').status_code == 404

        new_delivery = signed_headers(webhook_secret, raw)
        with ThreadPoolExecutor(max_workers=2) as pool:
            duplicate_pushes = list(pool.map(deliver, [new_delivery] * 2))
        assert all(r.status_code == 202 and r.json() == accepted.json() for r in duplicate_pushes)
        deletion = push_body(deleted=True, after='0' * 40)
        ignored = client.post(PATH, content=deletion, headers=signed_headers(webhook_secret, deletion))
        assert ignored.status_code == 200 and ignored.json() == {'status': 'ignored', 'run_id': None}
    assert len(saved_deliveries(dedicated_database)) == 3
    with api() as (client, _):
        manual = client.post('/runs', json=payload())
        assert manual.status_code == 200 and manual.json()['id'] == str(run_id)
    assert run_ids(dedicated_database) == [run_id]


@pytest.mark.parametrize('position,count', [('before_commit', 0), ('after_commit', 1)])
def test_receiver_sigkill_and_redelivery(dedicated_database, webhook_secret, tmp_path, position, count):
    marker = tmp_path / 'reached'
    raw = push_body()
    headers = signed_headers(webhook_secret, raw)
    with api(position, marker, webhook=True) as (client, receiver):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, PATH, content=raw, headers=headers)
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists(), 'webhook transaction did not reach the commit boundary'
            receiver.kill()
            receiver.wait(timeout=5)
            with pytest.raises(httpx.TransportError):
                pending.result(timeout=5)
    before = run_ids(dedicated_database)
    assert len(before) == len(saved_deliveries(dedicated_database)) == count
    with api(webhook=True) as (client, _):
        replay = client.post(PATH, content=raw, headers=headers)
        assert replay.status_code == 202
    ids = run_ids(dedicated_database)
    assert ids == [UUID(replay.json()['run_id'])]
    assert not before or before == ids
    assert len(saved_deliveries(dedicated_database)) == 1


def test_database_outage_accepts_only_after_redelivery(dedicated_database, webhook_secret):
    project = os.environ.get('CI_TEST_COMPOSE_PROJECT')
    filename = os.environ.get('CI_TEST_COMPOSE_FILE')
    if project != 'ci-runner-261117-p0-check' or filename != str(ROOT / 'compose.yaml'):
        pytest.skip('use the disposable verify_db.py Compose project')
    compose = ['docker', 'compose', '--project-name', project, '-f', filename]
    raw = push_body()
    headers = signed_headers(webhook_secret, raw)
    with api(webhook=True) as (client, _):
        try:
            subprocess.run(compose + ['stop', 'db'], check=True, capture_output=True, timeout=30)
            refused = client.post(PATH, content=raw, headers=headers)
            assert refused.status_code == 503 and refused.json() == {'detail': 'database unavailable'}
        finally:
            subprocess.run(compose + ['up', '-d', '--wait', 'db'], check=True, capture_output=True, timeout=60)
        assert saved_deliveries(dedicated_database) == run_ids(dedicated_database) == []
        replay = client.post(PATH, content=raw, headers=headers)
        assert replay.status_code == 202
    assert run_ids(dedicated_database) == [UUID(replay.json()['run_id'])]
    assert len(saved_deliveries(dedicated_database)) == 1
