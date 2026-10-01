"""Settings edits the installed env file, and signout logins read it live."""
from collections import defaultdict
from pathlib import Path

import pytest

from cash import files, user_access
from cash.db import connect
from cash.web import auth
from cash.web.app import create_app


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, '_attempts', defaultdict(list))
    env_file = tmp_path / 'config' / 'cash-27.env'
    env_file.parent.mkdir()
    env_file.write_text('# Existing settings\nCASH_ADMIN_PASSWORD=admin\nCASH_USER_PASSWORD=old\nCASH_SECRET_KEY=unchanged\nCASH_FY_START_MONTH=9\nCUSTOM_SETTING=keep=this\n')
    env_file.chmod(0o600)
    app = create_app(tmp_path / 'data', admin_password='admin', user_password='old',
                     testing=True, auth_env_file=env_file)
    client = app.test_client()
    token = client.post('/api/login', json={'password':'admin'}).json['data']['csrf']
    return app, client, {'X-CSRF':token}, env_file


def save(desk, password, **extra):
    return desk[1].post('/api/settings/user-password', headers=desk[2],
                        json={'password':password, 'confirmation':password, **extra})


def test_live_password_preserves_other_config_and_database(desk):
    app, admin, headers, path = desk
    user = app.test_client()
    assert user.post('/api/user-login', json={'password':'old', 'remember':True}).status_code == 200
    before = path.read_bytes()
    with connect(app.config['DATA_ROOT'] / 'cash.db') as conn:
        database_before = list(conn.iterdump())
        name = 'Nüeva $HOME #pass="word"'
        result = save(desk, name)
        assert result.status_code == 200
        assert result.json['data'] == {'saved':True}
        assert name not in admin.get('/api/settings').text
        assert name not in admin.get('/settings').text
        assert list(conn.iterdump()) == database_before
    assert path.read_bytes() == before.replace(b'CASH_USER_PASSWORD=old', ('CASH_USER_PASSWORD=' + name).encode())
    assert path.stat().st_mode & 0o777 == 0o600
    assert user.get('/api/session').json['data']['user'] is True
    assert app.test_client().post('/api/user-login', json={'password':'old'}).status_code == 401
    assert app.test_client().post('/api/user-login', json={'password':name}).status_code == 200
    assert app.test_client().post('/api/login', json={'password':'admin'}).status_code == 200
    restarted = create_app(app.config['DATA_ROOT'], admin_password='admin', user_password='stale', testing=True, auth_env_file=path)
    assert restarted.test_client().post('/api/user-login', json={'password':name}).status_code == 200
    # Simulate nano/setup atomically replacing the same host file.
    replacement = path.with_suffix('.new')
    replacement.write_bytes(path.read_bytes().replace(name.encode(), b'manually-edited'))
    replacement.replace(path)
    assert app.test_client().post('/api/user-login', json={'password':'manually-edited'}).status_code == 200
    assert not (app.config['DATA_ROOT'] / 'config/user-access.json').exists()


@pytest.mark.parametrize('value', ['', ' ', ' before', 'after ', 'a\nb', 'a\rb', 'a\x00b', 'a'*1025, None, ['bad']])
def test_invalid_password_preserves_file(desk, value):
    before = desk[3].read_bytes()
    assert save(desk, value).status_code == 422
    assert desk[3].read_bytes() == before


def test_requires_admin_csrf_and_matching_confirmation(desk):
    app, client, headers, path = desk
    before = path.read_bytes()
    assert client.post('/api/settings/user-password', json={'password':'new', 'confirmation':'new'}).status_code == 403
    assert save(desk, 'new', confirmation='different').status_code == 422
    assert save(desk, 'new', unexpected=True).status_code == 400
    assert client.post('/api/settings/user-password', headers=headers, json=[]).status_code == 400
    user = app.test_client()
    token = user.post('/api/user-login', json={'password':'old'}).json['data']['csrf']
    assert user.post('/api/settings/user-password', headers={'X-CSRF':token}, json={'password':'new','confirmation':'new'}).status_code == 401
    assert path.read_bytes() == before


def test_read_errors_fail_closed_and_writes_are_atomic(desk, monkeypatch):
    app, client, _, path = desk
    before = path.read_bytes()
    def fail(*args):
        raise OSError('disk full')
    with monkeypatch.context() as patch:
        patch.setattr(files.os, 'replace', fail)
        assert save(desk, 'new').status_code == 422
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]
    path.write_text('CASH_USER_PASSWORD=one\nCASH_USER_PASSWORD=two\n')
    assert app.test_client().post('/api/user-login', json={'password':'old'}).status_code == 503
    assert save(desk, 'new').status_code == 422
    path.unlink()
    assert app.test_client().post('/api/user-login', json={'password':'old'}).status_code == 503
    assert save(desk, 'new').status_code == 422
    assert not path.exists()


def test_development_fallback_and_line_endings(tmp_path):
    app = create_app(tmp_path / 'data', admin_password='admin', user_password='dev', testing=True)
    assert app.test_client().post('/api/user-login', json={'password':'dev'}).status_code == 200
    path = tmp_path / 'cash-27.env'
    for ending in ('\n', '\r\n', ''):
        original = f'# header\r\nCASH_ADMIN_PASSWORD=secret\r\nCASH_USER_PASSWORD=old{ending}'
        path.write_bytes(original.encode())
        user_access.save_password(path, 'new')
        assert path.read_bytes() == original.replace('=old', '=new').encode()
        assert user_access.password(path, 'fallback') == 'new'


def test_compose_mount_uses_existing_env_location_without_setup(tmp_path):
    import json
    import os
    import subprocess
    root = Path(__file__).resolve().parents[1]
    config = tmp_path / 'existing config'
    config.mkdir()
    (config / 'compose.env').write_text(f'CASH_APP_ENV_FILE={config}/cash-27.env\n')
    commands = tmp_path / 'bin'
    commands.mkdir()
    podman = commands / 'podman'
    podman.write_text('#!/usr/bin/env python3\nimport os,json\nprint(json.dumps({k:os.environ[k] for k in ("CASH_APP_CONFIG_DIR","CASH_APP_ENV_NAME")}))\n')
    podman.chmod(0o755)
    output = subprocess.run([str(root / 'deploy/compose.sh'), 'config'], env={**os.environ,
        'CASH_CONFIG_DIR':str(config), 'CASH_RUNTIME_DELEGATED':'1',
        'PATH':str(commands) + os.pathsep + os.environ['PATH']}, capture_output=True, text=True, check=True)
    assert json.loads(output.stdout) == {'CASH_APP_CONFIG_DIR':str(config), 'CASH_APP_ENV_NAME':'cash-27.env'}
    compose = (root / 'compose.yaml').read_text().split('  document-worker:')[0]
    assert '${CASH_APP_CONFIG_DIR}:/runtime-config:rw,z' in compose
    assert 'CASH_AUTH_ENV_FILE: /runtime-config/${CASH_APP_ENV_NAME}' in compose
