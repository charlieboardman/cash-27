"""Receipt attachments must survive refresh before the expense is completed."""

import io

import pytest
from pypdf import PdfWriter

from test_browser_workflows import (async_web, choose_from_inbox, expect,
                                   prepare, upload)


def filed_direct_expense(page, post):
    vid = prepare(post)["voucher_id"]
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    buffer = io.BytesIO()
    writer.write(buffer)
    captured = page.request.post("/api/documents/capture-merge", multipart={
        "files": {"name": "signed-voucher.pdf", "mimeType": "application/pdf",
                  "buffer": buffer.getvalue()}}).json()["data"]
    post(f"/api/review/direct-expense/{vid}/attach-voucher", expected_version=1,
         document_id=captured["document_id"])
    return vid


def detail(page, vid):
    return page.request.get(f"/api/review/item/direct_expense/{vid}").json()["data"]


def saved(page):
    expect(page.locator("#direct-expense-form .form-status")).to_have_text("All changes saved")
    expect(page.get_by_role("button", name="Save progress", exact=True)).to_be_enabled()


def reload_without_warning(page):
    dialogs = []

    def accept(dialog):
        dialogs.append(dialog.type)
        dialog.accept()

    page.on("dialog", accept)
    page.reload()
    expect(page.locator("#direct-expense-form")).to_be_visible()
    page.remove_listener("dialog", accept)
    assert dialogs == []


@pytest.mark.parametrize("width,source", [
    (1440, "inbox"), (390, "upload"), (1440, "drop"), (1440, "search")])
def test_receipt_attachment_saves_before_coding_and_leaves_inbox(web, width, source):
    page, post, _ = web
    vid = filed_direct_expense(page, post)
    ledger = page.request.get("/api/ledger").json()["data"]["transactions"]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/review?item=direct_expense:{vid}")
    if source == "upload":
        upload(page, "Upload receipt", "store-receipt.pdf")
    else:
        upload(page, "Upload", "store-receipt.pdf")
        expect(page.locator(".document-preview-title > strong")).to_have_text("store-receipt.pdf")
        if source == "search":
            page.locator("#attach-to-transaction").click()
            page.get_by_role("button", name="As receipt", exact=True).click()
        else:
            page.get_by_role("button", name="Back to documents").click()
            if source == "inbox":
                choose_from_inbox(page, "receipt")
                page.locator(".evidence-card").click()
            else:
                page.locator(".evidence-card").drag_to(
                    page.locator('.doc-row[data-document-slot="support"]'))
    saved(page)
    expect(page.locator("#inbox-count")).to_have_text("0")
    expect(page.locator("#mobile-inbox-count")).to_have_text("0")
    expect(page.locator('[data-nav-count="inbox"]')).to_be_hidden()
    expect(page.locator(".evidence-card")).to_have_count(0)
    expect(page.locator(".staged-receipt .doc-name")).to_have_text("store-receipt.pdf")
    draft = detail(page, vid)["review_draft"]
    assert len(draft["receipt_document_ids"]) == 1
    assert draft["rows"][0]["class_code"] is None
    assert detail(page, vid)["voucher"]["status"] == "pending"
    assert page.request.get("/api/documents/inbox").json()["data"]["documents"] == []
    assert page.request.get("/api/nav-counts").json()["data"]["inbox"] == 0
    assert page.request.get("/api/ledger").json()["data"]["transactions"] == ledger
    reload_without_warning(page)
    expect(page.locator(".staged-receipt .doc-name")).to_have_text("store-receipt.pdf")
    expect(page.locator("#inbox-count")).to_have_text("0")


def test_receipt_removal_and_undo_survive_refresh(web):
    page, post, _ = web
    page.context.add_init_script("window.cashUndoMs = 20000")
    vid = filed_direct_expense(page, post)
    page.goto(f"/review?item=direct_expense:{vid}")
    upload(page, "Upload receipt", "receipt.pdf")
    saved(page)
    reload_without_warning(page)
    page.get_by_role("button", name="Remove Receipt 1", exact=True).click()
    saved(page)
    expect(page.locator("#inbox-count")).to_have_text("1")
    expect(page.locator(".evidence-card")).to_contain_text("receipt.pdf")
    assert detail(page, vid)["review_draft"]["receipt_document_ids"] == []
    page.locator(".undo-toast").get_by_role("button", name="Undo").click()
    saved(page)
    expect(page.locator("#inbox-count")).to_have_text("0")
    reload_without_warning(page)
    expect(page.locator(".staged-receipt .doc-name")).to_have_text("receipt.pdf")
    page.get_by_role("button", name="Remove Receipt 1", exact=True).click()
    saved(page)
    reload_without_warning(page)
    expect(page.locator(".staged-receipt")).to_have_count(0)
    expect(page.locator(".evidence-card")).to_contain_text("receipt.pdf")


@pytest.mark.parametrize("failure", ["rejected", "lost_response"])
def test_receipt_save_failure_keeps_changes_and_can_be_retried(web, failure):
    page, post, _ = web
    vid = filed_direct_expense(page, post)
    page.goto(f"/review?item=direct_expense:{vid}")
    requests = []

    def fail_once(route):
        requests.append(route.request.post_data_json)
        if len(requests) > 1:
            return route.continue_()
        if failure == "lost_response":
            route.fetch()
            return route.abort()
        route.fulfill(status=409, json={
            "ok": False, "error": {"message": "Attachment save failed"}})

    page.route(f"**/api/review/direct-expense/{vid}/draft", fail_once)
    upload(page, "Upload receipt", "receipt.pdf")
    expect(page.locator("#review-message.danger")).to_be_visible()
    expect(page.locator(".staged-receipt .doc-name")).to_have_text("receipt.pdf")
    assert page.evaluate("""() => {
        const event = new Event('beforeunload', {cancelable: true});
        window.dispatchEvent(event);
        return event.defaultPrevented;
    }""")
    if failure == "rejected":
        assert detail(page, vid)["review_draft"] is None
        expect(page.locator("#inbox-count")).to_have_text("1")
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    expect(page.locator("#inbox-count")).to_have_text("0")
    if failure == "lost_response":
        assert requests[0]["submission_id"] == requests[1]["submission_id"]
    assert len(detail(page, vid)["review_draft"]["receipt_document_ids"]) == 1
    reload_without_warning(page)
    expect(page.locator(".staged-receipt .doc-name")).to_have_text("receipt.pdf")


def test_edit_during_receipt_save_remains_unsaved_until_explicit_save(web):
    page, post, _ = web
    vid = filed_direct_expense(page, post)
    page.goto(f"/review?item=direct_expense:{vid}")
    held = []

    def hold_response(route):
        held.append((route, route.fetch()))
        page.evaluate("window.receiptSaveCommitted = true")

    endpoint = f"**/api/review/direct-expense/{vid}/draft"
    page.route(endpoint, hold_response)
    upload(page, "Upload receipt", "receipt.pdf")
    expect(page.locator("#direct-expense-form .form-status")).to_have_text("Saving…")
    page.wait_for_function("window.receiptSaveCommitted === true")
    project = page.locator('#direct-expense-form [name="project"]')
    project.fill("Newer edit")
    route, response = held[0]
    route.fulfill(response=response)
    expect(page.get_by_role("button", name="Save progress", exact=True)).to_be_enabled()
    expect(project).to_have_value("Newer edit")
    expect(page.locator("#direct-expense-form .form-status")).to_be_empty()
    assert detail(page, vid)["review_draft"]["rows"][0]["project"] == ""
    assert page.evaluate("state.dirty")
    page.unroute(endpoint)
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    reload_without_warning(page)
    expect(project).to_have_value("Newer edit")
    expect(page.locator(".staged-receipt")).to_have_count(1)


def test_receipt_upload_saves_when_processing_finishes(async_web, monkeypatch):
    from cash.document_worker import process_next
    from cash.web import qr_capture

    page, post, artifacts = async_web
    vid = prepare(post)["voucher_id"]
    page.goto(f"/review?item=direct_expense:{vid}")
    upload(page, "Upload receipt", "processing-receipt.pdf")
    expect(page.locator("#detail .document-processing")).to_be_visible()
    page.locator('#direct-expense-form [name="project"]').fill("Keep while processing")
    monkeypatch.setattr(qr_capture, "recognize", lambda *_: {"status": "no_code", "ids": []})
    assert process_next(artifacts / "data")
    saved(page)
    expect(page.locator("#detail .document-processing")).to_have_count(0)
    expect(page.locator("#inbox-count")).to_have_text("0")
    receipt = detail(page, vid)["documents"][0]
    reload_without_warning(page)
    expect(page.locator(".staged-receipt .doc-name")).to_have_text(receipt["display_name"])
    expect(page.locator(".staged-receipt .doc-open")).to_have_attribute(
        "data-document-id", receipt["document_id"])
    expect(page.locator('#direct-expense-form [name="project"]')).to_have_value("Keep while processing")


def test_multiple_receipts_wait_for_the_previous_save(web):
    page, post, _ = web
    vid = filed_direct_expense(page, post)
    page.goto(f"/review?item=direct_expense:{vid}")
    held, requests = [], []

    def hold_first(route):
        requests.append(route.request.post_data_json)
        if len(requests) == 1:
            held.append((route, route.fetch()))
            page.evaluate("window.receiptSaveCommitted = true")
        else:
            route.continue_()

    page.route(f"**/api/review/direct-expense/{vid}/draft", hold_first)
    upload(page, "Upload receipt", "first.pdf")
    page.wait_for_function("window.receiptSaveCommitted === true")
    upload(page, "Upload receipt", "second.pdf")
    expect(page.locator(".staged-receipt")).to_have_count(2)
    assert len(requests) == 1
    route, response = held[0]
    route.fulfill(response=response)
    saved(page)
    assert len(requests) == 2
    assert requests[1]["expected_version"] == requests[0]["expected_version"] + 1
    reload_without_warning(page)
    expect(page.locator(".staged-receipt .doc-name")).to_have_text(["first.pdf", "second.pdf"])
    expect(page.locator("#inbox-count")).to_have_text("0")
    # Completing review uses the version returned by the last attachment save.
    page.locator('#direct-expense-form [name="class_code"]').select_option("7.5")
    page.locator('#direct-expense-form [name="expense_account"]').select_option("1000")
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    assert detail(page, vid)["voucher"]["status"] == "complete"
    assert detail(page, vid)["review_draft"] is None


def test_switching_records_waits_for_receipt_save(web):
    page, post, _ = web
    vid = filed_direct_expense(page, post)
    other = prepare(post)["voucher_id"]
    page.goto(f"/review?item=direct_expense:{vid}")
    held = []

    def hold_response(route):
        held.append((route, route.fetch()))
        page.evaluate("window.receiptSaveCommitted = true")

    page.route(f"**/api/review/direct-expense/{vid}/draft", hold_response)
    upload(page, "Upload receipt", "receipt.pdf")
    page.wait_for_function("window.receiptSaveCommitted === true")
    page.locator(".review-summary-row").filter(has_text=other).click()
    expect(page.locator("#review-drawer-title")).to_have_text(vid)
    expect(page.locator("dialog.confirm-dialog")).to_have_count(0)
    route, response = held[0]
    route.fulfill(response=response)
    expect(page.locator("#review-drawer-title")).to_have_text(other)
    page.locator(".review-summary-row").filter(has_text=vid).click()
    expect(page.locator(".staged-receipt .doc-name")).to_have_text("receipt.pdf")
    expect(page.locator("#inbox-count")).to_have_text("0")
