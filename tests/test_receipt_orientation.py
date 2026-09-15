"""Receipt guesses must abstain on weak evidence and preserve QR decisions."""
import hashlib
import fcntl
import io
import json
import subprocess
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from cash.receipt_orientation_worker import agreed_correction
from cash.web import qr_capture
from tests.test_qr_orientation import env, image_pdf, image_stream


@pytest.mark.parametrize('correction', [0, 90, 180, 270])
def test_four_views_map_to_same_original_page(correction):
    predictions = [((offset - correction) % 360, .92) for offset in (0, 90, 180, 270)]
    assert agreed_correction(predictions) == correction


@pytest.mark.parametrize('predictions', [
    [], [(0, .99)] * 4,
    [(0, .99), (90, .99), (180, .89), (270, .99)],
    [(0, .99), (90, .99), (180, float('nan')), (270, .99)],
    [(0, .99), (90, .99), (180, 1.01), (270, .99)],
])
def test_inconsistent_or_weak_evidence_abstains(predictions):
    assert agreed_correction(predictions) is None


def enable(app):
    app.config['RECEIPT_ORIENTATION_ENABLED'] = True
    return app.config


def test_only_non_qr_pages_are_sent_and_modified(env, monkeypatch):
    config = enable(env[0])
    recognition = {'status': 'ambiguous', 'ids': ['FY27-0001', 'FY27-0002'],
                   'rotations': [90, None, None], 'qr_pages': [True, True, False]}
    def run(command, **kwargs):
        assert json.loads(command[-1]) == [2]
        return SimpleNamespace(returncode=0, stdout='{"rotations": [180, 180, 270]}')
    monkeypatch.setattr(subprocess, 'run', run)
    result = qr_capture.receipt_rotations('unused.pdf', recognition, config)
    assert result == {**recognition, 'rotations': [90, None, 270]}
    assert recognition['rotations'] == [90, None, None]


@pytest.mark.parametrize('failure', ['timeout', 'exit', 'json', 'count', 'angle'])
def test_worker_failure_preserves_existing_decisions(env, monkeypatch, failure):
    config = enable(env[0])
    recognition = {'status': 'detected', 'ids': ['FY27-0002'],
                   'rotations': [180, None], 'qr_pages': [True, False]}
    def run(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args[0], 15)
        return SimpleNamespace(returncode=1 if failure == 'exit' else 0,
                               stdout={'json': 'bad', 'count': '{"rotations": [0]}',
                                       'angle': '{"rotations": [0, true]}'}.get(failure, '{}'))
    monkeypatch.setattr(subprocess, 'run', run)
    assert qr_capture.receipt_rotations('unused.pdf', recognition, config) == recognition


@pytest.mark.parametrize('recognition', [
    {'status': 'failed', 'ids': []},
    {'status': 'ambiguous', 'ids': ['FY27-0001', 'FY27-0002'],
     'rotations': [None], 'qr_pages': [True]},
])
def test_no_guess_when_qr_presence_is_unknown_or_conflicting(env, monkeypatch, recognition):
    config = enable(env[0])
    def unexpected(*args, **kwargs):
        pytest.fail('Receipt model should not run')
    monkeypatch.setattr(subprocess, 'run', unexpected)
    assert qr_capture.receipt_rotations('unused.pdf', recognition, config) == recognition


@pytest.mark.parametrize('disabled', [False, True])
def test_busy_or_disabled_receipt_worker_keeps_page_unchanged(env, monkeypatch, disabled):
    config = enable(env[0])
    config['RECEIPT_ORIENTATION_ENABLED'] = not disabled
    recognition = {'status': 'no_code', 'ids': [], 'rotations': [None], 'qr_pages': [False]}
    def unexpected(*args, **kwargs):
        pytest.fail('Receipt subprocess should not run')
    monkeypatch.setattr(subprocess, 'run', unexpected)
    with (config['DATA_ROOT'] / 'staging' / 'qr.lock').open('a') as lock:
        if not disabled:
            fcntl.flock(lock, fcntl.LOCK_EX)
        assert qr_capture.receipt_rotations('unused.pdf', recognition, config) == recognition


def test_receipt_capture_rotates_without_matching_and_publishes_final_hash(env, monkeypatch):
    app, client, headers, conn = env
    enable(app)
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {
        'status': 'no_code', 'ids': [], 'rotations': [None], 'qr_pages': [False]})
    monkeypatch.setattr(subprocess, 'run', lambda *_, **__: SimpleNamespace(
        returncode=0, stdout='{"rotations": [270]}'))
    original = image_pdf(90)
    response = client.post('/api/documents/capture-merge',
                           data={'files': (io.BytesIO(original), 'receipt.pdf')}, headers=headers)
    assert response.status_code == 200
    doc = response.json['data']
    assert doc['qr']['status'] == 'no_code'
    assert doc['display_name'].startswith('scan-')
    saved = client.get(f"/api/documents/{doc['document_id']}/stream").data
    after = PdfReader(io.BytesIO(saved)).pages[0]
    assert after.rotation % 360 == 0
    assert image_stream(after) == image_stream(PdfReader(io.BytesIO(original)).pages[0])
    row = conn.execute('SELECT * FROM documents').fetchone()
    assert row['sha256'] == hashlib.sha256(saved).hexdigest()
    assert row['size_bytes'] == len(saved)
    assert row['state'] == 'inbox'
    assert conn.execute('SELECT count(*) FROM transaction_documents').fetchone()[0] == 0
