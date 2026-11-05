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
