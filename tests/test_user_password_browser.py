"""Password settings work on desktop and mobile without showing saved secrets."""
import threading

import pytest
from werkzeug.serving import make_server

from test_user_password import desk
from test_browser_workflows import browser, expect


@pytest.mark.parametrize('width', [1440, 390])
def test_save_user_password_from_settings(desk, browser, width):
    app = desk[0]
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    context = browser.new_context(viewport={'width':width,'height':1000})
    try:
        context.request.post(origin + '/api/login', data={'password':'admin'})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(origin + '/settings')
        form = page.locator('#user-password-form')
        expect(form.locator('[name=password]')).to_have_value('')
        form.get_by_label('New employee password', exact=True).fill('nueva-pass')
        form.get_by_label('Confirm employee password', exact=True).fill('wrong')
        form.get_by_role('button').click()
        expect(page.locator('#user-password-status')).to_contain_text('do not match')
        form.get_by_label('Confirm employee password', exact=True).fill('nueva-pass')
        form.get_by_role('button').click()
        expect(page.locator('#user-password-status')).to_contain_text('Employee password saved')
        expect(form.locator('[name=password]')).to_have_value('')
        expect(form.locator('[name=confirmation]')).to_have_value('')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        fresh = browser.new_context()
        try:
            assert fresh.request.post(origin + '/api/user-login', data={'password':'old'}).status == 401
            assert fresh.request.post(origin + '/api/user-login', data={'password':'nueva-pass'}).ok
        finally:
            fresh.close()
        assert not errors
    finally:
        context.close()
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.parametrize('width', [1440, 390])
def test_drive_backup_token_from_settings(desk, browser, width):
    app = desk[0]
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    context = browser.new_context(viewport={'width':width,'height':1000})
    try:
        context.request.post(origin + '/api/login', data={'password':'admin'})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(origin + '/settings')
        card = page.locator('#sync')
        expect(card.locator('#sync-state')).to_contain_text('Not set up')
        expect(card.locator('#sync-revoke')).to_be_hidden()
        card.get_by_role('button', name='Create access token', exact=True).click()
        token = card.locator('#sync-token')
        expect(token).not_to_have_value('')
        response = context.request.get(origin + '/api/sync/database.sql',
                                       headers={'Authorization': f'Bearer {token.input_value()}'})
        assert response.ok
        expect(card.locator('#sync-state')).to_contain_text('No run has completed yet')
        page.once('dialog', lambda dialog: dialog.accept())
        card.get_by_role('button', name='Revoke token', exact=True).click()
        expect(card.locator('#sync-state')).to_contain_text('Not set up')
        expect(card.locator('#sync-token-shown')).to_be_hidden()
        page.reload()
        expect(card.locator('#sync-token-shown')).to_be_hidden()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert not errors
    finally:
        context.close()
        server.shutdown()
        thread.join(timeout=5)
