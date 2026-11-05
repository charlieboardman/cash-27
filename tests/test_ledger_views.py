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

    third = ledger(client, "?page=3&per_page=10")
    assert (third["page"], third["count"], third["total"], third["pages"]) == (3, 7, 27, 3)


def test_ledger_view_search_filter_and_page_state_survive_reload(web):
    expect = pytest.importorskip("playwright.sync_api").expect
    page, post, _ = web
    for sequence in range(26):
        post("/api/entries/withdrawal", date="2026-09-03", usd="1",
             notes=f"Ledgerpage {sequence:02}")

    page.goto("/ledger?q=Ledgerpage")
    expect(page.locator("#txns tbody tr")).to_have_count(25)
    expect(page.locator("#ledger-summary")).to_have_text("Showing 1–25 of 26")
    page.get_by_role("button", name="Next", exact=True).click()
    expect(page).to_have_url(re.compile(r"/ledger\?q=Ledgerpage&page=2$"))
    expect(page.locator("#ledger-summary")).to_have_text("Showing 26–26 of 26")

    page.reload()
    expect(page.get_by_role("searchbox", name="Search", exact=True)).to_have_value("Ledgerpage")
    expect(page.locator("#ledger-summary")).to_have_text("Showing 26–26 of 26")
    page.locator('#filters [name="type"]').select_option("bank_withdrawal")
    expect(page).to_have_url(
        re.compile(r"/ledger\?type=bank_withdrawal&q=Ledgerpage$"))
    expect(page.locator("#txns tbody tr")).to_have_count(25)

    page.get_by_role("button", name="Next", exact=True).click()
    expect(page.locator("#ledger-summary")).to_have_text("Showing 26–26 of 26")
    page.get_by_role("searchbox", name="Search", exact=True).fill("Ledgerpage 25")
    expect(page).to_have_url(
        re.compile(r"/ledger\?type=bank_withdrawal&q=Ledgerpage\+25$"))
    # The second match is transaction ID 25; broad search includes both ID and notes.
    expect(page.locator("#txns tbody tr")).to_have_count(2)
    page.get_by_role("button", name="Needs attention", exact=True).click()
    expect(page).to_have_url(re.compile(
        r"/ledger\?view=attention&type=bank_withdrawal&q=Ledgerpage\+25$"))
    expect(page.get_by_role("button", name="Needs attention", exact=True)).to_have_attribute(
        "aria-pressed", "true")
    expect(page.locator("#txns tbody tr")).to_have_count(2)


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
