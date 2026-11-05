"""Configurable defaults are captured per document, never changed on reprint."""
import json
import re
import uuid

import pytest

from cash import files
from cash.db import connect
from app_factory import create_app
from test_settings import LATEST_MIGRATION


@pytest.fixture
def desk(tmp_path):
    app = create_app(tmp_path, admin_password='test', testing=True)
    client = app.test_client()
    csrf = client.post('/api/login', json={'password': 'test'}).json['data']['csrf']
    def post(url, **body):
        return client.post(url, json={'submission_id': str(uuid.uuid4()), **body}, headers={'X-CSRF': csrf})
    assert post('/api/entries/opening', date='2026-09-01', usd='1000').status_code == 200
    with connect(tmp_path / 'cash.db') as conn:
        yield client, post, conn


def tokens(client, url):
    response = client.get(url)
    assert response.status_code == 200
    return json.loads(re.search(r'<script id="tokens" type="application/json">(.*?)</script>', response.text).group(1))


@pytest.mark.parametrize('kind', ['direct_expense', 'signout'])
def test_paper_uses_authenticated_name_and_preserves_reprints(desk, kind):
    client, post, conn = desk
    first = client.get('/api/settings/admins').json['data']['current_id']
    before = list(conn.iterdump())
    assert post(f'/api/settings/admins/{first}', name='First Admin').status_code == 200
    assert list(conn.iterdump()) == before
    submission = str(uuid.uuid4())
    saved = post('/api/vouchers/prepare', submission_id=submission, type=kind,
                 date='2026-09-02', recipient='Recipient', purpose='Supplies', usd='100',
                 approver_name='Spoofed name').json['data']
    assert tokens(client, saved['print_url'])['APPROVER'] == 'First Admin'
    post('/api/settings/admins', name='Second Admin', password='second')
    other = client.application.test_client()
    csrf = other.post('/api/login', json={'password': 'second'}).json['data']['csrf']
    response = other.post('/api/vouchers/prepare', headers={'X-CSRF': csrf}, json={
        'submission_id': str(uuid.uuid4()), 'type': kind, 'date': '2026-09-02',
        'recipient': 'Recipient', 'purpose': 'Supplies', 'usd': '10'})
    assert tokens(other, response.json['data']['print_url'])['APPROVER'] == 'Second Admin'
    post(f'/api/settings/admins/{first}', name='Renamed Admin')
    assert tokens(client, saved['print_url'])['APPROVER'] == 'First Admin'
    assert post('/api/vouchers/prepare', submission_id=submission, type=kind,
                date='2026-09-02', recipient='Recipient', purpose='Supplies', usd='100').json['data'] == saved
    assert tokens(client, saved['print_url'])['APPROVER'] == 'First Admin'
    if kind == 'signout':
        addition = post('/api/addenda/prepare', voucher_id=saved['voucher_id'], kind='positive',
                        date='2026-09-03', usd='10', reason='Supplies', approver_name='Spoofed').json['data']
        assert tokens(client, addition['print_url'])['APPROVER'] == 'Renamed Admin'


def test_unwritable_selection_rolls_back_voucher_without_partial_database_changes(desk, monkeypatch):
    client, post, conn = desk
    before = list(conn.iterdump())

    def fail_replace(*args):
        raise OSError('disk full')

    monkeypatch.setattr(files.os, 'replace', fail_replace)
    response = post('/api/vouchers/prepare', type='signout', date='2026-09-02',
                    recipient='Recipient', purpose='Supplies', usd='10', approver_name='Name')
    assert response.status_code == 422
    assert response.json['error']['code'] == 'approver_config'
    assert list(conn.iterdump()) == before
    root = client.application.config['DATA_ROOT']
    assert not list((root / 'config/voucher-approvers').iterdir())
