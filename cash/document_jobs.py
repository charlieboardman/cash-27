"""Durable capture jobs and readiness checks, independent of the web process."""
from .commands import ValidationFailure

PENDING = ('queued', 'running')


def enqueue(conn, document_id, rel_path, sha256, captured_at):
    """Called in the same transaction that accepts the document."""
    conn.execute(
        "INSERT INTO document_processing"
        " (document_id, status, source_rel_path, source_sha256, captured_at)"
        " VALUES (?, 'queued', ?, ?, ?)",
        (document_id, rel_path, sha256, captured_at.isoformat()))


def metadata(conn, document_id):
    row = conn.execute("SELECT status, error FROM document_processing WHERE document_id=?",
                       (document_id,)).fetchone()
    if not row:
        return None
    return {
        'status': row['status'],
        'ready': row['status'] not in PENDING,
        'message': ('Document processing…' if row['status'] in PENDING else
                    'Processing failed; document saved for manual review.' if row['status'] == 'failed' else
                    'Document ready.'),
    }


def require_ready(conn, document_id):
    state = metadata(conn, document_id)
    if state and not state['ready']:
        raise ValidationFailure('document_processing',
                                'This document is still processing. Please wait before attaching it.',
                                {'document_id': 'processing'})


def object_refs(conn):
    """Include immutable originals so backups remain safe during finalization.

    Accept snapshots from before the jobs migration, too.
    """
    sql = "SELECT rel_path, sha256 FROM documents WHERE state!='missing'"
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='document_processing'").fetchone():
        sql += (" UNION SELECT source_rel_path AS rel_path, source_sha256 AS sha256"
                " FROM document_processing p JOIN documents d USING(document_id)"
                " WHERE d.state!='missing'")
    return conn.execute(sql).fetchall()
