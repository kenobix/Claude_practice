import os

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="CI runner P0", version="0.0.1")


@app.get("/health/live")
def live():
    return {"status": "ok", "phase": "P0"}


@app.get("/health/ready")
def ready():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    try:
        with psycopg.connect(dsn, connect_timeout=2) as conn:
            # Schema readiness, not merely TCP connectivity.
            row = conn.execute(
                "SELECT version FROM schema_migrations WHERE version = 1"
            ).fetchone()
            if row != (1,):
                return JSONResponse({"status": "unavailable"}, status_code=503)
    except psycopg.Error:
        # Do not expose DSN or database errors to HTTP clients.
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}
