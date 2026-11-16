"""Apply the initial schema atomically; never print credentials."""
import os
from pathlib import Path

import psycopg


def migrate():
    sql = (Path(__file__).resolve().parents[1] / "migrations/001_initial.sql").read_text()
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
        # Serialize migration tools using a transaction-scoped lock.
        conn.execute("SELECT pg_advisory_xact_lock(261117, 1)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version = 1").fetchone():
            print("schema 1 already applied")
            return
        conn.execute(sql)
        conn.execute("INSERT INTO schema_migrations(version) VALUES (1)")
    print("schema 1 applied")


if __name__ == "__main__":
    migrate()
