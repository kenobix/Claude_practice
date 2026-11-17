from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import database, logs, runs
from .body_limit import RunBodyLimit
from .models import Run, RunInput, RunPage
from .settings import POLICY, SCHEMA_VERSION


def create_app(connection_factory=None):
    app = FastAPI(title='CI runner', version='0.1.0')
    app.add_middleware(RunBodyLimit)
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
