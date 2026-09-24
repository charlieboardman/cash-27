"""Configurable defaults are captured per document, never changed on reprint."""
import json
import re
import uuid

import pytest

from cash import voucher_defaults
from cash.db import connect
from cash.web.app import create_app
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
def test_default_override_blank_and_reprint_are_persisted(desk, kind):
    client, post, conn = desk
    before = list(conn.iterdump())
    assert post('/api/settings/voucher-defaults', approver_name='  Default Approver  ').status_code == 200
    assert list(conn.iterdump()) == before
    root = client.application.config['DATA_ROOT']
    assert json.loads((root / 'config/voucher-defaults.json').read_text()) == {'approver_name': 'Default Approver'}
    assert client.get('/api/settings').json['data']['default_approver_name'] == 'Default Approver'
    assert 'value="Default Approver"' in client.get('/transactions/new').text
    documents = []
    for override, expected in [({}, 'Default Approver'), ({'approver_name': 'Other <Approver> & Co'}, 'Other <Approver> & Co'), ({'approver_name': ''}, '')]:
        submission = str(uuid.uuid4())
        response = post('/api/vouchers/prepare', submission_id=submission, type=kind,
                        date='2026-09-02', recipient='Recipient', purpose='Supplies', usd='10', **override)
        assert response.status_code == 200, response.json
        saved = response.json['data']
        assert json.loads((root / 'config/voucher-approvers' / f'{submission}.json').read_text()) == {'approver_name': expected}
        assert tokens(client, saved['print_url'])['APPROVER'] == expected
        documents.append((saved, expected, submission))
    assert post('/api/settings/voucher-defaults', approver_name='New Default').status_code == 200
    for saved, expected, submission in documents:
        assert tokens(client, saved['print_url'])['APPROVER'] == expected
        retry = post('/api/vouchers/prepare', submission_id=submission, type=kind,
                     date='2026-09-02', recipient='Recipient', purpose='Supplies', usd='10')
        assert retry.json['data'] == saved
    assert conn.execute('SELECT count(*) FROM vouchers').fetchone()[0] == 3


def test_addenda_take_current_default_or_override(desk):
    client, post, conn = desk
    saved = post('/api/vouchers/prepare', type='signout', date='2026-09-02',
                 recipient='Recipient', purpose='Supplies', usd='100').json['data']
    assert tokens(client, saved['print_url'])['APPROVER'] == ''
    assert post('/api/settings/voucher-defaults', approver_name='New Approver').status_code == 200
    vid = saved['voucher_id']
    assert client.get(f'/{vid}').text.count('value="New Approver"') == 2
    for kind, extra, expected in [('positive', {}, 'New Approver'), ('return', {'approver_name': 'Return Approver'}, 'Return Approver')]:
        response = post('/api/addenda/prepare', voucher_id=vid, kind=kind,
                        date='2026-09-03', usd='10', reason='Supplies', **extra)
        assert response.status_code == 200, response.json
        saved_addendum = response.json['data']
        assert tokens(client, saved_addendum['print_url'])['APPROVER'] == expected
    # Configuring a default must not backfill earlier signed documents.
    assert tokens(client, saved['print_url'])['APPROVER'] == ''


@pytest.mark.parametrize('value', ['x' * 121, 'First\nSecond', ['Name'], 42])
def test_invalid_approver_rolls_back_setting_and_voucher(desk, value):
    client, post, conn = desk
    assert post('/api/settings/voucher-defaults', approver_name=value).status_code == 422
    result = post('/api/vouchers/prepare', type='direct_expense', date='2026-09-02',
                  recipient='Recipient', purpose='Supplies', usd='10', approver_name=value)
    assert result.status_code == 422
    assert result.json['error']['code'] == 'bad_approver'
    assert conn.execute('SELECT count(*) FROM vouchers').fetchone()[0] == 0
    assert client.get('/api/settings').json['data']['default_approver_name'] == ''


def test_default_setting_requires_admin(desk):
    client, post, _ = desk
    assert post('/api/settings/voucher-defaults', approver_name='Name', unknown='value').status_code == 400
    assert post('/api/settings/voucher-defaults').status_code == 422
    client.get('/api/logout')
    csrf = client.post('/api/user-login', json={'password': 'test'}).json['data']['csrf']
    response = client.post('/api/settings/voucher-defaults', headers={'X-CSRF': csrf},
                           json={'submission_id': str(uuid.uuid4()), 'approver_name': 'No'})
    assert response.status_code == 401


def test_config_is_read_live_survives_restart_and_needs_no_schema_changes(desk):
    client, post, conn = desk
    root = client.application.config['DATA_ROOT']
    assert conn.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] == LATEST_MIGRATION
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='voucher_defaults'").fetchone()
    for table in ('vouchers', 'addenda'):
        assert 'approver_name' not in [row[1] for row in conn.execute(f'PRAGMA table_info({table})')]
    assert post('/api/settings/voucher-defaults', approver_name='Original').status_code == 200
    path = root / 'config/voucher-defaults.json'
    path.write_text('{"approver_name": "Edited on disk"}\n')
    assert client.get('/api/settings').json['data']['default_approver_name'] == 'Edited on disk'
    other = create_app(root, admin_password='test', testing=True).test_client()
    other.post('/api/login', json={'password': 'test'})
    assert other.get('/api/settings').json['data']['default_approver_name'] == 'Edited on disk'
    path.write_text('broken json')
    response = client.get('/api/settings')
    assert response.status_code == 422
    assert response.json['error']['code'] == 'approver_config'
    assert post('/api/settings/voucher-defaults', approver_name='Fixed').status_code == 200


def test_unwritable_selection_rolls_back_voucher_without_partial_database_changes(desk, monkeypatch):
    client, post, conn = desk
    before = list(conn.iterdump())

    def fail_replace(*args):
        raise OSError('disk full')

    monkeypatch.setattr(voucher_defaults.os, 'replace', fail_replace)
    response = post('/api/vouchers/prepare', type='signout', date='2026-09-02',
                    recipient='Recipient', purpose='Supplies', usd='10', approver_name='Name')
    assert response.status_code == 422
    assert response.json['error']['code'] == 'approver_config'
    assert list(conn.iterdump()) == before
    root = client.application.config['DATA_ROOT']
    assert not list((root / 'config/voucher-approvers').iterdir())


def test_full_backup_restores_config_and_document_approver(desk, tmp_path):
    from cash.ops import backup_archive, restore_archive

    client, post, conn = desk
    assert post('/api/settings/voucher-defaults', approver_name='Original').status_code == 200
    result = post('/api/vouchers/prepare', type='signout', date='2026-09-02',
                  recipient='Recipient', purpose='Supplies', usd='10',
                  approver_name='Document Approver').json['data']
    archive = tmp_path / 'backup.tar.gz'
    backup_archive(client.application.config['DATA_ROOT'], archive)
    root = tmp_path / 'restored'
    restore_archive(root, archive)
    other = create_app(root, admin_password='test', testing=True).test_client()
    other.post('/api/login', json={'password': 'test'})
    assert other.get('/api/settings').json['data']['default_approver_name'] == 'Original'
    assert tokens(other, result['print_url'])['APPROVER'] == 'Document Approver'
