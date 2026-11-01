"""T08 opt-in legacy REAL migration and restore, using two owned PostgreSQLs."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import selectors
import signal
import struct
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import psycopg
from psycopg import sql
# Standalone fixture configuration must not inherit the user's active profile.
os.environ.update(DATA_PROFILE_ID='uci-bike-sharing', FEATURE_CONTRACT_VERSION='bike-demand-features-v1', MLFLOW_MODEL_NAME='bike-demand')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from bike_mlops import api
from bike_mlops.db import Database, SCHEMA, RequestConflict, stable_hash
from test_monitor_persistence_postgres import docker
PROFILE, TARGET = ('uci-bike-sharing', 'uci-bike-sharing-cnt-dataset-local-hour')
VALUES = (1 / 3, 415.72473, 15.189926280821982, 0.0)

def binary32(value):
    return struct.unpack('!f', struct.pack('!f', value))[0]

def record(index=0):
    stamp = f'2012-07-01 {10 + index:02d}:00:00'
    return dict(request_id=f'legacy-request-{index:03d}', input_hash=stable_hash(dict(target_hour=stamp, as_of=stamp, feature_contract_version='bike-demand-features-v1')), target_hour=stamp, as_of=stamp, predicted=VALUES[index], model_name='bike-demand', model_version='7', candidate_id=None, model_quality_gate='passed', artifact_sha256='a' * 64, data_version_sha256='b' * 64, data_profile_id=PROFILE, target_contract_id=TARGET, feature_contract_version='bike-demand-features-v1', features_json='{}', created_at='2012-07-01T00:00:00+00:00')

def snapshot(database):
    with database.session() as connection:
        tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
        result = {}
        for table in tables:
            name = table['tablename']
            rows = [dict(row) for row in connection.execute(sql.SQL('SELECT * FROM {} ORDER BY 1').format(sql.Identifier(name)))]
            if name == 'predictions':
                # REAL text output is not an exact binary64 representation: cast in SQL.
                exact = {r['request_id']: r['exact'] for r in connection.execute('SELECT request_id,predicted::double precision AS exact FROM predictions')}
                for row in rows:
                    row['predicted'] = exact[row['request_id']]
            result[name] = rows
        return result

def columns(database):
    with database.session() as connection:
        return connection.execute("SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()

def predicted_type(database):
    return next((r['data_type'] for r in columns(database) if r['table_name'] == 'predictions' and r['column_name'] == 'predicted'))

def retry_api(database, index):
    # Use the production router without lifespan, which would migrate before the test.
    app = FastAPI()
    app.include_router(api.api_router)
    app.state.database, app.state.model, app.state.model_version = (database, None, '99')
    app.state.model_error = 'Synthetic migration fixture has no model'
    source = record(index)
    payload = {key: source[key] for key in ('request_id', 'target_hour', 'as_of', 'feature_contract_version')}
    with TestClient(app) as client:
        first, second = (client.post('/predict', json=payload), client.post('/predict', json=payload))
        if first.status_code != 200 or second.status_code != 200:
            raise AssertionError('Saved prediction retry did not return HTTP 200')
        if first.json() != second.json() or first.json()['model_version'] != '7':
            raise AssertionError('Saved prediction/version changed within retry')
        if client.post('/predict', json=dict(payload, target_hour='2012-07-02 10:00:00', as_of='2012-07-02 10:00:00')).status_code != 409:
            raise AssertionError('Changed input was not refused with HTTP 409')
        return first.json()

class MigrationAcceptance(unittest.TestCase):
    source_url = restore_url = source_container = restore_container = app_password = evidence = None
    observations = {}

    def setUp(self):
        self.db = Database(self.source_url, PROFILE, TARGET)
        with psycopg.connect(self.source_url, autocommit=True) as c:
            c.execute('DROP SCHEMA public CASCADE')
            c.execute('CREATE SCHEMA public')
        with self.db.transaction() as c:
            for statement in SCHEMA:
                c.execute(statement.replace('predicted DOUBLE PRECISION', 'predicted REAL'))
            self.db.execute(c, 'INSERT INTO data_profile_metadata VALUES (1,?,?)', (PROFILE, TARGET))
        for i in range(len(VALUES)):
            self.db.save_prediction(record(i))
        self.db.add_observation('2012-01-01 00:00:00', 123, '2012-01-01 03:00:00', 'c' * 64)
        with self.db.transaction() as c:
            c.execute("INSERT INTO monitor_windows VALUES ('old-monitor','replay','2012-01-01 01:00:00',1,0,0,NULL,NULL,'{}','{}','fixture')")
            self.db.execute(c, "INSERT INTO deployments(deployment_id,status,phase,data_profile_id,target_contract_id,to_slot,to_version,approver,reason,created_at,updated_at) VALUES ('old-deploy','completed','fixture',?,?,'blue','7','synthetic','fixture','fixture','fixture')", (PROFILE, TARGET))
        self.assertEqual(predicted_type(self.db), 'real')

    def assert_migrated(self, before):
        self.assertEqual(predicted_type(self.db), 'double precision')
        self.assertEqual(before, snapshot(self.db))
        self.assertTrue(all((r['data_type'] == 'real' for r in columns(self.db) if r['table_name'] == 'monitor_windows' and r['column_name'] in ('mae', 'rmse', 'label_coverage'))))

    def test_storage_metadata_and_idempotent_initialize(self):
        before, schema = (snapshot(self.db), columns(self.db))
        self.db.initialize()
        self.assert_migrated(before)
        for old, new in zip(schema, columns(self.db), strict=True):
            self.assertEqual(dict(old, data_type='double precision') if old['table_name'] == 'predictions' and old['column_name'] == 'predicted' else old, new)
        self.db.initialize()
        self.assert_migrated(before)
        for i, value in enumerate(VALUES):
            self.assertEqual(self.db.saved_prediction(record(i)['request_id'])['predicted'], binary32(value))
        self.observations['storage_metadata_repeat_initialize'] = 'passed'

    def test_api_retry_binary_identity_and_decimal_display_difference(self):
        before = [retry_api(self.db, i) for i in range(len(VALUES))]
        rows = snapshot(self.db)
        self.db.initialize()
        after = [retry_api(self.db, i) for i in range(len(VALUES))]
        changes = []
        for i, (old, new) in enumerate(zip(before, after, strict=True)):
            self.assertEqual(struct.pack('!f', old['prediction']), struct.pack('!f', new['prediction']))
            self.assertEqual(new['prediction'], binary32(VALUES[i]))
            self.assertEqual({k: v for k, v in old.items() if k != 'prediction'}, {k: v for k, v in new.items() if k != 'prediction'})
            if old['prediction'] != new['prediction']:
                changes.append(dict(request_id=old['request_id'], before_text_decoded=old['prediction'], after_exact_binary32=new['prediction']))
        self.assertTrue(changes)
        self.assertEqual(rows, snapshot(self.db))
        self.observations['api_retry'] = dict(same_binary32_stored_value=True, stable_after_migration=True, exact_historical_decimal_json_preservation=False, decimal_display_changes=changes, changed_input_status=409, model_reload_required=False)

    def test_new_binary64_and_conflicting_input(self):
        self.db.initialize()
        new = dict(record(2), request_id='new-binary64-request', model_version='99')
        saved = self.db.save_prediction(new)
        self.assertEqual(saved['predicted'], VALUES[2])
        self.assertNotEqual(saved['predicted'], binary32(VALUES[2]))
        self.assertEqual(saved, self.db.save_prediction(dict(new, predicted=999, model_version='100')))
        before = snapshot(self.db)
        with self.assertRaises(RequestConflict):
            self.db.save_prediction(dict(new, input_hash='changed'))
        self.assertEqual(before, snapshot(self.db))
        self.observations['new_binary64_and_conflict'] = 'passed'

    def test_profile_rejection_rolls_back_alter(self):
        before, schema = (snapshot(self.db), columns(self.db))
        with self.assertRaisesRegex(ValueError, 'Database profile mismatch'):
            Database(self.source_url, 'capital-bikeshare', 'capital-trip-starts-hourly-v1').initialize()
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual(schema, columns(self.db))
        self.observations['profile_rejection_rollback'] = 'passed'

    def test_exception_and_connection_loss_rollback_then_resume(self):
        before, schema = (snapshot(self.db), columns(self.db))
        original = self.db.execute
        for failure in ('exception', 'connection_loss'):
            alter_reached = []

            def failed(c, statement, params=()):
                result = original(c, statement, params)
                if statement.startswith('ALTER TABLE predictions ALTER COLUMN predicted TYPE'):
                    alter_reached.append(True)
                    if failure == 'connection_loss':
                        with psycopg.connect(self.source_url, autocommit=True) as killer:
                            killer.execute('SELECT pg_terminate_backend(%s)', (c.info.backend_pid,))
                        c.execute('SELECT 1')
                    raise RuntimeError('Synthetic interruption after ALTER')
                return result
            expected_error = RuntimeError if failure == 'exception' else psycopg.OperationalError
            with patch.object(self.db, 'execute', side_effect=failed), self.assertRaises(expected_error):
                self.db.initialize()
            self.assertEqual(alter_reached, [True])
            self.assertEqual(before, snapshot(self.db))
            self.assertEqual(schema, columns(self.db))
        self.db.initialize()
        self.assert_migrated(before)
        self.observations['exception_and_connection_loss'] = 'passed'

    def test_sigkill_before_and_after_commit(self):
        child = '''
import os,time
from bike_mlops.db import Database
db=Database(os.environ['T08_URL'])
def pause():
    print('READY',flush=True)
    time.sleep(60)
original=db.execute
def execute(c,s,p=()):
    result=original(c,s,p)
    if s.startswith('ALTER TABLE predictions ALTER COLUMN predicted TYPE'):
        pause()
    return result
if os.environ['T08_PHASE']=='before': db.execute=execute
db.initialize()
pause()
'''
        before, schema = (snapshot(self.db), columns(self.db))
        for phase in ('before', 'after'):
            process = subprocess.Popen([sys.executable, '-c', child], env=dict(os.environ, T08_URL=self.source_url, T08_PHASE=phase), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=15), 'Migration barrier not reached')
                self.assertEqual(process.stdout.readline().strip(), 'READY')
                process.kill()
                process.communicate(timeout=10)
                self.assertEqual(process.returncode, -9)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
            self.assertEqual(before, snapshot(self.db))
            if phase == 'before':
                self.assertEqual(schema, columns(self.db))
            else:
                self.assert_migrated(before)
        self.db.initialize()
        self.assert_migrated(before)
        self.observations['sigkill_before_after_commit'] = 'passed'

    def test_parallel_initialize(self):
        before = snapshot(self.db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(Database(self.source_url, PROFILE, TARGET).initialize) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
        self.assert_migrated(before)
        self.observations['parallel_initialize'] = 'passed'

    def test_unmarked_old_columns_migration(self):
        before = snapshot(self.db)
        with self.db.transaction() as c:
            c.execute('DROP TABLE data_profile_metadata')
            for name in ('candidate_id', 'data_profile_id', 'target_contract_id'):
                c.execute(sql.SQL('ALTER TABLE predictions DROP COLUMN {}').format(sql.Identifier(name)))
        self.db.initialize()
        self.assert_migrated(before)
        self.observations['old_unmarked_uci_columns'] = 'passed'

    def test_backup_restore_before_after_on_second_volume(self):
        checks = []
        for phase in ('before', 'after'):
            if phase == 'after':
                self.db.initialize()
                self.db.save_prediction(dict(record(2), request_id='new-restore-binary64', model_version='99'))
            before, schema = (snapshot(self.db), columns(self.db))
            env = dict(os.environ, PGPASSWORD=self.app_password)
            dumped = subprocess.run(['docker', 'exec', '-e', 'PGPASSWORD', self.source_container, 'pg_dump', '--host=127.0.0.1', '--username=t08_app', '--dbname=t08_source', '--format=custom', '--no-owner', '--no-acl'], env=env, capture_output=True, timeout=60)
            self.assertEqual(dumped.returncode, 0, 'Owned pg_dump failed')
            self.assertTrue(dumped.stdout.startswith(b'PGDMP'))
            with (self.evidence / f'{phase}.dump').open('xb') as stream:
                stream.write(dumped.stdout)
            restored = subprocess.run(['docker', 'exec', '-i', '-e', 'PGPASSWORD', self.restore_container, 'pg_restore', '--host=127.0.0.1', '--username=t08_app', '--dbname=t08_restore', '--clean', '--if-exists', '--no-owner', '--no-acl', '--exit-on-error', '--single-transaction'], input=dumped.stdout, env=env, capture_output=True, timeout=60)
            self.assertEqual(restored.returncode, 0, 'Owned pg_restore failed')
            db = Database(self.restore_url, PROFILE, TARGET)
            self.assertEqual(schema, columns(db))
            self.assertEqual(before, snapshot(db))
            self.assertEqual(retry_api(db, 1), retry_api(self.db, 1))
            if phase == 'before':
                self.assertEqual(predicted_type(db), 'real')
                db.initialize()
                self.assertEqual(before, snapshot(db))
                self.assertEqual(predicted_type(db), 'double precision')
            else:
                self.assertEqual(db.saved_prediction('new-restore-binary64')['predicted'], VALUES[2])
            checks.append(dict(phase=phase, bytes=len(dumped.stdout), sha256=hashlib.sha256(dumped.stdout).hexdigest(), schema_rows_metadata_and_api_match=True, restore_target='second_owned_instance_and_volume'))
        self.observations['backup_restore'] = checks

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-disposable', action='store_true', required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    args.evidence_dir.mkdir(parents=True, exist_ok=False)

    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    context = docker('context', 'show')
    host = json.loads(docker('context', 'inspect', context, '--format', '{{json .Endpoints.docker.Host}}'))
    if not host.startswith('unix://') or not os.environ.get('DOCKER_HOST', 'unix://').startswith('unix://'):
        raise RuntimeError('Local Unix-socket Docker required')
    image_id = docker('image', 'inspect', 'postgres:17.6-bookworm', '--format', '{{.Id}}')
    label, owned, result = ('bike.prediction.migration.test', [], None)
    owned_volumes = set()
    password, app_password = (secrets.token_hex(24), secrets.token_hex(24))
    env = dict(os.environ, POSTGRES_PASSWORD=password)
    try:
        for name in ('t08_source', 't08_restore'):
            token = secrets.token_hex(8)
            cid = docker('run', '-d', '--rm', '--pull=never', '--name', f'bike-t08-{token}', '--label', f'{label}={token}', '--publish', '127.0.0.1::5432', '-e', 'POSTGRES_PASSWORD', 'postgres:17.6-bookworm', env=env)
            if len(cid) != 64 or any((c not in '0123456789abcdef' for c in cid)):
                raise RuntimeError('Invalid container ID')
            owned.append((cid, token))
            binding = docker('port', cid, '5432/tcp')
            if not binding.startswith('127.0.0.1:') or '\n' in binding:
                raise RuntimeError('Loopback binding required')
            port = int(binding.rsplit(':', 1)[1])
            admin = f'postgresql://postgres:{password}@127.0.0.1:{port}/postgres'
            for attempt in range(60):
                try:
                    with psycopg.connect(admin, connect_timeout=2) as c:
                        c.execute('SELECT 1')
                    break
                except psycopg.OperationalError:
                    if attempt == 59:
                        raise RuntimeError('Owned PostgreSQL startup failed') from None
                    time.sleep(0.25)
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(sql.SQL('CREATE ROLE t08_app LOGIN PASSWORD {}').format(sql.Literal(app_password)))
                c.execute(sql.SQL('CREATE DATABASE {} OWNER t08_app').format(sql.Identifier(name)))
            url = f'postgresql://t08_app:{app_password}@127.0.0.1:{port}/{name}'
            if name == 't08_source':
                MigrationAcceptance.source_url, MigrationAcceptance.source_container = (url, cid)
            else:
                MigrationAcceptance.restore_url, MigrationAcceptance.restore_container = (url, cid)
        mounts = [json.loads(docker('inspect', cid, '--format', '{{json .Mounts}}')) for cid, _ in owned]
        volumes = [{m['Name'] for m in group if m.get('Type') == 'volume' and m.get('Name')} for group in mounts]
        if not volumes[0] or not volumes[1] or volumes[0] & volumes[1]:
            raise RuntimeError('Separate data volumes required')
        owned_volumes = set.union(*volumes)
        MigrationAcceptance.app_password, MigrationAcceptance.evidence = (app_password, args.evidence_dir)
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MigrationAcceptance))
    finally:
        for cid, token in reversed(owned):
            labels = json.loads(docker('inspect', cid, '--format', '{{json .Config.Labels}}'))
            if labels.get(label) != token:
                raise RuntimeError('Cleanup ownership mismatch')
            docker('rm', '-f', '-v', cid)
        if docker('ps', '-a', '--filter', f'label={label}', '--format', '{{.ID}}'):
            raise RuntimeError('Owned migration container remains')
        if owned_volumes & set(docker('volume', 'ls', '-q').splitlines()):
            raise RuntimeError('Owned migration volume remains')
        if owned:
            print('T08 source/restore containers and anonymous volumes removed; cleanup_complete=true', flush=True)
    output = dict(task='T08', scope='synthetic_only', status='passed' if result.wasSuccessful() else 'failed', tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors), observations=MigrationAcceptance.observations, image_id=image_id, separate_source_and_restore_volumes=True, cleanup_complete=True, real_database_migration_performed=False, deployment_permission='not_evaluated')
    with (args.evidence_dir / 'summary.json').open('x') as stream:
        json.dump(output, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(output, allow_nan=False))
    return 0 if result.wasSuccessful() else 1
if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit('T08 interrupted; owned-resource cleanup attempted') from None
    except Exception as error:
        raise SystemExit(f'T08 failed ({type(error).__name__}); owned-resource cleanup attempted') from None
