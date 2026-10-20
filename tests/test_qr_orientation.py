"""Page orientation is independent of QR payload decoding and must be lossless."""
import hashlib
import io
from types import SimpleNamespace

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from cash.qr_worker import correction_for_orientations
from tests.test_qr_capture import finish
from cash.web import qr_capture
from cash.web.qr_capture import recognize as real_recognize
from cash.db import connect
from experiments.qr.fixtures import fixture


@pytest.fixture
def env(tmp_path):
    app, client, headers = fixture(tmp_path)
    app.config['QR_ENABLED'] = True
    with connect(tmp_path / 'cash.db') as conn:
        yield app, client, headers, conn


@pytest.mark.parametrize('direction,angle', [(0, 0), (-90, 90), (180, 180), (-180, 180), (90, 270), (-2, 0), (92, 270)])
def test_clockwise_correction(direction, angle):
    assert correction_for_orientations([direction, direction]) == angle


@pytest.mark.parametrize('directions', [[], [None], [0, 180], [-90, None], ['invalid'], [True], [45], [float('nan')], [float('inf')]])
def test_uncertain_or_conflicting_orientation_is_not_guessed(directions):
    assert correction_for_orientations(directions) is None


def image_pdf(rotation=0):
    image = Image.new('RGB', (60, 90), 'white')
    image.putpixel((3, 5), (255, 0, 0))
    output = io.BytesIO()
    image.save(output, 'PDF')
    reader = PdfReader(io.BytesIO(output.getvalue()))
    writer = PdfWriter()
    page = reader.pages[0]
    page.rotate(rotation)
    writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def image_stream(page):
    objects = page['/Resources']['/XObject']
    return [obj.get_object().get_data() for obj in objects.values()]


@pytest.mark.parametrize('existing,correction', [(0, 0), (90, 270), (180, 180), (270, 90)])
def test_rotates_relative_to_existing_pdf_rotation_without_reencoding(existing, correction):
    original = image_pdf(existing)
    result = qr_capture.orient_pdf(original, {'rotations': [correction]})
    before = PdfReader(io.BytesIO(original)).pages[0]
    after = PdfReader(io.BytesIO(result)).pages[0]
    assert after.rotation % 360 == 0
    assert image_stream(after) == image_stream(before)
    assert list(before.mediabox) == list(after.mediabox)
    if correction == 0:
        assert result == original


def test_mixed_document_does_not_rotate_pages_without_evidence():
    writer = PdfWriter()
    for rotation in [180, 90, 0]:
        writer.add_page(PdfReader(io.BytesIO(image_pdf(rotation))).pages[0])
    output = io.BytesIO()
    writer.write(output)
    result = qr_capture.orient_pdf(output.getvalue(), {'rotations': [180, None, 0]})
    assert [p.rotation % 360 for p in PdfReader(io.BytesIO(result)).pages] == [0, 90, 0]


def test_capture_publishes_corrected_hash_and_size(env, monkeypatch):
    app, client, headers, conn = env
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {
        'status': 'detected', 'ids': ['FY27-0002'], 'rotations': [180]})
    original = image_pdf(180)
    response = client.post('/api/documents/capture-merge',
                           data={'files': (io.BytesIO(original), 'backwards.pdf')}, headers=headers)
    assert response.status_code == 200
    doc = finish(env, response.json['data'])
    saved = client.get(f"/api/documents/{doc['document_id']}/document.pdf", follow_redirects=True)
    assert saved.status_code == 200
    assert PdfReader(io.BytesIO(saved.data)).pages[0].rotation % 360 == 0
    row = conn.execute('SELECT * FROM documents').fetchone()
    assert row['sha256'] == hashlib.sha256(saved.data).hexdigest()
    assert row['size_bytes'] == len(saved.data)
    assert row['state'] == 'inbox'
    assert image_stream(PdfReader(io.BytesIO(saved.data)).pages[0]) == image_stream(PdfReader(io.BytesIO(original)).pages[0])


def test_wrong_page_count_preserves_original_upload(env, monkeypatch):
    _, client, headers, _ = env
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {
        'status': 'detected', 'ids': ['FY27-0002'], 'rotations': [180, 0]})
    response = client.post('/api/documents/capture-merge',
                           data={'files': (io.BytesIO(image_pdf(180)), 'backwards.pdf')}, headers=headers)
    doc = finish(env, response.json['data'])
    assert doc['qr']['status'] == 'failed'
    saved = client.get(f"/api/documents/{doc['document_id']}/document.pdf", follow_redirects=True)
    assert saved.status_code == 200
    assert PdfReader(io.BytesIO(saved.data)).pages[0].rotation == 180


@pytest.mark.parametrize('rotations', [[False], [45], ['180'], {}, [0]*41])
def test_rejects_invalid_worker_orientation(env, monkeypatch, rotations):
    import json
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *_, **__: SimpleNamespace(
        returncode=0, stdout=json.dumps({'ids': ['FY27-0002'], 'rotations': rotations})))
    assert real_recognize('ignored.pdf', env[0].config)['status'] == 'failed'
