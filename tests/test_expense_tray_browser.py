"""Receipt editing stays separate from the compact report list."""

import pytest

from test_browser_workflows import expect, recipient, saved, signed_signout


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("width", [390, 1440])
def test_tray_keeps_rows_compact_and_preserves_edits(web, language, width):
    page, post, artifacts = web
    vid = signed_signout(page, post)["voucher_id"]
    post(f"/{vid}/api/rows", date="2026-09-03", vendor="Bendix", purpose="2 tornos",
         mxn="30", class_code="7.5", expense_account="1000")
    page.set_viewport_size({"width": width, "height": 844})
    recipient(page, vid)
    if language == "es":
        page.get_by_role("button", name="Español", exact=True).click()
    row = page.locator(".er-row")
    before = row.bounding_box()["height"]
    row.locator(".er-row-open").click()
    tray = page.locator("#er-tray")
    expect(tray).to_be_visible()
    assert row.bounding_box()["height"] == before
    assert row.locator(".er-editor").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(page.locator("#er-tray-done")).to_have_text("Listo" if language == "es" else "Done")
    tray.locator('[name="vendor"]').fill("Bendix updated")
    # Closing before the debounce fires must still save the latest value.
    page.keyboard.press("Escape")
    expect(tray).to_be_hidden()
    expect(row.locator(".er-row-open")).to_be_focused()
    saved(page, language)
    expect(row).to_contain_text("Bendix updated")
    row.locator(".er-row-open").click()
    expect(tray.locator('[name="vendor"]')).to_have_value("Bendix updated")
    tray.get_by_role("button", name="Agregar recibo" if language == "es" else "Add receipt", exact=True).click()
    expect(page.locator("#er-attach")).to_be_visible()
    page.keyboard.press("Escape")
    expect(tray).to_be_visible()
    expect(page.locator("#er-attach")).to_be_hidden()
    page.screenshot(path=str(artifacts / f"tray-{language}-{width}.png"))
    page.locator("#er-tray-done").click()
    expect(tray).to_be_hidden()
    page.locator("#er-add").click()
    expect(tray).to_be_visible()
    expect(page.locator(".er-row")).to_have_count(2)
    expect(tray.locator('[name="vendor"]')).to_have_value("")
