"""Password settings work without showing saved secrets."""
from test_user_password import desk
from test_browser_workflows import expect, answer_confirm


def test_save_user_password_from_settings(desk, browser, serve):
    origin = serve(desk[0])
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
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


def test_drive_backup_token_from_settings(desk, browser, serve):
    origin = serve(desk[0])
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    try:
        context.request.post(origin + '/api/login', data={'password':'admin'})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(origin + '/settings')
        card = page.locator('#sync')
        expect(card.locator('#sync-state')).to_contain_text('Not set up')
        expect(card.locator('#sync-revoke')).to_be_hidden()
        card.get_by_role('button', name='Generate token', exact=True).click()
        token = card.locator('#sync-token')
        expect(card.locator('#sync-state')).to_contain_text('No run has completed yet')
        generated = token.input_value()
        assert generated
        page.reload()
        expect(token).to_have_value(generated)  # Shown again, not just once.
        token.fill('has spaces')
        card.get_by_role('button', name='Save token', exact=True).click()
        answer_confirm(page)
        expect(card.locator('#sync-status')).to_contain_text('without spaces')
        chosen = 'short'
        token.fill(chosen)
        card.get_by_role('button', name='Save token', exact=True).click()
        answer_confirm(page)
        expect(card.locator('#sync-status')).to_contain_text('Token saved')
        fresh = browser.new_context()
        try:
            response = fresh.request.get(origin + '/api/sync/database.sql',
                                         headers={'Authorization': f'Bearer {chosen}'})
            assert response.ok
        finally:
            fresh.close()
        card.get_by_role('button', name='Revoke token', exact=True).click()
        answer_confirm(page)
        expect(card.locator('#sync-state')).to_contain_text('Not set up')
        expect(token).to_have_value('')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert not errors
    finally:
        context.close()
