"""Bounded, read-only access to the run's committed official log only."""
from dataclasses import dataclass
import html
import unicodedata
from uuid import UUID

from fastapi import HTTPException, Response

MAX_PAGE_BYTES = 16384
MAX_LOG_BYTES = 1048576
SAFE_HEADERS = {
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': "default-src 'none'; sandbox; base-uri 'none'; form-action 'none'",
}


@dataclass(frozen=True)
class LogPage:
    attempt_id: UUID
    chunk: bytes
    total_bytes: int
    retained_bytes: int
    observed_bytes: int


def read_page(connection_factory, run_id, stream, offset, limit):
    # Column names are server-owned. Everything else is a bound SQL parameter.
    columns = {'stdout': 'stdout', 'stderr': 'stderr'}
    if (stream not in columns or type(offset) is not int or not 0 <= offset <= MAX_LOG_BYTES
            or type(limit) is not int or not 1 <= limit <= MAX_PAGE_BYTES):
        raise HTTPException(422, 'invalid log page')
    column = columns[stream]
    with connection_factory() as conn:
        row = conn.execute(
            'SELECT r.state,r.completed_attempt_id,a.state AS attempt_state,'
            'l.attempt_id AS log_attempt_id,l.observed_bytes,'
            f'octet_length(l.{column}) AS total_bytes,'
            'octet_length(l.stdout)+octet_length(l.stderr) AS retained_bytes,'
            f'substring(l.{column} FROM %s FOR %s) AS chunk '
            'FROM runs r LEFT JOIN attempts a ON a.id=r.completed_attempt_id AND a.run_id=r.id '
            'LEFT JOIN attempt_logs l ON l.attempt_id=a.id WHERE r.id=%s',
            (offset+1, limit, run_id)).fetchone()
    if row is None:
        raise HTTPException(404, 'run not found')
    if (row['completed_attempt_id'] is None
            or row['state'] not in ('succeeded', 'failed', 'timed_out', 'canceled')
            or row['attempt_state'] != row['state']):
        raise HTTPException(409, 'logs not finalized')
    if row['log_attempt_id'] is None:
        raise HTTPException(404, 'logs not found')
    if offset > row['total_bytes']:
        raise HTTPException(416, 'offset exceeds retained log size',
                            headers={'X-Log-Total-Bytes': str(row['total_bytes'])})
    return LogPage(row['log_attempt_id'], row['chunk'], row['total_bytes'],
                   row['retained_bytes'], row['observed_bytes'])


def display_text(chunk):
    # Byte offsets remain exact; display is intentionally not a binary download.
    text = chunk.decode('utf-8', errors='replace')
    return ''.join(f'\\u{ord(c):04x}' if c not in ('\n', '\t')
                   and unicodedata.category(c) in ('Cc', 'Cf') else c for c in text)


def response(page, stream, offset, format):
    text = display_text(page.chunk)
    headers = SAFE_HEADERS | {
        'X-Log-Attempt-Id': str(page.attempt_id),
        'X-Log-Stream': stream,
        'X-Log-Offset': str(offset),
        'X-Log-Bytes': str(len(page.chunk)),
        'X-Log-Total-Bytes': str(page.total_bytes),
        'X-Log-Retained-Bytes': str(page.retained_bytes),
        'X-Log-Observed-Bytes': str(page.observed_bytes),
        'X-Log-Truncated': str(page.observed_bytes > page.retained_bytes).lower(),
    }
    next_offset = offset + len(page.chunk)
    if next_offset < page.total_bytes:
        headers['X-Log-Next-Offset'] = str(next_offset)
    if format == 'html':
        return Response('<pre>\n'+html.escape(text, quote=True)+'</pre>',
                        media_type='text/html', headers=headers)
    return Response(text, media_type='text/plain', headers=headers)
