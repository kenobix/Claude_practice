import os

import psycopg
from psycopg.rows import dict_row


class DatabaseUnavailable(Exception):
    pass


def connect():
    dsn = os.environ.get('DATABASE_URL')
    if not dsn:
        raise DatabaseUnavailable()
    # Fresh connections use READ COMMITTED; conflicts wait only for a bounded time.
    conn = psycopg.connect(dsn, connect_timeout=2, row_factory=dict_row, application_name='ci-runner-intake',
                           options='-c statement_timeout=5000 -c lock_timeout=3000')
    conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
    return conn
