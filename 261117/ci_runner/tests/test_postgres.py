"""Opt-in integration test. Does not substitute SQLite for PostgreSQL."""
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest


@pytest.mark.integration
def test_migration_and_constraints(monkeypatch):
    dsn = os.environ.get("CI_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("CI_TEST_DATABASE_URL must point to the dedicated test DB")
    monkeypatch.setenv("DATABASE_URL", dsn)
    script = Path(__file__).resolve().parents[1] / "scripts/migrate.py"
    for _ in range(2):
        subprocess.run([sys.executable, str(script)], check=True)
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [
            (1,), (2,), (3,), (4,), (5,), (6,)]
        run_id = conn.execute(
            "INSERT INTO runs(repository_id, ref, commit_sha, workflow_version) "
            "VALUES ('fixture', 'refs/heads/main', %s, 'v1') RETURNING id", ('a' * 40,)
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO runs(repository_id, ref, commit_sha, workflow_version) "
                    "VALUES ('fixture', 'refs/heads/main', %s, 'v1')", ('a' * 40,)
                )
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("UPDATE runs SET state = 'unknown' WHERE id = %s", (run_id,))
        with pytest.raises(psycopg.errors.CheckViolation) as invalid_recovery:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO attempts(run_id, attempt_no, worker_id, lease_token, started_at, "
                    "lease_expires_at, state, finished_at, reason, retry_kind, container_recovered_at) "
                    "VALUES (%s, 1, 'worker-1', gen_random_uuid(), now()-interval '1 minute', "
                    "now(), 'failed', now(), 'infra_lease_expired', 'initial', now())", (run_id,))
        assert invalid_recovery.value.diag.constraint_name == 'attempts_container_recovery_identity'
        conn.rollback()  # test data never persists; schema remains for local development
