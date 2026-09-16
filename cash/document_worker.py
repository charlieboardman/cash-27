"""One durable job at a time; ML lives only in short-lived child processes."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import fcntl
import hashlib
import logging
import os
from pathlib import Path
import signal
import threading
import time
from types import SimpleNamespace

from .db import connect
from .web import qr_capture
from .web.documents import _publish_pdf, document_rel_path

LOG = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
HEALTH_MAX_AGE = 300


def configuration(root):
    app = SimpleNamespace(config={'DATA_ROOT': root})
    qr_capture.configure(app)
    return app.config


def process_next(root: Path, config=None) -> bool:
    """The OS lock covers claiming, processing, and publication (not a DB lock).

    After a crash the OS releases this lock; the next process resumes the oldest
    running job. Repeated crashes give up with a usable original, not an endless
    poison job. The child inherits the lock so restarting only the parent cannot
    launch a second model while its previous child is still running.
    """
    config = config or configuration(root)
    heartbeat = root / 'staging' / 'document-worker.heartbeat'
    with (root / 'staging' / 'document-worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        with closing(connect(root / 'cash.db')) as conn:
            # The application owns migrations; an early worker waits for it.
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='document_processing'").fetchone():
                return False
            conn.execute('BEGIN IMMEDIATE')
            try:
                row = conn.execute(
                    "SELECT p.*, d.state, d.rel_path FROM document_processing p"
                    " JOIN documents d USING(document_id) WHERE p.status IN ('queued','running')"
                    " ORDER BY p.captured_at, p.document_id LIMIT 1").fetchone()
                if not row:
                    conn.execute('COMMIT')
                    heartbeat.touch()
                    return False
                if row['state'] != 'inbox':
                    conn.execute("UPDATE document_processing SET status='cancelled' WHERE document_id=?",
                                 (row['document_id'],))
                    conn.execute('COMMIT')
                    return True
                conn.execute("UPDATE document_processing SET status='running', attempts=attempts+1,"
                             " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE document_id=?",
                             (row['document_id'],))
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise
            heartbeat.touch()
            config = {**config, 'WORKER_LOCK_FD': lock.fileno()}
            recognition = {'status': 'failed', 'ids': []}
            output = None
            try:
                source = root / row['source_rel_path']
                if hashlib.sha256(source.read_bytes()).hexdigest() != row['source_sha256']:
                    raise ValueError('Original document failed its integrity check')
                if row['attempts'] >= MAX_ATTEMPTS:
                    raise RuntimeError('Processing repeatedly interrupted; retained original')
                recognition = qr_capture.recognize(source, config)
                recognition = qr_capture.receipt_rotations(source, recognition, config)
                contents = source.read_bytes()
                rotated = qr_capture.orient_pdf(contents, recognition)
                # Keep the original immutable for in-flight backup snapshots.
                # Unchanged PDFs keep their object; only their display name changes.
                if rotated != contents:
                    display_name = qr_capture.capture_name(recognition, datetime.fromisoformat(row['captured_at']))
                    rel_path = document_rel_path(row['document_id'], display_name[:-4] + '-processed.pdf',
                                                 datetime.fromisoformat(row['captured_at']))
                    target = root / rel_path
                    digest = hashlib.sha256(rotated).hexdigest()
                    if target.exists():
                        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                            raise ValueError('Conflicting processed document')
                    else:
                        _publish_pdf(rotated, root / 'documents', rel_path)
                    output = (rel_path, digest, len(rotated))
            except Exception:
                LOG.exception('Processing failed for %s; retaining original', row['document_id'])
                recognition = {'status': 'failed', 'ids': []}
                output = None
            conn.execute('BEGIN IMMEDIATE')
            try:
                # Deleting during processing must never resurrect the document.
                current = conn.execute('SELECT state FROM documents WHERE document_id=?',
                                       (row['document_id'],)).fetchone()
                if current['state'] != 'inbox':
                    conn.execute("UPDATE document_processing SET status='cancelled' WHERE document_id=?",
                                 (row['document_id'],))
                    conn.execute('COMMIT')
                    if output:
                        (root / output[0]).unlink(missing_ok=True)
                    return True
                qr_capture.record_match(conn, row['document_id'], recognition)
                display_name = qr_capture.capture_name(recognition, datetime.fromisoformat(row['captured_at']))
                conn.execute("UPDATE documents SET display_name=?,"
                             " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE document_id=?",
                             (display_name, row['document_id']))
                if output:
                    conn.execute('UPDATE documents SET rel_path=?, sha256=?, size_bytes=? WHERE document_id=?',
                                 (*output, row['document_id']))
                failed = recognition['status'] in ('failed', 'timeout', 'busy')
                conn.execute("UPDATE document_processing SET status=?, error=?,"
                             " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE document_id=?",
                             ('failed' if failed else 'complete', recognition['status'] if failed else None,
                              row['document_id']))
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise
            heartbeat.touch()
            LOG.info('Processed %s: %s', row['document_id'], recognition['status'])
            return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['loop', 'once', 'health'], nargs='?', default='loop')
    args = parser.parse_args()
    root = Path(os.environ.get('CASH_DATA_ROOT', '/data')).resolve()
    heartbeat = root / 'staging' / 'document-worker.heartbeat'
    if args.command == 'health':
        if not heartbeat.exists() or time.time() - heartbeat.stat().st_mtime > HEALTH_MAX_AGE:
            raise SystemExit('Document worker heartbeat is stale')
        return
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    (root / 'staging').mkdir(parents=True, exist_ok=True)
    if args.command == 'once':
        process_next(root)
        return
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    while not stop.is_set():
        try:
            if not process_next(root):
                stop.wait(2)
        except Exception:
            LOG.exception('Document worker will retry')
            stop.wait(5)


if __name__ == '__main__':
    main()
