from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg

from ci_runner.app import create_app
from ci_runner.webhook import create_app as create_webhook


def no_database():
    raise AssertionError('this UI route must not connect to the database')


def test_demo_is_read_only_offline_and_ui_requires_loopback():
    with TestClient(create_app(no_database), base_url='http://127.0.0.1') as client:
        demo = client.get('/ui/demo')
        assert demo.status_code == 200
        assert '過去の障害試験を再現したデモ' in demo.text
        assert all(text in demo.text for text in ('チェック失敗', '基盤障害', '正式結果', '試行 2'))
        assert 'data-put=' not in demo.text and '<script' not in demo.text
        assert 'aria-current="page">過去のデモ' in demo.text
        assert demo.headers['cache-control'] == 'no-store'
        assert "frame-ancestors 'none'" in demo.headers['content-security-policy']
        assert "'unsafe-inline'" not in demo.headers['content-security-policy']
        for route in ('/ui', '/ui/demo', '/ui/assets/actions.js'):
            assert client.get(route, headers={'Host': 'external.example'}).status_code == 403
        assert client.get('/ui/assets/console.css').headers['content-type'].startswith('text/css')
        assert client.get('/ui/assets/actions.js').headers['content-type'].startswith('text/javascript')
        assert client.get('/ui/assets/unknown.js').status_code == 422
        assert client.get('/ui/lookup?run_id=invalid', follow_redirects=False).status_code == 422
        run_id = uuid4()
        found = client.get('/ui/lookup', params={'run_id': str(run_id)}, follow_redirects=False)
        assert (found.status_code, found.headers['location']) == (303, f'/ui/runs/{run_id}')
    assert TestClient(create_webhook()).get('/ui/demo').status_code == 404


def test_live_db_error_is_safe_and_demo_still_loads():
    def unavailable():
        raise psycopg.OperationalError('private-db-diagnostic')

    with TestClient(create_app(unavailable), base_url='http://127.0.0.1') as client:
        down = client.get('/ui')
        assert down.status_code == 503 and 'DBの稼働を確認' in down.text
        assert 'private-db-diagnostic' not in down.text
        assert down.headers['retry-after'] == '1' and down.headers['cache-control'] == 'no-store'
        assert client.get('/ui/demo').status_code == 200
