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


@pytest.mark.parametrize("width", [1440, 390])
def test_signee_language_toggle_preserves_receipt_and_login_preference(web, width):
    page, post, artifacts = web
    vid = prepare(post, kind="signout", recipient="Ana")["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/{vid}")
    page.get_by_label("Receipt date", exact=True).fill("2026-09-03")
    page.get_by_label("Vendor", exact=True).fill("Open")
    page.get_by_label("What was purchased", exact=True).fill("Supplies")
    page.get_by_label("MXN spent", exact=True).fill("0")
    upload(page, "Choose files", "receipt.pdf")
    page.get_by_role("button", name="Español", exact=True).click()
    expect(page.locator("html")).to_have_attribute("lang", "es")
    expect(page.get_by_label("Proveedor", exact=True)).to_have_value("Open")
    expect(page.get_by_label("Qué se compró", exact=True)).to_have_value("Supplies")
    expect(page.locator("#receipt-page-count")).to_have_text("1 página")
    expect(page.get_by_role("button", name="Eliminar página 1", exact=True)).to_be_visible()
    page.locator("#save-claim").click()
    expect(page.locator("#claim-status")).to_have_text("Ingrese un importe positivo en USD o MXN.")
    page.get_by_role("button", name="English", exact=True).click()
    expect(page.locator("#claim-status")).to_have_text("Enter a positive amount in USD or MXN.")
    expect(page.get_by_label("Vendor", exact=True)).to_have_value("Open")
    page.get_by_label("MXN spent", exact=True).fill("1200")
    page.get_by_role("button", name="Español", exact=True).click()
    page.locator("#save-claim").click()
    expect(page.locator("#claim-count")).to_have_text("1")
    expect(page.locator("#claims")).to_contain_text("1,200.00")
    expect(page.locator("#claims .status-pill")).to_have_text("Sin enviar")
    page.get_by_label("Seleccionar Open", exact=True).check()
    page.get_by_role("button", name="English", exact=True).click()
    expect(page.get_by_label("Select Open", exact=True)).to_be_checked()
    page.get_by_role("button", name="Español", exact=True).click()
    page.get_by_role("button", name="Enviar recibos seleccionados a revisión", exact=True).click()
    expect(page.locator("#claims .status-pill")).to_have_text("Enviado a revisión")
    expect(page.locator("#workspace-status")).to_have_text("1 recibo enviado a revisión.")
    page.reload()
    expect(page.locator("html")).to_have_attribute("lang", "es")
    expect(page.locator("#packages")).to_contain_text("1 recibo")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / f"signee-spanish-{width}.png"), full_page=True)

    # The preference follows the employee through login, but does not translate admin pages.
    page.goto(f"/signouts/{vid}")
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.locator("[data-signee-language]")).to_have_count(0)
    page.context.clear_cookies()
    page.goto(f"/{vid}")
    page.wait_for_url("**/user-login?next=**")
    expect(page.get_by_role("heading", name="Abrir página de recibos")).to_be_visible()
    page.get_by_label("Contraseña compartida", exact=True).fill("wrong")
    page.get_by_role("button", name="Continuar", exact=True).click()
    expect(page.locator("#login-status")).to_have_text("Contraseña incorrecta")
    page.get_by_label("Contraseña compartida", exact=True).fill("test")
    page.get_by_role("button", name="Continuar", exact=True).click()
    page.wait_for_url(f"**/{vid}")
    expect(page.locator("html")).to_have_attribute("lang", "es")
    expect(page.locator("#claims .status-pill")).to_have_text("Enviado a revisión")


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("language", ["en", "es"])
def test_employee_receipt_editor_and_cards(web, width, language):
    page, post, artifacts = web
    vid = prepare(post, kind="signout")["voucher_id"]
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"/{vid}")
    expect(page.locator("#claim-editor")).to_be_visible()
    assert page.evaluate("scrollY") == 0
    if language == "es":
        page.get_by_role("button", name="Español", exact=True).click()
    expect(page.locator("#claim-editor #receipt-document-picker")).to_be_visible()
    expect(page.locator("#receipt-list-panel")).to_be_hidden()
    expect(page.locator("#signee-document-viewer")).to_be_hidden()
    expect(page.locator('[name="record_cash_payment"]')).to_have_count(0)
    expect(page.locator("#receipt-face-fields")).to_be_hidden()
    assert page.locator('#take-receipt-photo [data-i18n]').bounding_box()["width"] > 40
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / f"employee-editor-{language}-{width}.png"), full_page=True)
    page.locator('#claim-form [name="vendor"]').fill("Store")
    page.locator('#claim-form [name="description"]').fill("Supplies")
    page.locator('#claim-form [name="mxn"]').fill("40")
    upload(page, "Elegir archivos" if language == "es" else "Choose files", "receipt.pdf")
    page.locator("#save-claim").click()
    expect(page.locator("#claim-editor")).to_be_hidden()
    expect(page.locator("#claims > li")).to_have_count(1)
    expect(page.locator("#claims")).to_contain_text("MXN 40.00")
    page.locator(".edit-claim").click()
    expect(page.locator('#claim-form [name="mxn"]')).to_have_value("40.00")
    page.locator('#claim-form [name="mxn"]').fill("45")
    page.locator("#save-claim").click()
    expect(page.locator("#claim-editor")).to_be_hidden()
    expect(page.locator("#claims")).to_contain_text("MXN 45.00")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / f"employee-receipts-{language}-{width}.png"), full_page=True)
    page.locator("[data-claim-select]").check()
    page.locator("#submit-selected").click()
    expect(page.locator("#claims .status-pill")).to_have_text(
        "Enviado a revisión" if language == "es" else "Sent for review")
    page.locator(".signee-claim-row").click()
    expect(page.locator("#signee-document-viewer iframe")).to_be_visible()
    expect(page.locator("#claim-form")).to_be_hidden()
    expect(page.locator("#receipt-document-picker")).to_be_hidden()
    page.locator("#cancel-edit").click()
    expect(page.locator("#claim-editor")).to_be_hidden()


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
    page.locator('#claim-form [name="vendor"]').fill("Store")
    page.locator('#claim-form [name="description"]').fill("Supplies")
    page.locator('#claim-form [name="usd"]').fill(usd)
    page.locator('#claim-form [name="mxn"]').fill(mxn)
    if face_currency:
        page.set_viewport_size({"width": 390, "height": 1000})
        page.get_by_role("button", name="Español", exact=True).click()
        expect(page.get_by_label(f"Total del recibo en {face_currency.upper()}", exact=True)).to_be_visible()
        expect(page.locator(f"#receipt-face-{face_currency}")).to_be_visible()
        page.locator(f'#claim-form [name="face_{face_currency}"]').fill(face)
        other = "usd" if face_currency == "mxn" else "mxn"
        expect(page.locator(f"#receipt-face-{other}")).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(artifacts / f"signed-cash-{face_currency}.png"), full_page=True)
        page.get_by_role("button", name="English", exact=True).click()
    else:
        expect(page.locator("#receipt-face-fields")).to_be_hidden()
    upload(page, "Choose files", "receipt.pdf")
    if usd == "50":
        page.locator('#claim-form [name="usd"]').fill("50.001")
        page.locator("#save-claim").click()
        expect(page.locator("#claim-status")).to_have_text("Enter amounts with at most two decimal places.")
        assert page.request.get(f"/{vid}/state").json()["data"]["claims"] == []
        page.locator('#claim-form [name="usd"]').fill("50")
    page.locator("#save-claim").click()
    expect(page.locator("#claim-count")).to_have_text("1")
    saved = page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    assert saved["usd_paid_cents"] - saved["usd_change_cents"] == round(float(usd or 0) * 100)
    assert saved["mxn_paid_centavos"] - saved["mxn_change_centavos"] == round(float(mxn or 0) * 100)
    assert saved["face_usd_cents"] == (int(face) * 100 if face_currency == "usd" else None)
    assert saved["face_mxn_centavos"] == (int(face) * 100 if face_currency == "mxn" else None)
    page.locator("[data-claim-select]").check()
    page.locator("#submit-selected").click()
    expect(page.locator("#claims .status-pill")).to_have_text("Sent for review")
    pid = page.request.get(f"/{vid}/state").json()["data"]["packages"][0]["package_id"]
    page.goto(f"/review?item=package:{pid}")
    page.locator('#claim-review-form [name="expense_account"]').select_option("1000")
    page.locator('#claim-review-form [name="class_code"]').select_option("7.5")
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
    page.locator('#claim-form [name="vendor"]').fill("Store")
    page.locator('#claim-form [name="description"]').fill("Supplies")
    page.locator('#claim-form [name="usd"]').fill("10")
    upload(page, "Choose files", "receipt.pdf")
    page.locator("#save-claim").click()
    expect(page.locator("#claim-count")).to_have_text("1")
    claim = page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    legacy = {"submission_id": str(uuid.uuid4()), "expected_content_version": 1,
              "document_id": claim["document_id"], "date": claim["claim_date"],
              "vendor": "Store", "description": "Old receipt", "payment_mode": mode,
              "usd_paid": "20", "usd_change": "10", "face_mxn": "160"}
    response = page.request.patch(f"/{vid}/claims/{claim['claim_id']}", data=legacy)
    assert response.ok, response.text()
    before = page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    page.reload()
    page.locator(".edit-claim").click()
    page.locator('#claim-form [name="description"]').fill("Corrected description")
    page.locator("#save-claim").click()
    expect(page.locator("#claim-editor")).to_be_hidden()
    after = page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    for field in ("usd_paid_cents", "usd_change_cents", "mxn_paid_centavos",
                  "mxn_change_centavos", "face_usd_cents", "face_mxn_centavos", "payment_mode"):
        assert after[field] == before[field]
    assert after["description"] == "Corrected description"
    page.locator(".edit-claim").click()
    page.locator('#claim-form [name="usd"]').fill("10")
    page.locator('#claim-form [name="mxn"]').fill("-60")
    expect(page.locator("#receipt-face-mxn")).to_be_visible()
    page.locator('#claim-form [name="face_mxn"]').fill("100")
    page.locator('#claim-form [name="mxn"]').fill("0")
    expect(page.locator("#receipt-face-fields")).to_be_hidden()
    page.locator('#claim-form [name="usd"]').fill("12")
    page.locator("#save-claim").click()
    expect(page.locator("#claim-editor")).to_be_hidden()
    after = page.request.get(f"/{vid}/state").json()["data"]["claims"][0]
    assert after["payment_mode"] == "cash"
    assert after["usd_paid_cents"] == 1200
    assert after["usd_change_cents"] == after["mxn_change_centavos"] == 0
    assert after["face_usd_cents"] is after["face_mxn_centavos"] is None


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
                      "**/review?item=exchange:*" if kind == "exchange" else
                      "**/ledger?txn=*")


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
    page.get_by_label("Receipt date", exact=True).fill("2026-09-03")
    page.get_by_label("Vendor", exact=True).fill("Store")
    page.get_by_label("What was purchased", exact=True).fill("Supplies")
    page.get_by_label("MXN spent", exact=True).fill("400")
    expect(page.locator("#receipt-face-fields")).to_be_hidden()
    upload(page, "Choose files", "receipt.pdf")
    page.locator("#save-claim").click()
    expect(page.locator("#claim-count")).to_have_text("1")
    page.get_by_label("Select Store", exact=True).check()
    page.get_by_role("button", name="Send selected receipts for review", exact=True).click()
    expect(page.locator("#claims .status-pill")).to_have_text("Sent for review")
    state = page.request.get(f"/{vid}/state").json()["data"]
    assert state["claims"][0]["payment_mode"] == "cash"
    pid = state["claims"][0]["package_id"]
    page.goto(f"/review?item=package:{pid}")
    page.locator('#claim-review-form [name="expense_account"]').select_option("1000")
    page.locator('#claim-review-form [name="class_code"]').select_option("7.5")
    page.get_by_role("button", name="Save & mark checked", exact=True).click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Approve all 1 receipts", exact=True).click()
    expect(page.locator("#review-drawer")).to_be_hidden()
    page.goto(f"/signouts/{vid}")
    page.locator("#closeout-card > summary").click()
    page.get_by_label("Closeout date", exact=True).fill("2026-09-04")
    page.get_by_label("MXN returned now", exact=True).fill("100")
    page.get_by_role("button", name="Review closeout", exact=True).click()
    expect(page.locator("#closeout-preview")).to_contain_text("25.000000 MXN/USD")
    expect(page.locator("#closeout-preview")).to_contain_text("USD 4.00")
    page.get_by_role("button", name="Confirm closeout", exact=True).click()
    expect(page.locator("#closeout-message")).to_contain_text("confirm that all cash is accounted for")
    page.locator('#closeout-form [name="confirmed"]').check()
    page.screenshot(path=str(artifacts / f"closeout-{width}.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), page.evaluate(
        "[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(e => [e.tagName, e.id, e.className, e.getBoundingClientRect().width]).slice(0, 20)")
    with page.expect_popup() as popup:
        page.get_by_role("button", name="Confirm closeout", exact=True).click()
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
    page.locator("#closeout-card > summary").click()
    expect(page.locator("#closeout-state")).to_contain_text("All cash and paperwork are reconciled")
    expect(page.locator("#closeout-form")).to_be_hidden()
