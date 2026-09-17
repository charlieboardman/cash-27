"""Remembered logins retain roles, expire after inactivity, and survive restarts."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from cash.web.app import create_app


@contextmanager
def at_time(timestamp):
    # Cookie expiry uses datetime.now; signed-cookie age uses time.time.
    with patch('time.time', return_value=timestamp), patch('flask.sessions.datetime') as clock:
        clock.now.return_value = datetime.fromtimestamp(timestamp, timezone.utc)
        yield


@pytest.mark.parametrize('role,path,password', [
    ('admin', '/api/login', 'admin-pass'),
    ('user', '/api/user-login', 'employee-pass'),
])
@pytest.mark.parametrize('remember', [True, False])
def test_remembered_login_survives_app_restart_and_logout(tmp_path, role, path, password, remember):
    options = dict(admin_password='admin-pass', user_password='employee-pass',
                   secret_key='stable-test-session-key', testing=True)
    app = create_app(tmp_path, **options)
    client = app.test_client()
    response = client.post(path, json={'password': password, 'remember': remember})
    assert response.status_code == 200
    cookie = client.get_cookie('session')
    assert (cookie.expires is not None) == remember
    assert cookie.http_only
    assert cookie.same_site == 'Lax'
    assert password not in cookie.value
    # Restart the app with the installed signing key; reuse the browser cookie.
    restarted = create_app(tmp_path, **options).test_client()
    restarted.set_cookie('session', cookie.value)
    state = restarted.get('/api/session').json['data']
    assert state[role] is True
    assert state['user' if role == 'admin' else 'admin'] is False
    assert state['csrf'] == response.json['data']['csrf']
    assert restarted.post('/api/settings/classes', json={}).status_code == 403
    assert restarted.get('/api/logout').status_code == 302
    assert restarted.get_cookie('session') is None
    assert restarted.get('/api/session').json['data'][role] is False


def test_remembered_session_refreshes_and_expires_after_90_idle_days(tmp_path):
    app = create_app(tmp_path, admin_password='test', testing=True)
    assert app.permanent_session_lifetime == timedelta(days=90)
    client = app.test_client()
    start = 2_000_000_000
    day = 86400
    with at_time(start):
        client.post('/api/login', json={'password': 'test', 'remember': True})
    first = client.get_cookie('session')
    for elapsed in (89, 178):
        with at_time(start + elapsed * day):
            assert client.get('/api/session').json['data']['admin'] is True
        assert client.get_cookie('session').expires > first.expires
    with at_time(start + 269 * day):
        assert client.get('/api/session').json['data']['admin'] is False
        assert client.get('/api/balances').status_code == 401


def test_production_cookie_is_secure_and_api_defaults_to_temporary_login(tmp_path):
    app = create_app(tmp_path, admin_password='test', secret_key='test-key')
    client = app.test_client()
    response = client.post('/api/login', json={'password': 'test'})
    assert response.status_code == 200
    cookie = client.get_cookie('session')
    assert cookie.secure and cookie.http_only
    assert cookie.expires is None


def test_invalid_password_cannot_create_remembered_login(tmp_path):
    client = create_app(tmp_path, admin_password='correct', testing=True).test_client()
    assert client.post('/api/login', json={'password': 'wrong', 'remember': True}).status_code == 401
    assert client.get_cookie('session') is None
