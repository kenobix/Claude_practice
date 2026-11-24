"""Apply forward SQL migrations atomically; never print credentials."""
import os
from pathlib import Path

import psycopg


def migrate():
    files = sorted((Path(__file__).resolve().parents[1] / 'migrations').glob('[0-9][0-9][0-9]_*.sql'))
    migrations = {int(p.name.split('_', 1)[0]): p for p in files}
    if len(migrations) != len(files):
        raise SystemExit('duplicate migration version')
    with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=3) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(261117, 1)')
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations '
                     '(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())')
        applied = {row[0] for row in conn.execute('SELECT version FROM schema_migrations')}
        if not applied.issubset(migrations):
            raise SystemExit('database has unknown migration versions')
        for version, path in migrations.items():
            if version not in applied:
                conn.execute(path.read_text())
                conn.execute('INSERT INTO schema_migrations(version) VALUES (%s)', (version,))
                print(f'schema {version} applied')
    print(f'schema {max(migrations)} ready')


if __name__ == '__main__':
    migrate()
