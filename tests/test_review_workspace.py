"""Focused browser coverage for the record-centric Review workspace."""

import io
import re

import pytest
from pypdf import PdfWriter

from test_browser_workflows import answer_confirm, expect, prepare, upload


def capture(page, name):
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    contents = io.BytesIO()
    writer.write(contents)
    response = page.request.post("/api/documents/capture-merge", multipart={
        "files": {"name": name, "mimeType": "application/pdf",
                  "buffer": contents.getvalue()}})
    assert response.ok, response.text()
    return response.json()["data"]["document_id"]


@pytest.mark.parametrize("width", [1440, 800, 390])
def test_documents_are_an_on_demand_right_slide_over(web, width):
    page, post, _ = web
    voucher = prepare(post)
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto("/review")
    rail = page.locator("#document-rail")
    expect(rail).to_have_attribute("aria-hidden", "true")
    expect(rail).to_be_hidden()
    expect(page.locator("#open-documents")).to_be_visible()

    page.locator(".review-summary-row").filter(
        has_text=voucher["voucher_id"]).click()
    expect(page.locator("#review-drawer")).to_be_visible()
    expect(page.locator("#previous-record")).to_be_visible()
    expect(page.locator("#next-record")).to_be_visible()
    expect(rail).to_have_attribute("aria-hidden", "true")
    assert rail.evaluate("el => getComputedStyle(el).zIndex") == "100"
    if width <= 1200:
        assert page.locator("#review-drawer").evaluate(
            "el => getComputedStyle(el).zIndex") == "95"
    assert page.locator("#review-drawer").evaluate(
        "el => el.scrollWidth <= el.clientWidth + 1")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    if width > 1200:
        columns = page.locator("#review-sheet-layout").evaluate(
            "el => getComputedStyle(el).gridTemplateColumns.split(' ').length")
        assert columns == 2
        assert page.locator("#review-drawer").bounding_box()["x"] > page.locator(
            ".review-sheet-pane").bounding_box()["x"]

    upload(page, "Upload", "workspace-receipt.pdf")
    expect(rail).to_have_attribute("aria-hidden", "false")
    expect(rail).to_be_visible()
    expect(page.locator(".document-preview-title > strong")).to_have_text(
        "workspace-receipt.pdf")
    bounds = rail.bounding_box()
    assert bounds["x"] + bounds["width"] == pytest.approx(width, abs=1)
    page.locator("#close-documents").click()
    expect(rail).to_have_attribute("aria-hidden", "true")
    expect(rail).to_be_hidden()

    page.locator("#drawer-documents").click()
    expect(rail).to_be_visible()
    expect(page.locator(".document-preview-title > strong")).to_have_text(
        "workspace-receipt.pdf")
    document_id = page.request.get("/api/documents/inbox").json()["data"][
        "documents"][0]["document_id"]
    page.goto(f"/review?document={document_id}")
    expect(rail).to_have_attribute("aria-hidden", "false")
    expect(rail).to_be_visible()
    expect(page.locator(".document-preview-title > strong")).to_have_text(
        "workspace-receipt.pdf")

    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    expect(page.locator("#review-drawer-title")).to_have_text(voucher["voucher_id"])
    expect(rail).to_have_attribute("aria-hidden", "true")
    expect(rail).to_be_hidden()


def test_queue_need_filters_use_badges_and_reports_disable_them(web):
    page, post, _ = web
    both = prepare(post)
    codes_only = prepare(post)
    document_id = capture(page, "signed-voucher.pdf")
    post(f"/api/review/direct-expense/{codes_only['voucher_id']}/attach-voucher",
         expected_version=1, document_id=document_id)
    withdrawal = post("/api/entries/withdrawal", date="2026-09-03", usd="80")
    txn_id = str(withdrawal["txn_ids"][0])
    page.goto("/review")
    rows = page.locator(".review-summary-row")
    expect(rows).to_have_count(3)
    need = page.locator("#need-filter")

    need.select_option("documents")
    expect(rows).to_have_count(2)
    expect(page.locator("#worklist")).to_contain_text(both["voucher_id"])
    expect(page.locator("#worklist")).to_contain_text(txn_id)
    expect(page.locator("#worklist")).not_to_contain_text(codes_only["voucher_id"])
    expect(page.locator("#queue-summary")).to_have_text(
        "3 transactions to review · 2 shown")

    need.select_option("codes")
    expect(rows).to_have_count(2)
    expect(page.locator("#worklist")).to_contain_text(both["voucher_id"])
    expect(page.locator("#worklist")).to_contain_text(codes_only["voucher_id"])
    expect(page.locator("#worklist")).not_to_contain_text(txn_id)
    need.select_option("all")
    expect(rows).to_have_count(3)

    need.select_option("codes")
    page.get_by_role("button", name="Expense reports", exact=True).click()
    expect(need).to_be_disabled()
    expect(need).to_be_hidden()
    page.get_by_role("button", name="Transactions", exact=True).click()
    expect(need).to_be_enabled()
    expect(need).to_be_visible()
    expect(need).to_have_value("codes")
    expect(rows).to_have_count(2)

    page.goto("/review?tab=reports")
    expect(page.locator("#work-heading")).to_have_text("Expense reports")
    expect(need).to_be_disabled()
    expect(need).to_be_hidden()


def test_previous_next_and_jk_respect_boundaries_forms_and_dirty_guards(web):
    page, post, _ = web
    for _ in range(3):
        prepare(post)
    items = page.request.get("/api/review/worklist").json()["data"]["items"]
    assert len(items) == 3
    first, second, third = items

    def open_url(item):
        return f"/review?item={item['kind']}:{item['id']}"

    page.goto(open_url(first))
    title = page.locator("#review-drawer-title")
    previous = page.locator("#previous-record")
    following = page.locator("#next-record")
    expect(title).to_have_text(first["id"])
    expect(previous).to_be_disabled()
    expect(following).to_be_enabled()

    following.click()
    expect(title).to_have_text(second["id"])
    expect(page).to_have_url(re.compile(re.escape(open_url(second)) + "$"))
    page.keyboard.press("k")
    expect(title).to_have_text(first["id"])
    page.keyboard.press("j")
    expect(title).to_have_text(second["id"])

    project = page.locator('#direct-expense-form [name="project"]')
    project.fill("Unsaved coding")
    page.keyboard.press("j")
    expect(title).to_have_text(second["id"])
    expect(page.locator("dialog.confirm-dialog")).to_have_count(0)
    page.locator("#review-drawer-title").click()
    page.keyboard.press("j")
    dialog = page.locator("dialog.confirm-dialog")
    expect(dialog).to_be_visible()
    page.keyboard.press("k")
    expect(dialog).to_be_visible()
    expect(title).to_have_text(second["id"])
    answer_confirm(page, accept=False)
    expect(title).to_have_text(second["id"])

    following.click()
    answer_confirm(page)
    expect(title).to_have_text(third["id"])
    expect(following).to_be_disabled()
    expect(previous).to_be_enabled()
    page.keyboard.press("k")
    expect(title).to_have_text(second["id"])

    page.locator(".review-summary-row.selected").focus()
    page.keyboard.press("ArrowDown")
    expect(title).to_have_text(third["id"])
    page.locator("#review-drawer-title").click()
    page.keyboard.press("f")
    expect(page.locator("#work-filter")).to_be_focused()
