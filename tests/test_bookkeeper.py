"""Read-only, linked report snapshots work with legacy documents and offline."""
import csv
import hashlib
import io
import json
import uuid
from zipfile import ZipFile

from openpyxl import load_workbook
from pypdf import PdfWriter
import pytest

from cash.db import connect
from cash.web.app import create_app


@pytest.fixture
def desk(tmp_path):
    app = create_app(tmp_path, admin_password='test', testing=True)
    client = app.test_client()
    client.post('/api/login', json={'password': 'test'})
    conn = connect(tmp_path / 'cash.db')

    def txn(**extra):
        values = dict(movement_id=str(uuid.uuid4()), submission_id=str(uuid.uuid4()),
                      date='2026-09-02', txn_type='expense', from_account='SAFE',
                      to_account='EXP_SUPPLIES', usd_cents=0, mxn_centavos=0)
        values.update(extra)
        return conn.execute(f"INSERT INTO transactions ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", list(values.values())).lastrowid

    def doc(label):
        did = str(uuid.uuid4())  # Deliberately predates DOC naming and QR decoding.
        path = tmp_path / 'documents' / f'{did}.pdf'
        pdf = PdfWriter()
        pdf.add_blank_page(width=100, height=150)
        pdf.add_metadata({'/Title': label})
        pdf.write(path)
        conn.execute("INSERT INTO documents(document_id, rel_path, sha256, size_bytes, display_name, state) VALUES (?,?,?,?,?,'filed')",
                     (did, f'documents/{did}.pdf', hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, 'image.jpg'))
        return did, path

    txn(date='2026-08-01', txn_type='opening', from_account='OPENING_EQUITY', to_account='SAFE', usd_cents=100000, mxn_centavos=155000, assigned_rate_milli=15500)
    vid, rid, aid = 'FY27-0001', 'FY27-0001-R1', 'FY27-0001-A1'
    conn.execute("INSERT INTO vouchers(voucher_id,type,fiscal_year,seq,recipient,purpose) VALUES (?,'signout',2027,1,'Ana','Materials')", (vid,))
    conn.execute("INSERT INTO accounts(account_id,name,kind) VALUES (?,'Ana','signout')", (vid,))
    conn.execute("INSERT INTO classes(class_code,name) VALUES ('4.3','Homebuilding')")
    signout = txn(txn_type='signout', to_account=vid, voucher_id=vid, usd_cents=10000)
    voucher_doc, voucher_path = doc('Signed voucher')
    receipt_doc, receipt_path = doc('Receipt')
    addendum_doc, addendum_path = doc('Return addendum')
    conn.execute("INSERT INTO receipts(receipt_id,voucher_id,seq,document_id,vendor) VALUES (?,?,1,?,'=Vendor <script>')", (rid, vid, receipt_doc))
    conn.execute("INSERT INTO addenda(addendum_id,voucher_id,seq,kind,document_id) VALUES (?,?,1,'return',?)", (aid, vid, addendum_doc))
    txn(date='2026-09-04', txn_type='addendum', from_account=vid, to_account='SAFE', voucher_id=vid, addendum_id=aid, usd_cents=1000)
    movement = str(uuid.uuid4())
    ids = [txn(date='2026-09-03', movement_id=movement, from_account=vid, voucher_id=vid, receipt_id=rid, usd_cents=1000, allocation_line=i, class_code='4.3', description=f'Allocation {i}', notes='Receipt notes') for i in (1, 2)]
    for tid in ids:
        conn.execute("INSERT INTO transaction_documents(txn_id,document_id,role) VALUES (?,?,'support')", (tid, receipt_doc))
    for role in ('voucher', 'support'):
        conn.execute('INSERT INTO transaction_documents(txn_id,document_id,role) VALUES (?,?,?)', (signout, voucher_doc, role))
    txn(date='2026-09-05', mxn_centavos=10000, description='Unattached purchase')
    txn(date='2026-09-05', usd_cents=9900, description='SUPERSEDED', posting_state='superseded')
    txn(date='2026-09-05', usd_cents=8800, description='VOID', posting_state='void')
    yield app, client, conn, (voucher_path, receipt_path, addendum_path)
    conn.close()


def add_pdf(conn, root, label):
    did = str(uuid.uuid4())
    path = root / 'documents' / f'{did}.pdf'
    pdf = PdfWriter()
    pdf.add_blank_page(width=100, height=150)
    pdf.add_metadata({'/Title': label})
    pdf.write(path)
    conn.execute("INSERT INTO documents(document_id, rel_path, sha256, size_bytes, state) VALUES (?,?,?,?,'filed')",
                 (did, f'documents/{did}.pdf', hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size))
    return did


def expense(conn, **extra):
    values = dict(movement_id=str(uuid.uuid4()), submission_id=str(uuid.uuid4()),
                  date='2026-09-06', txn_type='expense', from_account='SAFE',
                  to_account='EXP_SUPPLIES', usd_cents=500, mxn_centavos=0)
    values.update(extra)
    return conn.execute(f"INSERT INTO transactions ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", list(values.values())).lastrowid


def archive(client, query='fy=2027'):
    response = client.get('/api/export/bookkeeper.zip?' + query)
    assert response.status_code == 200, response.text
    data = response.data
    response.close()
    return ZipFile(io.BytesIO(data))


def test_legacy_documents_grouped_deduplicated_linked_and_database_unchanged(desk):
    app, client, conn, paths = desk
    before = list(conn.iterdump())
    response = client.get('/reports/bookkeeper?fy=2027')
    assert response.status_code == 200
    assert 'FY27-0001-R1' in response.text
    assert 'FY27-0001-A1' in response.text
    assert '&lt;script&gt;' in response.text
    assert '=Vendor <script>' not in response.text
    assert 'SUPERSEDED' not in response.text and '>VOID<' not in response.text
    assert '2 allocation lines' in response.text
    with archive(client) as bundle:
        pdfs = [n for n in bundle.namelist() if n.endswith('.pdf')]
        assert sorted(pdfs) == ['documents/FY27-0001-A1.pdf', 'documents/FY27-0001-R1.pdf',
                                'documents/FY27-0001.pdf']
        assert {hashlib.sha256(bundle.read(n)).hexdigest() for n in pdfs} == {hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        html = bundle.read('index.html').decode()
        assert '/api/documents/' not in html
        assert '/static/' not in html
        for pdf in pdfs:
            assert f'href="{pdf}"' in html
        assert 'Same purchase' in html
        manifest = json.loads(bundle.read('manifest.json'))
        assert len(manifest['rows']) == 3
        assert len(manifest['rows'][0]['documents']) == 3
        rows = list(csv.DictReader(io.StringIO(bundle.read('report.csv').decode())))
        assert [r['Calc USD'] for r in rows] == ['10.00', '10.00', '6.45']
        assert rows[-1]['ER'] == '15.500'
        assert rows[0]['Payee/Vendor'].startswith("'=Vendor")
        assert 'Receipt notes' in rows[0]['Description']
        workbook = load_workbook(io.BytesIO(bundle.read('report.xlsx')))
        assert workbook['Report']['B2'].data_type == 's'
        assert workbook['Report']['I2'].value == 10
        assert workbook['Report']['I2'].number_format == '0.00'
        assert workbook['Report']['J2'].hyperlink.location.startswith("'Documents'!")
        assert workbook['Documents']['C2'].hyperlink.target in pdfs
    assert list(conn.iterdump()) == before
    assert list((app.config['DATA_ROOT'] / 'reports').iterdir()) == []


def test_cash_ledger_matches_existing_export_and_period_filters(desk):
    _, client, _, _ = desk
    original = client.get('/api/export/transactions.csv?export_type=cash_ledger')
    expected = list(csv.DictReader(io.StringIO(original.text)))
    with archive(client, 'export_type=cash_ledger') as bundle:
        actual = list(csv.DictReader(io.StringIO(bundle.read('report.csv').decode())))
    for a, e in zip(actual, expected, strict=True):
        for new, old in [('Date', 'date'), ('MXN','mxn'), ('ER','ER'), ('USD','usd'), ('Calc USD','calc_usd'), ('Safe Balance USD','Safe Balance USD'), ('Safe Balance MXN','Safe Balance MXN')]:
            assert a[new] == e[old]
    with archive(client, 'fy=2027&start=2026-09-03&end=2026-09-03') as bundle:
        rows = list(csv.DictReader(io.StringIO(bundle.read('report.csv').decode())))
        assert len(rows) == 2
    with archive(client, 'fy=2028') as bundle:
        assert len(list(csv.DictReader(io.StringIO(bundle.read('report.csv').decode())))) == 0


@pytest.mark.parametrize('query', ['export_type=bad', 'fy=no', 'start=2026-02-30', 'start=2026-09-03&end=2026-09-01'])
def test_invalid_options_rejected(desk, query):
    client = desk[1]
    for endpoint in ('/reports/bookkeeper', '/api/export/bookkeeper.zip'):
        assert client.get(endpoint + '?' + query).status_code == 400


def test_missing_file_marked_and_corrupt_file_blocks_package(desk):
    _, client, _, paths = desk
    paths[0].unlink()
    with archive(client) as bundle:
        assert 'Missing PDF' in bundle.read('index.html').decode()
        assert len([n for n in bundle.namelist() if n.endswith('.pdf')]) == 2
    paths[1].write_bytes(b'corrupt')
    response = client.get('/api/export/bookkeeper.zip')
    assert response.status_code == 410
    assert response.json['error']['code'] == 'document_package_failed'


def test_report_and_documents_require_admin(desk):
    app = desk[0]
    client = app.test_client()
    for endpoint in ('/reports/bookkeeper', '/api/export/bookkeeper.zip'):
        assert client.get(endpoint).status_code in (302, 401)
    client.post('/api/user-login', json={'password':'test'})
    assert client.get('/api/export/bookkeeper.zip').status_code == 401


def test_direct_expense_receipts_are_named_by_receipt_number(desk):
    app, client, conn, _ = desk
    root = app.config['DATA_ROOT']
    vid = 'FY27-0002'
    conn.execute("INSERT INTO vouchers(voucher_id,type,fiscal_year,seq,recipient,purpose,status)"
                 " VALUES (?,'direct_expense',2027,2,'Contractor','Labor','complete')", (vid,))
    payment = expense(conn, voucher_id=vid)
    voucher_doc = add_pdf(conn, root, 'Signed voucher')
    receipt_docs = [add_pdf(conn, root, name) for name in ('Supplies', 'Gas')]
    for role in ('voucher', 'support'):
        conn.execute('INSERT INTO transaction_documents(txn_id,document_id,role) VALUES (?,?,?)',
                     (payment, voucher_doc, role))
    for seq, did in enumerate(receipt_docs, 1):
        conn.execute("INSERT INTO receipts(receipt_id,voucher_id,seq,document_id) VALUES (?,?,?,?)",
                     (f'{vid}-R{seq}', vid, seq, did))
        conn.execute("INSERT INTO transaction_documents(txn_id,document_id,role) VALUES (?,?,'support')",
                     (payment, did))
    with archive(client) as bundle:
        names = set(bundle.namelist())
        assert {'documents/FY27-0002.pdf', 'documents/FY27-0002-R1.pdf',
                'documents/FY27-0002-R2.pdf'} <= names
        documents = {d['reference']: d['filename'] for d in
                     json.loads(bundle.read('manifest.json'))['documents']}
        assert documents['FY27-0002-R2'] == 'documents/FY27-0002-R2.pdf'


def test_documents_that_would_share_a_name_block_the_report(desk):
    app, client, conn, _ = desk
    root = app.config['DATA_ROOT']
    txn_id = expense(conn)
    for label in ('First slip', 'Second slip'):
        conn.execute("INSERT INTO transaction_documents(txn_id,document_id,role) VALUES (?,?,'support')",
                     (txn_id, add_pdf(conn, root, label)))
    for endpoint in ('/reports/bookkeeper?fy=2027', '/api/export/bookkeeper.zip?fy=2027'):
        response = client.get(endpoint)
        assert response.status_code == 409
        assert response.json['error']['code'] == 'document_name_clash'
        assert f'Txn-{txn_id}-receipt.pdf' in response.json['error']['message']


def test_sync_sheets_give_each_year_both_views_with_document_hashes(desk):
    from datetime import datetime
    from cash import sync
    from cash.prep import fiscal_year_for
    app, client, conn, (voucher_path, receipt_path, _) = desk
    root = app.config['DATA_ROOT']
    token = sync.create_token(root)
    script = app.test_client()
    # The token is required; a logged-in session is not enough.
    assert client.get('/api/sync/sheets').status_code == 401

    def fetch():
        response = script.get('/api/sync/sheets', headers={'Authorization': f'Bearer {token}'})
        assert response.status_code == 200, response.text
        return {year['fiscal_year']: year for year in response.get_json()['data']['years']}

    years = fetch()
    today = datetime.now(app.config['TIMEZONE']).date().isoformat()
    assert list(years) == list(range(2026, fiscal_year_for(today) + 1))
    fy27 = years[2027]
    assert fy27['fiscal_label'] == 'FY27'
    expenses, ledger = fy27['tabs']
    assert (expenses['name'], ledger['name']) == ('Expense report', 'Cash ledger')
    assert expenses['columns'] == ['Date', 'Payee/Vendor', 'Category', 'Class', 'Description',
                                   'MXN', 'ER', 'USD', 'Calc USD']
    assert 'Safe Balance USD' in ledger['columns']
    described = [row[expenses['columns'].index('Description')] for row in expenses['rows']]
    assert described == ['Allocation 1\nReceipt notes', 'Allocation 2\nReceipt notes',
                         'Unattached purchase']
    assert expenses['rows'][0][expenses['columns'].index('USD')] == 10.0
    linked = {d['reference']: d['sha256'] for d in expenses['documents'][0]}
    receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    voucher_hash = hashlib.sha256(voucher_path.read_bytes()).hexdigest()
    assert linked['FY27-0001-R1'] == receipt_hash
    assert linked['FY27-0001'] == voucher_hash
    assert expenses['documents'][2] == []
    assert len(ledger['rows']) == len(ledger['documents'])

    again = fetch()
    assert again[2027]['revision'] == fy27['revision']
    expense(conn, description='Later purchase')
    changed = fetch()
    assert changed[2027]['revision'] != fy27['revision']
    assert changed[2026]['revision'] == years[2026]['revision']
