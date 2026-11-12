"""Read-only ledger views, broad search, pagination, and browser URL state."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from cash.db import connect


@pytest.fixture
def conn(app):
    connection = connect(Path(app.config["DATA_ROOT"]) / "cash.db")
    yield connection
    connection.close()


def insert(conn, **fields):
    values = dict(movement_id=str(uuid.uuid4()), submission_id=str(uuid.uuid4()),
                  date="2026-09-02", same_day_seq=0, txn_type="expense",
                  from_account="SAFE", to_account="EXP_SUPPLIES",
                  usd_cents=0, mxn_centavos=0)
    values.update(fields)
    return conn.execute(
        f"INSERT INTO transactions ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        list(values.values())).lastrowid


def opening(conn):
    return insert(conn, date="2026-08-01", txn_type="opening",
                  from_account="OPENING_EQUITY", to_account="SAFE",
                  usd_cents=1000_00, mxn_centavos=15000_00,
                  assigned_rate_milli=15000)


def ledger(client, query=""):
    response = client.get("/api/ledger" + query)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]


def ids(data):
    return [row["txn_id"] for row in data["transactions"]]


def state_rows(conn):
    return {
        "opening": opening(conn),
        "complete": insert(conn, date="2026-09-01", description="Complete"),
        "incomplete": insert(conn, date="2026-09-02",
                             to_account="UNALLOCATED_EXPENSE", description="Incomplete"),
        "void": insert(conn, date="2026-09-03", posting_state="void"),
        "superseded": insert(conn, date="2026-09-04", posting_state="superseded"),
    }


def test_default_active_all_history_needs_attention_and_voided_views(client, csrf, conn):
    transactions = state_rows(conn)

    default = ledger(client)
    assert ids(default) == [transactions["opening"], transactions["complete"],
                            transactions["incomplete"]]
    assert ids(ledger(client, "?view=active")) == ids(default)

    all_history = ledger(client, "?view=all")
    assert ids(all_history) == list(transactions.values())
    assert all_history["total"] == 5

    attention = ledger(client, "?view=attention")
    assert ids(attention) == [transactions["incomplete"]]
    assert attention["transactions"][0]["incomplete"] is True

    inactive = ledger(client, "?view=voided")
    assert ids(inactive) == [transactions["void"], transactions["superseded"]]
    assert {row["posting_state"] for row in inactive["transactions"]} == {
        "void", "superseded"}


@pytest.mark.parametrize("field,query", [
    ("txn_id", None),
    ("voucher_id", "fy27-9000"),
    ("recipient", "recipient needle"),
    ("vendor", "VENDOR NEEDLE"),
    ("description", "Description Needle"),
    ("project", "Project Needle"),
    ("notes", "Notes Needle"),
    ("from_account", "safe"),
    ("to_account", "exp_programs"),
    ("txn_type", "expense"),
])
def test_broad_search_covers_transaction_fields(client, csrf, conn, field, query):
    opening(conn)
    conn.execute(
        "INSERT INTO vouchers (voucher_id, type, fiscal_year, seq, recipient)"
        " VALUES ('FY27-9000', 'direct_expense', 2027, 9000, 'Recipient Needle')")
    txn_id = insert(
        conn, voucher_id="FY27-9000", recipient="Recipient Needle",
        vendor="Vendor Needle", description="Description Needle",
        project="Project Needle", notes="Notes Needle", to_account="EXP_PROGRAMS")
    value = str(txn_id) if field == "txn_id" else query

    result = ledger(client, f"?q={value.replace(' ', '+')}")

    assert txn_id in ids(result), field


def test_search_can_combine_terms_and_return_no_matches(client, csrf, conn):
    opening(conn)
    txn_id = insert(conn, recipient="Ana", vendor="Paper Shop",
                    description="Notebooks", project="After school")

    assert ids(ledger(client, "?q=ana+notebooks+paper")) == [txn_id]
    empty = ledger(client, "?q=not-present-anywhere")
    assert (empty["count"], empty["total"], empty["pages"]) == (0, 0, 1)


def test_server_side_pagination_defaults_to_25_and_reports_counts(client, csrf, conn):
    opening(conn)
    for sequence in range(26):
        insert(conn, same_day_seq=sequence + 1, description=f"Row {sequence}")

    first = ledger(client)
    assert (first["page"], first["per_page"], first["count"],
            first["total"], first["pages"]) == (1, 25, 25, 27, 2)
    assert len(first["transactions"]) == 25

    second = ledger(client, "?page=2")
    assert (second["page"], second["count"], second["total"], second["pages"]) == (2, 2, 27, 2)
    assert ids(second) == ids(ledger(client, "?per_page=100"))[25:]

    assert ids(ledger(client, "?page=last")) == ids(second)
    third = ledger(client, "?page=3&per_page=10")
    assert (third["page"], third["count"], third["total"], third["pages"]) == (3, 7, 27, 3)


@pytest.mark.parametrize("browser", ["chromium", "firefox"], indirect=True)
def test_ledger_view_search_filter_and_page_state_survive_reload(web):
    expect = pytest.importorskip("playwright.sync_api").expect
    page, post, _ = web
    txn_ids = []
    for sequence in range(76):
        txn_ids.extend(post("/api/entries/withdrawal", date="2026-09-03", usd="1",
                            notes=f"Ledgerpage {sequence:02}")["txn_ids"])
    page.goto("/ledger?q=Ledgerpage")
    expect(page.locator("#txns tbody tr")).to_have_count(25)
    page.locator('#ledger-end').scroll_into_view_if_needed()
    expect(page.locator("#txns tbody tr")).to_have_count(50)
    assert page.locator('#txns tbody tr').first.get_attribute('data-txn') == str(txn_ids[0])
    page.get_by_role("button", name="Jump to end", exact=True).click()
    expect(page).to_have_url(re.compile(r"/ledger\?q=Ledgerpage&page=last$"))
    expect(page.locator(f'tr[data-txn="{txn_ids[-1]}"]')).to_be_in_viewport()
    page.reload()
    expect(page.locator(f'tr[data-txn="{txn_ids[-1]}"]')).to_be_in_viewport()
    # Scroll up from the last batch to load earlier rows.
    page.locator('#ledger-start').scroll_into_view_if_needed()
    expect(page.locator(f'tr[data-txn="{txn_ids[50]}"]')).to_be_attached()
    page.get_by_role("searchbox", name="Search", exact=True).fill("Ledgerpage 75")
    expect(page).to_have_url(re.compile(r"/ledger\?q=Ledgerpage\+75$"))
    expect(page.locator('#txns tbody tr')).to_have_count(2)


@pytest.mark.parametrize("width", [390, 1440])
def test_ledger_shows_direct_expense_vendor(web, width):
    expect = pytest.importorskip("playwright.sync_api").expect
    page, post, _ = web
    voucher = post('/api/vouchers/prepare', type='direct_expense', date='2026-09-03',
                   recipient='Vendor name', purpose='Supplies', usd='10')
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto('/ledger?type=expense')
    expect(page.locator('.ledger-vendor')).to_have_text('Vendor name')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_ledger_deep_link_and_row_drawer_preserve_url_and_scroll(web):
    expect = pytest.importorskip("playwright.sync_api").expect
    page, post, _ = web
    txn_ids = []
    for sequence in range(26):
        txn_ids.extend(post("/api/entries/withdrawal", date="2026-09-03", usd="1",
                            notes=f"Scrolltest {sequence:02}")["txn_ids"])
    target = txn_ids[24]
    page.set_viewport_size({"width": 1200, "height": 500})
    base_query = "view=all&type=bank_withdrawal&q=Scrolltest"

    page.goto(f"/ledger?{base_query}&txn={target}")
    expect(page.locator("#transaction-drawer")).to_be_visible()
    expect(page.locator("#drawer-title")).to_have_text(f"Transaction #{target}")
    page.get_by_role("button", name="Close", exact=True).click()
    expect(page.locator("#transaction-drawer")).to_be_hidden()
    state = page.evaluate("Object.fromEntries(new URLSearchParams(location.search))")
    assert state == {"view": "all", "type": "bank_withdrawal", "q": "Scrolltest"}

    row = page.locator(f'#txns tbody tr[data-txn="{target}"]')
    row.scroll_into_view_if_needed()
    scroll = page.evaluate("window.scrollY")
    assert scroll > 0
    row.click()
    expect(page.locator("#transaction-drawer")).to_be_visible()
    state = page.evaluate("Object.fromEntries(new URLSearchParams(location.search))")
    assert state == {"view": "all", "type": "bank_withdrawal", "q": "Scrolltest",
                     "txn": str(target)}
    page.get_by_role("button", name="Close", exact=True).click()
    expect(page.locator("#transaction-drawer")).to_be_hidden()
    assert page.evaluate("window.scrollY") == scroll
    assert page.evaluate("new URLSearchParams(location.search).has('txn')") is False


@pytest.mark.parametrize("language", ["en", "es"])
def test_signout_ledger_edits_the_printed_purpose(web, language):
    expect = pytest.importorskip("playwright.sync_api").expect
    page, post, _ = web
    voucher = post('/api/vouchers/prepare', type='signout', date='2026-09-03',
                   recipient='Recipient', purpose='School tuition', usd='10')
    txn, vid = voucher['txn_ids'][0], voucher['voucher_id']
    page.add_init_script(f"localStorage.setItem('cash-language', '{language}')")
    page.goto(f'/ledger?txn={txn}')
    form = page.locator('#history-edit-form')
    purpose = form.get_by_label('Propósito' if language == 'es' else 'Purpose', exact=True)
    expect(purpose).to_have_value('School tuition')
    expect(purpose).to_have_attribute('maxlength', '100')
    purpose.fill('Correct tuition')
    with page.expect_response(lambda r: r.request.method == 'PATCH' and
                              r.url.endswith(f'/api/ledger/{txn}')) as response:
        form.get_by_role('button', name='Save changes', exact=True).click()
    assert response.value.ok, response.value.text()
    page.reload()
    expect(purpose).to_have_value('Correct tuition')
    detail = page.request.get(f'/api/review/item/signout/{vid}').json()['data']
    assert detail['voucher']['purpose'] == 'Correct tuition'
    assert 'Correct tuition' in page.request.get(f'/voucher/{vid}/print').text()
