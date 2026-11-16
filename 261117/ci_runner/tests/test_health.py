from fastapi.testclient import TestClient
import psycopg
from ci_runner.app import app

client = TestClient(app)


def test_liveness_without_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert client.get("/health/live").json() == {"status": "ok", "phase": "P0"}
    assert client.get("/health/ready").status_code == 503


def test_db_failure_is_unavailable_and_does_not_leak(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://placeholder")
    def fail(*args, **kwargs):
        raise psycopg.OperationalError("private database error")
    monkeypatch.setattr(psycopg, "connect", fail)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_job_api_is_not_implemented():
    assert client.post("/runs", json={}).status_code == 404
