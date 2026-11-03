"""Operation binding never infers approval or selects by latest version."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bike_mlops.db import Database
from bike_mlops.deployment_metadata import inspect_api_operation, operation_identity, read_connection

P, T = 'uci-bike-sharing', 'uci-bike-sharing-cnt-dataset-local-hour'


class DeploymentMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.url = 'sqlite:///' + str(Path(self.temp.name) / 'audit.db')
        self.db = Database(self.url, P, T)
        self.db.initialize()
        self.row = dict(deployment_id='operation-1', status='in_progress', phase='prepared',
            data_profile_id=P, target_contract_id=T, from_slot='blue', to_slot='green', from_version='2',
            to_version='3', approver='operator declaration', reason='user supplied reason',
            created_at='2026-11-03T00:00:05Z', updated_at='2026-11-03T00:00:05Z')
        self.binding = operation_identity(self.row, 'a'*64)
        identity = dict(model_name='bike-demand', model_version='3', artifact_sha256='a'*64,
            data_profile_id=P, target_contract_id=T, deployment_operation=self.binding)
        self.row['probe_json'] = json.dumps(dict(recovery_context=dict(schema_version=3, candidate_identity=identity)))
        self.insert(self.row)

    def insert(self, row):
        with self.db.transaction() as c:
            self.db.execute(c, 'INSERT INTO deployments(' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))

    def inspect(self, **changes):
        args = dict(url=self.url, operation_id='operation-1', slot='green', profile=P, target=T,
                    model_name='bike-demand', version='3', artifact_hash='a'*64)
        return inspect_api_operation(**dict(args, **changes))

    def test_prepared_then_completed_reads_same_row_and_declarations(self):
        self.assertEqual(self.inspect()['status'], 'matched_pending')
        with self.db.transaction() as c:
            c.execute("UPDATE deployments SET status='completed',phase='completed'")
        result = self.inspect()
        self.assertEqual(result['status'], 'matched_completed')
        self.assertEqual(result['operation'], self.binding)
        self.assertFalse(result['approval_evidence'])

    def test_same_version_later_operation_and_utc_regression_do_not_replace_binding(self):
        self.insert(dict(self.row, deployment_id='operation-2', approver='different', created_at='2026-11-02T00:00:00Z'))
        self.assertEqual(self.inspect()['operation'], self.binding)
        self.assertEqual(self.inspect(operation_id='operation-2')['status'], 'operation_metadata_mismatch')

    def test_missing_legacy_wrong_slot_profile_artifact_are_distinct(self):
        for changes, expected in (({'operation_id':None}, 'legacy_unverifiable'),
            ({'operation_id':'absent'}, 'operation_missing'), ({'slot':'blue'}, 'runtime_identity_mismatch'),
            ({'version':'2'}, 'runtime_identity_mismatch'), ({'profile':'other'}, 'database_profile_mismatch'),
            ({'artifact_hash':'b'*64}, 'artifact_identity_mismatch'), ({'slot':'other'}, 'invalid_operation_binding')):
            with self.subTest(changes=changes):
                self.assertEqual(self.inspect(**changes)['status'], expected)

    def test_changed_approval_or_failed_row_is_not_presented_as_bound(self):
        with self.db.transaction() as c:
            c.execute("UPDATE deployments SET approver='changed'")
        self.assertEqual(self.inspect()['status'], 'operation_metadata_mismatch')
        with self.db.transaction() as c:
            c.execute("UPDATE deployments SET approver='operator declaration',status='failed'")
        self.assertEqual(self.inspect()['status'], 'operation_not_active')

    def test_no_creation_readonly_and_safe_error_response(self):
        missing = Path(self.temp.name) / 'missing.db'
        self.assertEqual(self.inspect(url='sqlite:///' + str(missing))['status'], 'unavailable')
        self.assertFalse(missing.exists())
        with read_connection(self.url) as (c, _):
            with self.assertRaises(Exception):
                c.execute("DELETE FROM deployments")
        result = self.inspect(url='postgresql://secret:password@localhost:1/private')
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('password', json.dumps(result))

    def test_profile_is_checked_before_any_deployment_select(self):
        import sqlite3
        from contextlib import contextmanager
        selected=[]
        @contextmanager
        def connection(_):
            with read_connection(self.url) as (c, ph):
                c.set_trace_callback(selected.append)
                yield c, ph
        with patch('bike_mlops.deployment_metadata.read_connection', connection):
            # Keep original helper callable inside the fixture context.
            result=self.inspect(profile='foreign')
        self.assertEqual(result['status'], 'database_profile_mismatch')
        self.assertFalse(any('FROM deployments' in s for s in selected))

    def test_api_presents_pending_then_completed_and_refuses_tampered_binding(self):
        from fastapi.testclient import TestClient
        from bike_mlops import api
        class Constant:
            def predict(self, frame): return [1.0]
        with patch.dict(os.environ, DATABASE_URL=self.url, MODEL_VERSION='3', DEPLOYMENT_ID='operation-1', DEPLOYMENT_SLOT='green'), \
             patch.object(api, '_runtime_model', return_value=(Constant(), '3', 'a'*64, 'passed', None)), TestClient(api.app) as client:
            self.assertEqual(client.get('/ready').json()['deployment_operation']['status'], 'matched_pending')
            with self.db.transaction() as c:
                c.execute("UPDATE deployments SET status='completed',phase='completed'")
            self.assertEqual(client.get('/version').json()['deployment_operation']['operation'], self.binding)
            self.assertEqual(client.get('/ready').json()['deployment_operation']['status'], 'matched_completed')
            with self.db.transaction() as c:
                c.execute("UPDATE deployments SET reason='tampered'")
            self.assertEqual(client.get('/ready').status_code, 503)
            self.assertNotIn('tampered', client.get('/version').text)
