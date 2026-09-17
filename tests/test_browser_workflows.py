"""Optional real-browser regressions. Install Playwright + Chromium to run.

Every test serves a fresh temporary database on a random loopback port.
"""

import io
import threading
import uuid

import pytest
from pypdf import PdfReader, PdfWriter
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
    amounts = ({"usd": "20", "mxn": "150"}
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


def add_report_receipt(page, usd="", mxn="", notes="Supplies", language="en"):
    page.locator("#report-add").click()
    upload(page, "Elegir archivos" if language == "es" else "Choose files", "receipt.pdf")
    page.locator("#report-attach").click()
    card = page.locator(".report-receipt").last
    expect(card).to_be_visible()
    card.locator('[name="usd"]').fill(usd)
    card.locator('[name="mxn"]').fill(mxn)
    card.locator('[name="notes"]').fill(notes)
    return card


def submit_report(page, cash_usd="0", cash_mxn="0"):
    page.locator('#report-settlement [name="cash_usd"]').fill(cash_usd)
    page.locator('#report-settlement [name="cash_mxn"]').fill(cash_mxn)
    page.locator("#report-submit").click()
    expect(page.locator("#report-submit")).to_be_hidden()


@pytest.mark.parametrize("width", [1440, 390])
def test_signee_language_toggle_preserves_receipt_and_login_preference(web, width):
    page, post, artifacts = web
    vid = prepare(post, kind="signout", recipient="Ana")["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/{vid}")
    card = add_report_receipt(page, mxn="1200", notes="Supplies")
    page.get_by_role("button", name="Español", exact=True).click()
    expect(page.locator("html")).to_have_attribute("lang", "es")
    expect(card.locator('[name="notes"]')).to_have_value("Supplies")
    expect(page.locator("#report-save-state")).to_have_text("Todos los cambios guardados")
    expect(page.locator("#report-submit")).to_have_text("Enviar informe de gastos")
    page.locator("#report-add").click()
    upload(page, "Elegir archivos", "another.pdf")
    expect(page.locator('[data-picker-role="count"]')).to_have_text("1 página")
    expect(page.get_by_role("button", name="Eliminar página 1", exact=True)).to_be_visible()
    page.locator("#report-cancel-upload").click()
    submit_report(page)
    expect(page.locator(".report-receipt .status-pill")).to_have_text("Enviado a revisión")
    page.reload()
    expect(page.locator("html")).to_have_attribute("lang", "es")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / f"signee-spanish-{width}.png"), full_page=True)
    page.goto(f"/signouts/{vid}")
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.locator("[data-signee-language]")).to_have_count(0)
    page.context.clear_cookies()
    page.goto(f"/{vid}")
    page.wait_for_url(lambda url: "/user-login?next=" in url)
    expect(page.get_by_role("heading", name="Abrir página de recibos")).to_be_visible()
    page.get_by_label("Contraseña compartida", exact=True).fill("wrong")
    page.get_by_role("button", name="Continuar", exact=True).click()
    expect(page.locator("#login-status")).to_have_text("Contraseña incorrecta")
    page.get_by_label("Contraseña compartida", exact=True).fill("test")
    page.get_by_role("button", name="Continuar", exact=True).click()
    page.wait_for_url(f"**/{vid}")
    expect(page.locator("html")).to_have_attribute("lang", "es")
    expect(page.locator(".report-receipt .status-pill")).to_have_text("Enviado a revisión")


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("language", ["en", "es"])
def test_employee_receipt_editor_and_cards(web, width, language):
    page, post, artifacts = web
    vid = prepare(post, kind="signout")["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/{vid}")
    expect(page.locator("#report-add")).to_be_visible()
    assert page.evaluate("scrollY") == 0
    if language == "es":
        page.get_by_role("button", name="Español", exact=True).click()
    expect(page.locator('[name="record_cash_payment"]')).to_have_count(0)
    expect(page.locator('[name="face_mxn"]')).to_have_count(0)
    card=add_report_receipt(page,mxn="40",language=language)
    expect(page.locator("#report-balance")).to_have_text("USD 50.00 · MXN 60.00")
    card.locator('[name="mxn"]').fill("45")
    expect(page.locator("#report-balance")).to_have_text("USD 50.00 · MXN 55.00")
    expect(page.locator("#report-save-state")).to_have_text("Todos los cambios guardados" if language == "es" else "All changes saved")
    page.reload()
    expect(page.locator('.report-receipt [name="mxn"]')).to_have_value("45.00")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / f"employee-receipts-{language}-{width}.png"), full_page=True)
    submit_report(page,cash_usd="50",cash_mxn="55")
    expect(page.locator('.report-receipt [name="notes"]')).to_be_disabled()
    page.locator('[data-action="view"]').click()
    expect(page.locator("#report-document iframe")).to_be_visible()
    page.locator("#report-document-close").click()
    expect(page.locator("#report-document")).not_to_be_visible()


@pytest.mark.parametrize("usd,mxn,face_currency,face,expected_usd", [
    ("50", "", None, None, 50),
    ("10", "", None, None, 10),
    ("10", "-60", "mxn", "100", 6.25),
    ("-2", "100", "usd", "4", 100 / 15 - 2),
])
def test_signed_employee_cash_through_ledger(web, usd, mxn, face_currency, face, expected_usd):
    page, post, artifacts = web
    vid = prepare(post, kind="signout")["voucher_id"]
    page.goto(f"/{vid}")
    card=add_report_receipt(page,usd=usd,mxn=mxn)
    expect(page.locator('[name="face_mxn"]')).to_have_count(0)
    if usd == "50":
        card.locator('[name="usd"]').fill("50.001")
        expect(page.locator("#report-message")).to_have_text("Enter amounts with at most two decimal places.")
        card.locator('[name="usd"]').fill("50")
    submit_report(page)
    saved = page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    assert saved["usd_paid_cents"] - saved["usd_change_cents"] == round(float(usd or 0) * 100)
    assert saved["mxn_paid_centavos"] - saved["mxn_change_centavos"] == round(float(mxn or 0) * 100)
    assert saved["face_usd_cents"] is saved["face_mxn_centavos"] is None
    pid = saved["package_id"]
    page.goto(f"/review?item=package:{pid}")
    page.locator('#claim-review-form [name="expense_account"]').select_option("1000")
    page.locator('#claim-review-form [name="class_code"]').select_option("7.5")
    if face_currency:
        page.locator(f'#claim-review-form [name="face_{face_currency}"]').fill(face)
    page.get_by_role("button", name="Save & mark checked", exact=True).click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Approve all 1 receipts", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    rows = page.request.get("/api/ledger").json()["data"]["transactions"]
    expense = next(row for row in rows if row["txn_type"] == "expense" and row["voucher_id"] == vid)
    assert expense["usd_cents"] == round(float(usd or 0) * 100)
    assert expense["mxn_centavos"] == round(float(mxn or 0) * 100)
    assert float(expense["calc_usd"]) == pytest.approx(expected_usd)


@pytest.mark.parametrize("mode", ["cash", "receipt_currency"])
def test_signed_editor_preserves_existing_receipt_facts(web, mode):
    page, post, _ = web
    vid = prepare(post, kind="signout")["voucher_id"]
    page.goto(f"/{vid}")
    add_report_receipt(page,usd="10")
    expect(page.locator("#report-save-state")).to_have_text("All changes saved")
    claim = page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    legacy = {"submission_id": str(uuid.uuid4()), "expected_content_version": claim["content_version"],
              "document_id": claim["document_id"], "date": claim["claim_date"],
              "vendor": "Store", "description": "Old receipt", "payment_mode": mode,
              "usd_paid": "20", "usd_change": "10", "face_mxn": "160"}
    response = page.request.patch(f"/{vid}/claims/{claim['claim_id']}", data=legacy)
    assert response.ok, response.text()
    before = page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    page.reload()
    page.locator('.report-receipt [name="notes"]').fill("Corrected description")
    expect(page.locator("#report-save-state")).to_have_text("All changes saved")
    after = page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    for field in ("usd_paid_cents", "usd_change_cents", "mxn_paid_centavos",
                  "mxn_change_centavos", "face_usd_cents", "face_mxn_centavos", "payment_mode"):
        assert after[field] == before[field]
    assert after["description"] == "Corrected description"
    page.locator('.report-receipt [name="usd"]').fill("12")
    page.locator('.report-receipt [name="mxn"]').fill("0")
    expect(page.locator("#report-save-state")).to_have_text("All changes saved")
    after = page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    assert after["payment_mode"] == "cash"
    assert after["usd_paid_cents"] == 1200
    assert after["usd_change_cents"] == after["mxn_change_centavos"] == 0
    assert after["face_mxn_centavos"] == 16000  # Editing cash never silently erases receipt evidence.


def assert_no_drawer_overflow(page):
    assert page.locator("#review-drawer").evaluate(
        "el => el.scrollWidth <= el.clientWidth + 1")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


@pytest.mark.parametrize("print_voucher", [False, True])
def test_new_transaction_authorized_amount_and_save_destination(web, print_voucher):
    page, _, _ = web
    page.goto("/transactions/new")
    page.get_by_label("Vendor", exact=True).fill("Contractor")
    page.get_by_label("Purpose", exact=True).fill("Repairs")
    page.locator('#simple-amount-fields [name="usd"]').fill("20")
    page.locator('#simple-amount-fields [name="mxn"]').fill("150")
    expect(page.get_by_label("Receipt value, USD portion", exact=True)).to_have_count(0)
    if print_voucher:
        with page.expect_popup() as popup:
            page.locator("#prep-submit").click()
        popup.value.wait_for_url("**/voucher/*/print")
        expect(popup.value.locator("#paper")).to_be_visible()
        popup.value.close()
    else:
        page.locator("#prep-save").click()
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
                      f"**/review?item={kind}:*")


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("kind", ["positive", "return"])
@pytest.mark.parametrize("print_addendum", [False, True])
def test_addendum_save_destination_and_print_from_ledger(
        web, width, kind, print_addendum):
    page, post, _ = web
    voucher = prepare(post, kind="signout", recipient="Ana")
    vid = voucher["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/signouts/{vid}")
    form = page.locator("#addendum-form")
    expect(form).to_be_hidden()
    expect(page.locator("#closeout-form")).to_be_hidden()
    expect(page.locator("#signout-movements")).to_be_visible()
    page.locator("#addendum-card > summary").click()
    form.locator(".segmented-control label").filter(
        has=page.locator(f'[name="kind"][value="{kind}"]')).click()
    form.locator('[name="date"]').fill("2026-09-03")
    form.locator('[name="reason"]').fill("Cash adjustment")
    form.locator('[name="usd"]').fill("10")
    form.locator('[name="mxn"]').fill("50")
    print_requests = []
    page.on("request", lambda request: print_requests.append(request.url)
            if request.url.endswith("/print") else None)
    button_name = "Save and print addendum" if print_addendum else "Save"
    with page.expect_response("**/api/addenda/prepare") as response:
        if print_addendum:
            with page.expect_popup() as popup:
                form.get_by_role("button", name=button_name, exact=True).click()
        else:
            form.get_by_role("button", name=button_name, exact=True).click()
    assert response.value.ok, response.value.text()
    saved = page.request.get(f"/api/signouts/{vid}").json()["data"]
    assert len(saved["addenda"]) == 1
    addendum = saved["addenda"][0]
    aid = addendum["addendum_id"]
    print_url = f"/voucher/{vid}/addendum/{aid}/print"
    if print_addendum:
        popup.value.wait_for_url(f"**{print_url}")
        expect(popup.value.locator("#paper")).to_contain_text(aid)
        popup.value.close()
        page.wait_for_url(f"**/signouts/{vid}")
    else:
        expect(page.locator("#signout-movements tr")).to_have_count(2)
        page.locator("#addendum-card > summary").click()
        expect(form.locator('[name="usd"]')).to_have_value("")
        assert page.url.endswith(f"/signouts/{vid}")
        assert print_requests == []
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    with page.expect_popup() as popup:
        page.locator("#signout-movements").get_by_role(
            "link", name=f"Print {aid}", exact=True).click()
    popup.value.wait_for_url(f"**{print_url}")
    expect(popup.value.locator(".usd-box")).to_have_text("USD 10.00")
    expect(popup.value.locator(".mxn-box")).to_have_text("MXN 50.00")
    assert page.url.endswith(f"/signouts/{vid}")
    popup.value.close()

    page.goto("/ledger")
    page.locator("#txns tbody tr").filter(
        has=page.get_by_role("cell", name=f"#{addendum['txn_id']}", exact=True)).click()
    with page.expect_popup() as popup:
        page.locator("#transaction-drawer").get_by_role(
            "link", name="Print addendum", exact=True).click()
    popup.value.wait_for_url(f"**{print_url}")
    expect(popup.value.locator("#paper")).to_contain_text(aid)
    assert "/ledger" in page.url
    popup.value.close()
    saved = page.request.get(f"/api/signouts/{vid}").json()["data"]
    assert len(saved["addenda"]) == 1
    assert saved["addenda"][0]["status"] == "pending"
    assert saved["summary"]["usd_cents"] == (6000 if kind == "positive" else 4000)
    assert saved["summary"]["mxn_centavos"] == (15000 if kind == "positive" else 5000)


@pytest.mark.parametrize("width", [1440, 390])
def test_both_export_formats_on_ledger_and_reports(web, width):
    page, _, _ = web
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto('/ledger')
    expect(page.locator('#ledger-export [name="export_type"]')).to_have_value('cash_ledger')
    for export_type, name in [('cash_ledger', 'cash-ledger'), ('expenses', 'expenses')]:
        page.locator('#ledger-export [name="export_type"]').select_option(export_type)
        for format in ('xlsx', 'csv'):
            page.locator('#ledger-export [name="format"]').select_option(format)
            with page.expect_download() as download:
                page.get_by_role('button', name='Download export', exact=True).click()
            assert download.value.suggested_filename == f'{name}.{format}'
            assert download.value.failure() is None
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.goto('/reports')
    expect(page.locator('#report-type')).to_have_value('expenses')
    page.locator('#report-fy').fill('2027')
    for index, (export_type, name, label) in enumerate([
            ('expenses', 'expenses', 'Expense report'),
            ('cash_ledger', 'cash-ledger', 'Cash ledger')], 1):
        page.locator('#report-type').select_option(export_type)
        for format in ('xlsx', 'csv'):
            page.locator('#report-format').select_option(format)
            with page.expect_download() as download:
                page.get_by_role('button', name='Download preview', exact=True).click()
            assert download.value.suggested_filename == f'FY2027-{name}-preview.{format}'
            assert download.value.failure() is None
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Save final report', exact=True).click()
        expect(page.locator('#report-list tr')).to_have_count(index)
        row = page.locator('#report-list tr').last
        expect(row.locator('[data-label="Export type"]')).to_have_text(label)
        for format in ('XLSX', 'CSV'):
            link = row.get_by_role('link', name=f'Download {format}', exact=True)
            box = link.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width
            with page.expect_download() as download:
                link.click()
            assert download.value.suggested_filename == f'FY2027-r{index}.{format.lower()}'
            assert download.value.failure() is None
    page.reload()
    expect(page.locator('#report-list tr').last.locator('[data-label="Export type"]')).to_have_text('Cash ledger')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("kind", ["direct_expense", "signout"])
def test_review_delete_clears_staged_voucher_after_confirmation(web, width, kind):
    page, post, _ = web
    voucher = prepare(post, kind=kind)
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/review?item={kind}:{voucher['voucher_id']}")
    if kind == "direct_expense":
        page.locator('#direct-expense-form [name="project"]').fill("Keep this draft")
    upload(page, "Upload voucher", "unwanted-voucher.pdf")
    expect(page.locator(".attached-document-preview")).to_be_visible()
    if width < 1200:
        page.locator("#drawer-documents").click()
    page.get_by_label("Document actions", exact=True).click()
    delete = page.get_by_role("button", name="Delete unwanted-voucher.pdf", exact=True)
    page.once("dialog", lambda dialog: dialog.dismiss())
    delete.click()
    expect(page.locator("#inbox-count")).to_have_text("1")
    expect(delete).to_be_enabled()
    page.once("dialog", lambda dialog: dialog.accept())
    delete.click()
    expect(page.locator("#inbox-count")).to_have_text("0")
    expect(page.locator("#mobile-inbox-count")).to_have_text("0")
    expect(page.locator(".inline-document-viewer")).to_have_count(0)
    expect(page.locator(".attached-document-preview")).to_have_count(0)
    assert page.request.get("/api/documents/inbox").json()["data"]["documents"] == []
    if width < 1200:
        page.locator("#close-documents").click()
    if kind == "direct_expense":
        expect(page.locator('#direct-expense-form [name="project"]')).to_have_value("Keep this draft")
    else:
        expect(page.get_by_role("button", name="File voucher", exact=True)).to_have_count(0)
    page.reload()
    expect(page.locator("#inbox-count")).to_have_text("0")
    expect(page.locator(".attached-document-preview")).to_have_count(0)


def test_review_delete_receipt_handles_failure_and_preserves_coding(web):
    page, post, _ = web
    voucher = prepare(post)
    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    upload(page, "Upload receipt", "unwanted-receipt.pdf")
    expect(page.locator(".attached-document-preview")).to_be_visible()
    page.locator('#direct-expense-form [name="project"]').fill("Keep receipt coding")
    page.get_by_label("Document actions", exact=True).click()
    delete = page.get_by_role("button", name="Delete unwanted-receipt.pdf", exact=True)
    page.route("**/api/documents/*/discard", lambda route: route.fulfill(
        status=409, json={"ok": False, "error": {"message": "Document not in inbox"}}))
    page.once("dialog", lambda dialog: dialog.accept())
    delete.click()
    expect(page.locator("#review-message")).to_have_text("Document not in inbox")
    expect(delete).to_be_enabled()
    expect(page.locator("#inbox-count")).to_have_text("1")
    expect(page.locator(".attached-document-preview")).to_be_visible()
    page.unroute("**/api/documents/*/discard")
    page.once("dialog", lambda dialog: dialog.accept())
    delete.click()
    expect(page.locator("#inbox-count")).to_have_text("0")
    expect(page.locator(".attached-document-preview")).to_have_count(0)
    expect(page.locator('#direct-expense-form [name="project"]')).to_have_value("Keep receipt coding")


@pytest.mark.parametrize("width", [1440, 390])
def test_account_and_class_fzf_search_and_tab_completion(web, width):
    page, post, artifacts = web
    post("/api/settings/classes", class_code="4.3", name="Community Homebuilding")
    post("/api/settings/expense-accounts", account_id="1001", name="Construction Materials")
    voucher = prepare(post)
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    class_code = page.locator('#direct-expense-form [name="class_code"]')
    account = page.locator('#direct-expense-form [name="expense_account"]')
    class_code.focus()
    page.keyboard.type("HOME")
    results = page.locator(".code-picker-options [role=option]")
    expect(results).to_have_text(["Community Homebuilding · 4.3"])
    page.keyboard.press("Tab")
    expect(class_code).to_have_value("4.3")
    expect(account).to_be_focused()
    expect(page.locator(".code-picker")).to_have_count(0)

    # Match the later word in an account name, not just the start of its label.
    account.click()
    search = page.get_by_role("combobox", name="Search Account code", exact=True)
    search.fill("materials")
    expect(results).to_have_text(["Construction Materials · 1001"])
    bounds = page.locator(".code-picker").bounding_box()
    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
    page.screenshot(path=str(artifacts / f"code-search-{width}.png"))
    page.keyboard.press("Enter")
    expect(account).to_have_value("1001")
    expect(page.locator("#review-drawer")).to_be_visible()

    # fzf subsequence search and code search both work; Escape cancels only search.
    class_code.click()
    search = page.get_by_role("combobox", name="Search Class code", exact=True)
    search.fill("hmbld")
    expect(results).to_have_text(["Community Homebuilding · 4.3"])
    search.fill("4.3")
    expect(results).to_have_text(["Community Homebuilding · 4.3"])
    search.fill("zzzz-no-such-code")
    expect(results).to_have_count(0)
    expect(page.locator(".code-picker-status")).to_have_text("No matching codes")
    page.keyboard.press("Escape")
    expect(class_code).to_have_value("4.3")
    expect(class_code).to_be_focused()
    expect(page.locator("#review-drawer")).to_be_visible()
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    page.reload()
    expect(class_code).to_have_value("4.3")
    expect(account).to_have_value("1001")

    class_code.click()
    page.keyboard.press("ArrowDown")
    active_code = page.locator('.code-picker-option[aria-selected="true"]').inner_text().split(" · ")[-1]
    page.keyboard.press("Enter")
    expect(class_code).to_have_value(active_code)
    class_code.click()
    page.get_by_role("combobox", name="Search Class code", exact=True).fill("home")
    results.first.click()
    expect(class_code).to_have_value("4.3")

    # Newly rendered split fields get the same picker without initialization.
    page.get_by_role("button", name="Add split", exact=True).click()
    class_code.last.focus()
    page.keyboard.type("home")
    page.keyboard.press("Enter")
    expect(class_code.last).to_have_value("4.3")


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
    upload(page, "Upload", "exchange-receipt.pdf")
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
    page.get_by_role("button", name=f"Attach receipt to exchange #{txn_id}", exact=True).click()
    expect(page.locator(".attached-document-preview")).to_be_visible()
    page.get_by_role("button", name="Remove", exact=True).click()
    expect(page.locator(".attached-document-preview")).to_have_count(0)
    if width < 1200:
        page.get_by_role("button", name="Choose from documents", exact=True).click()
        page.locator(".evidence-card").click()
    page.get_by_role("button", name=f"Attach receipt to exchange #{txn_id}", exact=True).click()
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


@pytest.mark.parametrize("use_as_receipt", [False, True])
def test_completed_expense_ledger_requires_class(web, use_as_receipt):
    page, post, _ = web
    voucher = prepare(post)
    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    page.locator('#direct-expense-form [name="expense_account"]').select_option("1000")
    page.locator('#direct-expense-form [name="class_code"]').select_option("7.5")
    upload(page, "Upload voucher", "signed-voucher.pdf")
    expect(page.locator(".attached-document-preview")).to_be_visible()
    if use_as_receipt:
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
    assert sorted(d["role"] for d in detail["documents"]) == (
        ["support", "voucher"] if use_as_receipt else ["voucher"])


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
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
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
    page.get_by_role("button", name="Back to documents", exact=False).click()
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


def test_voucher_prints_authorization_and_one_notes_area_on_one_page(web):
    page, post, artifacts = web
    voucher = post("/api/vouchers/prepare", type="direct_expense", date="2026-09-02",
                   recipient="Vendor", purpose="Supplies", usd="20", mxn="0",
                   notes="First note\nSecond note with <b>literal text</b> & details")
    page.goto(voucher["print_url"])
    expect(page.locator("#paper")).not_to_contain_text("Receipt value")
    expect(page.locator(".notes")).to_have_count(1)
    expect(page.locator(".notes")).to_have_text("First note\nSecond note with <b>literal text</b> & details")
    expect(page.locator(".notes b")).to_have_count(0)
    pdf = artifacts / "voucher.pdf"
    page.pdf(path=str(pdf), prefer_css_page_size=True)
    assert len(PdfReader(pdf).pages) == 1
    page.screenshot(path=str(artifacts / "voucher.png"), full_page=True)


@pytest.mark.parametrize("width", [1440, 390])
def test_peso_entry_through_confirmed_cross_currency_closeout(web, width):
    page, post, artifacts = web
    voucher = post("/api/vouchers/prepare", type="signout", date="2026-09-02",
                   recipient="Ana", purpose="Supplies", usd="20", mxn="0")
    vid = voucher["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/review?item=signout:{vid}")
    upload(page, "Upload voucher", "signed-voucher.pdf")
    page.get_by_role("button", name="File voucher", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    page.goto(f"/{vid}")
    add_report_receipt(page,mxn="400")
    submit_report(page,cash_mxn="100")
    state = page.request.get(f"/{vid}/state").json()["data"]
    assert state["claims"][0]["payment_mode"] == "cash"
    pid = state["claims"][0]["package_id"]
    page.goto(f"/review?item=package:{pid}")
    page.locator('#claim-review-form [name="expense_account"]').select_option("1000")
    page.locator('#claim-review-form [name="class_code"]').select_option("7.5")
    page.locator('#claim-review-form [name="receipt_date"]').fill("2026-09-03")
    page.get_by_role("button", name="Save & mark checked", exact=True).click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Approve all 1 receipts", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    page.goto(f"/signouts/{vid}")
    page.get_by_label("Settlement date", exact=True).fill("2026-09-04")
    page.get_by_label("MXN returned now", exact=True).fill("100")
    page.get_by_role("button", name="Review settlement", exact=True).click()
    expect(page.locator("#closeout-preview")).to_contain_text("25.000000 MXN/USD")
    expect(page.locator("#closeout-preview")).to_contain_text("USD 4.00")
    page.get_by_role("button", name="Confirm settlement and print addendum", exact=True).click()
    expect(page.locator("#closeout-message")).to_contain_text("confirm that all cash is accounted for")
    page.locator('#closeout-form [name="confirmed"]').check()
    page.screenshot(path=str(artifacts / f"closeout-{width}.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), page.evaluate(
        "[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(e => [e.tagName, e.id, e.className, e.getBoundingClientRect().width]).slice(0, 20)")
    with page.expect_popup() as popup:
        page.get_by_role("button", name="Confirm settlement and print addendum", exact=True).click()
    popup.value.wait_for_url("**/addendum/*/print")
    assert page.url.endswith(f"/signouts/{vid}")
    popup.value.close()
    state = page.request.get(f"/api/signouts/{vid}").json()["data"]["summary"]
    assert state["reasons"] == ["addendum needed"]
    aid = state["closeout"]["return_addendum_id"]
    page.goto(f"/review?item=addendum:{aid}")
    upload(page, "Upload & file addendum", "signed-return.pdf")
    expect(page.locator("#review-drawer")).to_be_hidden()
    page.goto(f"/signouts/{vid}")
    expect(page.locator("#closeout-state")).to_contain_text("All cash and paperwork are reconciled")
    expect(page.locator("#closeout-form")).to_be_hidden()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("decision", ["approve", "correct"])
def test_signout_workspace_reviews_submissions_in_voucher_context(web, width, decision):
    page, post, artifacts = web
    vid = prepare(post, kind="signout", recipient="Ana")["voucher_id"]
    other_vid = prepare(post, kind="signout", recipient="Someone else")["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/{vid}")
    add_report_receipt(page,mxn="30",notes="Store supplies")
    submit_report(page,cash_usd="50",cash_mxn="70")
    pid = page.request.get(f"/{vid}/state").json()["data"]["claims"][0]["package_id"]
    page.goto(f"/signouts/{vid}")
    expect(page.locator("#signout-receipt-review")).to_contain_text("Store")
    expect(page.locator(".signout-submission.selected")).to_have_count(1)
    page.goto(f"/review?item=package:{pid}")
    page.get_by_role("link", name=f"Open signout {vid}", exact=True).click()
    page.wait_for_url(f"**/signouts/{vid}?submission={pid}")
    expect(page.locator("#signout-overview")).to_contain_text("1 receipt awaiting review")
    expect(page.locator("#signout-receipt-workspace")).to_be_visible()
    expect(page.locator("#signout-receipt-review")).to_contain_text("Store")
    expect(page.locator("#signout-receipt-preview iframe")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert page.locator("#claim-review-form").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    if decision == "approve":
        page.locator('#claim-review-form [name="expense_account"]').select_option("1000")
        page.locator('#claim-review-form [name="class_code"]').select_option("7.5")
        page.get_by_role("button", name="Save & mark checked", exact=True).click()
        expect(page.get_by_role("button", name="Approve all 1 receipts", exact=True)).to_be_enabled()
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Approve all 1 receipts", exact=True).click()
        expect(page.locator("#signout-submissions")).to_contain_text("Approved")
        expect(page.locator("#signout-movements tr")).to_have_count(2)
        expect(page.locator("#signout-overview")).to_contain_text("70.00 MXN")
        expect(page.locator("#signout-documents")).to_contain_text("receipt.pdf")
    else:
        page.locator("#return-item-note").fill("Please upload a clearer receipt.")
        page.locator("#return-summary").fill("The receipt is unreadable.")
        page.get_by_role("button", name="Request corrections", exact=True).click()
        expect(page.locator("#signout-submissions")).to_contain_text("Needs corrections")
        expect(page.locator("#signout-receipt-review")).to_contain_text("The receipt is unreadable.")
        expect(page.locator("#signout-movements tr")).to_have_count(1)
    assert f"/signouts/{vid}" in page.url
    expect(page.locator("#claim-review-form")).to_have_count(0)
    expect(page.locator("#signout-overview")).not_to_contain_text("awaiting review")
    page.screenshot(path=str(artifacts / f"signout-review-{decision}-{width}.png"), full_page=True)
    page.reload()
    expect(page.locator("#signout-receipt-workspace")).to_be_visible()
    expect(page.locator("#claim-review-form")).to_have_count(0)
    page.goto(f"/signouts/{other_vid}?submission={pid}")
    expect(page.locator("#signout-submissions")).to_contain_text("No receipts submitted yet")
    expect(page.locator("#signout-receipt-workspace")).to_be_hidden()


@pytest.mark.parametrize('kind', ['signout', 'addendum'])
def test_qr_match_opens_exact_review_without_filing(web, kind):
    from cash.db import connect
    from cash.web.qr_capture import record_match
    page, post, artifacts = web
    vid = prepare(post, kind='signout', recipient='QR Example')['voucher_id']
    code = vid
    if kind == 'addendum':
        code = post('/api/addenda/prepare', voucher_id=vid, kind='positive',
                    date='2026-09-03', usd='5', mxn='0', reason='Supplies')['addendum_id']
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    buffer = io.BytesIO()
    writer.write(buffer)
    captured = page.request.post('/api/documents/capture-merge', multipart={
        'files': {'name': code + '.pdf', 'mimeType': 'application/pdf',
                  'buffer': buffer.getvalue()}}).json()['data']
    conn = connect(artifacts / 'data' / 'cash.db')
    try:
        record_match(conn, captured['document_id'], {'status': 'detected', 'ids': [code]})
        page.goto(f'/signouts/{vid}')
        expect(page.locator('#signout-documents')).to_contain_text('Awaiting signed-paper review')
        page.locator('#signout-documents a').click()
        page.wait_for_url(lambda url: url.endswith(f"/review?item={kind}:{code}"))
        button = page.get_by_role('button', name='File voucher' if kind == 'signout' else f'File opened · {code}.pdf', exact=True)
        expect(button).to_be_visible()
        assert conn.execute('SELECT count(*) FROM transaction_documents').fetchone()[0] == 0
        button.click()
        # Wait on the observable worklist result, then check authoritative state.
        expect(page.locator('#undo-toast')).to_contain_text(f'{code} voucher filed.' if kind == 'signout' else f'{code} filed.')
        assert conn.execute('SELECT count(*) FROM transaction_documents').fetchone()[0] == 1
    finally:
        conn.close()


def test_expense_report_requires_notes_and_recovers_lost_save_response(web):
    page,post,_=web
    vid=prepare(post,kind="signout")["voucher_id"]
    page.goto(f"/{vid}")
    card=add_report_receipt(page,usd="10",notes="")
    expect(page.locator("#report-save-state")).to_have_text("All changes saved")
    page.locator('#report-settlement [name="cash_usd"]').fill("40")
    page.locator('#report-settlement [name="cash_mxn"]').fill("100")
    page.locator("#report-submit").click()
    expect(page.locator("#report-message")).to_have_text("Describe the purpose of every receipt before submitting")
    assert page.request.get(f"/{vid}/state").json()["data"]["packages"] == []
    requests=[]
    def lose_response(route):
        if route.request.method != "PATCH":
            return route.continue_()
        requests.append(route.request.post_data_json["submission_id"])
        if len(requests)==1:
            route.fetch()  # Server commits, but the browser never receives that response.
            route.abort()
        else:
            route.continue_()
    page.route(f"**/{vid}/claims/*",lose_response)
    card.locator('[name="notes"]').fill("School supplies")
    expect(page.locator("#report-retry")).to_be_visible()
    card.locator('[name="notes"]').fill("School supplies, pencils")
    expect(page.locator("#report-save-state")).to_have_text("All changes saved")
    assert requests[0] == requests[1]
    assert page.request.get(f"/{vid}/state").json()["data"]["claims"][0]["notes"] == "School supplies, pencils"
    page.locator("#report-submit").click()
    expect(page.locator("#report-submit")).to_be_hidden()


@pytest.mark.parametrize("width",[1440,390])
def test_report_receipt_pages_and_admin_entry(web,width):
    page,post,_=web
    vid=prepare(post,kind="signout")["voucher_id"]
    page.set_viewport_size({"width":width,"height":1000})
    page.goto(f"/signouts/{vid}")
    page.locator("#admin-expense-report > summary").click()
    card=add_report_receipt(page,usd="10",notes="Office supplies")
    expect(page.locator("#report-save-state")).to_have_text("All changes saved")
    before=page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    thumb=page.request.get(f"/{vid}/documents/{before['document_id']}/thumbnail")
    assert thumb.ok and thumb.headers["content-type"] == "image/jpeg"
    card.locator('[data-action="append"]').click()
    upload(page,"Choose files","page-two.pdf")
    page.locator("#report-attach").click()
    expect(page.locator("#report-upload")).to_be_hidden()
    after=page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    assert before["document_id"] != after["document_id"]
    pdf=PdfReader(io.BytesIO(page.request.get(after["stream_url"]).body()))
    assert len(pdf.pages)==2
    expect(card.locator('[name="notes"]')).to_have_value("Office supplies")
    card.locator('[data-action="replace"]').click()
    upload(page,"Choose files","replacement.pdf")
    page.locator("#report-attach").click()
    expect(page.locator("#report-upload")).to_be_hidden()
    after=page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    assert len(PdfReader(io.BytesIO(page.request.get(after["stream_url"]).body())).pages)==1
    page.once("dialog",lambda dialog:dialog.accept())
    card.locator('[data-action="remove"]').click()
    expect(page.locator(".report-receipt")).to_have_count(0)
    submit_report(page,cash_usd="50",cash_mxn="100")
    expect(page.locator("#reported-settlement")).to_contain_text("50.00 USD · 100.00 MXN")
    expect(page.locator('#closeout-form [name="return_usd"]')).to_have_value("50.00")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


@pytest.mark.parametrize("width", [1440, 1280, 390])
def test_existing_withdrawal_receipt_from_capture_and_document_search(web, width):
    page, post, artifacts = web
    txn_id = post("/api/entries/withdrawal", date="2026-09-03", usd="80",
                  notes="Downtown ATM")["txn_ids"][0]
    balances = page.request.get("/api/balances").json()["data"]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/ledger?txn={txn_id}")
    link = page.get_by_role("link", name="Attach receipt", exact=True)
    expect(link).to_have_attribute("href", f"/review?item=withdrawal:{txn_id}")
    link.click()
    expect(page.get_by_role("button", name="Upload receipt", exact=True)).to_be_visible()

    # Open the inbox independently, as when scans arrive before matching.
    page.goto("/review")
    if width < 1200:
        page.locator("#open-documents").click()
    upload(page, "Upload", "atm-receipt.pdf")
    expect(page.locator(".document-preview-title > strong")).to_have_text("atm-receipt.pdf")
    upload(page, "Upload", "other-receipt.pdf")
    expect(page.locator(".document-preview-title > strong")).to_have_text("other-receipt.pdf")
    before = page.locator("#document-rail").bounding_box()
    page.get_by_role("button", name="Next document", exact=True).click()
    expect(page.locator(".document-preview-title > strong")).to_have_text("atm-receipt.pdf")
    page.get_by_role("button", name="Previous document", exact=True).click()
    expect(page.locator(".document-preview-title > strong")).to_have_text("other-receipt.pdf")
    page.get_by_role("button", name="Back to documents", exact=False).click()
    expect(page.locator(".evidence-card")).to_have_count(2)
    after = page.locator("#document-rail").bounding_box()
    assert before["width"] == after["width"]
    page.screenshot(path=str(artifacts / f"document-inbox-{width}.png"), full_page=True)

    page.goto("/capture")
    page.get_by_role("link", name="Attach to transaction…", exact=True).first.click()
    expect(page.locator(".document-preview-title > strong")).to_have_text("atm-receipt.pdf")
    page.get_by_role("button", name="Attach to transaction…", exact=True).click()
    search = page.get_by_label("Find an existing transaction", exact=True)
    search.fill("No such withdrawal")
    expect(page.locator("#document-transaction-results")).to_contain_text("No matching transactions")
    search.fill("2026-09-03 80 downtown")
    result = page.locator(".document-transaction-result")
    expect(result).to_have_count(1)
    expect(result).to_contain_text("Bank withdrawal")
    result.click()
    attach = page.get_by_role("button", name=f"Attach receipt to withdrawal #{txn_id}", exact=True)
    expect(attach).to_be_visible()
    # Previewing and choosing the transaction do not file evidence on their own.
    assert page.request.get(f"/api/ledger/{txn_id}").json()["data"]["documents"] == []
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / f"document-preview-{width}.png"), full_page=True)
    attach.click()
    expect(page.locator(".attached-document-preview")).to_be_visible()
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    detail = page.request.get(f"/api/ledger/{txn_id}").json()["data"]
    assert detail["transaction"]["incomplete"] is False
    assert detail["documents"][0]["doc_type"] == "withdrawal_receipt"
    assert detail["documents"][0]["display_name"] == "atm-receipt.pdf"
    after = page.request.get("/api/balances").json()["data"]
    assert after.pop("ledger_revision") == balances.pop("ledger_revision") + 1
    assert after == balances
    ledger = page.request.get("/api/ledger").json()["data"]["transactions"]
    assert len([row for row in ledger if row["txn_type"] == "bank_withdrawal"]) == 1


@pytest.mark.parametrize("kind", ["withdrawal", "exchange"])
def test_document_create_menu_and_unsaved_entry_guard(web, kind):
    page, post, _ = web
    page.goto("/review")
    upload(page, "Upload", "receipt.pdf")
    expect(page.locator(".document-preview-title > strong")).to_have_text("receipt.pdf")
    label = "bank withdrawal" if kind == "withdrawal" else "currency exchange"
    expect(page.get_by_role("button", name=f"Create new {label}", exact=True)).not_to_be_visible()
    page.locator(".document-create-menu > summary").click()
    page.get_by_role("button", name=f"Create new {label}", exact=True).click()
    form = page.locator("#document-entry-form")
    form.locator('[name="date"]').fill("2026-09-03")
    form.locator('[name="usd"]').fill("20")
    form.locator('[name="notes"]').fill("Receipt-first entry")
    if kind == "exchange":
        form.locator('[name="mxn"]').fill("320")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.get_by_role("button", name="Back to documents", exact=False).click()
    expect(form.locator('[name="usd"]')).to_have_value("20")
    page.get_by_role("button", name=f"Create {label}", exact=True).click()
    expect(page.locator("#inbox-count")).to_have_text("0")
    rows = page.request.get("/api/ledger").json()["data"]["transactions"]
    created = [row for row in rows if row["notes"] == "Receipt-first entry"]
    assert len(created) == 1
    assert created[0]["incomplete"] is False
    assert page.request.get("/api/review/worklist").json()["data"]["items"] == []


@pytest.fixture
def async_web(monkeypatch, request):
    # The browser's real HTTP server queues captures; tests drive a real worker
    # with only the expensive model replaced by a deterministic result.
    monkeypatch.setenv('CASH_QR_ENABLED', '1')
    return request.getfixturevalue('web')


@pytest.mark.parametrize('width', [1440, 390])
def test_capture_processing_updates_name_and_match_without_reload(async_web, monkeypatch, width):
    from cash.document_worker import process_next
    from cash.web import qr_capture
    page, post, artifacts = async_web
    vid = prepare(post, kind='signout')['voucher_id']
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto('/capture')
    upload(page, 'Choose files', 'camera-photo.pdf')
    page.locator('#go').click()
    expect(page.locator('#inbox .document-processing')).to_contain_text('Document processing…')
    expect(page.get_by_role('link', name='Review ' + vid, exact=True)).to_have_count(0)
    # The page stays interactive while the job waits for the worker.
    expect(page.get_by_role('button', name='Choose files', exact=True)).to_be_enabled()
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {
        'status': 'detected', 'ids': [vid], 'rotations': [180]})
    assert process_next(artifacts / 'data')
    expect(page.get_by_role('link', name='Review ' + vid, exact=True)).to_be_visible(timeout=8000)
    expect(page.locator('#inbox .evidence-copy strong')).to_have_text(vid + '.pdf')
    expect(page.locator('#inbox .processing-spinner')).to_have_count(0)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(artifacts / f'capture-ready-{width}.png'), full_page=True)


@pytest.mark.parametrize('width', [1440, 390])
def test_review_processing_keeps_unsaved_coding_and_updates_preview(async_web, monkeypatch, width):
    from cash.document_worker import process_next
    from cash.web import qr_capture
    page, post, artifacts = async_web
    vid = prepare(post)['voucher_id']
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto(f'/review?item=direct_expense:{vid}')
    form = page.locator('#direct-expense-form')
    form.locator('[name="expense_account"]').select_option('1000')
    form.locator('[name="class_code"]').select_option('7.5')
    upload(page, 'Upload voucher', 'camera-photo.pdf')
    expect(page.locator('#detail .document-processing')).to_be_visible()
    expect(page.get_by_role('button', name='Save & review next', exact=True)).to_be_disabled()
    project = form.locator('[name="project"]')
    project.fill('Keep my unfinished coding')
    project.focus()
    page.screenshot(path=str(artifacts / f'review-processing-{width}.png'), full_page=True)
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {
        'status': 'detected', 'ids': [vid], 'rotations': [180]})
    assert process_next(artifacts / 'data')
    expect(page.locator('#detail .document-processing')).to_have_count(0, timeout=8000)
    expect(page.locator('#detail .attachment-slot-heading strong').first).to_have_text(vid + '.pdf')
    expect(project).to_have_value('Keep my unfinished coding')
    expect(project).to_be_focused()
    expect(form.locator('[name="expense_account"]')).to_have_value('1000')
    expect(form.locator('[name="class_code"]')).to_have_value('7.5')
    expect(page.get_by_role('button', name='Save & review next', exact=True)).to_be_enabled()
    # A scanner upload arriving while Review is open also appears automatically.
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    buffer = io.BytesIO()
    writer.write(buffer)
    page.request.post('/api/documents/capture', multipart={
        'files': {'name': 'scanner.pdf', 'mimeType': 'application/pdf', 'buffer': buffer.getvalue()}})
    expect(page.locator('#inbox-count')).to_have_text('2', timeout=8000)
    expect(project).to_have_value('Keep my unfinished coding')
    expect(project).to_be_focused()


def test_failed_processing_becomes_manually_attachable_without_reload(async_web, monkeypatch):
    from cash.document_worker import process_next
    from cash.web import qr_capture
    page, _, artifacts = async_web
    page.goto('/review')
    upload(page, 'Upload', 'receipt.pdf')
    expect(page.locator('#document-preview .processing-spinner')).to_be_visible()
    expect(page.get_by_role('button', name='Attach to transaction…', exact=True)).to_have_count(0)
    monkeypatch.setattr(qr_capture, 'recognize', lambda *_: {'status': 'timeout', 'ids': []})
    assert process_next(artifacts / 'data')
    expect(page.locator('#document-preview .document-processing-failed')).to_be_visible(timeout=8000)
    expect(page.get_by_role('button', name='Attach to transaction…', exact=True)).to_be_visible()
    expect(page.get_by_role('link', name='Open full size', exact=True)).to_be_visible()


@pytest.mark.parametrize('width', [1440, 390])
def test_balances_show_signout_peso_basis_and_combined_value(web, width):
    page, post, _ = web
    peso = post('/api/vouchers/prepare', type='signout', date='2026-09-02',
                recipient='Peso holder', purpose='Supplies', usd='0', mxn='10000')
    mixed = post('/api/vouchers/prepare', type='signout', date='2026-09-03',
                 recipient='Mixed cash holder', purpose='Supplies', usd='50', mxn='1500')
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto('/balances')
    expect(page.locator('#safe-totals')).to_contain_text(
        '2 open signouts · recorded USD value $816.67')
    peso_row = page.locator(f'.signout-link[href="/signouts/{peso["voucher_id"]}"]')
    expect(peso_row.locator('.signout-basis')).to_have_text('Recorded USD value of MXN 666.67')
    expect(peso_row.locator('.signout-cash > b').first).to_have_text('USD 0.00')
    expect(peso_row.locator('.signout-cash > b').last).to_have_text('MXN 10,000.00')
    mixed_row = page.locator(f'.signout-link[href="/signouts/{mixed["voucher_id"]}"]')
    expect(mixed_row.locator('.signout-basis')).to_have_text('Recorded USD value of MXN 100.00')
    expect(mixed_row.locator('.signout-cash > b').first).to_have_text('USD 50.00')
    for row in (peso_row, mixed_row):
        box = row.locator('.signout-basis').bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= width
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
