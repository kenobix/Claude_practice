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
            (1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,)]
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


@pytest.mark.integration
def test_latest_migrations_preserve_original_attempts_and_artifact_metadata():
    dsn = os.environ.get('CI_TEST_DATABASE_URL')
    if not dsn or os.environ.get('CI_TEST_DISPOSABLE') != '1':
        pytest.skip('use verify_db.py: disposable dedicated PostgreSQL required')
    migrations = sorted((Path(__file__).resolve().parents[1] / 'migrations').glob('[0-9][0-9][0-9]_*.sql'))
    with psycopg.connect(dsn) as conn:
        conn.execute('CREATE SCHEMA manual_retry_upgrade')
        conn.execute('SET LOCAL search_path TO manual_retry_upgrade, pg_catalog')
        for path in migrations:
            if int(path.name[:3]) < 7:
                conn.execute(path.read_text())
        run_id, = conn.execute(
            "INSERT INTO runs(repository_id,ref,commit_sha,workflow_version,state) "
            "VALUES ('fixture','refs/heads/main',%s,'v1','running') RETURNING id", ('a'*40,),
        ).fetchone()
        conn.execute(
            'INSERT INTO attempts(run_id, attempt_no, worker_id, lease_token, started_at, lease_expires_at, '
            'finished_at, state, retry_kind) '
            "VALUES (%s,1,'old',gen_random_uuid(),now()-interval '1 minute',now()-interval '30 seconds',"
            "now()-interval '30 seconds','failed','initial'), "
            "(%s,2,'current',gen_random_uuid(),now(),now()+interval '30 seconds',NULL,'running','infrastructure')",
            (run_id, run_id),
        )
        query = 'SELECT id, attempt_no, retry_kind, lease_token, state FROM attempts ORDER BY attempt_no'
        old = conn.execute(query).fetchall()
        conn.execute(next(path for path in migrations if path.name.startswith('007_')).read_text())
        assert conn.execute(query).fetchall() == old
        assert conn.execute('SELECT DISTINCT retry_start_no FROM attempts').fetchall() == [(1,)]
        assert conn.execute('SELECT count(*) FROM manual_retries').fetchone() == (0,)
        conn.execute(
            'INSERT INTO artifacts(attempt_id,relative_name,size_bytes,sha256,storage_key) VALUES (%s,%s,%s,%s,%s)',
            (old[0][0], 'legacy.txt', 16, 'a'*64, 'legacy-metadata'),
        )
        conn.execute(next(path for path in migrations if path.name.startswith('008_')).read_text())
        assert conn.execute(query).fetchall() == old
        assert conn.execute('SELECT claimable_at, execution_ms FROM attempts').fetchall() == [(None, None)] * 2
        assert conn.execute('SELECT relative_name, size_bytes, payload FROM artifacts').fetchone() == (
            'legacy.txt', 16, None,
        )
        assert conn.execute('SELECT used_bytes FROM artifact_storage_budget').fetchone() == (0,)
        conn.rollback()
