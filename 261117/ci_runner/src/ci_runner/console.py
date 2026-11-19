"""Loopback HTML views for the fixed-workflow operator console."""
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from . import logs, runs
from .models import Attempt, AttemptPage, Run, RunPage
from .settings import POLICY

ASSETS = Path(__file__).with_name('console_assets')
PAGE_SIZE = 20
LOG_BYTES = 4096
UUID_PATTERN = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
HEADERS = {
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': (
        "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}
STATES = {
    'queued': '待機中', 'running': '実行中', 'succeeded': '成功',
    'failed': '失敗', 'timed_out': '時間切れ', 'canceled': '取消済み',
}
REASONS = {
    'success': 'チェック成功',
    'check_failure': 'チェック失敗',
    'timeout': '時間切れ',
    'log_limit': 'ログ出力上限',
    'infra_lease_expired': '基盤障害 · 実行権の期限切れ',
    'infra_exhausted': '基盤障害 · 自動再試行の上限',
    'canceled_lease_expired': '取消後に実行権が期限切れ',
}
KINDS = {'initial': '初回', 'infrastructure': '自動再試行', 'manual': '手動再試行'}


def _time(value: datetime) -> str:
    return f'<time datetime="{value.isoformat()}">{value.astimezone(UTC):%Y-%m-%d %H:%M:%S}</time>'


def _badge(state: str) -> str:
    return f'<span class="state {state}">{STATES[state]}</span>'


def _link(path: str, **query) -> str:
    return escape(f'{path}?{urlencode(query)}', quote=True)


def _page(title: str, body: str, *, demo=False, actions=False, status=200) -> HTMLResponse:
    live_current = '' if demo else ' aria-current="page"'
    demo_current = ' aria-current="page"' if demo else ''
    script = '<script src="/ui/assets/actions.js" defer></script>' if actions else ''
    return HTMLResponse(f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} | CI Runner</title>
<link rel="stylesheet" href="/ui/assets/console.css">
{script}
</head>
<body>
<header class="top"><div class="shell bar"><a class="brand" href="/ui">CI Runner</a>
<nav aria-label="データの切り替え"><a href="/ui"{live_current}>ライブ</a>
<a href="/ui/demo"{demo_current}>過去のデモ</a></nav></div></header>
<main class="shell" id="main">{body}</main>
</body></html>""", status_code=status, headers=HEADERS)


def error_page(title: str, message: str, status: int) -> HTMLResponse:
    return _page(title, f'<h1>{escape(title)}</h1><p>{escape(message)}</p><a href="/ui">実行一覧へ</a>', status=status)


def _listing(page: RunPage, cursor: str | None) -> HTMLResponse:
    rows = ''.join(
        f'<tr><td><a href="/ui/runs/{run.id}" aria-label="実行 {run.id} の詳細">'
        f'<code>{run.id}</code></a></td><td><code title="{escape(run.commit_sha)}">{escape(run.commit_sha[:12])}'
        f'</code></td><td>{_badge(run.state)}</td><td>{_time(run.created_at)}</td></tr>'
        for run in page.items
    )
    table = f"""<div class="table-scroll"><table>
<thead><tr><th scope="col">実行ID</th><th scope="col">コミット</th><th scope="col">状態</th>
<th scope="col">受付時刻（UTC）</th></tr></thead><tbody>{rows}</tbody></table></div>""" if rows else (
        '<p class="empty">このページに実行履歴はありません。</p>' if cursor else
        '<p class="empty">実行履歴はありません。ジョブを受け付けると、ここに表示されます。</p>'
    )
    first = '<a href="/ui">最新のページ</a>' if cursor else ''
    more = f'<a href="{_link("/ui", cursor=page.next_cursor)}">以前の実行</a>' if page.next_cursor else ''
    return _page('実行一覧', f"""
<h1>実行一覧</h1>
<form class="lookup" action="/ui/lookup" method="get">
<label for="run_id">実行IDで詳細・ログを探す</label>
<input type="search" name="run_id" id="run_id" required maxlength="36" pattern="{UUID_PATTERN}"
 autocomplete="off" spellcheck="false"><button type="submit">検索</button>
</form>
{table}<div class="pager">{first}{more}</div>""")


def _attempt_card(attempt: Attempt) -> str:
    reason = (attempt.reason or '').removeprefix('commit_').removeprefix('fixture_')
    label = REASONS.get(reason, attempt.reason or '結果待ち')
    official = '<span class="official">正式結果</span>' if attempt.official else ''
    finished = _time(attempt.finished_at) if attempt.finished_at else '継続中'
    exit_code = f'<span>終了コード <code>{attempt.exit_code}</code></span>' if attempt.exit_code is not None else ''
    return f"""<li class="attempt {attempt.state}">
<div class="attempt-title"><h3>試行 {attempt.attempt_no}</h3>{_badge(attempt.state)}{official}</div>
<p class="reason">{escape(label)}</p>
<div class="attempt-meta"><span>{KINDS[attempt.retry_kind]}</span>
<span>開始 {_time(attempt.started_at)}</span><span>終了 {finished}</span>{exit_code}</div>
</li>"""


def _detail(run: Run, history: AttemptPage, retry_id: UUID | None = None, *, demo=False, before=None) -> HTMLResponse:
    url = f'/ui/runs/{run.id}'
    log_link = ''
    button = ''
    if not demo:
        if run.completed_attempt_id:
            log_link = f'<a class="button secondary" href="{_link(f"{url}/logs", attempt_id=run.completed_attempt_id)}">正式ログを開く</a>'
        if retry_id:
            button = f'<button type="button" data-put="/runs/{run.id}/retry/{retry_id}">再試行する</button>'
        elif run.state in ('queued', 'running') and not run.cancel_requested_at and (
                run.repository_id, run.ref, run.workflow_version) == (POLICY.repository_id, POLICY.ref, POLICY.workflow_version):
            button = f'<button class="cancel" type="button" data-put="/runs/{run.id}/cancel">取り消す</button>'
    pending = '<p class="notice">取消要求を受付済み。実行権の終了を待っています。</p>' if (
        run.state == 'running' and run.cancel_requested_at
    ) else ''
    if demo:
        top = '<p class="notice">過去の障害試験を再現したデモです。表示専用です。</p>'
    else:
        top = '<a class="back" href="/ui">実行一覧</a>'
    items = (f'<ol class="timeline" reversed>{"".join(map(_attempt_card, history.items))}</ol>' if history.items
             else '<p class="empty">このページに試行はありません。</p>')
    first = f'<a href="{url}">最新の試行</a>' if before is not None else ''
    more = f'<a href="{_link(url, before=history.next_before)}">以前の試行</a>' if history.next_before else ''
    final = f'<code>{run.completed_attempt_id}</code>' if run.completed_attempt_id else '未確定' if (
        run.state in ('queued', 'running')) else 'なし'
    return _page('実行の詳細', f"""
{top}
<div class="heading"><h1>実行の詳細</h1>{_badge(run.state)}</div>
<dl class="identity">
<div><dt>実行ID</dt><dd><code>{run.id}</code></dd></div>
<div><dt>コミット</dt><dd><code>{escape(run.commit_sha)}</code></dd></div>
<div><dt>受付時刻（UTC）</dt><dd>{_time(run.created_at)}</dd></div>
<div><dt>正式結果の試行ID</dt><dd>{final}</dd></div>
</dl>
{pending}
<div class="actions">{log_link}{button}</div><p id="action-status" role="status"></p>
<div class="section-heading"><h2>試行履歴</h2><span>新しい順 / UTC</span></div>
{items}<div class="pager">{first}{more}</div>""", demo=demo, actions=bool(button))


def _demo() -> HTMLResponse:
    # A read-only reconstruction of the fixed-fixture recovery scenario.
    start = datetime(2026, 11, 18, 9, tzinfo=UTC)
    run_id = UUID('36cf1363-0431-4c5b-aee0-d84067f2ae2f')
    old_id = UUID('f6dbe494-d718-4aed-a934-ac22ff846131')
    completed_id = UUID('138e24f1-7f15-4df3-96ff-25bdd71b4062')
    run = Run(id=run_id, repository_id=POLICY.repository_id, ref=POLICY.ref,
              workflow_version=POLICY.workflow_version, commit_sha='3eb342a89fc0eb990c0a48a79dbea42e8be44283',
              state='failed', created_at=start, available_at=start, cancel_requested_at=None,
              completed_attempt_id=completed_id)
    attempts = AttemptPage(items=[
        Attempt(id=completed_id, attempt_no=2, retry_kind='infrastructure', state='failed',
                started_at=start + timedelta(seconds=41), finished_at=start + timedelta(seconds=45),
                exit_code=1, reason='commit_check_failure', official=True),
        Attempt(id=old_id, attempt_no=1, retry_kind='initial', state='failed',
                started_at=start + timedelta(seconds=2), finished_at=start + timedelta(seconds=33),
                exit_code=None, reason='infra_lease_expired', official=False),
    ], next_before=None)
    return _detail(run, attempts, demo=True)


def router(factory, require_loopback_host) -> APIRouter:
    api = APIRouter(prefix='/ui', dependencies=[Depends(require_loopback_host)], include_in_schema=False)

    @api.get('')
    def index(cursor: Annotated[str | None, Query(min_length=1, max_length=256)] = None):
        try:
            position = runs.decode_cursor(cursor)
        except HTTPException:
            return error_page('ページを開けません', '実行一覧から開き直してください。', 422)
        return _listing(runs.list_runs(factory, PAGE_SIZE, position), cursor)

    @api.get('/lookup')
    def find(run_id: Annotated[str, Query(max_length=36)]):
        try:
            identifier = UUID(run_id)
        except ValueError:
            return error_page('実行IDを確認してください', '一覧に表示される36文字の実行IDを使ってください。', 422)
        return RedirectResponse(f'/ui/runs/{identifier}', status_code=303, headers=HEADERS)

    @api.get('/demo')
    def demo():
        return _demo()

    @api.get('/assets/{asset}')
    def asset(asset: Literal['console.css', 'actions.js']):
        media = 'text/css' if asset == 'console.css' else 'text/javascript'
        return FileResponse(ASSETS / asset, media_type=media, headers={'X-Content-Type-Options': 'nosniff'})

    @api.get('/runs/{run_id}')
    def detail(run_id: UUID, before: Annotated[int | None, Query(ge=1, le=2147483647)] = None):
        try:
            run, history, retry_id = runs.get_run_history(factory, run_id, PAGE_SIZE, before)
        except HTTPException as exc:
            return error_page('実行が見つかりません', '実行IDを確認するか、一覧から開き直してください。', exc.status_code)
        return _detail(run, history, retry_id, before=before)

    @api.get('/runs/{run_id}/logs')
    def read_log(run_id: UUID, attempt_id: UUID, stream: Literal['stdout', 'stderr'] = 'stdout',
                 offset: Annotated[int, Query(ge=0, le=logs.MAX_LOG_BYTES)] = 0):
        url = f'/ui/runs/{run_id}/logs'
        back = f'<a class="back" href="/ui/runs/{run_id}">実行の詳細</a>'
        try:
            page = logs.read_page(factory, run_id, stream, offset, LOG_BYTES, expected_attempt_id=attempt_id)
        except HTTPException as exc:
            message = {
                404: '指定した実行のログは見つかりません。',
                409: '正式結果が変わったか、確定を待っています。実行の詳細から開き直してください。',
                416: '選択したページはログの範囲外です。',
            }[exc.status_code]
            return _page('ログを開けません', f'{back}<h1>ログを開けません</h1><p>{message}</p>',
                         status=exc.status_code)
        end = offset + len(page.chunk)
        tabs = ''.join(
            f'<a href="{_link(url, attempt_id=attempt_id, stream=name)}"'
            + (' aria-current="page"' if name == stream else '') + f'>{name}</a>'
            for name in ('stdout', 'stderr')
        )
        first = f'<a href="{_link(url, attempt_id=attempt_id, stream=stream)}">最初のページ</a>' if offset else ''
        more = (f'<a href="{_link(url, attempt_id=attempt_id, stream=stream, offset=end)}">次のページ</a>'
                if end < page.total_bytes else '')
        truncated = '<p class="notice">出力が保存上限を超えました。保存された範囲を表示しています。</p>' if (
            page.observed_bytes > page.retained_bytes) else ''
        content = escape(logs.display_text(page.chunk)) if page.chunk else '出力なし'
        return _page('正式ログ', f"""{back}
<h1>正式ログ</h1><p class="muted">試行ID <code>{attempt_id}</code></p>
<nav class="streams" aria-label="出力の切り替え">{tabs}</nav>
{truncated}<p class="muted">バイト範囲 <code>[{offset}, {end}) / {page.total_bytes}</code></p>
<pre tabindex="0">{content}</pre><div class="pager">{first}{more}</div>""")

    return api
