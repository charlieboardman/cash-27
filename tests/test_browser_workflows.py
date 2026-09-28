"""Optional real-browser regressions. Install Playwright + Chromium to run.

Every test serves a fresh temporary database on a random loopback port.
"""

from datetime import datetime
import io
import re
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
    # Deleting a row or removing a receipt waits for Undo; keep the wait short.
    context.add_init_script("window.cashUndoMs = 1500")

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


def answer_confirm(page, accept=True):
    """Answer the app's own confirmation dialog (it replaced window.confirm)."""
    dialog = page.locator("dialog.confirm-dialog")
    expect(dialog).to_be_visible()
    dialog.locator(f'button[value="{"ok" if accept else "cancel"}"]').click()
    expect(dialog).to_have_count(0)


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
    slot = button.removeprefix("Upload ") if button.startswith("Upload ") else None
    with page.expect_file_chooser() as chooser:
        if slot:
            # A document slot in an open record: Attach menu, then Upload.
            menu = page.locator(f'[data-attach-menu$="{slot}"]').first
            menu.locator(".attach-toggle").click()
            menu.locator(".slot-upload").click()
        else:
            page.get_by_role("button", name=button, exact=True).click()
    chooser.value.set_files({"name": name, "mimeType": "application/pdf",
                             "buffer": buffer.getvalue()})


def choose_from_inbox(page, slot=""):
    """Attach menu, then From inbox: the rail opens to pick a document."""
    menu = page.locator(f'[data-attach-menu$="{slot}"]' if slot else "[data-attach-menu]").first
    menu.locator(".attach-toggle").click()
    menu.locator(".slot-choose").click()


def recipient(page, vid):
    """Sign in as the signout's recipient with the shared password."""
    page.context.clear_cookies()
    page.context.set_extra_http_headers({})
    page.goto(f"/{vid}")
    page.wait_for_url(lambda url: "/user-login?next=" in url)
    page.locator('input[name="password"]').fill("test")
    page.locator('#user-login-form button[type="submit"]').click()
    page.wait_for_url(f"**/{vid}")
    expect(page.locator("#er-save-state")).not_to_be_empty()


def administrator(page):
    """Sign back in as the administrator for API setup and admin pages."""
    csrf = page.context.request.post("/api/login", data={"password": "test"}).json()["data"]["csrf"]
    page.context.set_extra_http_headers({"X-CSRF": csrf})


def add_row(page, usd="", mxn="", purpose="Supplies", receipt=True, language="en",
            date="2026-09-03"):
    page.locator("#er-add").click()
    editor = page.locator(".er-row.open .er-editor")
    expect(editor).to_be_visible()
    if receipt:
        attach_receipt(page, "receipt.pdf", language=language)
        expect(editor.locator(".er-receipt-thumb")).to_be_visible()
    editor.locator('[name="date"]').fill(date)
    editor.locator('[name="usd"]').fill(usd)
    editor.locator('[name="mxn"]').fill(mxn)
    editor.locator('[name="purpose"]').fill(purpose)
    return editor


def attach_receipt(page, *names, language="en", button="Add receipt"):
    """Stage every page in the receipt dialog, then attach them as one PDF."""
    es = language == "es"
    labels = {"Add receipt": "Agregar recibo", "Replace": "Reemplazar",
              "Attach signed statement": "Adjuntar declaración firmada",
              "Attach purchase log": "Adjuntar registro de compras"}
    page.locator(".er-row.open .er-editor").get_by_role(
        "button", name=labels[button] if es else button, exact=True).click()
    dialog = page.locator("#er-attach")
    expect(dialog).to_be_visible()
    for name in names:
        upload(page, "Elegir archivos" if es else "Choose files", name)
    expect(dialog.locator(".receipt-page-grid li")).to_have_count(len(names))
    dialog.get_by_role("button", name="Adjuntar" if es else "Attach", exact=True).click()
    expect(dialog).to_be_hidden()


def saved(page, language="en"):
    expect(page.locator("#er-save-state")).to_have_text(
        "Todos los cambios guardados" if language == "es" else "All changes saved")


def code_row(editor, class_code="7.5", account="1000"):
    editor.locator('[name="class_code"]').select_option(class_code)
    editor.locator('[name="expense_account"]').select_option(account)


def approve(page):
    page.locator("#er-approve").click()
    answer_confirm(page)
    expect(page.locator("#er-approve")).to_be_hidden()


def submit_report(page):
    page.locator("#er-submit").click()
    expect(page.locator("#er-submit")).to_be_hidden()


@pytest.mark.parametrize("width", [1440, 390])
def test_recipient_language_toggle_and_login_preference(web, width):
    page, post, artifacts = web
    vid = prepare(post, kind="signout", recipient="Ana")["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    recipient(page, vid)
    editor = add_row(page, mxn="1200", purpose="Supplies")
    saved(page)
    page.get_by_role("button", name="Español", exact=True).click()
    expect(page.locator("html")).to_have_attribute("lang", "es")
    expect(editor.locator('[name="purpose"]')).to_have_value("Supplies")
    expect(page.locator("#er-add")).to_have_text("Agregar fila")
    expect(page.locator("#er-submit")).to_have_text("Enviar informe")
    submit_report(page)
    expect(page.locator("#er-status")).to_have_text("Enviado")
    page.reload()
    expect(page.locator("html")).to_have_attribute("lang", "es")
    expect(page.locator("#er-status")).to_have_text("Enviado")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / f"report-spanish-{width}.png"), full_page=True)
    page.context.clear_cookies()
    page.goto(f"/{vid}")
    page.wait_for_url(lambda url: "/user-login?next=" in url)
    expect(page.get_by_role("heading", name="Inicie sesión en su informe de gastos")).to_be_visible()
    page.get_by_label("Contraseña compartida", exact=True).fill("wrong")
    page.get_by_role("button", name="Continuar", exact=True).click()
    expect(page.locator("#login-status")).to_have_text("Contraseña incorrecta")
    page.get_by_label("Contraseña compartida", exact=True).fill("test")
    page.get_by_role("button", name="Continuar", exact=True).click()
    page.wait_for_url(f"**/{vid}")
    expect(page.locator("html")).to_have_attribute("lang", "es")
    expect(page.locator("#er-status")).to_have_text("Enviado")
    administrator(page)
    page.goto(f"/{vid}")
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.locator("[data-signee-language]")).to_have_count(0)


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("language", ["en", "es"])
def test_recipient_rows_update_the_balance_and_lock_on_submit(web, width, language):
    page, post, artifacts = web
    vid = prepare(post, kind="signout")["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    recipient(page, vid)
    expect(page.locator("#er-add")).to_be_visible()
    assert page.evaluate("scrollY") == 0
    if language == "es":
        page.get_by_role("button", name="Español", exact=True).click()
    editor = add_row(page, mxn="40", language=language)
    expect(editor.locator(".rf-receipt-total")).to_be_hidden()
    saved(page, language)
    expect(page.locator("#er-balance")).to_have_text("USD 50.00 · MXN 60.00")
    editor.locator('[name="mxn"]').fill("45")
    saved(page, language)
    expect(page.locator("#er-balance")).to_have_text("USD 50.00 · MXN 55.00")
    page.reload()
    page.locator(".er-row-open").first.click()
    expect(page.locator('.er-editor [name="mxn"]')).to_have_value("45.00")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / f"report-rows-{language}-{width}.png"), full_page=True)
    submit_report(page)
    expect(page.locator('.er-editor [name="purpose"]')).to_be_disabled()
    page.locator(".er-editor [data-view]").click()
    expect(page.locator("#er-viewer iframe")).to_be_visible()
    page.locator("#er-viewer-close").click()
    expect(page.locator("#er-viewer")).not_to_be_visible()


@pytest.mark.parametrize("usd,mxn,receipt_total,expected_usd", [
    ("50", "", None, 50),
    ("10", "", None, 10),
    ("10", "-60", "100", 6.25),
    ("-2", "100", "4", 100 / 15 - 2),
])
def test_signed_rows_through_approval_to_the_ledger(web, usd, mxn, receipt_total, expected_usd):
    page, post, artifacts = web
    vid = prepare(post, kind="signout")["voucher_id"]
    recipient(page, vid)
    editor = add_row(page, usd=usd, mxn=mxn)
    total = editor.locator(".rf-receipt-total")
    if receipt_total:
        expect(total).to_be_visible()
        total.locator("input").fill(receipt_total)
    else:
        expect(total).to_be_hidden()
    if usd == "50":
        editor.locator('[name="usd"]').fill("50.001")
        expect(page.locator("#er-message")).to_have_text("Enter amounts with at most two decimal places.")
        editor.locator('[name="usd"]').fill("50")
    saved(page)
    submit_report(page)
    administrator(page)
    page.goto("/review")
    page.get_by_role("button", name="Expense reports").click()
    page.locator(".review-summary-row").filter(has_text=vid).click()
    page.wait_for_url(f"**/{vid}")
    page.locator(".er-row-open").first.click()
    code_row(page.locator(".er-editor"))
    saved(page)
    approve(page)
    rows = page.request.get("/api/ledger").json()["data"]["transactions"]
    expense = next(row for row in rows if row["txn_type"] == "expense" and row["voucher_id"] == vid)
    assert float(expense["usd"]) == float(usd or 0)
    assert float(expense["mxn"]) == float(mxn or 0)
    assert float(expense["calc_usd"]) == pytest.approx(expected_usd)


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
    assert (expenses[0]["usd"], expenses[0]["mxn"]) == ("20.00", "150.00")


@pytest.mark.parametrize("kind", ["signout", "exchange", "withdrawal"])
def test_other_transaction_types_still_save_without_printing(web, kind):
    page, _, _ = web
    page.goto("/transactions/new")
    page.locator(".type-picker label").filter(
        has=page.locator(f'[name="type"][value="{kind}"]')).click()
    if kind == "signout":
        page.get_by_label("Recipient", exact=True).fill("Ana")
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
    page.wait_for_url("**/FY*" if kind == "signout" else
                      f"**/review?item={kind}:*")


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("kind", ["positive", "return"])
@pytest.mark.parametrize("print_addendum", [False, True])
def test_addendum_save_destination_and_print_from_ledger(
        web, width, kind, print_addendum):
    page, post, artifacts = web
    post("/api/settings/voucher-defaults", approver_name="Default Approver")
    voucher = prepare(post, kind="signout", recipient="Ana")
    vid = voucher["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/{vid}")
    form = page.locator("#er-addendum-form")
    expect(form).to_be_hidden()
    expect(page.locator("#er-settlement")).to_be_hidden()
    expect(page.locator("#er-activity li")).to_have_count(1)
    page.locator("#er-addendum > summary").click()
    form.locator(".segmented-control label").filter(
        has=page.locator(f'[name="kind"][value="{kind}"]')).click()
    form.locator('[name="date"]').fill("2026-09-03")
    form.locator('[name="reason"]').fill("Cash adjustment")
    expect(form.locator('[name="approver_name"]')).to_have_value("Default Approver")
    form.locator('[name="approver_name"]').fill("Addendum Approver")
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
    expect(page.locator("#er-addenda li")).to_have_count(1)
    addendum = page.request.get(f"/{vid}/api/report").json()["data"]["paperwork"]["addenda"][0]
    aid = addendum["addendum_id"]
    print_url = f"/voucher/{vid}/addendum/{aid}/print"
    if print_addendum:
        popup.value.wait_for_url(f"**{print_url}")
        expect(popup.value.locator("#paper")).to_contain_text(aid)
        popup.value.close()
    else:
        expect(page.locator("#er-activity li")).to_have_count(2)
        expect(form.locator('[name="usd"]')).to_have_value("")
        assert print_requests == []
    assert page.url.endswith(f"/{vid}")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    with page.expect_popup() as popup:
        page.locator("#er-addenda li").filter(has_text=aid).get_by_role(
            "link", name="Print", exact=True).click()
    popup.value.wait_for_url(f"**{print_url}")
    expect(popup.value.locator(".usd-box")).to_have_text("USD 10.00")
    expect(popup.value.locator(".mxn-box")).to_have_text("MXN 50.00")
    for text in ("Ana", "Addendum Approver", "Cash adjustment"):
        field = popup.value.locator('.line.filled').filter(has_text=text)
        expect(field).to_have_count(1)
        assert field.evaluate('node => getComputedStyle(node).fontSize') == '22px'
    pdf = artifacts / 'addendum-large-names.pdf'
    popup.value.pdf(path=str(pdf), prefer_css_page_size=True)
    assert len(PdfReader(pdf).pages) == 1
    assert page.url.endswith(f"/{vid}")
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
    report = page.request.get(f"/{vid}/api/report").json()["data"]
    assert len(report["paperwork"]["addenda"]) == 1
    assert report["paperwork"]["addenda"][0]["status"] == "pending"
    assert report["balance"]["usd"] == ("60.00" if kind == "positive" else "40.00")
    assert report["balance"]["mxn"] == ("150.00" if kind == "positive" else "50.00")


@pytest.mark.parametrize("width", [1440, 390])
def test_both_export_formats_for_all_years_and_one_year(web, width):
    page, _, _ = web
    page.set_viewport_size({"width": width, "height": 1000})
    page.clock.set_fixed_time(datetime(2026, 9, 27, 12))
    page.goto('/ledger')
    page.get_by_role('link', name='Exports and reports', exact=True).click()
    page.wait_for_url('**/reports')
    # The device's date picks the current fiscal year (September start).
    expect(page.locator('#report-fy')).to_have_value('2027')
    expect(page.locator('#report-fy option:checked')).to_have_text('FY2027 (current)')
    expect(page.locator('#period-description')).to_have_text(
        'FY2027: September 1, 2026 to August 31, 2027.')
    for fy, period in [('', 'all-years'), ('2027', 'FY2027')]:
        page.locator('#report-fy').select_option(fy)
        for export_type, name in [('cash_ledger', 'cash-ledger'), ('expenses', 'expenses')]:
            page.locator('#report-type').select_option(export_type)
            for format in ('xlsx', 'csv'):
                page.locator('#report-format').select_option(format)
                with page.expect_download() as download:
                    page.get_by_role('button', name='Download', exact=True).click()
                assert re.fullmatch(rf'{period}-{name}-\d{{8}}T\d{{6}}Z\.{format}',
                                    download.value.suggested_filename)
                assert download.value.failure() is None
    expect(page.locator('#period-description')).to_have_text(
        'FY2027: September 1, 2026 to August 31, 2027.')
    expect(page.get_by_text('Save final report')).to_have_count(0)
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
    expect(page.locator(".doc-row.filled")).to_be_visible()
    if width < 1200:
        page.locator("#drawer-documents").click()
    page.get_by_label("Document actions", exact=True).click()
    delete = page.get_by_role("button", name="Delete unwanted-voucher.pdf", exact=True)
    delete.click()
    answer_confirm(page, accept=False)
    expect(page.locator("#inbox-count")).to_have_text("1")
    expect(delete).to_be_enabled()
    delete.click()
    answer_confirm(page)
    expect(page.locator("#inbox-count")).to_have_text("0")
    expect(page.locator("#mobile-inbox-count")).to_have_text("0")
    expect(page.locator(".inline-document-viewer")).to_have_count(0)
    expect(page.locator(".doc-row.filled")).to_have_count(0)
    assert page.request.get("/api/documents/inbox").json()["data"]["documents"] == []
    if width < 1200:
        page.locator("#close-documents").click()
    if kind == "direct_expense":
        expect(page.locator('#direct-expense-form [name="project"]')).to_have_value("Keep this draft")
    else:
        expect(page.get_by_role("button", name="File voucher", exact=True)).to_have_count(0)
    page.reload()
    expect(page.locator("#inbox-count")).to_have_text("0")
    expect(page.locator(".doc-row.filled")).to_have_count(0)


def test_review_delete_receipt_handles_failure_and_preserves_coding(web):
    page, post, _ = web
    voucher = prepare(post)
    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    upload(page, "Upload receipt", "unwanted-receipt.pdf")
    expect(page.locator(".doc-row.filled")).to_be_visible()
    page.locator('#direct-expense-form [name="project"]').fill("Keep receipt coding")
    page.get_by_label("Document actions", exact=True).click()
    delete = page.get_by_role("button", name="Delete unwanted-receipt.pdf", exact=True)
    page.route("**/api/documents/*/discard", lambda route: route.fulfill(
        status=409, json={"ok": False, "error": {"message": "Document not in inbox"}}))
    delete.click()
    answer_confirm(page)
    expect(page.locator("#review-message")).to_have_text("Document not in inbox")
    expect(delete).to_be_enabled()
    expect(page.locator("#inbox-count")).to_have_text("1")
    expect(page.locator(".doc-row.filled")).to_be_visible()
    page.unroute("**/api/documents/*/discard")
    delete.click()
    answer_confirm(page)
    expect(page.locator("#inbox-count")).to_have_text("0")
    expect(page.locator(".doc-row.filled")).to_have_count(0)
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

    # The split detail drawer keeps the searchable picker inside the modal.
    page.get_by_role("button", name="Split receipt", exact=True).click()
    split_class = page.locator('.split-workspace .sfw-detail [name="class_code"]')
    split_class.select_option("")
    split_class.focus()
    page.keyboard.type("home")
    page.keyboard.press("Enter")
    expect(split_class).to_have_value("4.3")


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
    # Choosing from the inbox attaches the clicked document in one step.
    choose_from_inbox(page)
    expect(page.locator("#evidence-heading")).to_have_text("Choose an exchange receipt")
    page.locator(".evidence-card").click()
    expect(page.locator(".doc-row.filled")).to_be_visible()
    page.get_by_role("button", name="Remove Exchange receipt", exact=True).click()
    expect(page.locator(".doc-row.filled")).to_have_count(0)
    choose_from_inbox(page)
    page.locator(".evidence-card").click()
    expect(page.locator(".doc-row.filled")).to_be_visible()
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
    page.get_by_role("button", name="Split receipt", exact=True).click()
    workspace = page.locator(".split-workspace")
    expect(workspace).to_be_visible()
    expect(workspace.locator(".sfw-card")).to_have_count(2)
    expect(workspace.locator('.sfw-detail [name="expense_account"]')).to_have_value("1000")
    expect(workspace.locator('.sfw-detail [name="class_code"]')).to_have_value("7.5")
    workspace.locator('.sfw-detail [name="usd"]').fill("10")
    workspace.locator('.sfw-detail [name="mxn"]').fill("75")
    workspace.locator('[data-sfw-select="1"]').click()
    workspace.locator('.sfw-detail [name="usd"]').fill("10")
    workspace.locator('.sfw-detail [name="mxn"]').fill("75")
    workspace.get_by_role("button", name="Done").click()
    expect(workspace).to_have_count(0)
    page.get_by_role("button", name="Undo split", exact=True).click()
    expect(account).to_have_count(1)
    page.locator('#direct-expense-form [name="project"]').fill("School supplies")
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    page.reload()
    expect(account).to_have_value("1000")
    expect(class_code).to_have_value("7.5")
    expect(page.locator('#direct-expense-form [name="project"]')).to_have_value("School supplies")
    expect(page.locator(".review-summary-row .badge")).to_have_text(["Needs voucher"])
    expect(page.locator("#review-drawer-identity .badge")).to_have_text(["Needs voucher"])
    pending = page.request.get(f"/api/ledger/{voucher['txn_ids'][0]}").json()["data"]
    assert pending["transaction"]["incomplete"] is True
    assert pending["documents"] == []
    # Finishing without a voucher still stores the latest edits and leaves the
    # expense queued. The signed voucher is what removes it from the queue.
    page.locator('#direct-expense-form [name="purpose"]').fill("Saved before the voucher")
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("signed voucher")
    expect(page.locator("#review-drawer")).to_be_visible()
    expect(page.locator(".review-summary-row .badge")).to_have_text(["Needs voucher"])
    page.reload()
    expect(page.locator('#direct-expense-form [name="purpose"]')).to_have_value("Saved before the voucher")
    expect(page.locator("#review-drawer-identity .badge")).to_have_text(["Needs voucher"])
    upload(page, "Upload voucher", "signed-voucher.pdf")
    expect(page.locator(".doc-row.filled")).to_be_visible()
    page.get_by_label("Also file as a receipt", exact=True).check()
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    page.reload()
    expect(page.locator(".doc-row.filled")).to_be_visible()
    expect(page.get_by_label("Also file as a receipt", exact=True)).to_be_checked()
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
    expect(page.locator(".doc-row.filled")).to_be_visible()
    if use_as_receipt:
        page.get_by_label("Also file as a receipt", exact=True).check()
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
    page.get_by_label("Reason for change", exact=True).fill("Correct class")
    page.get_by_role("button", name="Save changes", exact=True).click()
    assert class_field.evaluate("el => el.validity.valueMissing")
    detail = page.request.get(f"/api/ledger/{expense['txn_id']}").json()["data"]
    assert detail["transaction"]["class_code"] == expense["class_code"]
    assert detail["transaction"]["version"] == expense["version"]
    assert sorted(d["role"] for d in detail["documents"]) == (
        ["support", "voucher"] if use_as_receipt else ["voucher"])


@pytest.mark.parametrize("width", [1440, 1280, 390])
def test_drawer_receipt_choice_and_receipts_keep_coding_and_layout(web, width):
    page, post, artifacts = web
    voucher = prepare(post)
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    choice = page.get_by_label("Also file as a receipt", exact=True)
    expect(choice).to_have_count(0)
    project = page.locator('#direct-expense-form [name="project"]')
    page.locator('#direct-expense-form [name="expense_account"]').select_option("1000")
    page.locator('#direct-expense-form [name="class_code"]').select_option("7.5")
    project.fill("Clinic repairs")
    upload(page, "Upload voucher", "signed-voucher.pdf")
    expect(page.locator('.doc-row.filled[data-document-slot="primary"]')).to_be_visible()
    expect(choice).not_to_be_checked()
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    expect(project).to_have_value("Clinic repairs")
    # The voucher can be a receipt alongside separate receipts.
    choice.check()
    upload(page, "Upload receipt", "separate-receipt.pdf")
    expect(page.locator(".staged-receipt")).to_be_visible()
    expect(choice).to_be_checked()
    expect(page.locator("#direct-expense-form .direct-row")).to_have_count(1)
    expect(project).to_have_value("Clinic repairs")
    assert_no_drawer_overflow(page)
    page.screenshot(path=str(artifacts / f"drawer-{width}.png"))
    page.locator(".staged-receipt .unstage-receipt").click()
    expect(page.locator(".staged-receipt")).to_have_count(0)
    expect(choice).to_be_checked()
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
    file_voucher = page.get_by_role("button", name="File voucher", exact=True)
    expect(file_voucher).to_be_visible()
    expect(page.get_by_label("Also file as a receipt", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Back to documents", exact=False).click()
    expect(file_voucher).to_be_visible()
    page.get_by_role("button", name="Close transaction workspace").click()
    answer_confirm(page)
    page.locator(".review-summary-row").filter(has_text=second["voucher_id"]).click()
    expect(page.locator("#review-drawer-title")).to_have_text(second["voucher_id"])
    expect(page.get_by_role("button", name="File voucher", exact=True)).to_have_count(0)
    expect(page.locator('[data-attach-menu="voucher"] .attach-toggle')).to_be_visible()


def test_voucher_and_several_receipts_complete_as_one_payment(web):
    page, post, _ = web
    voucher = prepare(post)
    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    upload(page, "Upload receipt", "supplies-receipt.pdf")
    expect(page.locator(".staged-receipt")).to_have_count(1)
    upload(page, "Upload receipt", "gas-receipt.pdf")
    expect(page.locator(".staged-receipt")).to_have_count(2)
    expect(page.locator(".staged-receipt .doc-label")).to_have_text(
        ["Receipt 1", "Receipt 2"])
    editor = page.locator("#direct-expense-form .direct-row")
    expect(editor).to_have_count(1)
    editor.locator('[name="expense_account"]').select_option("1000")
    editor.locator('[name="class_code"]').select_option("7.5")
    editor.locator('[name="project"]').fill("Contractor")
    page.get_by_role("button", name="Save progress", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    page.reload()
    expect(page.locator(".staged-receipt")).to_have_count(2)
    expect(editor.locator('[name="project"]')).to_have_value("Contractor")
    expect(editor.locator('[name="expense_account"]')).to_have_value("1000")
    upload(page, "Upload voucher", "signed-voucher.pdf")
    expect(page.locator('.doc-row.filled[data-document-slot="primary"]')).to_be_visible()
    page.get_by_label("Also file as a receipt", exact=True).check()
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    rows = page.request.get("/api/ledger").json()["data"]["transactions"]
    expenses = [row for row in rows if row["txn_type"] == "expense"
                and row["posting_state"] == "active"]
    assert len(expenses) == 1
    assert expenses[0]["receipt_id"] is None
    assert expenses[0]["project"] == "Contractor"
    detail = page.request.get(f"/api/ledger/{expenses[0]['txn_id']}").json()["data"]
    assert sorted(d["role"] for d in detail["documents"]) == [
        "support", "support", "support", "voucher"]
    page.goto(f"/ledger?txn={expenses[0]['txn_id']}")
    links = page.locator(".drawer-documents .document-link")
    expect(links.locator("strong")).to_have_text([
        voucher["voucher_id"], f"{voucher['voucher_id']}-R1", f"{voucher['voucher_id']}-R2"])
    expect(links.first.locator("small")).to_contain_text("Voucher and Receipt")
    # A receipt that turns up later is added from the ledger as R3.
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    buffer = io.BytesIO()
    writer.write(buffer)
    page.request.post("/api/documents/capture-merge", multipart={
        "files": {"name": "late-receipt.pdf", "mimeType": "application/pdf",
                  "buffer": buffer.getvalue()}})
    page.reload()
    page.get_by_label("Add receipt").select_option(label="late-receipt.pdf")
    page.get_by_role("button", name="Add receipt", exact=True).click()
    expect(links.locator("strong")).to_have_text([
        voucher["voucher_id"], f"{voucher['voucher_id']}-R1",
        f"{voucher['voucher_id']}-R2", f"{voucher['voucher_id']}-R3"])


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
    editor = add_row(page, mxn="400")
    code_row(editor)
    saved(page)
    expect(page.locator("#er-balance-help")).to_contain_text("spent more pesos")
    approve(page)
    settlement = page.locator("#er-settlement-form")
    expect(settlement).to_be_visible()
    settlement.get_by_label("Settlement date", exact=True).fill("2026-09-04")
    settlement.get_by_label("MXN returned", exact=True).fill("100")
    page.get_by_role("button", name="Review settlement", exact=True).click()
    expect(page.locator("#er-settlement-preview")).to_contain_text("25.000000 MXN/USD")
    expect(page.locator("#er-settlement-preview")).to_contain_text("USD 4.00")
    page.get_by_role("button", name="Confirm settlement", exact=True).click()
    expect(page.locator("#er-settlement-message")).to_contain_text("confirm that all cash is accounted for")
    settlement.locator('[name="confirmed"]').check()
    page.screenshot(path=str(artifacts / f"closeout-{width}.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), page.evaluate(
        "[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(e => [e.tagName, e.id, e.className, e.getBoundingClientRect().width]).slice(0, 20)")
    with page.expect_popup() as popup:
        page.get_by_role("button", name="Confirm settlement", exact=True).click()
    popup.value.wait_for_url("**/addendum/*/print")
    assert page.url.endswith(f"/{vid}")
    popup.value.close()
    paperwork = page.request.get(f"/{vid}/api/report").json()["data"]["paperwork"]
    assert paperwork["reasons"] == ["addendum needed"]
    aid = paperwork["closeout"]["return_addendum_id"]
    page.goto(f"/review?item=addendum:{aid}")
    upload(page, "Upload addendum", "signed-return.pdf")
    page.get_by_role("button", name="File addendum", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    page.goto(f"/{vid}")
    expect(page.locator("#er-settlement-state")).to_contain_text("All cash and paperwork are reconciled")
    expect(page.locator("#er-settlement-form")).to_be_hidden()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("decision", ["approve", "correct"])
def test_administrator_reviews_the_report_on_its_own_page(web, width, decision):
    page, post, artifacts = web
    vid = prepare(post, kind="signout", recipient="Ana")["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    recipient(page, vid)
    add_row(page, mxn="30", purpose="Store supplies")
    saved(page)
    submit_report(page)
    administrator(page)
    page.goto("/review")
    page.get_by_role("button", name="Expense reports").click()
    expect(page.locator(".review-summary-row")).to_contain_text("Needs approval")
    page.locator(".review-summary-row").filter(has_text=vid).click()
    page.wait_for_url(f"**/{vid}")
    expect(page.locator("#er-status")).to_have_text("Submitted")
    expect(page.locator("#er-corrections")).to_be_visible()
    page.locator(".er-row-open").first.click()
    editor = page.locator(".er-editor")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    if decision == "approve":
        code_row(editor)
        saved(page)
        approve(page)
        expect(page.locator("#er-status")).to_have_text("Approved")
        expect(page.locator("#er-activity li")).to_have_count(2)
        expect(page.locator("#er-balance")).to_have_text("USD 50.00 · MXN 70.00")
        expect(page.locator("#er-documents")).to_contain_text("receipt.pdf")
        expect(page.locator(".er-row-text small").first).to_contain_text("R1")
        # An approved receipt that is already filed is never added to.
        if not page.locator(".er-editor").count():
            page.locator(".er-row-open").first.click()
        for label in ("Add receipt", "Replace", "Remove"):
            expect(page.locator(".er-editor").get_by_role("button", name=label, exact=True)).to_have_count(0)
        expect(page.locator(".er-editor [name='usd']")).to_be_disabled()
    else:
        editor.locator("[data-correction]").fill("Please upload a clearer receipt.")
        page.locator("#er-corrections").click()
        page.locator("#er-corrections-summary").fill("The receipt is unreadable.")
        page.get_by_role("button", name="Send back to recipient", exact=True).click()
        expect(page.locator("#er-status")).to_have_text("Draft")
        expect(page.locator("#er-review")).to_contain_text("The receipt is unreadable.")
        expect(page.locator(".er-row .status-pill.warning")).to_have_text("Needs correction")
        expect(page.locator("#er-activity li")).to_have_count(1)
        recipient(page, vid)
        expect(page.locator("#er-review")).to_contain_text("The receipt is unreadable.")
        page.locator(".er-row-open").first.click()
        expect(page.locator(".er-correction")).to_contain_text("Please upload a clearer receipt.")
        expect(page.locator("#er-submit")).to_have_text("Submit corrections")
    page.screenshot(path=str(artifacts / f"report-review-{decision}-{width}.png"), full_page=True)


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
        page.goto(f'/{vid}')
        awaiting = page.locator('#er-documents li').filter(has_text='Awaiting signed-paper review')
        expect(awaiting).to_have_count(1)
        awaiting.locator('a').click()
        page.wait_for_url(lambda url: url.endswith(f"/review?item={kind}:{code}"))
        button = page.get_by_role('button', name='File voucher' if kind == 'signout' else 'File addendum', exact=True)
        expect(button).to_be_visible()
        assert conn.execute('SELECT count(*) FROM transaction_documents').fetchone()[0] == 0
        button.click()
        # Wait on the observable worklist result, then check authoritative state.
        expect(page.locator('#undo-toast')).to_contain_text(f'{code} voucher filed.' if kind == 'signout' else f'{code} filed.')
        assert conn.execute('SELECT count(*) FROM transaction_documents').fetchone()[0] == 1
    finally:
        conn.close()


def test_a_row_without_a_receipt_is_approved_with_a_signed_statement(web):
    page, post, _ = web
    vid = prepare(post, kind="signout", recipient="Ana")["voucher_id"]
    recipient(page, vid)
    editor = add_row(page, usd="10", purpose="Market", receipt=False)
    editor.locator('[name="no_receipt_reason"]').fill("Stall had none")
    code_row(editor)
    saved(page)
    statement = editor.get_by_role("link", name="Print statement", exact=True)
    expect(statement).to_have_attribute("href", re.compile(rf"^/{vid}/statements\?row="))
    expect(page.locator("#er-print-statements")).to_be_visible()
    printed = page.request.get(statement.get_attribute("href")).text()
    assert f"{vid}-R1x" in printed and "Stall had none" in printed
    attach_receipt(page, "signed-statement.pdf", button="Attach signed statement")
    expect(editor.get_by_role("button", name="View statement")).to_be_visible()
    expect(page.locator("#er-print-statements")).to_be_hidden()
    saved(page)
    submit_report(page)
    administrator(page)
    page.goto(f"/{vid}")
    approve(page)
    row = page.request.get(f"/{vid}/api/report").json()["data"]["rows"][0]
    assert row["posted"]["receipt_id"] == f"{vid}-R1" and row["document_kind"] == "statement"
    # The real receipt turns up later: it takes the next number, the statement stays.
    if not page.locator(".er-editor").count():
        page.locator(".er-row-open").first.click()
    attach_receipt(page, "found-receipt.pdf")
    expect(page.locator(".er-editor").get_by_role("button", name="View receipt R2")).to_be_visible()
    expect(page.locator(".er-editor").get_by_role("button", name="View statement")).to_be_visible()


def test_the_purchase_log_is_attached_to_one_row(web):
    page, post, _ = web
    vid = prepare(post, kind="signout", recipient="Ana")["voucher_id"]
    recipient(page, vid)
    expect(page.locator("#er-print-log")).to_have_attribute("href", f"/{vid}/purchase-log")
    editor = add_row(page, usd="25", purpose="Purchase log", receipt=False)
    attach_receipt(page, "log-sheet-1.pdf", "log-sheet-2.pdf", button="Attach purchase log")
    expect(editor.get_by_role("button", name="View purchase log")).to_be_visible()
    saved(page)
    page.locator("#er-add").click()
    other = page.locator(".er-row.open .er-editor")
    expect(other.get_by_role("button", name="Add receipt", exact=True)).to_be_visible()
    expect(other.get_by_role("button", name="Attach purchase log")).to_have_count(0)
    row = page.request.get(f"/{vid}/api/report").json()["data"]["rows"][0]
    assert row["document_kind"] == "log"


def test_every_safe_count_is_saved_and_its_signed_sheet_filed(web):
    page, post, _ = web
    page.goto("/balances")
    count = page.locator("#count")
    count.locator('[name="usd"]').fill("2000")
    count.locator('[name="mxn"]').fill("15000")
    count.get_by_role("button", name="Compare").click()
    expect(page.locator("#delta")).to_contain_text("The count matches.")
    expect(page.get_by_label("Count sheet or photo (optional)")).to_have_count(0)
    page.get_by_role("button", name="Save count", exact=True).click()
    sheet = page.locator("#delta").get_by_role("link", name="Print count sheet")
    expect(sheet).to_be_visible()
    count_id = sheet.get_attribute("href").split("/")[-2]
    assert f"Txn-{count_id}" in page.request.get(sheet.get_attribute("href")).text()
    page.goto(f"/review?item=count:{count_id}")
    expect(page.locator("#review-drawer")).to_contain_text("matched the records")
    upload(page, "Upload count sheet", "signed-count.pdf")
    expect(page.locator('.doc-row.filled[data-document-slot="primary"]')).to_be_visible()
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    detail = page.request.get(f"/api/ledger/{count_id}").json()["data"]
    assert [d["reference"] for d in detail["documents"]] == [f"Txn-{count_id}"]
    assert detail["transaction"]["incomplete"] is False


def test_expense_report_requires_purpose_and_recovers_lost_save_response(web):
    page, post, _ = web
    vid = prepare(post, kind="signout")["voucher_id"]
    recipient(page, vid)
    editor = add_row(page, usd="10", purpose="")
    saved(page)
    page.locator("#er-submit").click()
    expect(page.locator("#er-message")).to_have_text("Row 1: describe what it was for")
    requests = []
    def lose_response(route):
        if route.request.method != "PATCH":
            return route.continue_()
        requests.append(route.request.post_data_json["submission_id"])
        if len(requests) == 1:
            route.fetch()  # The server commits, but the browser never sees the response.
            route.abort()
        else:
            route.continue_()
    page.route(f"**/{vid}/api/rows/*", lose_response)
    editor.locator('[name="purpose"]').fill("School supplies")
    expect(page.locator("#er-retry")).to_be_visible()
    editor.locator('[name="purpose"]').fill("School supplies, pencils")
    saved(page)
    assert requests[0] == requests[1]
    row = page.request.get(f"/{vid}/api/report").json()["data"]["rows"][0]
    assert row["purpose"] == "School supplies, pencils"
    submit_report(page)


@pytest.mark.parametrize("width", [1440, 390])
def test_receipt_pages_can_be_added_replaced_and_removed(web, width):
    page, post, _ = web
    vid = prepare(post, kind="signout")["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/{vid}")
    editor = add_row(page, usd="10", purpose="Office supplies")
    saved(page)
    row = lambda: page.request.get(f"/{vid}/api/report").json()["data"]["rows"][0]
    view_rows = lambda: page.request.get(f"/{vid}/api/report").json()["data"]["rows"]
    before = row()
    thumb = page.request.get(before["document"]["thumbnail_url"])
    assert thumb.ok and thumb.headers["content-type"] == "image/jpeg"
    # Wait for the save that attaches the new document, not an earlier autosave.
    row_saved = lambda response: ("/api/rows/" in response.url and response.request.method == "PATCH"
                                  and "document_id" in (response.request.post_data_json or {}))
    assert len(PdfReader(io.BytesIO(page.request.get(before["document"]["stream_url"]).body())).pages) == 1
    expect(editor.get_by_role("button", name="Add page", exact=True)).to_have_count(0)
    # Replacing stages the whole receipt again, every page in order.
    with page.expect_response(row_saved):
        attach_receipt(page, "page-one.pdf", "page-two.pdf", button="Replace")
    replaced = row()
    assert replaced["document_id"] != before["document_id"]
    assert len(PdfReader(io.BytesIO(page.request.get(replaced["document"]["stream_url"]).body())).pages) == 2
    expect(editor.locator('[name="purpose"]')).to_have_value("Office supplies")
    # Removing a receipt waits for Undo; using Undo keeps it.
    editor.get_by_role("button", name="Remove", exact=True).click()
    expect(page.locator(".undo-toast")).to_contain_text("Receipt removed")
    expect(editor.get_by_role("button", name="Add receipt", exact=True)).to_be_visible()
    page.locator(".undo-toast").get_by_role("button", name="Undo").click()
    expect(editor.locator(".er-receipt-thumb")).to_be_visible()
    assert row()["document"] is not None
    editor.get_by_role("button", name="Remove", exact=True).click()
    expect(page.locator(".undo-toast")).to_have_count(0)
    expect(editor.get_by_role("button", name="Add receipt", exact=True)).to_be_visible()
    for _ in range(40):
        if row()["document"] is None:
            break
        page.wait_for_timeout(100)
    assert row()["document"] is None
    # Deleting a row hides it at once, restores on Undo, and commits after the wait.
    editor.get_by_role("button", name="Delete row", exact=True).click()
    expect(page.locator(".er-row")).to_have_count(0)
    expect(page.locator(".undo-toast")).to_contain_text("Row deleted")
    page.locator(".undo-toast").get_by_role("button", name="Undo").click()
    expect(page.locator(".er-row")).to_have_count(1)
    assert len(view_rows()) == 1
    page.locator(".er-row-open").click()
    page.locator(".er-editor").get_by_role("button", name="Delete row", exact=True).click()
    expect(page.locator(".er-row")).to_have_count(0)
    for _ in range(40):
        if not view_rows():
            break
        page.wait_for_timeout(100)
    assert view_rows() == []
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
    expect(page.locator('[data-attach-menu="withdrawal receipt"] .attach-toggle')).to_be_visible()

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
    page.screenshot(path=str(artifacts / f"document-preview-{width}.png"), full_page=True)
    result.get_by_role("button", name="Attach", exact=True).click()
    expect(page.locator(".doc-row.filled")).to_be_visible()
    # Attaching stages the document; nothing is filed until the record is saved.
    assert page.request.get(f"/api/ledger/{txn_id}").json()["data"]["documents"] == []
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
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
    page.get_by_role("button", name="Back to documents", exact=False).click()
    answer_confirm(page, accept=False)
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
def test_capture_preview_returns_to_inbox(web, width):
    page, _, _ = web
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto('/capture')
    upload(page, 'Choose files', 'camera-photo.pdf')
    page.get_by_role('button', name='Save document').click()
    expect(page.get_by_role('link', name='Preview', exact=True)).to_be_visible()
    page.get_by_role('link', name='Preview', exact=True).click()
    expect(page.get_by_role('link', name='Back to Capture')).to_be_visible()
    expect(page.locator('.document-viewer iframe')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.get_by_role('link', name='Back to Capture').click()
    expect(page.get_by_role('heading', name='Document inbox')).to_be_visible()
    expect(page.get_by_role('link', name='Preview', exact=True)).to_be_visible()


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
    expect(page.locator('#detail .doc-row.filled .doc-name').first).to_have_text(vid + '.pdf')
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
        '2 open signouts worth USD 816.67')
    peso_row = page.locator(f'.signout-link[href="/{peso["voucher_id"]}"]')
    expect(peso_row.locator('.signout-basis')).to_have_text('USD value 666.67')
    expect(peso_row.locator('.signout-cash > b').first).to_have_text('USD 0.00')
    expect(peso_row.locator('.signout-cash > b').last).to_have_text('MXN 10,000.00')
    mixed_row = page.locator(f'.signout-link[href="/{mixed["voucher_id"]}"]')
    expect(mixed_row.locator('.signout-basis')).to_have_text('USD value 100.00')
    expect(mixed_row.locator('.signout-cash > b').first).to_have_text('USD 50.00')
    for row in (peso_row, mixed_row):
        box = row.locator('.signout-basis').bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= width
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


@pytest.mark.parametrize('remember', [True, False])
def test_phone_login_persistence_after_browser_restart(web, remember):
    page, _, tmp_path = web
    origin = page.request.get('/api/health').url.removesuffix('/api/health')
    browser_type = page.context.browser.browser_type
    profile = tmp_path / 'phone-profile'
    options = dict(base_url=origin, viewport={'width': 390, 'height': 844})
    context = browser_type.launch_persistent_context(profile, **options)
    try:
        phone = context.pages[0]
        phone.goto('/login')
        expect(phone.locator('[name="remember"]')).to_be_checked()
        phone.locator('[name="remember"]').set_checked(remember)
        phone.locator('[name="password"]').fill('test')
        assert phone.locator('link[rel="manifest"]').get_attribute('href') == '/static/manifest.webmanifest'
        assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        phone.get_by_role('button', name='Log in', exact=True).click()
        expect(phone).to_have_url(origin + '/transactions/new')
        cookie = next(c for c in context.cookies() if c['name'] == 'session')
        assert (cookie['expires'] > 0) == remember
    finally:
        context.close()
    context = browser_type.launch_persistent_context(profile, **options)
    try:
        phone = context.pages[0]
        phone.goto('/balances')
        if remember:
            expect(phone).to_have_url(origin + '/balances')
            phone.goto('/api/logout')
            expect(phone).to_have_url(origin + '/login')
            phone.goto('/balances')
            expect(phone).to_have_url(origin + '/login')
        else:
            expect(phone).to_have_url(origin + '/login')
    finally:
        context.close()


@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('kind', ['direct_expense', 'signout'])
def test_approver_default_override_and_larger_print_names(web, width, kind):
    page, _, artifacts = web
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto('/settings')
    default = page.locator('#voucher-defaults-form [name="approver_name"]')
    # Wait until the asynchronous settings load has populated the page.
    expect(page.locator('#account-list')).to_contain_text('Petty Cash Fund')
    default.fill('Default Approver')
    page.get_by_role('button', name='Save voucher defaults').click()
    expect(page.locator('#settings-status')).to_have_text('Voucher defaults saved.')
    page.reload()
    expect(default).to_have_value('Default Approver')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.goto('/transactions/new')
    page.locator(".type-picker label").filter(
        has=page.locator(f'[name="type"][value="{kind}"]')).click()
    expect(page.locator('[name="approver_name"]')).to_have_value('Default Approver')
    name = 'María Guadalupe Hernández de la Torre'
    approver = 'Alexandra Rodríguez <Operations>'
    purpose = 'Supplies and tools for the school maintenance and homebuilding projects'
    page.locator('[name="recipient"]').fill(name)
    page.locator('[name="purpose"]').fill(purpose)
    page.locator('[name="approver_name"]').fill(approver)
    page.locator('#simple-amount-fields [name="usd"]').fill('20')
    with page.expect_popup() as opened:
        page.locator('#prep-submit').click()
    paper = opened.value
    paper.wait_for_url('**/voucher/*/print')
    expect(paper.locator('#paper')).to_contain_text(approver)
    for value in (name, approver, purpose):
        field = paper.locator('.line.filled').filter(has_text=value)
        expect(field).to_have_count(1)
        assert field.evaluate('node => getComputedStyle(node).fontSize') == '22px'
    expect(paper.locator('operations')).to_have_count(0)
    pdf = artifacts / f'{kind}-large-names.pdf'
    paper.pdf(path=str(pdf), prefer_css_page_size=True)
    pages = PdfReader(pdf).pages
    assert len(pages) == 1
    assert approver in pages[0].extract_text()
    paper.screenshot(path=str(artifacts / f'{kind}-large-names.png'), full_page=True)
    paper.close()
    assert page.request.get('/api/settings').json()['data']['default_approver_name'] == 'Default Approver'


@pytest.mark.parametrize("width", [1440, 390])
def test_bulk_codes_split_receipt_and_cash_return_row(web, width):
    page, post, artifacts = web
    post("/api/settings/classes", class_code="8.1", name="School")
    vid = prepare(post, kind="signout", recipient="Ana")["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    recipient(page, vid)
    add_row(page, mxn="20", purpose="Pens")
    editor = add_row(page, mxn="50", purpose="Paint and brushes")
    saved(page)

    editor.get_by_role("button", name="Split receipt", exact=True).click()
    workspace = page.locator(".split-workspace")
    expect(workspace.locator(".sfw-card")).to_have_count(2)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    workspace.screenshot(path=str(artifacts / f"split-workspace-{width}.png"))
    workspace.locator('.sfw-detail [name="mxn"]').fill("30")
    workspace.locator('[data-sfw-select="1"]').click()
    workspace.locator('.sfw-detail [name="mxn"]').fill("15")
    expect(workspace.locator(".sfw-balance")).to_contain_text("MXN 5.00")
    workspace.locator('.sfw-detail [name="mxn"]').fill("20")
    expect(workspace.locator(".sfw-balance")).to_have_text("The parts add up.")
    workspace.get_by_role("button", name="Done").click()
    saved(page)
    page.locator(".er-row.open [data-edit-split]").click()
    expect(workspace.locator('.sfw-detail [name="mxn"]')).to_have_value("30.00")
    workspace.get_by_role("button", name="Cancel", exact=True).click()

    page.locator("#er-select-all").check()
    expect(page.locator("#er-bulk-count")).to_have_text("2 rows selected")
    page.locator("#er-bulk-class").select_option("8.1")
    page.locator("#er-bulk-account").select_option("1000")
    page.get_by_role("button", name="Apply to selected", exact=True).click()
    expect(page.locator("#er-bulk")).to_be_hidden()
    rows = page.request.get(f"/{vid}/api/report").json()["data"]["rows"]
    assert rows[0]["class_code"] == "8.1" and rows[0]["expense_account"] == "1000"
    assert {(s["class_code"], s["expense_account"]) for s in rows[1]["splits"]} == {("8.1", "1000")}
    expect(page.locator(".er-flag")).to_have_count(0)

    expect(page.locator("#er-balance")).to_have_text("USD 50.00 · MXN 30.00")
    page.get_by_role("button", name="Add cash return", exact=True).click()
    expect(page.locator("#er-balance")).to_have_text("USD 0.00 · MXN 0.00")
    expect(page.locator("#er-balance-help")).to_contain_text("Everything is accounted for.")
    submit_report(page)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / f"report-bulk-split-{width}.png"), full_page=True)

    administrator(page)
    page.goto(f"/{vid}")
    approve(page)
    ledger = page.request.get("/api/ledger").json()["data"]["transactions"]
    expenses = [row for row in ledger if row["voucher_id"] == vid and row["txn_type"] == "expense"]
    assert sorted(row["mxn"] for row in expenses) == ["20.00", "20.00", "30.00"]
    assert {row["class_code"] for row in expenses} == {"8.1"}
    expect(page.locator("#er-settlement-form [name='return_usd']")).to_have_value("50.00")
    expect(page.locator("#er-settlement-form [name='return_mxn']")).to_have_value("30.00")


def test_split_workspace_cancel_and_mixed_currency_receipt(web):
    page, post, _ = web
    vid = prepare(post, kind="signout")["voucher_id"]
    recipient(page, vid)
    editor = add_row(page, usd="10", mxn="-5", purpose="Market supplies")
    editor.locator('[name="receipt_total"]').fill("100")
    saved(page)
    expect(editor.locator('[name="class_code"]')).to_have_count(1)
    expect(page.locator(".split-workspace")).to_have_count(0)

    editor.get_by_role("button", name="Split receipt", exact=True).click()
    workspace = page.locator(".split-workspace")
    expect(workspace.locator(".sfw-face")).to_be_visible()
    workspace.locator('.sfw-detail [name="usd"]').fill("6")
    workspace.get_by_role("button", name="Cancel", exact=True).click()
    expect(workspace).to_have_count(0)
    assert page.request.get(f"/{vid}/api/report").json()["data"]["rows"][0]["splits"] == []

    editor.get_by_role("button", name="Split receipt", exact=True).click()
    workspace.get_by_role("button", name="Done").click()
    expect(workspace.locator(".sfw-error")).to_contain_text("must add up")
    workspace.locator('.sfw-detail [name="usd"]').fill("6")
    workspace.locator('.sfw-detail [name="mxn"]').fill("-3")
    workspace.locator('.sfw-detail [name="receipt_total"]').fill("60")
    workspace.locator('.sfw-detail [name="class_code"]').select_option("7.5")
    workspace.locator('.sfw-detail [name="expense_account"]').select_option("1000")
    workspace.get_by_role("button", name="Apply codes to all splits").click()
    workspace.locator('[data-sfw-select="1"]').click()
    expect(workspace.locator('.sfw-detail [name="class_code"]')).to_have_value("7.5")
    expect(workspace.locator('.sfw-detail [name="expense_account"]')).to_have_value("1000")
    workspace.locator('.sfw-detail [name="usd"]').fill("4")
    workspace.locator('.sfw-detail [name="mxn"]').fill("-2")
    workspace.locator('.sfw-detail [name="receipt_total"]').fill("40")
    expect(workspace.locator(".sfw-balance")).to_have_text("The parts add up.")
    workspace.get_by_role("button", name="Done").click()
    saved(page)
    splits = page.request.get(f"/{vid}/api/report").json()["data"]["rows"][0]["splits"]
    assert [(part["usd"], part["mxn"], part["receipt_total"]) for part in splits] == [
        ("6.00", "-3.00", "60.00"), ("4.00", "-2.00", "40.00")]
    assert {(part["class_code"], part["expense_account"]) for part in splits} == {
        ("7.5", "1000")}


def test_review_split_workspace_saves_draft_without_receipt(web):
    page, post, _ = web
    voucher = prepare(post)
    vid = voucher["voucher_id"]
    page.goto(f"/review?item=direct_expense:{vid}")
    expect(page.locator(".split-workspace")).to_have_count(0)
    expect(page.locator('#direct-expense-form [name="class_code"]')).to_have_count(1)
    page.get_by_role("button", name="Split receipt", exact=True).click()
    workspace = page.locator(".split-workspace")
    expect(workspace.locator(".sfw-no-receipt")).to_be_visible()
    workspace.locator('.sfw-totals [name="usd"]').fill("16")
    workspace.locator('.sfw-totals [name="mxn"]').fill("120")
    workspace.locator('.sfw-detail [name="usd"]').fill("6")
    workspace.locator('.sfw-detail [name="mxn"]').fill("40")
    workspace.locator('.sfw-detail [name="class_code"]').select_option("7.5")
    workspace.locator('.sfw-detail [name="expense_account"]').select_option("1000")
    workspace.get_by_role("button", name="Apply codes to all splits").click()
    workspace.locator('[data-sfw-select="1"]').click()
    workspace.locator('.sfw-detail [name="usd"]').fill("10")
    workspace.locator('.sfw-detail [name="mxn"]').fill("80")
    workspace.get_by_role("button", name="Done").click()
    expect(page.locator(".rf-split-summary strong")).to_have_text("Split into 2 parts")
    page.get_by_role("button", name="Save & review next", exact=True).click()
    expect(page.locator("#direct-expense-form .form-status")).to_contain_text("Progress saved")
    expect(page.locator("#review-drawer")).to_be_visible()
    expect(page.locator(".review-summary-row .badge")).to_have_text(["Needs voucher"])
    page.reload()
    page.get_by_role("button", name="Edit split", exact=True).click()
    expect(workspace.locator('.sfw-totals [name="usd"]')).to_have_value("16.00")
    expect(workspace.locator('.sfw-totals [name="mxn"]')).to_have_value("120.00")
    expect(workspace.locator('.sfw-detail [name="usd"]')).to_have_value("6.00")
    workspace.get_by_role("button", name="Cancel", exact=True).click()


def test_review_keeps_the_open_record_and_tab_in_the_url(web):
    page, post, _ = web
    voucher = prepare(post)
    page.goto("/review")
    page.locator(".review-summary-row").filter(has_text=voucher["voucher_id"]).click()
    expect(page).to_have_url(re.compile(rf"/review\?item=direct_expense:{voucher['voucher_id']}$"))
    page.reload()
    expect(page.locator("#review-drawer-title")).to_have_text(voucher["voucher_id"])
    expect(page.locator("#worklist .col-cash").first).to_be_hidden()
    page.get_by_role("button", name="Expense reports").click()
    expect(page).to_have_url(re.compile(r"/review\?tab=reports$"))
    page.reload()
    expect(page.locator("#work-heading")).to_have_text("Expense reports")
    expect(page.locator("#worklist th.col-rows")).to_be_visible()


def test_ledger_filters_live_in_the_url_and_forms_fit_each_type(web):
    page, post, _ = web
    signout = prepare(post, kind="signout", recipient="Ana")
    page.goto("/ledger")
    page.locator('#filters [name="type"]').select_option("signout")
    expect(page).to_have_url(re.compile(r"/ledger\?type=signout$"))
    expect(page.locator("#txns tbody tr")).to_have_count(1)
    page.reload()
    expect(page.locator('#filters [name="type"]')).to_have_value("signout")
    expect(page.locator("#txns tbody tr")).to_have_count(1)
    page.locator("#txns tbody tr").first.click()
    form = page.locator("#history-edit-form")
    expect(form.get_by_label("USD", exact=True)).to_have_value("50.00")
    expect(form.locator('[name="recipient"]')).to_have_count(0)
    expect(form.locator('[name="from_account"]')).to_have_count(0)
    expect(form.locator('[name="class_code"]')).to_have_count(0)
    page.get_by_role("button", name="Close", exact=True).click()
    page.get_by_role("button", name="Clear", exact=True).click()
    expect(page).to_have_url(re.compile(r"/ledger$"))
    expect(page.locator("#txns tbody tr")).to_have_count(2)
    assert signout["voucher_id"]


def test_ledger_drawer_manages_focus_and_void_asks_first(web):
    page, post, _ = web
    txn_id = post("/api/entries/withdrawal", date="2026-09-03", usd="80",
                  notes="Downtown ATM")["txn_ids"][0]
    page.goto("/ledger")
    # Fiscal year is a choice among the years that have transactions.
    years = page.locator('select[name="fy"] option')
    expect(years).to_have_text(["All years", "FY2027"])
    row = page.locator(f'#txns tbody tr[data-txn="{txn_id}"]')
    row.focus()
    page.keyboard.press("Enter")
    drawer = page.locator("#transaction-drawer")
    expect(drawer).to_be_visible()
    assert page.evaluate("document.activeElement.id") == "transaction-drawer"
    # Tab stays inside the drawer.
    for _ in range(40):
        page.keyboard.press("Tab")
        assert page.evaluate("document.getElementById('transaction-drawer').contains(document.activeElement)")
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()
    assert page.evaluate("document.activeElement.dataset.txn") == str(txn_id)
    # Voiding asks first; Escape and Cancel both leave the transaction alone.
    row.click()
    page.locator("#void-form textarea").fill("Entered twice")
    page.locator("#void-form").get_by_role("button", name="Void transaction").click()
    dialog = page.locator("dialog.confirm-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("no longer counts in balances")
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(drawer).to_be_visible()
    assert page.request.get(f"/api/ledger/{txn_id}").json()["data"]["transaction"]["posting_state"] == "active"
    page.locator("#void-form").get_by_role("button", name="Void transaction").click()
    answer_confirm(page)
    for _ in range(40):
        state = page.request.get(f"/api/ledger/{txn_id}").json()["data"]["transaction"]["posting_state"]
        if state != "active":
            break
        page.wait_for_timeout(100)
    assert state == "void"


def test_documents_list_menu_preview_and_undoable_removal(web):
    page, post, _ = web
    voucher = prepare(post)
    page.goto(f"/review?item=direct_expense:{voucher['voucher_id']}")
    # Documents come first, ahead of the coding fields, one line each.
    rows = page.locator(".doc-list .doc-row")
    expect(rows).to_have_count(2)
    assert page.evaluate("""() => {
        const list = document.querySelector('.doc-list');
        const form = document.getElementById('direct-expense-form');
        return Boolean(list.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING);
    }""")
    assert all(row.bounding_box()["height"] < 80 for row in rows.all())
    # The Attach menu opens on click, and Escape closes it and keeps focus on the button.
    toggle = page.locator('[data-attach-menu="voucher"] .attach-toggle')
    toggle.click()
    expect(page.locator('[data-attach-menu="voucher"] .attach-options')).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator('[data-attach-menu="voucher"] .attach-options')).to_be_hidden()
    assert page.evaluate("document.activeElement.classList.contains('attach-toggle')")
    upload(page, "Upload voucher", "signed-voucher.pdf")
    filled = page.locator('.doc-row.filled[data-document-slot="primary"]')
    expect(filled).to_be_visible()
    expect(filled.locator(".doc-name")).to_have_text("signed-voucher.pdf")
    # Clicking the document previews it in the inbox panel, not inline.
    filled.locator(".doc-open").click()
    expect(page.locator("#document-preview")).to_contain_text("signed-voucher.pdf")
    page.get_by_role("button", name="Back to documents").click()
    # Removing it can be undone, and the receipt toggle goes with the voucher.
    page.get_by_role("checkbox", name="Also file as a receipt").check()
    page.get_by_role("button", name="Remove voucher").click()
    expect(filled).to_have_count(0)
    expect(page.get_by_role("checkbox", name="Also file as a receipt")).to_have_count(0)
    page.locator("#undo-toast").get_by_role("button", name="Undo").click()
    expect(filled.locator(".doc-name")).to_have_text("signed-voucher.pdf")


@pytest.mark.parametrize("path", ["/transactions/new", "/capture", "/balances", "/reports",
                                  "/settings", "/review", "/donation"])
def test_admin_pages_do_not_scroll_sideways_on_a_phone(web, path):
    page, post, _ = web
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(path)
    page.wait_for_load_state("networkidle")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


def test_settings_groups_and_the_database_replace_warning(web):
    page, post, _ = web
    page.goto("/settings")
    expect(page.locator("h2.settings-group")).to_have_text(["General", "Codes", "Data"])
    # Drive backup and export are ordinary cards; replacing the database is set apart.
    expect(page.locator(".settings-card.danger-card h2")).to_have_text("Replace the database")
    expect(page.locator("#staged-database-import")).to_be_hidden()
    expect(page.locator("#sync-form")).to_be_visible()
    # Turning a code off still works from any group.
    page.locator("#account-list button[data-kind]").first.click()
    expect(page.locator("#account-list .status-pill", has_text="Off")).to_have_count(1)
