import re
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import database, logs, runs
from .body_limit import JsonBodyLimit
from .models import AttemptPage, ManualRetryReceipt, Run, RunInput, RunPage
from .settings import BODY_LIMIT, POLICY, SCHEMA_VERSION

LOOPBACK_HOST = re.compile(r'(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?', re.IGNORECASE)


def require_loopback_host(request: Request):
    hosts = request.headers.getlist('host')
    if len(hosts) != 1 or not LOOPBACK_HOST.fullmatch(hosts[0]):
        raise HTTPException(403, 'loopback host required')


def create_app(connection_factory=None):
    app = FastAPI(title='CI runner', version='0.1.0')
    app.add_middleware(JsonBodyLimit, path='/runs', limit=BODY_LIMIT)
    # Test processes can inject a connection context at the transaction boundary.
    # No HTTP/environment fault switch exists in production.
    factory = connection_factory or database.connect

    @app.exception_handler(database.DatabaseUnavailable)
    @app.exception_handler(psycopg.Error)
    async def unavailable(request, exc):
        return JSONResponse({'detail': 'database unavailable'}, 503, headers={'Retry-After': '1'})

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Do not reflect arbitrary request values into errors or logs.
        errors = [{'loc': e['loc'], 'type': e['type'], 'msg': e['msg']} for e in exc.errors()]
        return JSONResponse({'detail': errors}, 422)

    @app.get('/health/live')
    def live():
        return {'status': 'ok', 'phase': 'P1-intake'}

    @app.get('/health/ready')
    def ready():
        try:
            with factory() as conn:
                row = conn.execute('SELECT version FROM schema_migrations WHERE version=%s',
                                   (SCHEMA_VERSION,)).fetchone()
            if row is not None:
                return {'status': 'ready'}
        except (psycopg.Error, database.DatabaseUnavailable):
            pass
        return JSONResponse({'status': 'unavailable'}, 503)

    @app.post('/runs', response_model=Run, status_code=201)
    def submit(payload: RunInput, response: Response):
        if payload.repository_id != POLICY.repository_id:
            raise HTTPException(422, 'repository_id not allowed')
        result, created = runs.create_run(factory, payload.commit_sha)
        response.status_code = 201 if created else 200
        response.headers['Location'] = f'/runs/{result.id}'
        return result

    @app.get('/runs', response_model=RunPage)
    def history(limit: Annotated[int, Query(ge=1, le=100)] = 20,
                cursor: Annotated[str | None, Query(min_length=1, max_length=256)] = None):
        position = runs.decode_cursor(cursor)
        return runs.list_runs(factory, limit, position)

    @app.get('/runs/{run_id}', response_model=Run)
    def detail(run_id: UUID):
        return runs.get_run(factory, run_id)

    @app.put('/runs/{run_id}/cancel', response_model=Run, responses={202: {'model': Run}})
    def cancel(run_id: UUID, request: Request, response: Response):
        require_loopback_host(request)
        result = runs.cancel_run(factory, run_id)
        response.status_code = 202 if result.state == 'running' else 200
        return result

    @app.put('/runs/{run_id}/retry/{attempt_id}', response_model=ManualRetryReceipt, status_code=202)
    def retry(run_id: UUID, attempt_id: UUID, request: Request):
        require_loopback_host(request)
        return runs.request_manual_retry(factory, run_id, attempt_id)

    @app.get('/runs/{run_id}/attempts', response_model=AttemptPage)
    def attempts(run_id: UUID, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                 before: Annotated[int | None, Query(ge=1, le=2147483647)] = None):
        return runs.list_attempts(factory, run_id, limit, before)

    @app.get('/runs/{run_id}/logs', response_class=Response)
    def log_page(run_id: UUID,
                 stream: Literal['stdout', 'stderr'] = 'stdout',
                 offset: Annotated[int, Query(ge=0, le=logs.MAX_LOG_BYTES)] = 0,
                 limit: Annotated[int, Query(ge=1, le=logs.MAX_PAGE_BYTES)] = 4096,
                 format: Literal['text', 'html'] = 'text'):
        try:
            page = logs.read_page(factory, run_id, stream, offset, limit)
        except HTTPException as exc:
            exc.headers = logs.SAFE_HEADERS | (exc.headers or {})
            raise
        return logs.response(page, stream, offset, format)

    return app


app = create_app()
