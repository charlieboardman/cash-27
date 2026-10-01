"""Queue durability, isolation from HTTP, filing gates, and immutable backups."""
from concurrent.futures import ThreadPoolExecutor
import io
import threading
import time
import uuid

import pytest

from cash import ops
from cash.db import connect
from cash.document_worker import process_next
from cash.web import qr_capture
from cash.web.documents import recover_documents
from tests.test_qr_capture import env, upload
from tests.test_qr_orientation import image_pdf


def test_pending_upload_is_durable_idempotent_and_cannot_be_filed(env):
    app, client, headers, conn = env
    token = str(uuid.uuid4())
    contents = image_pdf()
    def capture():
        return client.post('/api/documents/capture',
            data={'files': (io.BytesIO(contents), 'scanner.pdf')},
            headers={**headers, 'X-Upload-ID': token})
    first = capture()
    assert first.status_code == 200
    doc = first.json['data']['documents'][0]
    assert doc['processing']['status'] == 'queued'
    assert client.get(f"/api/documents/{doc['document_id']}/document.pdf", follow_redirects=True).status_code == 200
    url = '/api/review/signout/FY27-0002/attach-voucher'
    body = {'submission_id': str(uuid.uuid4()), 'expected_version': 1,
            'document_id': doc['document_id']}
    rejected = client.post(url, json=body, headers=headers)
    assert rejected.status_code == 422
    assert rejected.json['error']['code'] == 'document_processing'
    assert conn.execute('SELECT COUNT(*) FROM transaction_documents').fetchone()[0] == 0
    assert capture().json == first.json
    process_next(app.config['DATA_ROOT'], app.config)
    # Scanner retries retain their canonical response even after processing.
    assert capture().json == first.json
    assert conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM document_processing').fetchone()[0] == 1
    assert client.post(url, json=body, headers=headers).status_code == 200


def test_slow_model_does_not_hold_database_or_http_and_only_one_worker_runs(env, monkeypatch):
    app, client, headers, conn = env
    upload(env, process=False)
    started, release = threading.Event(), threading.Event()
    def recognize(*_):
        started.set()
        assert release.wait(10)
        return {'status': 'no_code', 'ids': []}
    monkeypatch.setattr(qr_capture, 'recognize', recognize)
    with ThreadPoolExecutor(1) as pool:
        job = pool.submit(process_next, app.config['DATA_ROOT'], app.config)
        assert started.wait(3)
        try:
            assert not process_next(app.config['DATA_ROOT'], app.config)
            before = time.monotonic()
            assert client.get('/api/health').status_code == 200
            assert client.get('/api/ledger').status_code == 200
            assert client.get('/api/documents/inbox').json['data']['documents'][0]['processing']['status'] == 'running'
            # A separate upload can commit while the first model is blocked.
            assert upload(env, process=False)['processing']['status'] == 'queued'
            assert time.monotonic() - before < 2
        finally:
            release.set()
        assert job.result(timeout=5)
    assert conn.execute("SELECT attempts FROM document_processing ORDER BY document_id").fetchall()[0][0] == 1


@pytest.mark.parametrize('attempts,expected', [(1, 'complete'), (3, 'failed')])
def test_interrupted_job_resumes_but_poison_jobs_do_not_loop(env, monkeypatch, attempts, expected):
    app, client, _, conn = env
    doc = upload(env, process=False)
    conn.execute("UPDATE document_processing SET status='running', attempts=?", (attempts,))
    if attempts == 3:
        monkeypatch.setattr(qr_capture, 'recognize', lambda *_: pytest.fail('Poison job reran'))
    assert process_next(app.config['DATA_ROOT'], app.config)
    current = client.get('/api/documents/inbox').json['data']['documents'][0]
    assert current['processing']['status'] == expected
    assert current['processing']['ready']
    assert not process_next(app.config['DATA_ROOT'], app.config)
    assert client.get(f"/api/documents/{doc['document_id']}/document.pdf", follow_redirects=True).status_code == 200


def test_discard_during_processing_cannot_be_resurrected(env, monkeypatch):
    app, client, headers, conn = env
    doc = upload(env, process=False)
    def recognize(*_):
        assert client.post(f"/api/documents/{doc['document_id']}/discard", headers=headers).status_code == 200
        return {'status': 'detected', 'ids': ['FY27-0002'], 'rotations': [180]}
    monkeypatch.setattr(qr_capture, 'recognize', recognize)
    assert process_next(app.config['DATA_ROOT'], app.config)
    assert client.get('/api/documents/inbox').json['data']['documents'] == []
    assert conn.execute('SELECT status FROM document_processing').fetchone()[0] == 'cancelled'
    assert conn.execute('SELECT COUNT(*) FROM document_qr').fetchone()[0] == 0
    assert recover_documents(app.config['DATA_ROOT'], conn) == []


def test_crash_after_publication_resumes_without_double_rotation(env, monkeypatch):
    app, client, _, conn = env
    upload(env, process=False)
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {
        'status': 'detected', 'ids': ['FY27-0002'], 'rotations': [180]})
    original_record = qr_capture.record_match
    def crash(*_):
        raise SystemExit('Simulated crash after immutable publication')
    monkeypatch.setattr(qr_capture, 'record_match', crash)
    with pytest.raises(SystemExit):
        process_next(app.config['DATA_ROOT'], app.config)
    assert conn.execute('SELECT status FROM document_processing').fetchone()[0] == 'running'
    monkeypatch.setattr(qr_capture, 'record_match', original_record)
    assert process_next(app.config['DATA_ROOT'], app.config)
    assert conn.execute('SELECT COUNT(*) FROM document_qr').fetchone()[0] == 1
    assert recover_documents(app.config['DATA_ROOT'], conn) == []
    from pypdf import PdfReader
    doc = client.get('/api/documents/inbox').json['data']['documents'][0]
    pdf = client.get(f"/api/documents/{doc['document_id']}/document.pdf", follow_redirects=True).data
    assert PdfReader(io.BytesIO(pdf)).pages[0].rotation == 180


def test_backup_restore_before_and_after_rotation_preserves_job_and_original(env, monkeypatch, tmp_path):
    app, _, _, conn = env
    root = app.config['DATA_ROOT']
    upload(env, process=False)
    before = tmp_path / 'before'
    ops.backup(root, before)
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {
        'status': 'detected', 'ids': ['FY27-0002'], 'rotations': [180]})
    process_next(root, app.config)
    after = tmp_path / 'after'
    ops.backup(root, after)
    for source, status in [(before, 'queued'), (after, 'complete')]:
        restored = tmp_path / f'restored-{status}'
        ops.restore(restored, source)
        with connect(restored / 'cash.db') as db:
            assert db.execute('SELECT status FROM document_processing').fetchone()[0] == status
            assert recover_documents(restored, db) == []
        if status == 'queued':
            assert process_next(restored, {**app.config, 'DATA_ROOT': restored})
    assert recover_documents(root, conn) == []


def test_database_import_cannot_replace_database_under_a_running_worker(env):
    import fcntl
    from cash.db_transfer import activate_import, DatabaseTransferError
    app, _, _, conn = env
    with (app.config['DATA_ROOT'] / 'staging' / 'document-worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(DatabaseTransferError, match='document is processing'):
            activate_import(app.config['DATA_ROOT'], conn, 'unused-import-id')
    assert conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0] > 0


def test_worker_health_requires_recent_database_progress(env):
    import os
    import subprocess
    import sys
    app, _, _, _ = env
    root = app.config['DATA_ROOT']
    command = [sys.executable, '-m', 'cash.document_worker', 'health']
    environment = {**os.environ, 'CASH_DATA_ROOT': str(root)}
    def health():
        return subprocess.run(command, env=environment, capture_output=True).returncode
    assert health() != 0
    assert not process_next(root, app.config)  # Healthy idle worker, no model to load.
    assert health() == 0
    heartbeat = root / 'staging' / 'document-worker.heartbeat'
    os.utime(heartbeat, (time.time() - 601, time.time() - 601))
    assert health() != 0
