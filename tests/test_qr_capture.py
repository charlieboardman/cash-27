"""Recognition cannot approve paperwork or alter accounting."""
import fcntl
import io
import subprocess
import sys

import pytest
from pypdf import PdfWriter

from cash.db import connect
from cash.qr_worker import valid_ids
from cash.web import qr_capture
from experiments.qr.fixtures import fixture


@pytest.fixture
def env(tmp_path, monkeypatch):
    app, client, headers = fixture(tmp_path)
    app.config['QR_ENABLED'] = True
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {'status': 'detected', 'ids': ['FY27-0002']})
    with connect(tmp_path / 'cash.db') as conn:
        yield app, client, headers, conn


def finish(env, result):
    from cash.document_worker import process_next
    app, client, _, _ = env
    while process_next(app.config['DATA_ROOT'], app.config):
        pass
    documents = {doc['document_id']: doc for doc in client.get('/api/documents/inbox').json['data']['documents']}
    if 'documents' in result:
        return {**result, 'documents': [{**doc, **documents[doc['document_id']]} for doc in result['documents']]}
    return {**result, **documents[result['document_id']]}


def upload(env, *, endpoint='capture-merge', count=1, process=True):
    _, client, headers, _ = env
    def pdf():
        writer = PdfWriter()
        writer.add_blank_page(612, 792)
        stream = io.BytesIO()
        writer.write(stream)
        stream.seek(0)
        return stream
    response = client.post('/api/documents/' + endpoint,
                           data={'files': [(pdf(), 'image.jpg') for _ in range(count)]},
                           headers=headers)
    assert response.status_code == 200, response.json
    result = response.json['data']
    return finish(env, result) if process else result


def test_strict_ids_exclude_paths_urls_partial_matches_and_receipts():
    assert valid_ids(['FY27-0002', 'FY27-0002', 'FY27-0002-A1', 'FY27-10000',
                      'FY27-0002-R1', '../FY27-0002', 'https://x/FY27-0002',
                      'FY27-0002\n', 'fy27-0002', 'FY27-0002-A0', None,
                      'FY27-0002-R3x', 'FY27-0002-R0x', 'FY27-0002-R3X',
                      'FY27-0002-log', 'FY27-0002-A1-log', 'Txn-12', 'Txn-0',
                      'txn-12', 'FY27-0002-Axe']) == [
                          'FY27-0002', 'FY27-0002-A1', 'FY27-0002-R3x',
                          'FY27-0002-log', 'FY27-10000', 'Txn-12']


def test_codes_parse_into_their_parts():
    from cash.document_codes import parse
    assert parse("FY27-0042-A2").addendum_id == "FY27-0042-A2"
    assert parse("FY27-0042-A2").voucher_id == "FY27-0042"
    statement = parse("FY27-0042-R3x")
    assert (statement.kind, statement.voucher_id, statement.receipt_seq) == (
        "statement", "FY27-0042", 3)
    assert parse("FY27-0042-log").kind == "log"
    assert (parse("Txn-123").kind, parse("Txn-123").txn_id) == ("count", 123)
    # A voucher number that merely contains "-A" is still a voucher.
    assert parse("FY27-0042").kind == "voucher"
    assert parse("FY27-0042-R3") is None


def test_match_names_and_links_but_does_not_file(env):
    app, client, _, conn = env
    before = [tuple(row) for row in conn.execute('SELECT * FROM transactions')]
    result = upload(env)
    assert result['display_name'] == 'FY27-0002.pdf'
    assert result['qr']['status'] == 'matched'
    assert result['qr']['review_url'] == '/review?item=signout:FY27-0002'
    stored = conn.execute('SELECT * FROM documents').fetchone()
    assert stored['state'] == 'inbox'
    assert stored['display_name'] == 'FY27-0002.pdf'  # Immutable original keeps its storage path.
    assert conn.execute('SELECT count(*) FROM transaction_documents').fetchone()[0] == 0
    assert before == [tuple(row) for row in conn.execute('SELECT * FROM transactions')]
    assert conn.execute("SELECT status FROM vouchers WHERE voucher_id='FY27-0002'").fetchone()[0] == 'pending'
    docs = client.get('/FY27-0002/api/report').json['data']['paperwork']['documents']
    assert docs[0]['review_url'] == result['qr']['review_url']
    assert client.get('/api/documents/inbox').json['data']['documents'][0]['qr'] == result['qr']
    stream = client.get(f"/api/documents/{result['document_id']}/document.pdf", follow_redirects=True)
    assert result['document_id'] + '.pdf' in stream.headers['Content-Disposition']
    assert stream.status_code == 200
    assert not list((app.config['DATA_ROOT'] / 'staging').glob('qr-*'))


def test_addendum_matches_exact_paper_without_completing(env, monkeypatch):
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {'status': 'detected', 'ids': ['FY27-0002-A1']})
    result = upload(env)
    assert result['display_name'] == 'FY27-0002-A1.pdf'
    assert result['qr']['addendum_id'] == 'FY27-0002-A1'
    assert result['qr']['review_url'] == '/review?item=addendum:FY27-0002-A1'
    conn = env[3]
    assert conn.execute('SELECT status FROM addenda').fetchone()[0] == 'pending'
    assert conn.execute('SELECT document_id FROM addenda').fetchone()[0] is None


@pytest.mark.parametrize('status,ids', [('no_code', []), ('failed', []), ('timeout', []),
                                      ('busy', []), ('ambiguous', ['FY27-0002', 'FY27-0002-A1'])])
def test_timestamp_fallback_preserves_upload(env, monkeypatch, status, ids):
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {'status': status, 'ids': ids})
    result = upload(env)
    assert result['display_name'].startswith('scan-')
    assert result['display_name'].endswith('Z.pdf')
    assert result['qr']['status'] == status
    assert result['qr']['review_url'] is None
    assert env[1].get(f"/api/documents/{result['document_id']}/document.pdf", follow_redirects=True).status_code == 200


def test_unknown_qr_named_but_not_matched(env, monkeypatch):
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {'status': 'detected', 'ids': ['FY28-0099']})
    result = upload(env)
    assert result['display_name'] == 'FY28-0099.pdf'
    assert result['qr']['status'] == 'unknown'
    assert result['qr']['voucher_id'] is None


@pytest.mark.parametrize('code', ['Txn-1'])
def test_codes_without_a_matcher_are_named_but_not_matched(env, monkeypatch, code):
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {'status': 'detected', 'ids': [code]})
    result = upload(env)
    assert result['display_name'] == f'{code}.pdf'
    assert (result['qr']['status'], result['qr']['voucher_id']) == ('unknown', None)


def test_a_stack_of_different_papers_splits_into_documents(env, monkeypatch):
    from pypdf import PdfReader
    pages = [['FY27-0002'], [], [], ['FY27-0002-log'], ['FY27-0002-log']]
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {
        'status': 'ambiguous', 'ids': ['FY27-0002', 'FY27-0002-log'],
        'rotations': [None] * 5, 'qr_pages': [bool(ids) for ids in pages], 'page_ids': pages})
    app, client, _, conn = env
    first = upload(env, count=5)
    documents = client.get('/api/documents/inbox').json['data']['documents']
    # The voucher, a two-page vendor receipt without a code, and a two-sheet log.
    assert [(doc['page_count'], doc['qr']['status']) for doc in documents] == [
        (1, 'matched'), (2, 'no_code'), (2, 'matched')]
    assert documents[0]['display_name'] == 'FY27-0002.pdf'
    assert documents[2]['display_name'] == 'FY27-0002-log.pdf'
    assert documents[0]['document_id'] == first['document_id']
    assert all(doc['processing']['ready'] for doc in documents)
    for doc in documents:
        body = client.get(f"/api/documents/{doc['document_id']}/document.pdf", follow_redirects=True).data
        assert len(PdfReader(io.BytesIO(body)).pages) == doc['page_count']
    assert documents[2]['qr']['review_url'] == '/FY27-0002'


def test_batch_duplicate_and_discarded_match(env):
    result = upload(env, endpoint='capture', count=2)['documents']
    assert [doc['qr']['status'] for doc in result] == ['matched', 'duplicate']
    app, client, headers, conn = env
    assert len(list((app.config['DATA_ROOT'] / 'documents').glob('*.pdf'))) == 2
    client.post(f"/api/documents/{result[0]['document_id']}/discard", headers=headers)
    assert client.get('/FY27-0002/api/report').json['data']['paperwork']['documents'] == []
    assert upload(env)['qr']['status'] == 'matched'


def test_filed_target_is_not_replaced(env):
    from cash.prep import attach_signout_voucher
    result = upload(env)
    conn = env[3]
    attach_signout_voucher(conn, voucher_id='FY27-0002', expected_version=1,
                           document_id=result['document_id'])
    assert upload(env)['qr']['status'] == 'already_filed'
    assert conn.execute('SELECT count(*) FROM transaction_documents').fetchone()[0] == 1


def test_void_target_is_not_matched(env):
    env[3].execute("UPDATE vouchers SET status='void' WHERE voucher_id='FY27-0002'")
    assert upload(env)['qr']['status'] == 'inactive'


def test_unexpected_decoder_error_retains_upload(env, monkeypatch):
    def fail(*_):
        raise OSError('worker unavailable')
    monkeypatch.setattr(qr_capture, 'recognize', fail)
    assert upload(env)['qr']['status'] == 'failed'


def test_serialization_and_timeout_leave_no_locked_worker(tmp_path, monkeypatch):
    (tmp_path / 'staging').mkdir()
    config = {'DATA_ROOT': tmp_path, 'QR_PYTHON': sys.executable,
              'QR_TIMEOUT': 0.01}
    with (tmp_path / 'staging' / 'qr.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert qr_capture.recognize('missing.pdf', config)['status'] == 'busy'
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(subprocess, 'run', timeout)
    assert qr_capture.recognize('missing.pdf', config)['status'] == 'timeout'
    # Lock released even when the decoder times out.
    assert qr_capture.recognize('missing.pdf', config)['status'] == 'timeout'


def test_disabled_experiment_preserves_original_name(env):
    env[0].config['QR_ENABLED'] = False
    result = upload(env)
    assert result['display_name'] == 'image.jpg'
    assert result['qr'] is None


def test_batch_queues_all_uploads_without_running_models(env, monkeypatch):
    def unexpected(*_):
        pytest.fail('Decoder must not run in the request process')
    monkeypatch.setattr(qr_capture, 'recognize', unexpected)
    results = upload(env, endpoint='capture', count=2, process=False)['documents']
    assert [doc['processing']['status'] for doc in results] == ['queued', 'queued']
    assert env[3].execute('SELECT count(*) FROM document_processing').fetchone()[0] == 2


def test_direct_expense_is_selected_without_approving_allocations(env, monkeypatch):
    import uuid
    _, client, headers, conn = env
    created = client.post('/api/vouchers/prepare', json={
        'submission_id': str(uuid.uuid4()), 'type': 'direct_expense',
        'date': '2026-09-14', 'recipient': 'Example vendor', 'purpose': 'Supplies',
        'usd': '10', 'mxn': '0'}, headers=headers).json['data']
    code = created['voucher_id']
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {'status': 'detected', 'ids': [code]})
    result = upload(env)
    assert result['qr']['review_url'] == f'/review?item=direct_expense:{code}'
    assert conn.execute('SELECT status FROM vouchers WHERE voucher_id=?', (code,)).fetchone()[0] == 'pending'
    assert conn.execute('SELECT count(*) FROM transaction_documents').fetchone()[0] == 0
