"""Presentation follows filings while URLs retain document identity."""
import hashlib

from cash import document_names
from test_bookkeeper import desk, add_pdf, expense


def test_canonical_roles_and_unfiled_names(desk):
    app, client, conn, paths = desk
    names = document_names.canonical_filenames(conn)
    ids = [p.stem for p in paths]
    assert [names[did] for did in ids] == [
        'FY27-0001.pdf', 'FY27-0001-R1.pdf', 'FY27-0001-A1.pdf']
    # A receipt does not need a ledger association to retain its reference.
    conn.execute('DELETE FROM transaction_documents WHERE document_id=?', (ids[1],))
    assert document_names.canonical_filenames(conn)[ids[1]] == 'FY27-0001-R1.pdf'
    did = add_pdf(conn, app.config['DATA_ROOT'], 'unfiled')
    response = client.get(f'/api/documents/{did}/{did}.pdf')
    assert response.status_code == 200
    assert f'filename={did}.pdf' in response.headers['Content-Disposition']
    assert response.headers['Cache-Control'] == 'private, no-cache, must-revalidate'
    assert client.get(f'/api/documents/{did}/stream').status_code == 404


def test_report_tray_document_labels_use_canonical_names(desk):
    _, client, _, _ = desk
    response = client.get('/FY27-0001/api/report')
    assert response.status_code == 200
    report = response.get_json()['data']
    assert report['cash_rows']['voucher_documents'][0]['display_name'] == 'FY27-0001.pdf'
    assert report['cash_rows']['advances'][0]['document']['display_name'] == 'FY27-0001-A1.pdf'


def test_renumbered_url_keeps_same_document_even_when_reference_is_reused(desk):
    app, client, conn, paths = desk
    did = paths[1].stem
    old_url = f'/api/documents/{did}/FY27-0001-R1.pdf'
    before = client.get(old_url)
    assert before.status_code == 200
    other = add_pdf(conn, app.config['DATA_ROOT'], 'another receipt')
    conn.execute("UPDATE receipts SET document_id=? WHERE receipt_id='FY27-0001-R1'", (other,))
    conn.execute("INSERT INTO receipts(receipt_id,voucher_id,seq,document_id)"
                 " VALUES ('FY27-0001-R2','FY27-0001',2,?)", (did,))
    redirect = client.get(old_url)
    assert redirect.status_code == 302
    assert redirect.location == f'/api/documents/{did}/FY27-0001-R2.pdf'
    assert 'no-store' in redirect.headers['Cache-Control']
    after = client.get(old_url, follow_redirects=True)
    assert after.data == before.data
    assert 'filename=FY27-0001-R2.pdf' in after.headers['Content-Disposition']
    # An arbitrary alias cannot select another document either.
    assert client.get(f'/api/documents/{did}/anything.pdf').location == redirect.location
    anon = app.test_client().get(old_url)
    assert anon.status_code == 401 and 'Location' not in anon.headers
    assert client.get('/api/documents/unknown/name.pdf').status_code == 404


def test_names_use_active_filings_and_deterministic_role_precedence(desk):
    app, client, conn, paths = desk
    did = paths[0].stem
    # Shorter receipt references still lose to a voucher role.
    conn.execute("INSERT INTO receipts(receipt_id,voucher_id,seq,document_id)"
                 " VALUES ('R1','FY27-0001',2,?)", (did,))
    assert document_names.canonical_filenames(conn)[did] == 'FY27-0001.pdf'
    conn.execute("UPDATE transactions SET posting_state='void' WHERE txn_type='signout'")
    assert document_names.canonical_filenames(conn)[did] == 'R1.pdf'
    conn.execute("UPDATE receipts SET status='void' WHERE document_id=?", (did,))
    names = document_names.canonical_filenames(conn)
    assert document_names.filename(did, names) == did + '.pdf'


def test_statement_log_count_names_and_shortest_reference(desk):
    app, client, conn, paths = desk
    did = paths[1].stem
    conn.execute("UPDATE documents SET doc_type='statement' WHERE document_id=?", (did,))
    assert document_names.canonical_filenames(conn)[did] == 'FY27-0001-R1x.pdf'
    conn.execute("UPDATE documents SET doc_type='purchase_log' WHERE document_id=?", (did,))
    assert document_names.canonical_filenames(conn)[did] == 'FY27-0001-log.pdf'
    count = add_pdf(conn, app.config['DATA_ROOT'], 'count sheet')
    conn.execute("UPDATE documents SET doc_type='physical_count' WHERE document_id=?", (count,))
    tid = expense(conn)
    conn.execute("INSERT INTO transaction_documents(txn_id,document_id,role) VALUES (?,?,'support')", (tid, count))
    assert document_names.canonical_filenames(conn)[count] == f'Txn-{tid}.pdf'
    extra = expense(conn, voucher_id='FY27-0001')
    conn.execute("INSERT INTO transaction_documents(txn_id,document_id,role) VALUES (?,?,'support')", (extra, count))
    assert document_names.canonical_filenames(conn)[count] == f'Txn-{tid}.pdf'
    conn.execute("UPDATE transactions SET posting_state='void' WHERE txn_id=?", (tid,))
    assert document_names.canonical_filenames(conn)[count] == f'Txn-{extra}.pdf'


def test_bookkeeper_link_serves_canonical_filename_without_changing_storage(desk):
    app, client, conn, paths = desk
    did = paths[0].stem
    before = paths[0].read_bytes()
    url = f'/api/documents/{did}/FY27-0001.pdf'
    assert url in client.get('/reports/bookkeeper?fy=2027').text
    response = client.get(url)
    assert response.status_code == 200 and response.mimetype == 'application/pdf'
    assert response.headers['Content-Disposition'] == 'inline; filename=FY27-0001.pdf'
    assert response.data == before == paths[0].read_bytes()
    assert hashlib.sha256(response.data).hexdigest() == conn.execute(
        'SELECT sha256 FROM documents WHERE document_id=?', (did,)).fetchone()[0]
