"""Bookkeeper outputs use active expenses and final engine valuation."""
import csv
import io
import uuid

import pytest
from openpyxl import load_workbook

from cash.db import connect
from cash.web.app import create_app


HEADERS = ['date', 'Account', 'mxn', 'ER', 'usd', 'calc_usd',
           'voucher_id', 'receipt_id', 'addendum_id', 'class_code',
           'project', 'recipient', 'vendor', 'description', 'notes']


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path, admin_password='test', testing=True)
    client = app.test_client()
    token = client.post('/api/login', json={'password': 'test'}).json['data']['csrf']
    with connect(tmp_path / 'cash.db') as conn:
        yield client, {'X-CSRF': token}, conn


def insert(conn, **fields):
    values = dict(movement_id=str(uuid.uuid4()), submission_id=str(uuid.uuid4()),
                  date='2026-09-02', same_day_seq=0, txn_type='expense',
                  from_account='SAFE', to_account='EXP_SUPPLIES',
                  usd_cents=0, mxn_centavos=0)
    values.update(fields)
    return conn.execute(
        f"INSERT INTO transactions ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        list(values.values())).lastrowid


def opening(conn, *, mxn=155000, rate=15500):
    return insert(conn, date='2026-08-01', txn_type='opening',
                  from_account='OPENING_EQUITY', to_account='SAFE',
                  usd_cents=10000, mxn_centavos=mxn,
                  assigned_rate_milli=rate if mxn else None)


def preview(env, year=2027):
    response = env[0].post(f'/api/reports/{year}/generate', json={}, headers=env[1])
    assert response.status_code == 200
    reader = csv.DictReader(io.StringIO(response.data.decode()))
    assert reader.fieldnames == HEADERS
    return list(reader)


def workbook(env, query=''):
    response = env[0].get('/api/export/transactions.xlsx' + query)
    assert response.status_code == 200
    assert 'expenses.xlsx' in response.headers['Content-Disposition']
    return load_workbook(io.BytesIO(response.data))


def test_csv_xlsx_and_final_reports_include_only_active_expenses(env):
    client, headers, conn = env
    opening(conn)
    conn.execute("INSERT INTO accounts (account_id, name, kind) VALUES ('SIGNOUT', 'Person', 'signout')")
    for kind, source, target, usd, mxn in [
        ('bank_withdrawal', 'BANK', 'SAFE', 1000, 0),
        ('donation', 'DONATION', 'SAFE', 1000, 0),
        ('signout', 'SAFE', 'SIGNOUT', 500, 0),
        ('addendum', 'SAFE', 'SIGNOUT', 100, 0),
        ('exchange', 'SAFE', 'FX_COUNTERPARTY', 1000, -15500),
        ('adjustment', 'SAFE', 'RECONCILIATION', 100, 0),
    ]:
        insert(conn, txn_type=kind, from_account=source, to_account=target,
               usd_cents=usd, mxn_centavos=mxn,
               same_day_seq=conn.execute('SELECT count(*) FROM transactions').fetchone()[0])
    insert(conn, date='2026-09-03', usd_cents=9900, posting_state='superseded')
    insert(conn, date='2026-09-04', usd_cents=8800, posting_state='void')
    # Insert out of date order to check report ordering independently of IDs.
    insert(conn, date='2027-09-01', usd_cents=200, description='Next FY')
    insert(conn, date='2026-09-06', usd_cents=-100, description='Refund')
    insert(conn, date='2026-09-05', mxn_centavos=10000, description='Expense')
    rows = preview(env)
    assert [r['description'] for r in rows] == ['Expense', 'Refund']
    assert [r['calc_usd'] for r in rows] == ['6.45', '-1.00']
    assert rows[0]['ER'] == '15.500'  # Not 100 / the rounded 6.45.
    assert rows[1]['ER'] == ''
    wb = workbook(env)
    assert wb.sheetnames == ['expenses', 'FY2027', 'FY2028']
    assert list(wb['expenses'].values)[0] == tuple(HEADERS)
    assert [r[13] for r in list(wb['expenses'].values)[1:]] == ['Expense', 'Refund', 'Next FY']
    assert wb['FY2027']['C2'].value == 100
    assert wb['FY2027']['D2'].value == 15.5
    assert wb['FY2027']['F2'].value == 6.45
    assert wb['FY2027']['F3'].value == -1
    assert wb['FY2027']['D3'].value is None
    for column in ('C', 'E', 'F'):
        assert wb['FY2027'][column + '2'].data_type == 'n'
        assert wb['FY2027'][column + '2'].number_format == '0.00'
    assert wb['FY2027']['D2'].number_format == '0.000'
    final = client.post('/api/reports/2027/generate', headers=headers,
                        json={'final': True, 'submission_id': str(uuid.uuid4())})
    assert final.status_code == 200
    assert final.json['data']['rows'] == 2
    downloaded = client.get(f"/api/reports/{final.json['data']['report_id']}/download")
    assert list(csv.DictReader(io.StringIO(downloaded.data.decode()))) == rows


@pytest.mark.parametrize('usd,mxn,face,expected', [
    (500, 23250, 31000, ('15.500', '20.00')),
    (2000, -7000, 24000, ('15.500', '15.48')),
    (125, 0, None, ('', '1.25')),
])
def test_rate_uses_peso_valuation_for_mixed_tender_and_change(env, usd, mxn, face, expected):
    opening(env[2])
    insert(env[2], usd_cents=usd, mxn_centavos=mxn, receipt_face_mxn_centavos=face)
    row = preview(env)[0]
    assert (row['ER'], row['calc_usd']) == expected


def test_rounds_half_cents_only_at_export_and_preserves_code_text(env):
    conn = env[2]
    opening(conn, rate=8000)
    conn.execute("INSERT INTO accounts (account_id, name, kind) VALUES ('0010', 'Supplies', 'expense')")
    insert(conn, to_account='0010', mxn_centavos=100, vendor='=1+1')
    row = preview(env)[0]
    assert row['calc_usd'] == '0.13'
    assert row['ER'] == '8.000'
    assert row['vendor'] == "'=1+1"
    wb = workbook(env)
    assert wb['expenses']['B2'].value == '0010'
    assert wb['expenses']['B2'].data_type == 's'
    assert wb['expenses']['M2'].value == "'=1+1"
    assert wb['expenses']['M2'].data_type == 's'


def test_unresolved_rate_stays_blank_then_uses_full_later_funding(env):
    client, headers, conn = env
    opening(conn)
    tid = insert(conn, mxn_centavos=200000)
    before = preview(env)[0]
    assert before['ER'] == before['calc_usd'] == ''
    rejected = client.post('/api/reports/2027/generate', headers=headers,
                           json={'final': True, 'submission_id': str(uuid.uuid4())})
    assert rejected.status_code == 422
    assert rejected.json['error']['code'] == 'basis_unresolved'
    insert(conn, date='2026-09-03', txn_type='donation', from_account='DONATION',
           to_account='SAFE', mxn_centavos=45000, assigned_rate_milli=15000)
    after = preview(env)[0]
    assert after['calc_usd'] == '130.00'
    assert after['ER'] == '15.385'
    # Filtering the export never filters the history used by the engine.
    wb = workbook(env, f'?since_txn_id={tid - 1}')
    assert wb['expenses']['D2'].value == 15.385
    assert wb['expenses']['F2'].value == 130
    assert len(list(wb['expenses'].values)) == 2
