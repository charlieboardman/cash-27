"""Replacing voucher paper preserves accounting, roles, and concurrency guards."""
import io
import json

import pytest
from pypdf import PdfWriter

from test_review import conn, new_signout, opening, post, sid


def pdf_bytes(title):
    pdf = PdfWriter()
    pdf.add_blank_page(width=200, height=300)
    pdf.add_metadata({'/Title': title})
    buffer = io.BytesIO()
    pdf.write(buffer)
    return buffer.getvalue()


def capture(client, csrf, title):
    response = client.post('/api/documents/capture-merge', data={
        'files': (io.BytesIO(pdf_bytes(title)), f'{title}.pdf')},
        headers={'X-CSRF': csrf})
    assert response.status_code == 200, response.get_json()
    return response.get_json()['data']['document_id']


def replace(client, csrf, vid, old, new, submission=None):
    return post(client, csrf, f'/api/vouchers/{vid}/replace-scan', {
        'submission_id': submission or sid(), 'old_document_id': old, 'document_id': new})


@pytest.fixture
def filed(conn, client, csrf):
    opening(conn)
    voucher = new_signout(conn, purpose='Tuition', usd_cents=10000)
    old = capture(client, csrf, 'original')
    response = post(client, csrf, f"/api/review/signout/{voucher['voucher_id']}/attach-voucher", {
        'submission_id': sid(), 'expected_version': 1, 'document_id': old})
    assert response.status_code == 200
    return voucher, old


def test_replace_signout_scan_preserves_cash_and_rejects_stale_replacement(
        conn, client, csrf, filed, app):
    voucher, old = filed
    vid, txn = voucher['voucher_id'], voucher['txn_ids'][0]
    before = [dict(row) for row in conn.execute('SELECT * FROM transactions')]
    new = capture(client, csrf, 'corrected')
    submission = sid()
    response = replace(client, csrf, vid, old, new, submission)
    assert response.status_code == 200, response.get_json()
    assert [dict(row) for row in conn.execute('SELECT * FROM transactions')] == before
    assert conn.execute('SELECT status FROM vouchers WHERE voucher_id=?', (vid,)).fetchone()[0] == 'pending'
    documents = client.get(f'/api/ledger/{txn}').get_json()['data']['documents']
    assert [(d['document_id'], d['role']) for d in documents] == [(new, 'voucher')]
    assert documents[0]['reference'] == vid
    assert client.get(documents[0]['stream_url']).status_code == 200
    report = client.get(f'/{vid}/api/report').get_json()['data']
    assert report['cash_rows']['voucher_documents'][0]['document_id'] == new
    assert conn.execute('SELECT state FROM documents WHERE document_id=?', (old,)).fetchone()[0] == 'discarded'
    assert not any(d['document_id'] in (old, new) for d in client.get('/api/documents/inbox').get_json()['data']['documents'])
    # Command retry is idempotent; a different attempt against the old scan fails.
    assert replace(client, csrf, vid, old, new, submission).get_json() == response.get_json()
    third = capture(client, csrf, 'third')
    stale = replace(client, csrf, vid, old, third)
    assert stale.status_code == 422
    assert stale.get_json()['error']['code'] == 'scan_changed'
    assert conn.execute('SELECT state FROM documents WHERE document_id=?', (third,)).fetchone()[0] == 'inbox'
    # Old Review forms cannot save over the updated document selection.
    stale_purpose = client.patch(f'/api/vouchers/{vid}/purpose', json={
        'submission_id': sid(), 'expected_version': 2, 'purpose': 'Stale'}, headers={'X-CSRF': csrf})
    assert stale_purpose.status_code != 200


@pytest.mark.parametrize('invalid', ['filed', 'processing', 'staged', 'missing', 'wrong_voucher', 'void'])
def test_replace_scan_rejects_invalid_candidates_atomically(conn, client, csrf, filed, app, invalid):
    voucher, old = filed
    vid = voucher['voucher_id']
    new = capture(client, csrf, 'candidate')
    if invalid == 'filed':
        conn.execute("UPDATE documents SET state='filed' WHERE document_id=?", (new,))
    elif invalid == 'processing':
        doc = conn.execute('SELECT * FROM documents WHERE document_id=?', (new,)).fetchone()
        conn.execute("INSERT INTO document_processing(document_id,status,source_rel_path,source_sha256,captured_at) VALUES (?,'queued',?,?,?)",
                     (new, doc['rel_path'], doc['sha256'], doc['created_at']))
    elif invalid == 'staged':
        conn.execute('INSERT INTO direct_expense_review_drafts(voucher_id,draft_json) VALUES (?,?)',
                     (vid, json.dumps({'primary_document_id': new})))
    elif invalid == 'missing':
        path = conn.execute('SELECT rel_path FROM documents WHERE document_id=?', (new,)).fetchone()[0]
        (app.config['DATA_ROOT'] / path).unlink()
    elif invalid == 'wrong_voucher':
        vid = new_signout(conn, purpose='Another', usd_cents=1000, seq_no=None)['voucher_id']
    elif invalid == 'void':
        post(client, csrf, f"/api/ledger/{voucher['txn_ids'][0]}/void", {
            'submission_id': sid(), 'expected_version': 1, 'reason': 'Canceled'})
    before = [tuple(row) for row in conn.execute('SELECT * FROM transaction_documents')]
    response = replace(client, csrf, vid, old, new)
    assert response.status_code in (410, 422), response.get_json()
    assert [tuple(row) for row in conn.execute('SELECT * FROM transaction_documents')] == before


@pytest.mark.parametrize('complete', [False, True])
def test_direct_voucher_replacement_keeps_all_roles_and_draft(conn, client, csrf, complete):
    opening(conn)
    created = post(client, csrf, '/api/vouchers/prepare', {
        'submission_id': sid(), 'type': 'direct_expense', 'date': '2026-08-02',
        'recipient': 'Vendor', 'purpose': 'Supplies', 'usd': '20'}).get_json()['data']
    vid = created['voucher_id']
    old = capture(client, csrf, 'signed')
    if complete:
        conn.execute("INSERT INTO classes(class_code,name) VALUES ('GENERAL','General')")
        response = post(client, csrf, f'/api/review/direct-expense/{vid}/complete', {
            'submission_id': sid(), 'expected_version': 1, 'voucher_document_id': old,
            'use_voucher_as_receipt': True,
            'rows': [{'purpose': 'Supplies', 'usd': '20', 'splits': [
                {'purpose': 'Paper', 'usd': '12', 'expense_account': 'EXP_SUPPLIES', 'class_code': 'GENERAL'},
                {'purpose': 'Pens', 'usd': '8', 'expense_account': 'EXP_SUPPLIES', 'class_code': 'GENERAL'}]}]})
    else:
        response = post(client, csrf, f'/api/review/direct-expense/{vid}/attach-voucher', {
            'submission_id': sid(), 'expected_version': 1, 'document_id': old,
            'use_voucher_as_receipt': True})
        conn.execute('INSERT INTO direct_expense_review_drafts(voucher_id,draft_json) VALUES (?,?)',
                     (vid, json.dumps({'primary_document_id': old, 'rows': [], 'use_voucher_as_receipt': True})))
    assert response.status_code == 200, response.get_json()
    before = [dict(row) for row in conn.execute('SELECT * FROM transactions')]
    links = [dict(row) for row in conn.execute('SELECT * FROM transaction_documents WHERE document_id=?', (old,))]
    assert len(links) == (4 if complete else 2)
    new = capture(client, csrf, 'replacement')
    response = replace(client, csrf, vid, old, new)
    assert response.status_code == 200, response.get_json()
    assert [dict(row) for row in conn.execute('SELECT * FROM transactions')] == before
    after = [dict(row) for row in conn.execute('SELECT * FROM transaction_documents WHERE document_id=?', (new,))]
    assert after == [row | {'document_id': new} for row in links]
    if not complete:
        draft = json.loads(conn.execute('SELECT draft_json FROM direct_expense_review_drafts WHERE voucher_id=?', (vid,)).fetchone()[0])
        assert draft['primary_document_id'] == new
    assert conn.execute('SELECT status FROM vouchers WHERE voucher_id=?', (vid,)).fetchone()[0] == ('complete' if complete else 'pending')


def test_replace_scan_requires_admin_and_csrf(app, client, csrf, filed):
    voucher, old = filed
    new = capture(client, csrf, 'new')
    body = {'submission_id': sid(), 'old_document_id': old, 'document_id': new}
    url = f"/api/vouchers/{voucher['voucher_id']}/replace-scan"
    user = app.test_client()
    token = user.post('/api/user-login', json={'password': 'x'}).get_json()['data']['csrf']
    assert user.post(url, json=body, headers={'X-CSRF': token}).status_code == 401
    assert client.post(url, json=body).status_code == 403


@pytest.mark.parametrize('entry,language,width', [
    ('ledger', 'en', 1440), ('review', 'es', 390), ('report', 'en', 1440)])
def test_replace_scan_menu_dialog_and_reload(web, entry, language, width):
    expect = pytest.importorskip('playwright.sync_api').expect
    page, post_api, artifacts = web
    page.set_default_timeout(5000)
    page.set_viewport_size({'width': width, 'height': 950})
    page.add_init_script(f"localStorage.setItem('cash-language', '{language}')")
    kind = 'direct_expense' if entry == 'review' else 'signout'
    voucher = post_api('/api/vouchers/prepare', type=kind, date='2026-09-03',
                       recipient='Recipient', purpose='Tuition', usd='20')
    vid, txn = voucher['voucher_id'], voucher['txn_ids'][0]

    def upload(title):
        response = page.request.post('/api/documents/capture-merge', multipart={
            'files': {'name': title + '.pdf', 'mimeType': 'application/pdf', 'buffer': pdf_bytes(title)}})
        assert response.ok, response.text()
        return response.json()['data']['document_id']

    old = upload('original')
    post_api(f"/api/review/{'direct-expense' if kind == 'direct_expense' else 'signout'}/{vid}/attach-voucher",
             expected_version=1, document_id=old)
    new = None if entry == 'review' else upload('corrected')
    url = f'/ledger?txn={txn}' if entry == 'ledger' else f'/review?item=direct_expense:{vid}' if entry == 'review' else f'/{vid}'
    page.goto(url)
    if entry == 'report':
        page.locator('[data-cash-row="voucher"]').click()
    trigger = page.locator(f'[data-replace-scan="{old}"]')
    expect(trigger).to_be_hidden()
    trigger.locator('..').locator('summary').click()
    expect(trigger).to_be_visible()
    trigger.click()
    dialog = page.locator('.replace-scan-dialog')
    expect(dialog).to_be_visible()
    expect(dialog.locator('#replace-scan-title')).to_be_focused()
    expect(dialog.locator('[type="submit"]')).to_be_disabled()
    expect(dialog.locator('[data-current-scan] .page-viewer-image')).to_be_visible()
    expect(dialog.locator('#replace-scan-title')).to_be_focused()
    dialog.get_by_role('button', name='Cancelar' if language == 'es' else 'Cancel', exact=True).click()
    expect(dialog).to_have_count(0)
    detail = page.request.get(f'/api/ledger/{txn}').json()['data']
    assert detail['documents'][0]['document_id'] == old
    trigger.locator('..').locator('summary').click()
    trigger.click()
    if entry == 'review':
        dialog.get_by_label('Subir escaneo', exact=True).set_input_files({
            'name': 'corrected.pdf', 'mimeType': 'application/pdf', 'buffer': pdf_bytes('corrected')})
        expect(dialog.locator('[type="submit"]')).to_be_enabled()
        new = dialog.locator('[name="document_id"]').input_value()
    else:
        expect(dialog.locator(f'option[value="{new}"]')).to_be_attached()
        dialog.get_by_label('From inbox', exact=True).select_option(new)
    expect(dialog.locator('[data-new-scan] .page-viewer-image')).to_be_visible()
    assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
    page.screenshot(path=str(artifacts / f'replace-scan-{entry}-{language}.png'))
    with page.expect_response(lambda r: r.url.endswith('/replace-scan') and r.request.method == 'POST') as response:
        dialog.get_by_role('button', name='Reemplazar escaneo' if language == 'es' else 'Replace scan', exact=True).click()
    assert response.value.ok, response.value.text()
    expect(dialog).to_have_count(0)
    expect(page.locator(f'[data-replace-scan="{new}"]')).to_be_attached()
    expect(page.locator(f'[data-replace-scan="{old}"]')).to_have_count(0)
    page.reload()
    if entry == 'report':
        page.locator('[data-cash-row="voucher"]').click()
    expect(page.locator(f'[data-replace-scan="{new}"]')).to_be_attached()
    detail = page.request.get(f'/api/ledger/{txn}').json()['data']
    assert detail['transaction']['usd'] == '20.00'
    assert detail['documents'][0]['document_id'] == new
    if language == 'es':
        assert not page.evaluate('Array.from(cashI18n.missing)')
