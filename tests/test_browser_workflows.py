"""Optional real-browser regressions. Install Playwright + Chromium to run.

Every test serves a fresh temporary database on a random loopback port.
"""

import io
import threading
import uuid

import pytest
from pypdf import PdfWriter
from werkzeug.serving import make_server

from cash.web.app import create_app


playwright = pytest.importorskip("playwright.sync_api")
expect = playwright.expect


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def web(browser, tmp_path):
    app = create_app(tmp_path / "data", admin_password="test", testing=True)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = browser.new_context(base_url=f"http://127.0.0.1:{server.server_port}",
                                  viewport={"width": 1440, "height": 1000})
    csrf = context.request.post("/api/login", data={"password": "test"}).json()["data"]["csrf"]
    context.set_extra_http_headers({"X-CSRF": csrf})

    def post(path, **data):
        response = context.request.post(path, data={"submission_id": str(uuid.uuid4()), **data})
        assert response.ok, response.text()
        return response.json()["data"]

    post("/api/entries/opening", date="2026-09-01", usd="2000", mxn="15000",
         assigned_rate_milli=15000)
    post("/api/settings/classes", class_code="7.5", name="Infant Orphanage")
    post("/api/settings/expense-accounts", account_id="1000", name="Petty Cash Fund")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        yield page, post, tmp_path
        assert not errors
    finally:
        context.close()
        server.shutdown()
        thread.join(timeout=5)


def prepare(post, kind="direct_expense", recipient="Vendor"):
    amounts = ({"receipt_usd": "20", "receipt_mxn": "150"}
               if kind == "direct_expense" else {"usd": "50", "mxn": "100"})
    return post("/api/vouchers/prepare", type=kind, date="2026-09-02",
                recipient=recipient, purpose="Supplies", **amounts)


def upload(page, button, name):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": name})
    buffer = io.BytesIO()
    writer.write(buffer)
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name=button, exact=True).click()
    chooser.value.set_files({"name": name, "mimeType": "application/pdf",
                             "buffer": buffer.getvalue()})


def assert_no_drawer_overflow(page):
    assert page.locator("#review-drawer").evaluate(
        "el => el.scrollWidth <= el.clientWidth + 1")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


@pytest.mark.parametrize("print_voucher", [False, True])
def test_new_transaction_blank_payment_and_save_destination(web, print_voucher):
    page, _, _ = web
    page.goto("/transactions/new")
    page.get_by_label("Vendor", exact=True).fill("Contractor")
    page.get_by_label("Purpose", exact=True).fill("Repairs")
    page.get_by_label("Receipt value, USD portion", exact=True).fill("20")
    page.get_by_label("Receipt value, MXN portion", exact=True).fill("150")
    expect(page.locator("#direct-net")).to_have_text("USD 20.00 · MXN 150.00")
    page.get_by_label("USD handed to vendor", exact=True).fill("40")
    page.get_by_label("MXN change returned", exact=True).fill("100")
    expect(page.locator("#direct-net")).to_have_text("USD 40.00 · MXN -100.00")
    expect(page.locator("#direct-net")).not_to_have_class("invalid")
    page.get_by_label("USD handed to vendor", exact=True).fill("")
    page.get_by_label("MXN change returned", exact=True).fill("")
    page.locator("#prep-submit" if print_voucher else "#prep-save").click()
    if print_voucher:
        page.wait_for_url("**/voucher/*/print")
    else:
        page.wait_for_url("**/review?item=direct_expense:*")
        expect(page.locator("#review-drawer")).to_be_visible()
    rows = page.request.get("/api/ledger").json()["data"]["transactions"]
    expenses = [row for row in rows if row["txn_type"] == "expense"]
    assert len(expenses) == 1
    assert (expenses[0]["usd_cents"], expenses[0]["mxn_centavos"]) == (2000, 15000)


@pytest.mark.parametrize("kind", ["signout", "exchange", "withdrawal"])
def test_other_transaction_types_still_save_without_printing(web, kind):
    page, _, _ = web
    page.goto("/transactions/new")
    page.locator(".type-picker label").filter(
        has=page.locator(f'[name="type"][value="{kind}"]')).click()
    if kind == "signout":
        page.get_by_label("Person receiving cash", exact=True).fill("Ana")
        page.get_by_label("Purpose", exact=True).fill("Supplies")
        page.locator('#simple-amount-fields [name="usd"]').fill("50")
        expect(page.locator("#prep-submit")).to_be_visible()
    elif kind == "exchange":
        page.get_by_label("USD exchanged", exact=True).fill("10")
        page.get_by_label("MXN received", exact=True).fill("150")
        expect(page.locator("#prep-submit")).to_be_hidden()
    else:
        page.get_by_label("USD received", exact=True).fill("50")
        expect(page.locator("#prep-submit")).to_be_hidden()
    page.locator("#prep-save").click()
    page.wait_for_url("**/signouts/*" if kind == "signout" else
                      "**/review?item=exchange:*" if kind == "exchange" else
                      "**/ledger?txn=*")


@pytest.mark.parametrize("width", [1440, 390])
def test_exchange_review_requires_receipt_from_document_queue(web, width):
    page, post, _ = web
    exchange = post("/api/entries/exchange", date="2026-09-02",
                    source_account="SAFE", usd="10", mxn="150")
    txn_id = exchange["txn_ids"][0]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto("/review")
    if width < 1200:
        page.locator("#open-documents").click()
    upload(page, "Browse files", "exchange-receipt.pdf")
    expect(page.locator("#inbox-count")).to_have_text("1")
    page.goto(f"/transactions/{txn_id}")
    page.wait_for_url(f"**/review?item=exchange:{txn_id}")
    save = page.get_by_role("button", name="Save & review next", exact=True)
    save.click()
    expect(page.locator(".form-status")).to_have_text(
        "Attach the exchange receipt before saving.")
    if width < 1200:
        page.get_by_role("button", name="Choose from documents", exact=True).click()
    page.locator(".evidence-card").click()
    if width >= 1200:
        page.get_by_role("button", name="Use opened", exact=False).click()
    expect(page.locator(".attached-document-preview")).to_be_visible()
    page.get_by_role("button", name="Remove", exact=True).click()
    expect(page.locator(".attached-document-preview")).to_have_count(0)
    if width < 1200:
        page.get_by_role("button", name="Choose from documents", exact=True).click()
        page.locator(".evidence-card").click()
    else:
        page.get_by_role("button", name="Use opened", exact=False).click()
    assert_no_drawer_overflow(page)
    save.click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    expect(page.locator("#inbox-count")).to_have_text("0")
    detail = page.request.get(f"/api/ledger/{txn_id}").json()["data"]
    assert detail["transaction"]["incomplete"] is False
    assert detail["documents"][0]["doc_type"] == "exchange_receipt"


@pytest.mark.parametrize("width", [1440, 390])
def test_expense_coding_saves_before_paperwork_and_survives_reload(web, width):
    page, post, _ = web
    voucher = prepare(post)
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    account = page.locator('#direct-expense-form [name="expense_account"]')
    class_code = page.locator('#direct-expense-form [name="class_code"]')
    expect(account).to_have_value("")
    expect(class_code).to_have_value("")
    account.select_option("1000")
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    page.reload()
    expect(account).to_have_value("1000")
    expect(class_code).to_have_value("")
    class_code.select_option("7.5")
    page.get_by_role("button", name="Add split", exact=True).click()
    expect(account.nth(1)).to_have_value("")
    expect(class_code.nth(1)).to_have_value("")
    page.get_by_role("button", name="Remove split", exact=True).nth(1).click()
    page.locator('#direct-expense-form [name="project"]').fill("School supplies")
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    page.reload()
    expect(account).to_have_value("1000")
    expect(class_code).to_have_value("7.5")
    expect(page.locator('#direct-expense-form [name="project"]')).to_have_value("School supplies")
    expect(page.locator(".review-summary-row .badge")).to_have_text(["voucher needed"])
    expect(page.locator("#review-drawer-identity .badge")).to_have_text(["voucher needed"])
    pending = page.request.get(f"/api/ledger/{voucher['txn_ids'][0]}").json()["data"]
    assert pending["transaction"]["incomplete"] is True
    assert pending["documents"] == []
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Attach the signed voucher")
    upload(page, "Upload voucher", "signed-voucher.pdf")
    expect(page.locator(".attached-document-preview")).to_be_visible()
    page.get_by_label("Also use voucher as the receipt", exact=True).check()
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    page.reload()
    expect(page.locator(".attached-document-preview")).to_be_visible()
    expect(page.get_by_label("Also use voucher as the receipt", exact=True)).to_be_checked()
    assert_no_drawer_overflow(page)
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    rows = page.request.get("/api/ledger").json()["data"]["transactions"]
    expense = next(row for row in rows if row["txn_type"] == "expense"
                   and row["posting_state"] == "active")
    assert (expense["to_account"], expense["class_code"], expense["project"]) == (
        "1000", "7.5", "School supplies")
    assert expense["incomplete"] is False


def test_completed_expense_ledger_requires_class(web):
    page, post, _ = web
    voucher = prepare(post)
    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    page.locator('#direct-expense-form [name="expense_account"]').select_option("1000")
    page.locator('#direct-expense-form [name="class_code"]').select_option("7.5")
    upload(page, "Upload voucher", "signed-voucher.pdf")
    expect(page.locator(".attached-document-preview")).to_be_visible()
    page.get_by_label("Also use voucher as the receipt", exact=True).check()
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    rows = page.request.get("/api/ledger").json()["data"]["transactions"]
    expense = next(row for row in rows if row["txn_type"] == "expense"
                   and row["posting_state"] == "active")
    page.goto(f"/ledger?txn={expense['txn_id']}")
    class_field = page.get_by_role("combobox", name="Class code", exact=True)
    expect(class_field).to_have_attribute("required", "")
    expect(class_field.locator("option").filter(has_text="No class")).to_have_count(0)
    class_field.select_option("")
    page.get_by_label("Reason for an important change", exact=True).fill("Correct class")
    page.get_by_role("button", name="Save changes", exact=True).click()
    assert class_field.evaluate("el => el.validity.valueMissing")
    detail = page.request.get(f"/api/ledger/{expense['txn_id']}").json()["data"]
    assert detail["transaction"]["class_code"] == expense["class_code"]
    assert detail["transaction"]["version"] == expense["version"]


@pytest.mark.parametrize("width", [1440, 1280, 390])
def test_drawer_explicit_receipt_choice_preserves_allocations_and_layout(web, width):
    page, post, artifacts = web
    voucher = prepare(post)
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    choice = page.get_by_label("Also use voucher as the receipt", exact=True)
    expect(choice).not_to_be_checked()
    project = page.locator('#direct-expense-form [name="project"]')
    page.locator('#direct-expense-form [name="expense_account"]').select_option("1000")
    page.locator('#direct-expense-form [name="class_code"]').select_option("7.5")
    project.fill("Clinic repairs")
    upload(page, "Upload voucher", "signed-voucher.pdf")
    expect(page.locator('[data-document-slot="primary"] .attached-document-preview')).to_be_visible()
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Attach a receipt")
    expect(project).to_have_value("Clinic repairs")
    choice.check()
    expect(page.get_by_role("button", name="Upload receipt", exact=True)).to_be_hidden()
    choice.uncheck()
    upload(page, "Upload receipt", "separate-receipt.pdf")
    expect(page.locator('.direct-receipt-editor[data-document-id] .attached-document-preview')).to_be_visible()
    expect(project).to_have_value("Clinic repairs")
    assert_no_drawer_overflow(page)
    page.screenshot(path=str(artifacts / f"drawer-{width}.png"))
    page.locator(".direct-receipt-editor .unstage-receipt").click()
    expect(choice).not_to_be_checked()
    choice.check()
    page.locator('#direct-expense-form [name="expense_account"]').select_option("1000")
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    rows = page.request.get("/api/ledger").json()["data"]["transactions"]
    expense = next(row for row in rows if row["txn_type"] == "expense"
                   and row["posting_state"] == "active")
    assert expense["to_account"] == "1000"
    assert expense["class_code"] == "7.5"
    assert expense["project"] == "Clinic repairs"
    assert expense["receipt_id"] is None


def test_signout_staged_filing_does_not_leak_into_another_row(web):
    page, post, _ = web
    first = prepare(post, "signout", "Ana")
    second = prepare(post, "signout", "Ben")
    page.goto(f"/review?item=signout:{first['voucher_id']}")
    upload(page, "Upload voucher", "ana-signed-voucher.pdf")
    choice = page.get_by_label("Also use voucher as the receipt", exact=True)
    choice.check()
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(choice).to_be_checked()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Close transaction workspace").click()
    page.locator(".review-summary-row").filter(has_text=second["voucher_id"]).click()
    expect(page.locator("#review-drawer-title")).to_have_text(second["voucher_id"])
    expect(page.get_by_role("button", name="File voucher", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Upload voucher", exact=True)).to_be_visible()


def test_multiple_separate_receipts_can_be_completed_in_the_drawer(web):
    page, post, _ = web
    voucher = prepare(post)
    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    upload(page, "Upload receipt", "first-receipt.pdf")
    expect(page.locator('.direct-receipt-editor[data-document-id]')).to_have_count(1)
    upload(page, "Upload receipt", "second-receipt.pdf")
    editors = page.locator('.direct-receipt-editor[data-document-id]')
    expect(editors).to_have_count(2)
    for index in range(2):
        editor = editors.nth(index)
        expect(editor.locator('[name="expense_account"]')).to_have_value("")
        expect(editor.locator('[name="class_code"]')).to_have_value("")
        editor.locator('[name="usd"]').fill("10")
        editor.locator('[name="mxn"]').fill("75")
        editor.locator('[name="face_usd"]').fill("10")
        editor.locator('[name="face_mxn"]').fill("75")
        editor.locator('[name="expense_account"]').select_option("1000")
        editor.locator('[name="class_code"]').select_option("7.5")
        editor.locator('[name="project"]').fill(f"Project {index + 1}")
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    page.reload()
    expect(editors).to_have_count(2)
    for index in range(2):
        expect(editors.nth(index).locator('[name="project"]')).to_have_value(f"Project {index + 1}")
        expect(editors.nth(index).locator('[name="expense_account"]')).to_have_value("1000")
        expect(editors.nth(index).locator('[name="class_code"]')).to_have_value("7.5")
    upload(page, "Upload voucher", "signed-voucher.pdf")
    expect(page.locator('[data-document-slot="primary"] .attached-document-preview')).to_be_visible()
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    rows = page.request.get("/api/ledger").json()["data"]["transactions"]
    expenses = [row for row in rows if row["txn_type"] == "expense"
                and row["posting_state"] == "active"]
    assert len(expenses) == 2
    assert len({row["receipt_id"] for row in expenses}) == 2
    assert all(row["receipt_id"] for row in expenses)
    assert {row["project"] for row in expenses} == {"Project 1", "Project 2"}
