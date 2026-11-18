"""Dedicated HTTP ingress for one repository webhook."""
import hashlib
import hmac
import json
import os
import re

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
import psycopg

from . import database, runs
from .body_limit import JsonBodyLimit
from .settings import GITHUB_REPOSITORY_ID, POLICY

PATH = '/webhooks/github'
BODY_LIMIT = 256 * 1024
BODY_READ_SECONDS = 2
HOOK_ID = re.compile(r'[1-9][0-9]{0,19}')
DELIVERY_ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
EVENT = re.compile(r'[a-z][a-z0-9_]{0,63}')
SIGNATURE = re.compile(r'sha256=[0-9a-f]{64}')
COMMIT_SHA = re.compile(r'[0-9a-f]{40}')


def _single_header(request, name, pattern, *, status):
    values = request.headers.getlist(name)
    if len(values) != 1 or not pattern.fullmatch(values[0]):
        raise HTTPException(status, 'invalid webhook headers')
    return values[0]


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('duplicate JSON key')
    return result


def _invalid_constant(_value):
    raise ValueError('nonstandard JSON constant')


def _push_sha(event, raw):
    try:
        payload = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                             parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        raise HTTPException(400, 'invalid webhook JSON') from None
    if not isinstance(payload, dict):
        raise HTTPException(400, 'invalid webhook JSON')
    if event != 'push':
        return None

    repo = payload.get('repository')
    ref = payload.get('ref')
    deleted = payload.get('deleted')
    if (not isinstance(repo, dict) or type(repo.get('id')) is not int
            or not isinstance(repo.get('full_name'), str)
            or not isinstance(ref, str) or type(deleted) is not bool):
        raise HTTPException(400, 'invalid push payload')
    if deleted or (repo['id'], repo['full_name'], ref) != (GITHUB_REPOSITORY_ID, POLICY.repository_id, POLICY.ref):
        return None
    sha = payload.get('after')
    if not isinstance(sha, str) or not COMMIT_SHA.fullmatch(sha) or sha == '0' * 40:
        raise HTTPException(400, 'invalid push SHA')
    return sha


def _save_delivery(factory, hook, delivery, digest, sha):
    accepted = sha is not None
    result = 'accepted' if accepted else 'ignored'
    key = (hook, delivery)
    with factory() as conn:
        conn.execute("SET LOCAL transaction_timeout = '5s'")
        row = conn.execute(
            'INSERT INTO deliveries(hook_id, delivery_id, body_sha256, result) '
            "VALUES (%s, %s, %s, 'ignored') ON CONFLICT (hook_id, delivery_id) "
            'DO NOTHING RETURNING id', (*key, digest),
        ).fetchone()
        if row is None:
            # After the unique-key wait this statement sees the committed winner.
            saved = conn.execute(
                'SELECT body_sha256, result, run_id FROM deliveries WHERE hook_id=%s AND delivery_id=%s', key,
            ).fetchone()
            if saved is None:
                raise database.DatabaseUnavailable()
            if saved['body_sha256'] != digest or saved['result'] != result:
                raise HTTPException(409, 'delivery conflicts with stored delivery')
            run_id = saved['run_id']
        elif accepted:
            # The interim ignored slot and the run are visible only after this commit.
            run, _ = runs.upsert_run(conn, sha)
            conn.execute("UPDATE deliveries SET result='accepted', run_id=%s WHERE id=%s", (run.id, row['id']))
            run_id = run.id
        else:
            run_id = None
    return {'status': result, 'run_id': run_id}


def create_app(connection_factory=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.add_middleware(JsonBodyLimit, path=PATH, limit=BODY_LIMIT, read_timeout=BODY_READ_SECONDS)
    factory = connection_factory or database.connect
    hook = os.environ.get('CI_GITHUB_WEBHOOK_HOOK_ID', '')
    secret = os.environ.get('CI_GITHUB_WEBHOOK_SECRET', '').encode('utf-8')

    @app.exception_handler(database.DatabaseUnavailable)
    @app.exception_handler(psycopg.Error)
    async def unavailable(request, exc):
        return JSONResponse({'detail': 'database unavailable'}, 503, headers={'Retry-After': '1'})

    @app.post(PATH)
    async def receive(request: Request, response: Response):
        if not HOOK_ID.fullmatch(hook) or len(secret) < 32:
            raise HTTPException(503, 'webhook unconfigured')
        signature = _single_header(request, 'x-hub-signature-256', SIGNATURE, status=403)
        raw = await request.body()
        expected = 'sha256=' + hmac.digest(secret, raw, 'sha256').hex()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(403, 'invalid webhook signature')
        if _single_header(request, 'x-github-hook-id', HOOK_ID, status=403) != hook:
            raise HTTPException(403, 'unrecognized webhook')
        delivery = _single_header(request, 'x-github-delivery', DELIVERY_ID, status=400)
        event = _single_header(request, 'x-github-event', EVENT, status=400)
        sha = _push_sha(event, raw)
        receipt = await run_in_threadpool(_save_delivery, factory, hook, delivery, hashlib.sha256(raw).hexdigest(), sha)
        response.status_code = 202 if sha is not None else 200
        return receipt

    return app


app = create_app()
