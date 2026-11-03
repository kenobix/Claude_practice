"""Read only binding between a pinned API process and one deployment operation.

Names/reasons are operator declarations, never authenticated approval evidence.
No lookup by version, timestamp, or latest row is permitted.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3

from bike_mlops.capital_deployment_evidence import strict_json


@contextmanager
def read_connection(url):
    connection = None
    try:
        if url.startswith('sqlite:///'):
            raw = url[len('sqlite:///'):]
            if not raw or raw == ':memory:' or '?' in raw or '#' in raw:
                raise ValueError('Unsupported database URL')
            connection = sqlite3.connect(Path(raw).resolve().as_uri() + '?mode=ro', uri=True,
                                         timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            placeholder = '?'
        elif url.startswith(('postgresql://', 'postgres://')):
            import psycopg
            from psycopg.rows import dict_row
            connection = psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5,
                options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000')
            connection.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            placeholder = '%s'
        else:
            raise ValueError('Unsupported database URL')
        yield connection, placeholder
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()


def operation_identity(row, artifact_hash):
    return dict(deployment_id=row['deployment_id'], slot=row['to_slot'],
                model_version=str(row['to_version']), artifact_sha256=artifact_hash,
                data_profile_id=row['data_profile_id'], target_contract_id=row['target_contract_id'],
                declared_approver=row['approver'], declared_reason=row['reason'])


def inspect_api_operation(url, operation_id, slot, profile, target, model_name, version, artifact_hash):
    base = dict(status='unavailable', approval_evidence=False)
    if not operation_id:
        return dict(base, status='legacy_unverifiable')
    if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', operation_id) or slot not in {'blue', 'green'}:
        return dict(base, status='invalid_operation_binding')
    try:
        with read_connection(url) as (connection, placeholder):
            metadata = connection.execute('SELECT data_profile_id,target_contract_id FROM data_profile_metadata WHERE singleton=1').fetchone()
            if metadata is None or metadata['data_profile_id'] != profile or metadata['target_contract_id'] != target:
                return dict(base, status='database_profile_mismatch')
            row = connection.execute('SELECT * FROM deployments WHERE deployment_id=' + placeholder,
                                     (operation_id,)).fetchone()
            if row is None:
                return dict(base, status='operation_missing')
            row = dict(row)
            if row['data_profile_id'] != profile or row['target_contract_id'] != target:
                return dict(base, status='ledger_profile_mismatch')
            if row['to_slot'] != slot or str(row['to_version']) != str(version):
                return dict(base, status='runtime_identity_mismatch')
            context = strict_json(row['probe_json'] or '{}').get('recovery_context', {})
            identity = context.get('candidate_identity', {})
            expected = dict(model_name=model_name, model_version=str(version), artifact_sha256=artifact_hash,
                            data_profile_id=profile, target_contract_id=target)
            if not artifact_hash or any(identity.get(k) != v for k, v in expected.items()):
                return dict(base, status='artifact_identity_mismatch')
            # Declarations may be displayed, but never echo URI/secret-shaped input.
            declaration = row['approver'] + ' ' + row['reason']
            if re.search(r'(?i)(?:[a-z][a-z0-9+.-]*://|(?:password|token|secret|api[_-]?key|authorization)\s*[:=])', declaration):
                return dict(base, status='unsafe_declaration')
            binding = operation_identity(row, artifact_hash)
            if context.get('schema_version') != 3 or identity.get('deployment_operation') != binding:
                return dict(base, status='operation_metadata_mismatch')
            if row['status'] not in {'in_progress', 'completed'}:
                return dict(base, status='operation_not_active')
            return dict(base, status='matched_completed' if row['status'] == 'completed' else 'matched_pending',
                        operation=binding, ledger_phase=row['phase'], ledger_status=row['status'])
    except Exception as error:
        return dict(base, error_type=type(error).__name__)
