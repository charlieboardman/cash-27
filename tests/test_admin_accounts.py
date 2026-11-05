import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from cash import admin_accounts
from cash.commands import ValidationFailure
from cash.web.app import create_app


def login(client, password='x'):
    response = client.post('/api/login', json={'password': password})
    assert response.status_code == 200
    return {'X-CSRF': response.json['data']['csrf']}


def test_accounts_are_private_and_last_active_cannot_be_removed(app):
    client = app.test_client()
    headers = login(client)
    data = client.get('/api/settings/admins').json['data']
    first = data['current_id']
    assert set(data['admins'][0]) == {'id', 'name', 'enabled'}
    for body in ({'delete': True}, {'enabled': False}):
        assert client.post(f'/api/settings/admins/{first}', json=body, headers=headers).status_code == 422
    assert client.post('/api/settings/admins', json={'name': 'Other', 'password': 'x'}, headers=headers).status_code == 422
    added = client.post('/api/settings/admins', json={'name': 'Second Admin', 'password': 'second'}, headers=headers)
    assert added.status_code == 200
    second = added.json['data']['admins'][1]['id']
    assert 'password' not in added.text
    other = app.test_client()
    other_headers = login(other, 'second')
    assert other.get('/api/session').json['data']['admin_name'] == 'Second Admin'
    assert client.post(f'/api/settings/admins/{second}', json={'name': 'Renamed'}, headers=headers).status_code == 200
    assert other.get('/api/session').json['data']['admin_name'] == 'Renamed'
    assert client.post(f'/api/settings/admins/{second}', json={'password': 'replacement'}, headers=headers).status_code == 200
    assert other.get('/api/settings/admins').status_code == 401
    assert other.post('/api/login', json={'password': 'second'}).status_code == 401
    other_headers = login(other, 'replacement')
    assert other.post(f'/api/settings/admins/{first}', json={'enabled': False}, headers=other_headers).status_code == 200
    assert client.get('/api/settings').status_code == 401
    assert client.post('/api/login', json={'password': 'x'}).status_code == 401
    assert other.post(f'/api/settings/admins/{second}', json={'delete': True}, headers=other_headers).status_code == 422
    assert other.post(f'/api/settings/admins/{first}', json={'delete': True}, headers=other_headers).status_code == 200
    assert (app.config['ADMIN_ACCOUNTS_FILE'].stat().st_mode & 0o777) == 0o600


def test_config_failure_never_falls_back_to_old_password(app):
    client = app.test_client()
    login(client)
    path = app.config['ADMIN_ACCOUNTS_FILE']
    path.unlink()
    assert client.get('/api/settings').status_code == 422
    assert app.test_client().post('/api/login', json={'password': 'x'}).status_code == 422
    assert not path.exists()
    path.write_text('{broken')
    assert app.test_client().post('/api/login', json={'password': 'x'}).status_code == 422


def test_missing_config_has_no_bootstrap_or_database_changes(tmp_path):
    with pytest.raises(ValidationFailure, match='Administrator configuration unavailable'):
        create_app(tmp_path, testing=True, user_password='employee')
    assert not (tmp_path / 'config' / 'admins.json').exists()
    assert not (tmp_path / 'cash.db').exists()


def test_management_requires_admin_and_csrf(app):
    client = app.test_client()
    headers = login(client)
    assert client.post('/api/settings/admins', json={'name': 'Other', 'password': 'other'}).status_code == 403
    client.get('/api/logout')
    csrf = client.post('/api/user-login', json={'password': 'x'}).json['data']['csrf']
    assert client.get('/api/settings/admins').status_code == 401
    assert client.post('/api/settings/admins', json={'name': 'Other', 'password': 'other'}, headers={'X-CSRF': csrf}).status_code == 401


def test_simultaneous_deletes_leave_an_active_admin(app):
    path = app.config['ADMIN_ACCOUNTS_FILE']
    first = admin_accounts.read(path)[0]
    admin_accounts.change(path, first['id'], first['revision'], name='Other', password='other')
    accounts = admin_accounts.read(path)
    def remove(account):
        try:
            admin_accounts.change(path, account['id'], account['revision'], account_id=account['id'], delete=True)
            return True
        except ValidationFailure:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(remove, accounts)) == [False, True]
    assert len(admin_accounts.read(path)) == 1


def test_sessions_and_names_survive_restart_without_exposing_credentials(app):
    client = app.test_client(); login(client)
    path = app.config['ADMIN_ACCOUNTS_FILE']
    first = admin_accounts.read(path)[0]
    admin_accounts.change(path, first['id'], first['revision'], account_id=first['id'], name='Updated Name')
    restarted = create_app(app.config['DATA_ROOT'], admin_accounts_file=path, testing=True, secret_key=app.secret_key)
    other = restarted.test_client()
    other.set_cookie('session', client.get_cookie('session').value)
    assert other.get('/api/session').json['data']['admin_name'] == 'Updated Name'
