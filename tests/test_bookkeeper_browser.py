"""Desktop/mobile report browsing and the extracted offline package."""
import io
import threading
from zipfile import ZipFile

import pytest
from werkzeug.serving import make_server

from test_bookkeeper import desk
from test_browser_workflows import browser, expect


@pytest.mark.parametrize('width', [1440, 390])
def test_bookkeeper_filters_sort_links_and_offline_package(desk, browser, tmp_path, width):
    app, _, _, _ = desk
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    context = browser.new_context(viewport={'width': width, 'height': 1000})
    errors = []
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        context.request.post(origin + '/api/login', data={'password':'test'})
        page.goto(origin + '/reports')
        page.locator('#report-fy').select_option('2027')
        page.locator('#view-bookkeeper').click()
        expect(page.locator('h1')).to_have_text('Expense report')
        expect(page.locator('.bk-group')).to_have_count(2)
        headers = page.locator('#bk-table thead th').all_text_contents()
        assert [h.strip().replace(' ↕', '') for h in headers] == ['Date', 'Payee/Vendor', 'Category', 'Class', 'Description', 'MXN', 'ER', 'USD', 'Calc USD', 'Documents']
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        calc = page.locator('td.bk-calc').first
        assert calc.evaluate('e => getComputedStyle(e).backgroundColor') == 'rgb(237, 244, 238)'
        page.locator('#bk-class').select_option('4.3')
        expect(page.locator('.bk-group:visible')).to_have_count(1)
        expect(page.locator('.bk-group:visible tr')).to_have_count(2)
        page.locator('#bk-reset').click()
        page.locator('#bk-search').fill('unattached')
        expect(page.locator('.bk-group:visible')).to_have_count(1)
        page.locator('#bk-reset').click()
        page.get_by_role('button', name='Calc USD').click()
        expect(page.locator('.bk-group').first).to_contain_text('Unattached purchase')
        document = page.get_by_role('link', name='FY27-0001-R1', exact=True)
        assert document.get_attribute('target') == '_blank'
        pdf = context.request.get(origin + document.get_attribute('href'))
        assert pdf.ok and pdf.headers['content-type'].startswith('application/pdf')
        page.screenshot(path=str(tmp_path / f'bookkeeper-{width}.png'), full_page=True)
        with page.expect_download() as downloaded:
            page.locator('#bk-download').click()
        package = tmp_path / 'package.zip'
        downloaded.value.save_as(package)
        offline = tmp_path / 'offline'
        with ZipFile(package) as archive:
            archive.extractall(offline)
        page.goto((offline / 'index.html').as_uri())
        expect(page.locator('h1')).to_have_text('Expense report')
        expect(page.locator('#bk-download')).to_have_count(0)
        expect(page.locator('.bk-group')).to_have_count(2)
        page.locator('#bk-search').fill('Allocation')
        expect(page.locator('.bk-group:visible')).to_have_count(1)
        for href in page.locator('.bk-documents a').evaluate_all('(links) => links.map(a => a.getAttribute("href"))'):
            assert (offline / href).is_file()
        assert not errors
    finally:
        context.close()
        server.shutdown()
        thread.join(timeout=5)
