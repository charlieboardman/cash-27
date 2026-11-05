"""Prepared settlement scans attach from Review before a ledger addendum exists."""

import io

import pytest
from pypdf import PdfWriter

from cash.db import connect
from cash.web.qr_capture import record_match
from test_browser_workflows import expect, open_documents, signed_signout


def capture(page, name):
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.add_metadata({"/Title": name})
    contents = io.BytesIO()
    writer.write(contents)
    response = page.request.post("/api/documents/capture-merge", multipart={"files": {
        "name": name, "mimeType": "application/pdf", "buffer": contents.getvalue()}})
    assert response.ok, response.text()
    return response.json()["data"]["document_id"]


@pytest.mark.parametrize("width,kind,source", [
    (1440, "return", "suggested"), (390, "return", "suggested"),
    (1440, "positive", "suggested"), (1440, "return", "search"),
    (390, "positive", "unmatched")])
def test_review_attaches_settlement_from_suggestion_or_search(web, width, kind, source):
    page, post, artifacts = web
    vid = signed_signout(page, post)["voucher_id"]
    receipt = capture(page, "expense.pdf")
    post(f"/{vid}/api/rows", date="2026-09-03", purpose="Supplies",
         usd="35" if kind == "return" else "65", mxn="100", document_id=receipt,
         class_code="7.5", expense_account="1000")
    post(f"/{vid}/api/report/submit-calculated")
    page.set_viewport_size({"width": width, "height": 1000})
    # Review is already open when another tab prepares the paperwork and the
    # scanner delivers its signed copy.
    page.goto("/review")
    expect(page.locator("#queue-summary")).to_have_text("0 transactions to review")
    post(f"/{vid}/api/settlement/prepare/{kind}", date="2026-09-04")
    report = page.request.get(f"/{vid}/api/report").json()["data"]
    paper = report["cash_rows"]["stages"][0]["paper_id"]
    doc = capture(page, "signed-addendum.pdf")
    if source != "unmatched":
        with connect(artifacts / "data" / "cash.db") as conn:
            match = record_match(conn, doc, {"status": "detected", "ids": [paper]})
            assert match["status"] == "matched"
    before = page.request.get("/api/ledger").json()["data"]["transactions"]
    open_documents(page)
    card = page.locator(f'#evidence-list [data-document-id="{doc}"] .evidence-card')
    expect(card).to_be_visible(timeout=8000)
    card.click()
    button = page.locator("#confirm-suggested-attachment")
    if source == "unmatched":
        expect(button).to_have_count(0)
    else:
        expect(button).to_have_text(f"Attach to {paper}")
    if source in ("search", "unmatched"):
        page.locator("#attach-to-transaction").click()
        search = page.locator("#document-transaction-search")
        search.fill("Vendor 15.00")
        target = page.locator(".document-transaction-result").filter(has_text=paper)
        expect(target).to_have_count(1)
        search.fill(paper)
        expect(target).to_have_count(1)
        button = target.get_by_role("button", name="Attach", exact=True)
    with page.expect_response(f"**/{vid}/api/settlement/file/{kind}") as response:
        button.click()
    assert response.value.ok, response.value.text()
    expect(page.locator("#inbox-count")).to_have_text("0")
    expect(page.locator(".evidence-card")).to_have_count(0)
    assert page.request.get("/api/ledger").json()["data"]["transactions"] == before
    page.reload()
    expect(page.locator("#inbox-count")).to_have_text("0")
    expect(page.locator("#queue-summary")).to_have_text("0 transactions to review")
    report = page.request.get(f"/{vid}/api/report").json()["data"]
    assert report["status"] == "submitted"
    assert report["cash_rows"]["stages"][0]["document_id"] == doc
    assert report["cash_rows"]["stages"][0]["status"] == "filed"
    page.goto(f"/{vid}")
    page.locator(f'[data-cash-row="{kind}"]').click()
    expect(page.locator("#er-tray .er-row-document").first).to_be_visible()
