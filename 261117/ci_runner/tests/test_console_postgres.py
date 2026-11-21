from dataclasses import replace
from html.parser import HTMLParser
from uuid import uuid4

import pytest

from ci_runner import commit_source, database, finalization, runs, worker
from ci_runner.expiry_monitor import expire_next
from ci_runner.preflight import authorize_execution, reserve_container_create
from ci_runner.retry import schedule_next
from test_commit_source import GitHubFixture
from test_expiry_monitor import set_expiry
from test_finalization_input import report
from test_preflight import issue_claim
from test_retry_postgres import make_available
from test_runs_postgres import api, dedicated_database

pytestmark = pytest.mark.integration


def link(response, text):
    class Links(HTMLParser):
        href = None
        match = None

        def handle_starttag(self, tag, attrs):
            if tag == 'a':
                self.href = dict(attrs).get('href')

        def handle_data(self, data):
            if self.href and data == text:
                self.match = self.href

        def handle_endtag(self, tag):
            if tag == 'a':
                self.href = None

    parser = Links()
    parser.feed(response.text)
    assert parser.match is not None, text
    return parser.match


def test_live_failure_trail_retry_and_pinned_logs(dedicated_database):
    run, first = issue_claim()
    set_expiry(first, -1)
    assert expire_next().reason == 'infra_lease_expired'
    assert schedule_next().attempt_id == first.attempt_id
    make_available(run.id)
    target = (run.id, run.commit_sha)
    second = worker.claim_next('second', target=target)
    source = commit_source._verify(*target, GitHubFixture(run.commit_sha))
    canary = b'</pre><script>fixture log</script>\x1b'
    stdout = canary + b' ' * 4066 + b'next-page'
    finalization.finalize_fixture(second, replace(report(second, 'check_failure', 1),
                                  stdout=stdout, stderr=b'fixed failure',
                                  observed_bytes=len(stdout) + 13, verified_source=source))
    with api() as (client, _):
        history = client.get('/ui')
        assert str(run.id) in history.text and run.commit_sha[:12] in history.text
        detail = client.get('/ui/lookup', params={'run_id': str(run.id)}, follow_redirects=True)
        assert detail.status_code == 200 and detail.url.path == f'/ui/runs/{run.id}'
        assert all(text in detail.text for text in (
            run.commit_sha, str(second.attempt_id), '試行 1', '試行 2', '基盤障害', 'チェック失敗', '正式結果',
        ))
        assert str(first.lease_token) not in detail.text and str(second.lease_token) not in detail.text
        assert detail.text.count('class="official"') == 1
        retry = f'/runs/{run.id}/retry/{second.attempt_id}'
        assert f'data-put="{retry}"' in detail.text
        official_log = link(detail, '正式ログを開く')
        log_page = client.get(official_log)
        assert log_page.status_code == 200
        assert '&lt;script&gt;fixture log&lt;/script&gt;\\u001b' in log_page.text
        assert '<script>fixture log' not in log_page.text
        assert 'next-page' in client.get(link(log_page, '次のページ')).text
        assert 'fixed failure' in client.get(link(log_page, 'stderr')).text
        assert client.get(official_log + '&offset=20000').status_code == 416

        assert client.put(retry).status_code == 202
        assert client.get(official_log).status_code == 409
        third = worker.claim_next('manual', target=target)
        finalization.finalize_fixture(third, replace(report(third), verified_source=source))
        assert client.get(official_log).status_code == 409
        updated = client.get(detail.url)
        assert '試行 3' in updated.text and 'チェック成功' in updated.text
        new_log = link(updated, '正式ログを開く')
        assert str(third.attempt_id) in new_log and client.get(new_log).status_code == 200


def test_ui_paging_cancel_and_unsafe_retry_visibility(dedicated_database):
    run, claim = issue_claim()
    reserve_container_create(authorize_execution(claim), uuid4(), uuid4(), database.connect)
    set_expiry(claim, -1)
    assert expire_next().state == 'failed'
    later = [runs.create_run(database.connect, f'{i:040x}')[0] for i in range(20)]
    cancel_run = later[-1]
    with api() as (client, _):
        index = client.get('/ui')
        assert str(cancel_run.id) in index.text and str(run.id) not in index.text
        older = client.get(link(index, '以前の実行'))
        assert str(run.id) in older.text and str(cancel_run.id) not in older.text
        blocked = client.get(f'/ui/runs/{run.id}')
        assert blocked.status_code == 200 and '基盤障害' in blocked.text
        assert 'data-put=' not in blocked.text
        assert client.get(f'/ui/runs/{uuid4()}').status_code == 404

        cancel_path = f'/runs/{cancel_run.id}/cancel'
        detail = f'/ui/runs/{cancel_run.id}'
        assert f'data-put="{cancel_path}"' in client.get(detail).text
        assert client.put(cancel_path).status_code == 200
        canceled = client.get(detail)
        assert '取消済み' in canceled.text and 'data-put=' not in canceled.text
