"""Disposable PostgreSQL API and migration integration check. Removes only its own new project.

Run from the project venv. Docker daemon access and image pull are required.
Credentials are generated in memory and never saved or printed.
"""
import os
from contextlib import contextmanager
from pathlib import Path
import secrets
import subprocess
import sys

import psycopg

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'ci-runner-261117-p0-check'


@contextmanager
def disposable_database(project, port):
    env = os.environ.copy()
    env.update(
        COMPOSE_PROJECT_NAME=project,
        POSTGRES_USER='ci_runner_261117',
        POSTGRES_DB='ci_runner_261117',
        POSTGRES_PASSWORD=secrets.token_hex(24),
        CI_DB_PORT=str(port),
    )
    env['DATABASE_URL'] = (
        f"postgresql://{env['POSTGRES_USER']}:{env['POSTGRES_PASSWORD']}"
        f'@127.0.0.1:{port}/ci_runner_261117'
    )
    env['CI_TEST_DATABASE_URL'] = env['DATABASE_URL']
    env['CI_TEST_DISPOSABLE'] = '1'
    env['CI_TEST_COMPOSE_PROJECT'] = project
    env['CI_TEST_COMPOSE_FILE'] = str(ROOT / 'compose.yaml')
    compose = ['docker', 'compose', '--project-name', project, '-f', str(ROOT / 'compose.yaml')]
    # Refuse to mutate or remove any existing project, including abandoned volumes.
    for resource in ('ps', 'volume ls', 'network ls'):
        command = ['docker', *resource.split()]
        command += ['-aq'] if resource == 'ps' else ['-q']
        command += ['--filter', f'label=com.docker.compose.project={project}']
        if subprocess.check_output(command, env=env, text=True, timeout=15).strip():
            raise SystemExit('Existing verification project found; refusing to modify it')
    try:
        subprocess.run(compose + ['up', '-d', '--wait', 'db'], env=env, check=True, timeout=180)
        yield env, compose
    finally:
        subprocess.run(compose + ['down', '--volumes'], env=env, check=True, timeout=60)


def verify():
    with disposable_database(PROJECT, 55417) as (env, compose):
        for _ in range(2):
            subprocess.run([sys.executable, str(ROOT / 'scripts/migrate.py')], env=env, check=True)
        subprocess.run([sys.executable, '-m', 'pytest', '-q', str(ROOT / 'tests')], env=env, check=True)
        # Verify DB readiness against the real schema, without exposing credentials.
        from fastapi.testclient import TestClient
        from ci_runner.app import app
        old_dsn = os.environ.get('DATABASE_URL')
        os.environ['DATABASE_URL'] = env['DATABASE_URL']
        try:
            assert TestClient(app, base_url='http://127.0.0.1').get('/health/ready').status_code == 200
            print('real DB readiness: 200')
        finally:
            if old_dsn is None:
                os.environ.pop('DATABASE_URL', None)
            else:
                os.environ['DATABASE_URL'] = old_dsn
        subprocess.run(compose + ['restart', 'db'], env=env, check=True)
        subprocess.run(compose + ['up', '-d', '--wait', 'db'], env=env, check=True)
        with psycopg.connect(env['DATABASE_URL'], connect_timeout=3) as conn:
            assert conn.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall() == [
                (1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,)]
            print('PostgreSQL:', conn.execute('SHOW server_version').fetchone()[0])
            print('migration persists after restart')
        # A fresh pull by digest need not give the image a local tag.
        db_container = subprocess.check_output(compose + ['ps', '--quiet', 'db'],
                                               env=env, text=True, timeout=15).strip()
        image_id = subprocess.check_output(['docker', 'container', 'inspect', '--format', '{{.Image}}', db_container],
                                           env=env, text=True, timeout=15).strip()
        subprocess.run(['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}', image_id],
                       check=True, env=env, timeout=15)


if __name__ == '__main__':
    verify()
