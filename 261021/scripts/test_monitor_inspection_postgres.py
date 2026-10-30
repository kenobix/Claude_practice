"""Opt-in T06b acceptance using synthetic reports and one owned local PostgreSQL.

No project database, existing reports, or registry are accessed. Fixture setup
alone uses DDL/writes; every inspector call is checked for logical non-mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import secrets
import selectors
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from bike_mlops import monitor_inspection as inspection
from bike_mlops.db import Database
from bike_mlops.monitor_persistence import persist_monitor_run, MonitorPersistenceError
from test_monitor_persistence_postgres import docker

PROFILE, TARGET = 'capital-bikeshare', 'capital-trip-starts-hourly-v1'
CONNECT = psycopg.connect


class ObservedConnection:
    def __init__(self, connection, statements, hook=None):
        self.connection, self.statements, self.hook = connection, statements, hook

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else query.as_string(self.connection)
        self.statements.append((text, params))
        cursor = self.connection.execute(query, params)
        if self.hook:
            self.hook(self.connection, text, params)
        return cursor

    def rollback(self):
        self.statements.append(('ROLLBACK', None))
        self.connection.rollback()

    def close(self):
        self.connection.close()


def normalized(value):
    if isinstance(value, float) and math.isnan(value):
        return 'SQL_NaN'
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalized(item) for item in value]
    return value


class PostgresInspectionAcceptance(unittest.TestCase):
    url = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='t06b-report-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.csv = self.root / 'monitor.csv'
        self.run_id = str(uuid.uuid4())
        # Only this owned database is reset between cases. Never use project env.
        with CONNECT(self.url, autocommit=True) as connection:
            connection.execute('DROP SCHEMA public CASCADE')
            connection.execute('CREATE SCHEMA public')
        self.database = Database(self.url, PROFILE, TARGET)
        self.database.initialize()
        self.windows = pd.DataFrame([
            {'window_end': '2024-01-18 00:00:00', 'window_start': '2024-01-17 23:00:00',
             'prediction_count': 3, 'scored_count': 0, 'label_coverage': 0.0,
             'mae': None, 'rmse': None, 'quality_status': 'waiting_for_labels',
             'psi': {'hour': 1 / 3}, 'feature_missing_rates': {'lag': 1 / 3},
             'distribution_status': 'no_threshold_crossing'},
            {'window_end': '2024-01-18 01:00:00', 'window_start': '2024-01-18 00:00:00',
             'prediction_count': 3, 'scored_count': 1, 'label_coverage': 1 / 3,
             'mae': 72.53620455822316, 'rmse': 111.50603198624435,
             'quality_status': 'investigate_error_increase', 'psi': {'hour': .123456789},
             'feature_missing_rates': {'lag': 1 / 3}, 'distribution_status': 'investigate_distribution_change'},
        ])
        self.report = {'replay_id': 't06b-synthetic', 'model_name': 'bike-demand', 'model_version': '7',
                       'data_profile_id': PROFILE, 'target_contract_id': TARGET, 'data_sha256': 'a' * 64,
                       'window_count': 2, 'thresholds': {'window_hours': 1},
                       'monitor_period': {'start_inclusive': '2024-01-18 00:00:00',
                                          'end_exclusive': '2024-01-18 02:00:00'},
                       'latest_window': self.windows.iloc[-1].to_dict()}
        persist_monitor_run(self.database, self.windows, self.csv, self.report, run_id=self.run_id)
        self.marker = self.csv.with_suffix('.complete.json')
        self.statements = []

    def query(self, statement, params=None):
        with CONNECT(self.url, autocommit=True, row_factory=dict_row) as connection:
            cursor = connection.execute(statement, params)
            return cursor.fetchall() if cursor.description else None

    def logical_snapshot(self):
        with CONNECT(self.url, row_factory=dict_row) as connection:
            tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
            return {row['tablename']: normalized(connection.execute(sql.SQL('SELECT * FROM public.{} ORDER BY 1').format(
                sql.Identifier(row['tablename']))).fetchall()) for row in tables}

    def files_snapshot(self):
        return {p.name: (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns,
                        hashlib.sha256(p.read_bytes()).hexdigest()) for p in self.root.iterdir() if p.is_file()}

    def inspect(self, *, hook=None, preserve=True, url=None):
        self.statements = []
        before, files = self.logical_snapshot(), self.files_snapshot()
        def connect(*args, **kwargs):
            return ObservedConnection(CONNECT(*args, **kwargs), self.statements, hook)
        with patch('psycopg.connect', side_effect=connect), \
             patch.object(Database, 'initialize', side_effect=AssertionError('initializer forbidden')), \
             patch.object(Database, 'connect', side_effect=AssertionError('writer connector forbidden')):
            result = inspection.inspect_monitor_run(self.csv, self.run_id, database_url=url or self.url,
                                                   data_profile_id=PROFILE, target_contract_id=TARGET)
        if preserve:
            self.assertEqual(before, self.logical_snapshot())
            self.assertEqual(files, self.files_snapshot())
        for statement, params in self.statements:
            self.assertTrue(statement.startswith(('SELECT', 'BEGIN TRANSACTION', 'ROLLBACK')))
            if statement.startswith('SELECT window_id'):
                self.assertEqual(params, (f'monitor:{self.run_id}:%', inspection.MAX_ROWS + 1))
        payload = json.dumps(result, allow_nan=False)
        self.assertNotIn(self.url, payload)
        self.assertNotIn(self.url.split('@')[0].split(':')[-1], payload)
        return result

    def assert_status(self, result, status, reason=None):
        self.assertEqual(result['status'], status)
        self.assertEqual(result['exit_code'], 0 if status == 'matched' else 1 if status in ('mismatch', 'database_profile_mismatch') else 2)
        if reason:
            self.assertEqual(result['reason'], reason)

    def test_s01_s18_s19_real_nan_and_exact_json_match(self):
        result = self.inspect()
        self.assert_status(result, 'matched')
        self.assertEqual(result['database']['numeric_storage'], dict.fromkeys(inspection.METRICS, 'binary32'))
        self.assertEqual(result['database']['missing_representations'], {'sql_null': 0, 'sql_nan_missing': 2})
        self.assertEqual(result['database']['snapshot'], 'postgresql_repeatable_read_only')
        sample = result['database']['binary32_examples']['mae']
        self.assertNotEqual(sample['source_binary64'], sample['expected_binary32'])

    def test_s18_each_real_one_ulp_is_mismatch(self):
        for key in inspection.METRICS:
            with self.subTest(key=key):
                value = float(self.windows.iloc[-1][key])
                bits = struct.unpack('!I', struct.pack('!f', value))[0]
                next_value = struct.unpack('!f', struct.pack('!I', bits + 1))[0]
                self.query(sql.SQL("UPDATE monitor_windows SET {}=%s WHERE window_end='2024-01-18 01:00:00'").format(sql.Identifier(key)), (next_value,))
                self.assert_status(self.inspect(), 'mismatch')
                self.query(sql.SQL("UPDATE monitor_windows SET {}=%s WHERE window_end='2024-01-18 01:00:00'").format(sql.Identifier(key)), (value,))

    def test_s18_json_does_not_use_real_tolerance(self):
        self.query("UPDATE monitor_windows SET psi_json=%s WHERE window_end='2024-01-18 01:00:00'",
                   (json.dumps({'hour': math.nextafter(.123456789, 1)}),))
        self.assert_status(self.inspect(), 'mismatch')

    def test_database_json_bool_and_malformed_json_are_mismatch(self):
        for value in ('{"hour":true}', '{broken', '{"hour":NaN}', '{"hour":1,"hour":2}'):
            self.query('UPDATE monitor_windows SET psi_json=%s', (value,))
            self.assert_status(self.inspect(), 'mismatch')

    def test_double_precision_and_unknown_numeric_schema(self):
        for key in inspection.METRICS:
            self.query(sql.SQL('ALTER TABLE monitor_windows ALTER COLUMN {} TYPE DOUBLE PRECISION').format(sql.Identifier(key)))
            self.query(sql.SQL("UPDATE monitor_windows SET {}=%s WHERE window_end='2024-01-18 01:00:00'").format(sql.Identifier(key)), (float(self.windows.iloc[-1][key]),))
        result = self.inspect()
        self.assert_status(result, 'matched')
        self.assertEqual(result['database']['numeric_storage'], dict.fromkeys(inspection.METRICS, 'binary64'))
        self.query("UPDATE monitor_windows SET mae=%s WHERE window_end='2024-01-18 01:00:00'", (math.nextafter(72.53620455822316, 100),))
        self.assert_status(self.inspect(), 'mismatch')
        self.query('ALTER TABLE monitor_windows ALTER COLUMN mae TYPE TEXT')
        self.assert_status(self.inspect(), 'unsupported_input', 'monitor_numeric_type_unsupported')

    def test_s17_s19_null_and_nan_only_match_missing(self):
        self.query("UPDATE monitor_windows SET mae=NULL,rmse=NULL WHERE window_end='2024-01-18 00:00:00'")
        self.assert_status(self.inspect(), 'matched')
        for value in (None, float('nan'), float('inf')):
            self.query("UPDATE monitor_windows SET mae=%s WHERE window_end='2024-01-18 01:00:00'", (value,))
            self.assert_status(self.inspect(), 'mismatch')
        self.query("UPDATE monitor_windows SET mae=0 WHERE window_end='2024-01-18 00:00:00'")
        self.assert_status(self.inspect(), 'mismatch')

    def test_s19_latest_null_can_match_sql_nan(self):
        csv = self.root / 'latest-null.csv'
        self.windows.loc[1, ['mae', 'rmse']] = float('nan')
        self.report['latest_window'] = {k: None if isinstance(v, float) and math.isnan(v) else v
                                        for k, v in self.windows.iloc[-1].to_dict().items()}
        self.run_id = str(uuid.uuid4())
        persist_monitor_run(self.database, self.windows, csv, self.report, run_id=self.run_id)
        self.csv = csv
        self.assert_status(self.inspect(), 'matched')

    def test_s02_s03_s05_s06_unconfirmed_observations(self):
        self.marker.unlink()
        self.assert_status(self.inspect(), 'unconfirmed', 'database_without_marker')
        self.query('DELETE FROM monitor_windows')
        result = self.inspect()
        self.assert_status(result, 'unconfirmed', 'files_only')
        self.assertTrue(result['later_commit_possible'])
        for path in self.root.iterdir():
            path.unlink()
        self.assert_status(self.inspect(), 'unconfirmed', 'not_observed')
        self.query("INSERT INTO monitor_windows VALUES (%s,'old','2024-01-18 00:00:00',0,0,0,NULL,NULL,'{}','{}','fixture')",
                   (f'monitor:{self.run_id}:2024-01-18 00:00:00',))
        self.assert_status(self.inspect(), 'unconfirmed', 'database_only')

    def test_s09_partial_extra_and_changed_rows(self):
        self.query("UPDATE monitor_windows SET replay_id='wrong' WHERE window_end='2024-01-18 01:00:00'")
        self.assert_status(self.inspect(), 'mismatch')
        self.query("UPDATE monitor_windows SET replay_id='t06b-synthetic'")
        self.query("INSERT INTO monitor_windows VALUES (%s,'extra','2024-01-18 02:00:00',0,0,0,NULL,NULL,'{}','{}','fixture')",
                   (f'monitor:{self.run_id}:2024-01-18 02:00:00',))
        self.assert_status(self.inspect(), 'mismatch')
        self.query("DELETE FROM monitor_windows WHERE window_end='2024-01-18 02:00:00'")
        self.query("DELETE FROM monitor_windows WHERE window_end='2024-01-18 00:00:00'")
        self.assert_status(self.inspect(), 'mismatch')
        self.marker.unlink()
        self.assert_status(self.inspect(), 'mismatch')

    def test_s10_profile_refused_before_target_select(self):
        self.query("UPDATE data_profile_metadata SET target_contract_id='other'")
        self.assert_status(self.inspect(), 'database_profile_mismatch')
        self.assertFalse(any(q.startswith('SELECT window_id') for q, _ in self.statements))

    def test_s12_missing_metadata_and_unsupported_monitor_schema(self):
        self.query('ALTER TABLE monitor_windows DROP COLUMN psi_json')
        self.assert_status(self.inspect(), 'unsupported_input', 'monitor_schema_unsupported')
        self.query('DROP TABLE data_profile_metadata')
        self.assert_status(self.inspect(), 'legacy_unverifiable')
        self.assertFalse(any(q.startswith('SELECT window_id') for q, _ in self.statements))

    def test_s13_actual_table_permission_denial(self):
        # A SELECT-only, non-owner role is created in the disposable fixture.
        role = 'inspect_' + secrets.token_hex(4)
        with CONNECT(self.url, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT {} TO t06_app').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT SELECT ON data_profile_metadata TO {}').format(sql.Identifier(role)))
        def as_role(connection, statement, params):
            if statement.startswith('BEGIN'):
                connection.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
        self.assert_status(self.inspect(hook=as_role), 'database_unavailable')

    def test_s13_actual_lock_timeout(self):
        before, files = self.logical_snapshot(), self.files_snapshot()
        with CONNECT(self.url) as writer:
            def lock_after_snapshot(connection, statement, params):
                if statement.startswith('SELECT current_schema'):
                    writer.execute('LOCK TABLE monitor_windows IN ACCESS EXCLUSIVE MODE')
            started = time.monotonic()
            result = self.inspect(hook=lock_after_snapshot, preserve=False)
            elapsed = time.monotonic() - started
            self.assert_status(result, 'database_unavailable')
            self.assertIn('LockNotAvailable', result['reason'])
            self.assertLess(elapsed, 8)
        self.assertEqual(before, self.logical_snapshot())
        self.assertEqual(files, self.files_snapshot())

    def test_s13_actual_statement_timeout(self):
        def slow_query(connection, statement, params):
            if statement.startswith('SELECT current_schema'):
                connection.execute('SELECT pg_sleep(10)')
        started = time.monotonic()
        result = self.inspect(hook=slow_query)
        self.assert_status(result, 'database_unavailable')
        self.assertIn('QueryCanceled', result['reason'])
        self.assertLess(time.monotonic() - started, 12)

    def test_s13_actual_connect_failure_and_secret_redaction(self):
        bad_url = self.url.replace('/t06b', '/missing_' + secrets.token_hex(4))
        self.assert_status(self.inspect(url=bad_url), 'database_unavailable')

    def test_actual_connection_loss_during_snapshot_is_unavailable(self):
        def terminate(connection, statement, params):
            if statement.startswith('SELECT current_schema'):
                self.query('SELECT pg_terminate_backend(%s)', (connection.info.backend_pid,))
        self.assert_status(self.inspect(hook=terminate), 'database_unavailable')

    def test_s16_other_and_legacy_rows_are_preserved(self):
        for key in ('legacy:v1:old', f'monitor:{uuid.uuid4()}:2024-01-18 01:00:00'):
            self.query("INSERT INTO monitor_windows VALUES (%s,'old','2024-01-18 01:00:00',99,3,0.1,1,2,'{}','{}','old')", (key,))
        self.assert_status(self.inspect(), 'matched')

    def test_s20_update_rejected_on_inspector_connection(self):
        rejected = []
        def attempt_update(connection, statement, params):
            if statement.startswith('SELECT current_schema'):
                settings = connection.execute("SELECT current_setting('transaction_isolation') AS i, "
                    "current_setting('transaction_read_only') AS r, current_setting('statement_timeout') AS s, "
                    "current_setting('lock_timeout') AS l").fetchone()
                self.assertEqual(settings, {'i': 'repeatable read', 'r': 'on', 's': '5s', 'l': '1s'})
                try:
                    connection.execute('UPDATE monitor_windows SET prediction_count=99')
                except psycopg.errors.ReadOnlySqlTransaction:
                    rejected.append(True)
                    raise
                self.fail('UPDATE was accepted')
        self.assert_status(self.inspect(hook=attempt_update), 'database_unavailable')
        self.assertEqual(rejected, [True])

    def test_s21_late_commit_not_visible_until_next_inspection(self):
        self.marker.unlink()
        rows = self.query('SELECT * FROM monitor_windows ORDER BY window_id')
        self.query('DELETE FROM monitor_windows')
        writer = CONNECT(self.url)
        self.addCleanup(writer.close)
        for row in rows:
            writer.execute('INSERT INTO monitor_windows VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', tuple(row.values()))
        self.assert_status(self.inspect(), 'unconfirmed', 'files_only')
        committed = []
        def delayed_commit(connection, statement, params):
            # Schema SELECT is the first snapshot query. Commit after it, before
            # metadata/row SELECT, so read committed would incorrectly see rows.
            if statement.startswith('SELECT current_schema'):
                writer.commit()
                committed.append(True)
        first = self.inspect(hook=delayed_commit, preserve=False)
        self.assert_status(first, 'unconfirmed', 'files_only')
        self.assertEqual(committed, [True])
        second = self.inspect()
        self.assert_status(second, 'unconfirmed', 'database_without_marker')
        self.assertTrue(first['later_commit_possible'] and second['later_commit_possible'])
        self.assertEqual(normalized(rows), self.query_normalized())

    def test_profile_and_rows_share_the_same_snapshot(self):
        def change_metadata(connection, statement, params):
            if statement.startswith('SELECT current_schema'):
                self.query("UPDATE data_profile_metadata SET data_profile_id='other'")
        files, rows = self.files_snapshot(), self.query_normalized()
        self.assert_status(self.inspect(hook=change_metadata, preserve=False), 'matched')
        self.assertEqual(files, self.files_snapshot())
        self.assertEqual(rows, self.query_normalized())
        self.assert_status(self.inspect(), 'database_profile_mismatch')

    def query_normalized(self):
        return normalized(self.query('SELECT * FROM monitor_windows ORDER BY window_id'))

    def test_s14_marker_appears_during_database_read(self):
        marker_bytes = self.marker.read_bytes()
        self.marker.unlink()
        before = self.logical_snapshot()
        def appearance(connection, statement, params):
            if statement.startswith('SELECT current_schema'):
                self.marker.write_bytes(marker_bytes)
        self.assert_status(self.inspect(hook=appearance, preserve=False), 'changed_during_inspection')
        self.assertEqual(before, self.logical_snapshot())

    def test_row_limit_is_unsupported(self):
        with patch.object(inspection, 'MAX_ROWS', 1):
            # File bound must remain >=2 to reach the DB bound.
            from bike_mlops.monitor_inspection import _postgres_rows, _base, InspectionFailure
            out = _base(self.run_id, self.csv, PROFILE, TARGET)
            with self.assertRaises(InspectionFailure) as raised:
                _postgres_rows(self.url, self.run_id, PROFILE, TARGET, out)
            self.assertEqual(raised.exception.reason, 'database_row_limit_exceeded')

    def test_cli_postgres_json_and_exit_codes(self):
        environment = os.environ.copy()
        environment.update(DATABASE_URL=self.url, DATA_PROFILE_ID=PROFILE, MLFLOW_DISABLE_AGENT_HINT='1')
        for status, exit_code in (('matched', 0), ('mismatch', 1), ('unconfirmed', 2)):
            before, files = self.logical_snapshot(), self.files_snapshot()
            process = subprocess.run([sys.executable, '-m', 'bike_mlops.cli', 'monitor-inspect',
                                      '--csv', str(self.csv), '--run-id', self.run_id],
                                     env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, exit_code)
            self.assertEqual(json.loads(process.stdout)['status'], status)
            self.assertEqual(before, self.logical_snapshot())
            self.assertEqual(files, self.files_snapshot())
            self.assertNotIn(self.url, process.stdout + process.stderr)
            if status == 'matched':
                self.query("UPDATE monitor_windows SET prediction_count=99 WHERE window_end='2024-01-18 01:00:00'")
            elif status == 'mismatch':
                self.query("UPDATE monitor_windows SET prediction_count=3")
                self.marker.unlink()

    def test_writer_failure_before_commit_and_after_commit_without_marker(self):
        for phase in ('rollback', 'marker'):
            run_id, csv = str(uuid.uuid4()), self.root / f'{phase}.csv'
            before = self.logical_snapshot()
            if phase == 'rollback':
                original_execute = self.database.execute
                def fail_second(connection, statement, params=()):
                    if statement.startswith('INSERT INTO monitor_windows') and params[2].endswith('01:00:00'):
                        raise RuntimeError('synthetic interruption before commit')
                    return original_execute(connection, statement, params)
                with patch.object(self.database, 'execute', side_effect=fail_second), self.assertRaises(MonitorPersistenceError):
                    persist_monitor_run(self.database, self.windows, csv, self.report, run_id=run_id)
                self.assertEqual(before, self.logical_snapshot())
            else:
                from bike_mlops.monitor_persistence import _write_new_file
                def fail_marker(path, body):
                    if path.name.endswith('.complete.json'):
                        raise OSError('synthetic interruption after commit')
                    _write_new_file(path, body)
                with patch('bike_mlops.monitor_persistence._write_new_file', side_effect=fail_marker), self.assertRaises(MonitorPersistenceError):
                    persist_monitor_run(self.database, self.windows, csv, self.report, run_id=run_id)
            self.csv, self.run_id = csv, run_id
            self.assert_status(self.inspect(), 'unconfirmed', 'files_only' if phase == 'rollback' else 'database_without_marker')

    def test_actual_sigkill_writer_before_and_after_database_commit(self):
        child_code = '''
import json, os, sys, time
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from bike_mlops.db import Database
from bike_mlops.monitor_persistence import persist_monitor_run, _write_new_file
payload = json.load(sys.stdin)
database = Database(os.environ['T06B_FIXTURE_URL'], payload['profile'], payload['target'])
def pause():
    print('READY', flush=True)
    time.sleep(60)
original = database.execute
def execute(connection, statement, params=()):
    result = original(connection, statement, params)
    if statement.startswith('INSERT INTO monitor_windows'):
        pause()
    return result
def write(path, body):
    if path.name.endswith('.complete.json'):
        pause()
    _write_new_file(path, body)
if payload['phase'] == 'before':
    database.execute = execute
with patch('bike_mlops.monitor_persistence._write_new_file', side_effect=write):
    persist_monitor_run(database, pd.DataFrame(payload['windows']), Path(payload['csv']),
                        payload['report'], run_id=payload['run_id'])
'''
        for phase in ('before', 'after'):
            csv, run_id = self.root / f'kill-{phase}.csv', str(uuid.uuid4())
            before = self.logical_snapshot()
            payload = {'profile': PROFILE, 'target': TARGET, 'phase': phase, 'csv': str(csv),
                       'run_id': run_id, 'report': self.report,
                       'windows': [{k: None if isinstance(v, float) and math.isnan(v) else v
                                    for k, v in row.items()} for row in self.windows.to_dict('records')]}
            environment = os.environ.copy()
            environment['T06B_FIXTURE_URL'] = self.url
            process = subprocess.Popen([sys.executable, '-c', child_code], env=environment,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True)
            try:
                process.stdin.write(json.dumps(payload, allow_nan=False))
                process.stdin.close()
                process.stdin = None
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=15), 'Writer did not reach interruption point')
                self.assertEqual(process.stdout.readline().strip(), 'READY')
                process.kill()
                process.communicate(timeout=10)
                self.assertEqual(process.returncode, -9)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
            self.csv, self.run_id = csv, run_id
            self.assertFalse(csv.with_suffix('.complete.json').exists())
            self.assert_status(self.inspect(), 'unconfirmed', 'files_only' if phase == 'before' else 'database_without_marker')
            after = self.logical_snapshot()
            if phase == 'before':
                self.assertEqual(before, after)
            else:
                self.assertEqual(len(after['monitor_windows']), len(before['monitor_windows']) + 2)
                self.assertEqual(before['monitor_windows'], [row for row in after['monitor_windows']
                    if not row['window_id'].startswith(f'monitor:{run_id}:')])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-disposable', action='store_true', required=True)
    parser.parse_args()
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    context = docker('context', 'show')
    host = json.loads(docker('context', 'inspect', context, '--format', '{{json .Endpoints.docker.Host}}'))
    if not host.startswith('unix://') or (os.environ.get('DOCKER_HOST') and not os.environ['DOCKER_HOST'].startswith('unix://')):
        raise RuntimeError('Local Unix-socket Docker required')
    image = 'postgres:17.6-bookworm'
    image_id = docker('image', 'inspect', image, '--format', '{{.Id}}')
    token, password = secrets.token_hex(8), secrets.token_hex(24)
    label = 'bike.monitor.inspection.test'
    container_id = None
    result = None
    try:
        environment = os.environ.copy()
        environment['POSTGRES_PASSWORD'] = password
        container_id = docker('run', '-d', '--rm', '--pull=never', '--name', f'bike-t06b-{token}',
                              '--label', f'{label}={token}', '--publish', '127.0.0.1::5432',
                              '-e', 'POSTGRES_PASSWORD', image, env=environment)
        if len(container_id) != 64 or any(c not in '0123456789abcdef' for c in container_id):
            raise RuntimeError('Invalid container ID')
        binding = docker('port', container_id, '5432/tcp')
        if not binding.startswith('127.0.0.1:') or '\n' in binding:
            raise RuntimeError('Loopback binding required')
        port = int(binding.rsplit(':', 1)[1])
        admin = f'postgresql://postgres:{password}@127.0.0.1:{port}/postgres'
        for attempt in range(60):
            try:
                with CONNECT(admin, connect_timeout=2) as connection:
                    connection.execute('SELECT 1')
                break
            except psycopg.OperationalError:
                if attempt == 59:
                    raise RuntimeError('Owned PostgreSQL startup failed') from None
                time.sleep(.25)
        app_password = secrets.token_hex(24)
        with CONNECT(admin, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE ROLE t06_app LOGIN CREATEROLE PASSWORD {}').format(sql.Literal(app_password)))
            connection.execute('CREATE DATABASE t06b OWNER t06_app')
        PostgresInspectionAcceptance.url = f'postgresql://t06_app:{app_password}@127.0.0.1:{port}/t06b'
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PostgresInspectionAcceptance))
    finally:
        if container_id:
            labels = json.loads(docker('inspect', container_id, '--format', '{{json .Config.Labels}}'))
            if labels.get(label) != token:
                raise RuntimeError('Cleanup ownership mismatch')
            docker('rm', '-f', container_id)
            if docker('ps', '-a', '--filter', f'label={label}={token}', '--format', '{{.ID}}'):
                raise RuntimeError('Owned container remains')
            print('T06b owned container removed; cleanup_complete=true', flush=True)
    print(json.dumps({'task': 'T06b', 'scope': 'synthetic_only', 'status': 'passed' if result.wasSuccessful() else 'failed',
                      'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                      'image_id': image_id, 'cleanup_complete': True, 'real_capital_evidence': False,
                      'deployment_permission': 'not_evaluated'}, allow_nan=False))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit('T06b acceptance interrupted; owned-resource cleanup attempted') from None
    except Exception as error:
        raise SystemExit(f'T06b acceptance failed ({type(error).__name__}); cleanup attempted') from None
