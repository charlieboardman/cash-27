"""Responsive application shell and global transaction search."""
from test_browser_workflows import expect


def test_desktop_rail_mobile_workflow_bar_and_global_search(browser, web):
    page, post, _ = web
    page.goto('/')
    page.wait_for_url('**/overview')
    expect(page.locator('.topnav')).to_be_visible()
    assert page.locator('body').evaluate('e => getComputedStyle(e).paddingLeft') == '220px'
    expect(page.locator('.mobile-nav')).to_be_hidden()

    page.locator('.nav-search').click()
    expect(page.locator('#global-search-dialog')).to_be_visible()
    page.locator('#global-search-input').fill('SAFE')
    expect(page.locator('.global-search-result')).to_have_count(1)
    expect(page.locator('.global-search-result')).to_contain_text('Txn-1')
    page.locator('.global-search-result').click()
    expect(page.locator('#transaction-drawer')).to_be_visible()
    assert 'txn=1' in page.url

    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto('/balances')
    expect(page.locator('.mobile-nav')).to_be_visible()
    expect(page.locator('.topnav .nav-scroll')).to_be_hidden()
    expect(page.locator('.mobile-top-action')).to_be_visible()
    page.locator('.mobile-more > summary').click()
    expect(page.locator('.mobile-more-menu')).to_be_visible()
    page.locator('.mobile-more-menu a[href="/ledger"]').click()
    page.wait_for_url('**/ledger')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')

    page.keyboard.press('/')
    expect(page.locator('#global-search-dialog')).to_be_visible()
    page.locator('#global-search-input').fill('x')
    expect(page.locator('.global-search-result')).to_have_count(0)
    page.locator('#close-global-search').click()
    expect(page.locator('#global-search-dialog')).to_be_hidden()


def test_manual_rail_preference_survives_navigation_and_narrow_screens(web):
    page, _, _ = web
    page.set_viewport_size({'width': 1440, 'height': 1000})
    width = lambda: page.locator('body').evaluate('e => getComputedStyle(e).paddingLeft')
    for url in ['/overview', '/review', '/reports/bookkeeper']:
        page.goto(url)
        assert width() == '220px'
    toggle = page.locator('#rail-toggle')
    toggle.click()
    expect(toggle).to_have_attribute('aria-expanded', 'false')
    assert width() == '76px'
    page.goto('/review')
    assert width() == '76px'
    page.reload()
    assert width() == '76px'
    toggle.click()
    assert width() == '220px'
    page.set_viewport_size({'width': 900, 'height': 1000})
    assert width() == '76px'
    expect(toggle).to_be_hidden()
    page.set_viewport_size({'width': 1440, 'height': 1000})
    assert width() == '220px'
    page.reload()
    assert width() == '220px'


def test_rail_toggle_works_when_storage_is_unavailable(web):
    page, _, _ = web
    page.add_init_script("Object.defineProperty(window, 'localStorage', {get() {throw new Error('blocked');}})")
    page.goto('/review')
    toggle = page.locator('#rail-toggle')
    expect(toggle).to_have_attribute('aria-expanded', 'true')
    toggle.click()
    expect(toggle).to_have_attribute('aria-expanded', 'false')
