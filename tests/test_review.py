"""Review desk API and correctable-history integration coverage."""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

import pytest

from cash import repo
from cash.commands import run_command
from cash.db import connect
from cash.prep import (create_opening, prepare_addendum,
                       prepare_direct_expense, prepare_signout)
from cash.web.app import create_app


def sid():
    return str(uuid.uuid4())


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, admin_password="x", testing=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf(client):
    return client.post("/api/login", json={"password": "x"}).get_json()["data"]["csrf"]


@pytest.fixture
def conn(app):
    connection = connect(Path(app.config["DATA_ROOT"]) / "cash.db")
    yield connection
    connection.close()


def opening(conn):
    return run_command(
        conn, sid(), "opening",
        lambda c: create_opening(c, submission_id=sid(), date="2026-08-01",
                                 usd_cents=1000_00,
                                 mxn_centavos=1550_00,
                                 assigned_rate_milli=15500))


def document(conn, name):
    conn.execute(
        "INSERT INTO documents (document_id, rel_path, sha256, size_bytes,"
        " page_count, state) VALUES (?, ?, ?, 100, 1, 'inbox')",
        (name, f"documents/{name}.pdf", f"hash-{name}"))
    return name


def post(client, csrf, url, body):
    return client.post(url, json=body, headers={"X-CSRF": csrf})


def test_first_login_leads_to_separate_starting_cash_and_donation_pages(
        client, conn):
    login = client.post("/api/login", json={"password": "x"})
    login_data = login.get_json()["data"]
    assert login_data["next_url"] == "/setup"
    assert client.get("/").location == "/setup"
    assert client.get("/login").location == "/setup"
    assert "Set starting cash" in client.get("/setup").get_data(as_text=True)
    assert client.get("/donation").location == "/setup"

    created = client.post("/api/entries/opening", json={
        "submission_id": sid(), "date": "2026-09-01",
        "usd": "100.00", "mxn": "1550.00",
        "assigned_rate_milli": 15500, "notes": "Starting count",
    }, headers={"X-CSRF": login_data["csrf"]})
    assert created.status_code == 200, created.get_json()
    assert client.get("/setup").location == "/ledger"
    assert "Record a cash donation" in client.get(
        "/donation").get_data(as_text=True)
    new_transaction = client.get("/transactions/new").get_data(as_text=True)
    assert 'href="/donation"' in new_transaction
    assert "What happened?" not in new_transaction
    assert "Who and why" not in new_transaction
    assert "Notes for staff" not in new_transaction
    assert "Currency exchange" in new_transaction
    assert "Bank withdrawal" in new_transaction

    donated = client.post("/api/entries/donation", json={
        "submission_id": sid(), "date": "2026-09-01",
        "usd": "0", "mxn": "775.00", "notes": "Donation",
    }, headers={"X-CSRF": login_data["csrf"]})
    assert donated.status_code == 200, donated.get_json()
    safe = repo.replay_projected(conn).states["SAFE"]
    assert safe.mxn == 2325_00
    assert safe.basis == 150


def test_new_transaction_entry_api_records_withdrawal_and_exchange(
        client, conn, csrf):
    opening(conn)

    withdrawal = post(client, csrf, "/api/entries/withdrawal", {
        "submission_id": sid(), "date": "2026-09-01",
        "usd": "50.00", "notes": "ATM",
    })
    assert withdrawal.status_code == 200, withdrawal.get_json()
    withdrawal_id = withdrawal.get_json()["data"]["txn_ids"][0]
    withdrawal_row = conn.execute(
        "SELECT * FROM transactions WHERE txn_id=?", (withdrawal_id,)
    ).fetchone()
    assert (withdrawal_row["txn_type"], withdrawal_row["from_account"],
            withdrawal_row["to_account"], withdrawal_row["usd_cents"],
            withdrawal_row["notes"]) == (
                "bank_withdrawal", "BANK", "SAFE", 5000, "ATM")

    exchange = post(client, csrf, "/api/entries/exchange", {
        "submission_id": sid(), "date": "2026-09-01",
        "source_account": "SAFE", "usd": "20.00", "mxn": "400.00",
        "notes": "Exchange counter",
    })
    assert exchange.status_code == 200, exchange.get_json()
    exchange_id = exchange.get_json()["data"]["txn_ids"][0]
    exchange_row = conn.execute(
        "SELECT * FROM transactions WHERE txn_id=?", (exchange_id,)
    ).fetchone()
    assert (exchange_row["txn_type"], exchange_row["from_account"],
            exchange_row["to_account"], exchange_row["usd_cents"],
            exchange_row["mxn_centavos"], exchange_row["notes"]) == (
                "exchange", "SAFE", "FX_COUNTERPARTY", 2000, -40000,
                "Exchange counter")


@pytest.mark.parametrize("kind", ["exchange", "withdrawal"])
def test_cash_receipt_review_files_without_changing_cash(
        client, conn, csrf, kind):
    opening(conn)
    created = post(client, csrf, f"/api/entries/{kind}", {
        "submission_id": sid(), "date": "2026-09-01",
        "source_account": "SAFE", "usd": "20", "mxn": "400",
        "notes": "Exchange counter",
    }).get_json()["data"]
    txn_id = created["txn_ids"][0]
    balances_before = repo.replay_projected(conn).states
    rows_before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert len(work) == 1
    assert (work[0]["kind"], work[0]["id"], work[0]["badges"]) == (
        kind, str(txn_id), ["Needs receipt"])
    assert client.get(f"/transactions/{txn_id}").location == (
        f"/review?item={kind}:{txn_id}")
    detail = client.get(f"/api/review/item/{kind}/{txn_id}").get_json()["data"]
    version = detail["transaction"]["version"]
    assert client.get(f"/api/ledger/{txn_id}").get_json()["data"][
        "transaction"]["incomplete"] is True
    ledger = client.get(f"/api/ledger?type={'exchange' if kind == 'exchange' else 'bank_withdrawal'}").get_json()["data"]
    assert ledger["transactions"][0]["incomplete"] is True
    url = f"/api/review/{kind}/{txn_id}/complete"
    missing = post(client, csrf, url, {
        "submission_id": sid(), "expected_version": version})
    assert missing.get_json()["error"]["code"] == "document_required"
    document(conn, "exchange-receipt")
    body = {"submission_id": sid(), "expected_version": version,
            "document_id": "exchange-receipt"}
    completed = post(client, csrf, url, body)
    assert completed.status_code == 200, completed.get_json()
    assert post(client, csrf, url, body).get_json() == completed.get_json()
    assert client.get("/api/review/worklist").get_json()["data"]["items"] == []
    assert client.get("/api/documents/inbox").get_json()["data"]["documents"] == []
    assert client.get(f"/transactions/{txn_id}").location == f"/ledger?txn={txn_id}"
    detail = client.get(f"/api/ledger/{txn_id}").get_json()["data"]
    assert detail["transaction"]["incomplete"] is False
    assert detail["transaction"]["version"] == version + 1
    assert [(doc["document_id"], doc["role"], doc["doc_type"])
            for doc in detail["documents"]] == [
        ("exchange-receipt", "support", f"{kind}_receipt")]
    assert repo.replay_projected(conn).states == balances_before
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == rows_before


@pytest.mark.parametrize("kind", ["exchange", "withdrawal"])
@pytest.mark.parametrize("case,code", [
    ("missing_document", "document_unavailable"),
    ("filed_document", "document_unavailable"),
    ("stale_version", "conflict"),
    ("void_transaction", "not_pending"),
    ("wrong_type", "wrong_type"),
    ("already_reviewed", "not_pending"),
])
def test_cash_receipt_review_rejects_invalid_filing_atomically(
        client, conn, csrf, case, code, kind):
    if code == "wrong_type":
        code = f"not_{kind}"
    opening(conn)
    created = post(client, csrf, f"/api/entries/{kind}", {
        "submission_id": sid(), "date": "2026-09-01",
        "usd": "20", "mxn": "400",
    }).get_json()["data"]
    txn_id = created["txn_ids"][0]
    version = 1
    if case != "missing_document":
        document(conn, "exchange-receipt")
    if case == "filed_document":
        conn.execute("UPDATE documents SET state='filed'")
    elif case == "stale_version":
        conn.execute("UPDATE transactions SET version=2 WHERE txn_id=?", (txn_id,))
    elif case == "void_transaction":
        conn.execute("UPDATE transactions SET posting_state='void' WHERE txn_id=?",
                     (txn_id,))
        assert client.get("/api/review/worklist").get_json()["data"]["items"] == []
        assert client.get(f"/api/ledger/{txn_id}").get_json()["data"][
            "transaction"]["incomplete"] is False
    elif case == "wrong_type":
        txn_id = conn.execute(
            "SELECT txn_id FROM transactions WHERE txn_type='opening'").fetchone()[0]
    elif case == "already_reviewed":
        document(conn, "first-receipt")
        assert post(client, csrf, f"/api/review/{kind}/{txn_id}/complete", {
            "submission_id": sid(), "expected_version": version,
            "document_id": "first-receipt"}).status_code == 200
        version += 1
    before = conn.execute("SELECT COUNT(*) FROM transaction_documents").fetchone()[0]
    result = post(client, csrf, f"/api/review/{kind}/{txn_id}/complete", {
        "submission_id": sid(), "expected_version": version,
        "document_id": "exchange-receipt"})
    assert result.status_code in (422, 409), result.get_json()
    assert result.get_json()["error"]["code"] == code
    assert conn.execute("SELECT COUNT(*) FROM transaction_documents").fetchone()[0] == before
    if case not in ("missing_document", "filed_document"):
        assert conn.execute("SELECT state FROM documents WHERE document_id=?",
                            ("exchange-receipt",)).fetchone()[0] == "inbox"


@pytest.mark.parametrize("kind", ["exchange", "withdrawal"])
def test_cash_transaction_can_be_created_and_reviewed_from_inbox(client, conn, csrf, kind):
    opening(conn)
    document(conn, "exchange-receipt")
    body = {"submission_id": sid(), "date": "2026-09-01", "usd": "20",
            "mxn": "400", "document_id": "exchange-receipt"}
    response = post(client, csrf, f"/api/entries/{kind}", body)
    assert response.status_code == 200, response.get_json()
    assert post(client, csrf, f"/api/entries/{kind}", body).get_json() == response.get_json()
    txn_id = response.get_json()["data"]["txn_ids"][0]
    assert client.get(f"/api/ledger/{txn_id}").get_json()["data"][
        "transaction"]["incomplete"] is False
    assert client.get("/api/review/worklist").get_json()["data"]["items"] == []
    body["submission_id"] = sid()
    rejected = post(client, csrf, f"/api/entries/{kind}", body)
    assert rejected.get_json()["error"]["code"] == "document_unavailable"
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE txn_type=?",
                        ("exchange" if kind == "exchange" else "bank_withdrawal",)).fetchone()[0] == 1


def test_review_page_is_document_rail_table_and_admin_drawer(client, csrf):
    response = client.get("/review")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    evidence = page.index('class="evidence-pane document-rail"')
    worklist = page.index('class="work-pane review-sheet-pane"')
    drawer = page.index('class="review-admin-drawer"')
    assert evidence < worklist < drawer
    assert 'id="review-drawer"' in page
    assert 'id="open-documents"' in page
    assert 'id="close-review-drawer"' in page
    assert 'class="decision-pane"' not in page
    assert 'id="document-viewer"' not in page
    assert "Details" in page
    assert "Rows" in page
    assert "Transactions" in page
    assert "Expense reports" in page
    assert "New transaction" in page
    assert 'href="/review" aria-current="page">Review</a>' in page
    assert ">Capture</a>" in page
    assert ">Ledger</a>" in page
    assert ">Reports</a>" in page
    assert 'value="inbox_document"' not in page


def test_inbox_documents_stay_in_rail_not_open_rows(conn, client, csrf):
    document(conn, "unmatched-receipt")

    inbox = client.get("/api/documents/inbox").get_json()["data"]["documents"]
    assert [item["document_id"] for item in inbox] == ["unmatched-receipt"]
    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert all(item["kind"] != "inbox_document" for item in work)


def test_review_options_suggest_existing_projects(conn, client, csrf):
    opening(conn)
    conn.execute(
        "UPDATE transactions SET project='FIELD_TEAM' WHERE txn_type='opening'")

    options = client.get("/api/review/options").get_json()["data"]
    assert options["projects"] == ["FIELD_TEAM"]
    assert options["cash_accounts"][0]["account_id"] == "SAFE"


def test_new_exchange_defaults_to_cash_from_safe(client, conn, csrf):
    opening(conn)
    run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="Travel", notes="", usd_cents=10_00))

    page = client.get("/transactions/new").get_data(as_text=True)
    source_select = page.split('name="source_account"', 1)[1].split(
        "</select>", 1)[0]
    assert '<option value="SAFE" selected>' in source_select


def printed_tokens(response):
    match = re.search(r'<script id="tokens"[^>]*>(.*?)</script>',
                      response.get_data(as_text=True), re.DOTALL)
    assert match
    return json.loads(match.group(1))


@pytest.mark.parametrize("usd,mxn", [("20.00", ""), ("", "150.00"), ("600.00", "400.00")])
def test_direct_voucher_records_only_authorized_amounts(conn, client, csrf, usd, mxn):
    opening(conn)
    result = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense", "date": "2026-08-02",
        "recipient": "Contractor", "purpose": "Repairs", "usd": usd, "mxn": mxn})
    assert result.status_code == 200, result.get_json()
    vid = result.get_json()["data"]["voucher_id"]
    voucher = conn.execute("SELECT * FROM vouchers WHERE voucher_id=?", (vid,)).fetchone()
    assert (voucher["authorized_usd_cents"] or 0) == round(float(usd or 0) * 100)
    assert (voucher["authorized_mxn_centavos"] or 0) == round(float(mxn or 0) * 100)
    item = next(row for row in client.get("/api/review/worklist").get_json()["data"]["items"] if row["id"] == vid)
    assert item["receipt"] is None
    tokens = printed_tokens(client.get(result.get_json()["data"]["print_url"]))
    assert tokens["USD"] == usd and tokens["MXN"] == mxn
    assert tokens["VENDOR"] == "Contractor" and tokens["SIGNOUT_RECIPIENT"] == ""


@pytest.mark.parametrize("field", ["receipt_usd", "receipt_mxn", "face_value", "usd_paid", "mxn_change"])
def test_voucher_rejects_obsolete_purchase_inputs(conn, client, csrf, field):
    opening(conn)
    result = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense", "date": "2026-08-02",
        "recipient": "Vendor", "purpose": "Supplies", "usd": "20", field: "20"})
    assert result.status_code == 422
    assert result.get_json()["error"]["code"] == "unknown_fields"
    assert conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0] == 0


def test_mixed_direct_receipt_keeps_both_faces_on_final_row(
        conn, client, csrf):
    opening(conn)
    conn.execute(
        "INSERT INTO classes (class_code, name) VALUES ('GENERAL','General')")
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense",
        "date": "2026-08-02", "recipient": "Contractor",
        "purpose": "Repairs", "notes": "",
        "usd": "600.00", "mxn": "400.00",
    })
    voucher_id = prepared.get_json()["data"]["voucher_id"]
    pending_rows = client.get(
        "/api/ledger").get_json()["data"]["transactions"]
    pending_row = next(
        row for row in pending_rows if row["voucher_id"] == voucher_id
        and row["posting_state"] == "active")
    assert pending_row["incomplete"] is True
    document(conn, "mixed-voucher")
    completed = post(client, csrf,
                     f"/api/review/direct-expense/{voucher_id}/complete", {
        "submission_id": sid(), "expected_version": 1,
        "voucher_document_id": "mixed-voucher",
        "rows": [{"expense_account": "EXP_SUPPLIES", "class_code": "GENERAL",
                  "usd": "600.00", "mxn": "400.00", "purpose": "Repairs"}],
    })
    assert completed.status_code == 200, completed.get_json()
    # Paid in both currencies with no change: no receipt total is needed.
    row = conn.execute(
        "SELECT receipt_face_usd_cents, receipt_face_mxn_centavos FROM transactions"
        " WHERE voucher_id=? AND posting_state='active'", (voucher_id,)
    ).fetchone()
    assert dict(row) == {"receipt_face_usd_cents": None,
                         "receipt_face_mxn_centavos": None}
    completed_rows = client.get(
        "/api/ledger").get_json()["data"]["transactions"]
    active_row = next(
        row for row in completed_rows if row["voucher_id"] == voucher_id
        and row["posting_state"] == "active")
    replaced_row = next(
        row for row in completed_rows if row["voucher_id"] == voucher_id
        and row["posting_state"] == "superseded")
    assert active_row["incomplete"] is False
    assert replaced_row["incomplete"] is False


def test_direct_expense_is_one_payment_with_numbered_receipts(
        conn, client, csrf):
    opening(conn)
    conn.execute(
        "INSERT INTO classes (class_code, name) VALUES ('GENERAL','General')")
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense",
        "date": "2026-08-02", "recipient": "Contractor",
        "purpose": "Labor, supplies, and gas", "notes": "",
        "usd": "0", "mxn": "185.00",
    }).get_json()["data"]
    voucher_id = prepared["voucher_id"]
    for name in ("voucher", "supplies", "gas"):
        document(conn, name)
    body = {"submission_id": sid(), "expected_version": 1,
            "voucher_document_id": "voucher", "use_voucher_as_receipt": True,
            "receipt_document_ids": ["supplies", "gas"],
            "rows": [{"purpose": "Labor, supplies, and gas", "mxn": "185.00",
                      "expense_account": "EXP_PROGRAMS", "class_code": "GENERAL"}]}

    rejected = post(client, csrf, f"/api/review/direct-expense/{voucher_id}/complete",
                    {**body, "receipt_document_ids": ["supplies", "voucher"]})
    assert rejected.status_code == 422
    assert rejected.get_json()["error"]["code"] == "duplicate_document"
    old_shape = post(client, csrf, f"/api/review/direct-expense/{voucher_id}/complete",
                     {**body, "rows": [{**body["rows"][0], "document_id": "gas"}]})
    assert old_shape.status_code == 422

    response = post(client, csrf, f"/api/review/direct-expense/{voucher_id}/complete", body)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["receipt_ids"] == [
        f"{voucher_id}-R1", f"{voucher_id}-R2"]

    active = conn.execute(
        "SELECT txn_id, receipt_id, mxn_centavos FROM transactions"
        " WHERE voucher_id=? AND posting_state='active'", (voucher_id,)).fetchall()
    assert [(row["receipt_id"], row["mxn_centavos"]) for row in active] == [(None, 185_00)]
    documents = [tuple(row) for row in conn.execute(
        "SELECT document_id, role FROM transaction_documents WHERE txn_id=?"
        " ORDER BY document_id, role", (active[0]["txn_id"],))]
    assert documents == [("gas", "support"), ("supplies", "support"),
                         ("voucher", "support"), ("voucher", "voucher")]
    receipts = [tuple(row) for row in conn.execute(
        "SELECT receipt_id, document_id, usd_paid_cents, mxn_paid_centavos,"
        " face_mxn_centavos FROM receipts WHERE voucher_id=? ORDER BY seq",
        (voucher_id,))]
    assert receipts == [(f"{voucher_id}-R1", "supplies", 0, 0, None),
                        (f"{voucher_id}-R2", "gas", 0, 0, None)]
    assert {row[0]: row[1] for row in conn.execute(
        "SELECT document_id, doc_type FROM documents")} == {
        "voucher": "voucher", "supplies": "receipt", "gas": "receipt"}
    detail = client.get(f"/api/ledger/{active[0]['txn_id']}").get_json()["data"]
    assert sorted((d["reference"], d["role"]) for d in detail["documents"]) == [
        (voucher_id, "support"), (voucher_id, "voucher"),
        (f"{voucher_id}-R1", "support"), (f"{voucher_id}-R2", "support")]


def test_direct_expense_draft_keeps_receipts_beside_the_payment(
        conn, client, csrf):
    opening(conn)
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense",
        "date": "2026-08-02", "recipient": "Contractor",
        "purpose": "Labor", "notes": "", "usd": "20.00", "mxn": "0",
    }).get_json()["data"]
    voucher_id = prepared["voucher_id"]
    document(conn, "gas")
    saved = post(client, csrf, f"/api/review/direct-expense/{voucher_id}/draft", {
        "submission_id": sid(), "expected_version": 1,
        "use_voucher_as_receipt": True, "receipt_document_ids": ["gas"],
        "rows": [{"purpose": "Labor", "usd": "20.00"}]})
    assert saved.status_code == 200, saved.get_json()
    detail = client.get(
        f"/api/review/item/direct_expense/{voucher_id}").get_json()["data"]
    draft = detail["review_draft"]
    assert draft["format"] == 2
    assert draft["receipt_document_ids"] == ["gas"]
    assert draft["use_voucher_as_receipt"] is True
    assert [row["usd"] for row in draft["rows"]] == ["20.00"]


def test_direct_expense_drafts_with_a_row_per_receipt_become_one_payment():
    from cash.prep import load_direct_expense_review
    part = {"receipt_total": None, "class_code": "GENERAL", "splits": []}
    draft = load_direct_expense_review(json.dumps({
        "format": 2, "primary_document_id": "voucher",
        "use_voucher_as_receipt": False, "rows": [
            {**part, "document_id": "one", "date": "2026-08-02", "vendor": "A",
             "purpose": "First", "project": "ALPHA", "usd": 10_00, "mxn": 0,
             "expense_account": "EXP_SUPPLIES"},
            {**part, "document_id": "two", "date": "2026-08-03", "vendor": "B",
             "purpose": "Second", "project": "", "usd": 5_00, "mxn": 0,
             "expense_account": "EXP_PROGRAMS"}]}))
    assert draft["receipt_document_ids"] == ["one", "two"]
    assert draft["primary_document_id"] == "voucher"
    [row] = draft["rows"]
    assert (row["purpose"], row["project"], row["usd"], row["mxn"]) == (
        "First", "ALPHA", 15_00, 0)
    assert [(split["purpose"], split["usd"], split["expense_account"])
            for split in row["splits"]] == [
        ("First", 10_00, "EXP_SUPPLIES"), ("Second", 5_00, "EXP_PROGRAMS")]

    single = load_direct_expense_review(json.dumps({
        "format": 2, "primary_document_id": None, "use_voucher_as_receipt": True,
        "rows": [{**part, "document_id": None, "date": "2026-08-02", "vendor": "A",
                  "purpose": "Only", "project": "", "usd": 10_00, "mxn": 0,
                  "expense_account": "EXP_SUPPLIES"}]}))
    assert single["receipt_document_ids"] == []
    assert single["rows"] == [{**part, "purpose": "Only", "project": "",
                               "usd": 10_00, "mxn": 0,
                               "expense_account": "EXP_SUPPLIES"}]


def test_signout_and_addendum_paperwork_are_typed_review_commands(
        conn, client, csrf):
    opening(conn)
    signout = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="School supplies", notes="",
            usd_cents=100_00, mxn_centavos=200_00))["voucher_id"]
    addendum = run_command(
        conn, sid(), "prepare_addendum",
        lambda c: prepare_addendum(
            c, submission_id=sid(), voucher_id=signout,
            date="2026-08-03", seq_no=0, kind="positive", reason="More",
            usd_cents=10_00))["addendum_id"]
    document(conn, "signout-paper")
    document(conn, "addendum-paper")

    signout_tokens = printed_tokens(client.get(f"/voucher/{signout}/print"))
    assert signout_tokens["SIGNOUT_RECIPIENT"] == "Ana"
    assert signout_tokens["VENDOR"] == ""
    addendum_tokens = printed_tokens(client.get(
        f"/voucher/{signout}/addendum/{addendum}/print"))
    assert addendum_tokens["REASON"] == "More"

    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert {(item["kind"], item["id"]) for item in work} >= {
        ("signout", signout), ("addendum", addendum)}
    signout_item = next(item for item in work if item["kind"] == "signout")
    assert signout_item["date"] == "2026-08-02"
    ledger = client.get("/api/ledger").get_json()["data"]
    signout_row = next(
        row for row in ledger["transactions"]
        if row["voucher_id"] == signout and row["txn_type"] == "signout")
    addendum_row = next(
        row for row in ledger["transactions"]
        if row["addendum_id"] == addendum)
    assert signout_row["incomplete"] is True
    assert addendum_row["incomplete"] is True
    open_signouts = {s["voucher_id"] for s in client.get(
        "/api/balances").get_json()["data"]["signouts"] if s["open"]}
    assert signout in open_signouts
    response = post(client, csrf, f"/api/review/signout/{signout}/attach-voucher", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "signout-paper"})
    assert response.status_code == 200
    assert conn.execute("SELECT status FROM vouchers WHERE voucher_id=?",
                        (signout,)).fetchone()[0] == "pending"
    response = post(client, csrf, f"/api/review/addendum/{addendum}/complete", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "addendum-paper"})
    assert response.status_code == 200
    assert conn.execute("SELECT status FROM addenda WHERE addendum_id=?",
                        (addendum,)).fetchone()[0] == "complete"
    ledger = client.get("/api/ledger").get_json()["data"]
    signout_row = next(
        row for row in ledger["transactions"]
        if row["voucher_id"] == signout and row["txn_type"] == "signout")
    addendum_row = next(
        row for row in ledger["transactions"]
        if row["addendum_id"] == addendum)
    assert signout_row["incomplete"] is False
    assert addendum_row["incomplete"] is False
    open_signouts = {s["voucher_id"] for s in client.get(
        "/api/balances").get_json()["data"]["signouts"] if s["open"]}
    assert signout in open_signouts
    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert ("signout", signout) not in {(item["kind"], item["id"]) for item in work}
    assert ("addendum", addendum) not in {(item["kind"], item["id"]) for item in work}


def test_voided_signout_disappears_from_review_worklist(conn, client, csrf):
    opening(conn)
    mistaken = prepare_signout(
        conn, submission_id=sid(), date="2026-08-02", seq_no=0,
        recipient="Ana", purpose="Supplies", notes="",
        mxn_centavos=100_00)
    replacement = prepare_signout(
        conn, submission_id=sid(), date="2026-08-02", seq_no=1,
        recipient="Ana", purpose="Supplies", notes="",
        mxn_centavos=150_00)
    txn_id = mistaken["txn_ids"][0]
    before = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert {item["id"] for item in before if item["kind"] == "signout"} == {
        mistaken["voucher_id"], replacement["voucher_id"]}
    assert client.get("/api/balances").get_json()["data"]["safe"]["mxn"] == "1300.00"

    response = post(client, csrf, f"/api/ledger/{txn_id}/void", {
        "submission_id": sid(), "expected_version": 1,
        "reason": "Incorrect amount"})
    assert response.status_code == 200, response.get_json()
    after = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert {item["id"] for item in after if item["kind"] == "signout"} == {
        replacement["voucher_id"]}
    balances = client.get("/api/balances").get_json()["data"]
    assert [item["voucher_id"] for item in balances["signouts"]] == [
        replacement["voucher_id"]]
    assert balances["totals"]["open_count"] == 1
    assert balances["safe"]["mxn"] == "1400.00"
    assert balances["totals"]["tracked_mxn"] == "1550.00"


def test_reopen_signout_voucher_restores_paperwork_to_queue(
        conn, client, csrf):
    opening(conn)
    signout = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="School supplies", notes="",
            usd_cents=100_00, mxn_centavos=0))["voucher_id"]
    document(conn, "older-receipt")
    signout_txn_id = conn.execute(
        "SELECT txn_id FROM transactions WHERE voucher_id=?"
        " AND txn_type='signout'", (signout,)).fetchone()[0]
    conn.execute(
        "UPDATE documents SET state='filed', voucher_id=?, doc_type='receipt',"
        " updated_at='2000-01-01T00:00:00.000Z' WHERE document_id=?",
        (signout, "older-receipt"))
    conn.execute(
        "INSERT INTO transaction_documents (txn_id, document_id, role)"
        " VALUES (?,?,'support')", (signout_txn_id, "older-receipt"))
    document(conn, "signout-paper")
    assert post(client, csrf, f"/api/review/signout/{signout}/attach-voucher", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "signout-paper"}).status_code == 200
    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert ("signout", signout) not in {
        (item["kind"], item["id"]) for item in work}
    assert conn.execute(
        "SELECT state FROM documents WHERE document_id='signout-paper'"
    ).fetchone()[0] == "filed"
    document(conn, "second-voucher")
    duplicate = post(
        client, csrf, f"/api/review/signout/{signout}/attach-voucher", {
            "submission_id": sid(), "expected_version": 2,
            "document_id": "second-voucher"})
    assert duplicate.status_code == 422
    assert duplicate.get_json()["error"]["code"] == "voucher_already_filed"

    reopen = post(client, csrf, f"/api/review/signout/{signout}/reopen", {
        "submission_id": sid(), "expected_version": 2})
    assert reopen.status_code == 200, reopen.get_json()
    assert reopen.get_json()["data"]["restored_document_ids"] == [
        "signout-paper"]
    assert conn.execute(
        "SELECT state FROM documents WHERE document_id='signout-paper'"
    ).fetchone()[0] == "inbox"
    # Only the just-filed voucher is unlinked. Older signout evidence and the
    # cash movement are untouched.
    assert conn.execute(
        "SELECT COUNT(*) FROM transaction_documents td JOIN transactions t"
        " ON t.txn_id=td.txn_id WHERE t.voucher_id=?", (signout,)
    ).fetchone()[0] == 1
    older = conn.execute(
        "SELECT state FROM documents WHERE document_id='older-receipt'"
    ).fetchone()[0]
    assert older == "filed"
    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert ("signout", signout) in {
        (item["kind"], item["id"]) for item in work}
    # Stale version is rejected.
    assert post(client, csrf, f"/api/review/signout/{signout}/reopen", {
        "submission_id": sid(), "expected_version": 2}).status_code == 409


def test_signout_voucher_undo_expires_on_the_server(conn, client, csrf):
    opening(conn)
    signout = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="School supplies", notes="",
            usd_cents=100_00))["voucher_id"]
    document(conn, "signed-voucher")
    assert post(client, csrf, f"/api/review/signout/{signout}/attach-voucher", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "signed-voucher"}).status_code == 200
    conn.execute(
        "UPDATE documents SET updated_at='2000-01-01T00:00:00.000Z'"
        " WHERE document_id='signed-voucher'")

    response = post(client, csrf, f"/api/review/signout/{signout}/reopen", {
        "submission_id": sid(), "expected_version": 2})
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "undo_expired"
    assert conn.execute(
        "SELECT state FROM documents WHERE document_id='signed-voucher'"
    ).fetchone()[0] == "filed"


def test_reopen_addendum_returns_to_pending_and_document_to_inbox(
        conn, client, csrf):
    opening(conn)
    signout = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="Supplies", notes="",
            usd_cents=100_00))["voucher_id"]
    addendum = run_command(
        conn, sid(), "prepare_addendum",
        lambda c: prepare_addendum(
            c, submission_id=sid(), voucher_id=signout,
            date="2026-08-03", seq_no=0, kind="positive", reason="More",
            usd_cents=10_00))["addendum_id"]
    from cash.prep import attach_signout_voucher
    document(conn, "voucher-paper")
    run_command(conn, sid(), "attach",
        lambda c: attach_signout_voucher(
            c, voucher_id=signout, expected_version=1,
            document_id="voucher-paper"))
    document(conn, "addendum-paper")
    assert post(client, csrf, f"/api/review/addendum/{addendum}/complete", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "addendum-paper"}).status_code == 200
    assert conn.execute(
        "SELECT status FROM addenda WHERE addendum_id=?", (addendum,)
    ).fetchone()[0] == "complete"

    reopen = post(client, csrf, f"/api/review/addendum/{addendum}/reopen", {
        "submission_id": sid(), "expected_version": 2})
    assert reopen.status_code == 200, reopen.get_json()
    assert conn.execute(
        "SELECT status, document_id FROM addenda WHERE addendum_id=?",
        (addendum,)).fetchone()[0] == "pending"
    assert conn.execute(
        "SELECT state FROM documents WHERE document_id='addendum-paper'"
    ).fetchone()[0] == "inbox"
    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert ("addendum", addendum) in {
        (item["kind"], item["id"]) for item in work}


def test_addendum_undo_expires_on_the_server(conn, client, csrf):
    opening(conn)
    signout = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="Supplies", notes="",
            usd_cents=100_00))["voucher_id"]
    addendum = run_command(
        conn, sid(), "prepare_addendum",
        lambda c: prepare_addendum(
            c, submission_id=sid(), voucher_id=signout,
            date="2026-08-03", seq_no=0, kind="positive", reason="More",
            usd_cents=10_00))["addendum_id"]
    document(conn, "addendum-paper")
    assert post(client, csrf, f"/api/review/addendum/{addendum}/complete", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "addendum-paper"}).status_code == 200
    conn.execute(
        "UPDATE documents SET updated_at='2000-01-01T00:00:00.000Z'"
        " WHERE document_id='addendum-paper'")

    response = post(client, csrf, f"/api/review/addendum/{addendum}/reopen", {
        "submission_id": sid(), "expected_version": 2})
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "undo_expired"
    assert conn.execute(
        "SELECT status FROM addenda WHERE addendum_id=?", (addendum,)
    ).fetchone()[0] == "complete"


def test_signout_needs_confirmation_after_final_return_addendum_is_filed(
        conn, client, csrf):
    opening(conn)
    signout = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=None,
            recipient="Ana", purpose="Field work", notes="",
            usd_cents=100_00))["voucher_id"]
    document(conn, "voucher-paper")
    assert post(client, csrf, f"/api/review/signout/{signout}/attach-voucher", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "voucher-paper"}).status_code == 200
    addendum = run_command(
        conn, sid(), "return",
        lambda c: prepare_addendum(
            c, submission_id=sid(), voucher_id=signout,
            date="2026-08-03", seq_no=None, kind="return",
            reason="Unused cash", usd_cents=100_00))["addendum_id"]
    ledger = client.get("/api/ledger").get_json()["data"]
    open_signouts = {s["voucher_id"] for s in client.get(
        "/api/balances").get_json()["data"]["signouts"] if s["open"]}
    assert signout in open_signouts
    document(conn, "return-paper")
    assert post(client, csrf, f"/api/review/addendum/{addendum}/complete", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "return-paper"}).status_code == 200
    state = client.get(f"/{signout}/api/report").get_json()["data"]["paperwork"]
    assert state["reasons"] == ["closeout needed"]
    preview = post(client, csrf, f"/api/signouts/{signout}/closeout/preview", {
        "date": "2026-08-03"}).get_json()["data"]
    response = post(client, csrf, f"/api/signouts/{signout}/closeout", {
        "submission_id": sid(), "date": "2026-08-03", "confirmed": True,
        "preview_token": preview["preview_token"]})
    assert response.status_code == 200, response.get_json()
    ledger = client.get("/api/ledger").get_json()["data"]
    open_signouts = {s["voucher_id"] for s in client.get(
        "/api/balances").get_json()["data"]["signouts"] if s["open"]}
    assert signout not in open_signouts
    workspace = client.get(f"/{signout}/api/report").get_json()["data"]
    assert workspace["voucher"]["status"] == "closed"
    assert workspace["paperwork"]["reasons"] == []


def test_signout_filing_files_only_the_voucher(conn, client, csrf):
    opening(conn)
    signout = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="School supplies", notes="",
            usd_cents=100_00, mxn_centavos=200_00))["voucher_id"]
    document(conn, "signed-signout")
    document(conn, "vendor-receipt")

    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    item = next(row for row in work if row["kind"] == "signout")
    assert item["badges"] == ["Needs voucher"]

    for field, value in (("use_as_receipt", True),
                         ("receipt_document_id", "vendor-receipt")):
        response = post(client, csrf, f"/api/review/signout/{signout}/attach-voucher", {
            "submission_id": sid(), "expected_version": 1,
            "document_id": "signed-signout", field: value})
        assert response.status_code == 422, response.get_json()
        assert response.get_json()["error"]["code"] == "unknown_fields"

    response = post(client, csrf, f"/api/review/signout/{signout}/attach-voucher", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "signed-signout"})
    assert response.status_code == 200, response.get_json()

    documents = [tuple(row) for row in conn.execute(
        "SELECT td.document_id, td.role FROM transaction_documents td"
        " JOIN transactions t ON t.txn_id=td.txn_id"
        " WHERE t.voucher_id=? AND t.txn_type='signout'", (signout,))]
    assert documents == [("signed-signout", "voucher")]
    assert conn.execute(
        "SELECT state FROM documents WHERE document_id='vendor-receipt'"
    ).fetchone()[0] == "inbox"


def test_direct_expense_records_actual_spending_split_by_code(
        conn, client, csrf):
    opening(conn)
    conn.execute(
        "INSERT INTO classes (class_code, name) VALUES ('GENERAL','General')")
    prepared = run_command(
        conn, sid(), "prepare_direct",
        lambda c: prepare_direct_expense(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Vendor", purpose="Supplies", notes="",
            usd_cents=50_00, mxn_centavos=0))
    voucher_id = prepared["voucher_id"]
    voucher = conn.execute("SELECT * FROM vouchers WHERE voucher_id=?",
                           (voucher_id,)).fetchone()
    assert voucher["authorized_usd_cents"] == 50_00
    document(conn, "signed-voucher")
    response = post(
        client, csrf, f"/api/review/direct-expense/{voucher_id}/complete", {
            "submission_id": sid(), "expected_version": 1,
            "voucher_document_id": "signed-voucher",
            "rows": [{"usd": "40.00", "project": "OFFICE", "splits": [
                {"expense_account": "EXP_SUPPLIES", "usd": "30.00",
                 "class_code": "GENERAL", "purpose": "Paper"},
                {"expense_account": "EXP_PROGRAMS", "usd": "10.00",
                 "class_code": "GENERAL", "purpose": "Markers"},
            ]}]})
    assert response.status_code == 200, response.get_json()
    rows = conn.execute(
        "SELECT usd_cents, receipt_face_usd_cents,"
        " project, description FROM transactions WHERE voucher_id=? AND"
        " posting_state='active' ORDER BY allocation_line", (voucher_id,)).fetchall()
    assert [row["usd_cents"] for row in rows] == [30_00, 10_00]
    assert [row["receipt_face_usd_cents"] for row in rows] == [None, None]
    assert [row["project"] for row in rows] == ["OFFICE", "OFFICE"]
    assert [row["description"] for row in rows] == ["Paper", "Markers"]


def test_submitted_expense_reports_are_listed_with_a_link_to_their_page(
        conn, client, csrf):
    opening(conn)
    voucher_id = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="Program", notes="",
            mxn_centavos=500_00))["voucher_id"]
    listed = lambda: [item for item in client.get("/api/review/worklist").get_json()
                      ["data"]["items"] if item["kind"] == "expense_report"]
    response = post(client, csrf, f"/{voucher_id}/api/rows", {
        "submission_id": sid(), "date": "2026-08-03", "purpose": "Materials",
        "mxn": "100", "no_receipt_reason": "Market stall"})
    assert response.status_code == 200, response.get_json()
    assert listed() == []
    response = post(client, csrf, f"/{voucher_id}/api/report/submit", {"submission_id": sid()})
    assert response.status_code == 200, response.get_json()
    [item] = listed()
    assert (item["id"], item["label"], item["url"], item["receipt"]) == (
        voucher_id, "Ana", f"/{voucher_id}", "1 row")


@pytest.fixture
def pending_expense_review(conn, client, csrf):
    opening(conn)
    conn.execute("INSERT INTO classes (class_code, name) VALUES ('GENERAL','General')")
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense", "date": "2026-08-02",
        "recipient": "Vendor", "purpose": "Supplies", "usd": "20",
    }).get_json()["data"]
    return prepared


def test_expense_review_progress_persists_without_paperwork(
        conn, client, csrf, pending_expense_review):
    prepared = pending_expense_review
    voucher_id = prepared["voucher_id"]
    url = f"/api/review/direct-expense/{voucher_id}/draft"
    before = client.get(f"/api/ledger/{prepared['txn_ids'][0]}").get_json()["data"]
    body = {"submission_id": sid(), "expected_version": 1, "rows": [{
        "usd": "20", "project": "School", "splits": [
            {"expense_account": "EXP_SUPPLIES", "class_code": "GENERAL", "usd": "12"},
            {"expense_account": "", "class_code": None, "usd": "8"},
        ],
    }]}
    response = post(client, csrf, url, body)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["status"] == "pending"
    assert post(client, csrf, url, body).get_json() == response.get_json()
    detail = client.get(f"/api/review/item/direct_expense/{voucher_id}").get_json()["data"]
    row = detail["review_draft"]["rows"][0]
    assert [(s["expense_account"], s["class_code"], s["usd"])
            for s in row["splits"]] == [("EXP_SUPPLIES", "GENERAL", "12.00"), (None, None, "8.00")]
    assert row["project"] == "School"
    assert detail["voucher"]["status"] == "pending"
    assert detail["voucher"]["version"] == 2
    assert client.get(f"/api/ledger/{prepared['txn_ids'][0]}").get_json()["data"] == before
    assert client.get("/api/review/worklist").get_json()["data"]["items"][0]["id"] == voucher_id
    stale = post(client, csrf, url, {**body, "submission_id": sid()})
    assert stale.status_code == 409
    complete = post(client, csrf, f"/api/review/direct-expense/{voucher_id}/complete", {
        "submission_id": sid(), "expected_version": 2, "rows": body["rows"],
    })
    assert complete.get_json()["error"]["code"] == "voucher_required"


@pytest.mark.parametrize("allocation,error", [
    ({"expense_account": "SAFE"}, "bad_expense_account"),
    ({"expense_account": "UNALLOCATED_EXPENSE"}, "bad_expense_account"),
    ({"expense_account": "EXP_SUPPLIES", "class_code": "UNKNOWN"}, "bad_class"),
])
def test_expense_review_progress_validates_selected_codes(
        client, csrf, pending_expense_review, allocation, error):
    voucher_id = pending_expense_review["voucher_id"]
    result = post(client, csrf, f"/api/review/direct-expense/{voucher_id}/draft", {
        "submission_id": sid(), "expected_version": 1,
        "rows": [{"usd": "20", **allocation}],
    })
    assert result.status_code == 422, result.get_json()
    assert result.get_json()["error"]["code"] == error
    detail = client.get(f"/api/review/item/direct_expense/{voucher_id}").get_json()["data"]
    assert detail["review_draft"] is None
    assert detail["voucher"]["version"] == 1


def test_expense_review_progress_does_not_file_documents(
        conn, client, csrf, pending_expense_review):
    voucher_id = pending_expense_review["voucher_id"]
    document(conn, "signed-voucher")
    rows = [{"expense_account": "EXP_SUPPLIES", "class_code": "GENERAL", "usd": "20"}]
    body = {"submission_id": sid(), "expected_version": 1,
            "primary_document_id": "signed-voucher", "use_voucher_as_receipt": True,
            "rows": rows}
    draft_url = f"/api/review/direct-expense/{voucher_id}/draft"
    assert post(client, csrf, draft_url, body).status_code == 200
    assert conn.execute("SELECT state FROM documents WHERE document_id='signed-voucher'").fetchone()[0] == "inbox"
    assert conn.execute("SELECT COUNT(*) FROM transaction_documents").fetchone()[0] == 0
    result = post(client, csrf, f"/api/review/direct-expense/{voucher_id}/complete", {
        "submission_id": sid(), "expected_version": 2,
        "voucher_document_id": "signed-voucher", "rows": rows,
    })
    assert result.status_code == 200, result.get_json()
    assert conn.execute("SELECT COUNT(*) FROM direct_expense_review_drafts").fetchone()[0] == 0
    rejected = post(client, csrf, draft_url, {**body, "submission_id": sid(), "expected_version": 3})
    assert rejected.get_json()["error"]["code"] == "not_pending"


@pytest.fixture
def completed_expense(conn, client, csrf):
    opening(conn)
    conn.execute(
        "INSERT INTO classes (class_code, name) VALUES ('GENERAL','General')")
    conn.execute(
        "INSERT INTO classes (class_code, name, active)"
        " VALUES ('OLD','Old class',0)")
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense", "date": "2026-08-02",
        "recipient": "Vendor", "purpose": "Supplies", "usd": "20",
    }).get_json()["data"]
    voucher_id = prepared["voucher_id"]
    document(conn, "signed-voucher")
    completed = post(
        client, csrf, f"/api/review/direct-expense/{voucher_id}/complete", {
            "submission_id": sid(), "expected_version": 1,
            "voucher_document_id": "signed-voucher",
            "rows": [{"expense_account": "EXP_SUPPLIES", "class_code": "GENERAL",
                      "usd": "20"}],
        })
    assert completed.status_code == 200, completed.get_json()
    txn_id = completed.get_json()["data"]["txn_ids"][0]
    return client.get(f"/api/ledger/{txn_id}").get_json()["data"]


@pytest.mark.parametrize("class_code,error_code", [
    (None, "class_required"), ("", "class_required"),
    ("UNKNOWN", "bad_class"), ("OLD", "bad_class"),
])
def test_completed_expense_history_rejects_missing_or_invalid_class(
        conn, client, csrf, completed_expense, class_code, error_code):
    transaction = completed_expense["transaction"]
    url = f"/api/ledger/{transaction['txn_id']}"
    revision = conn.execute("SELECT ledger_revision FROM ledger_state").fetchone()[0]
    submission_id = sid()
    result = client.patch(url, json={
        "submission_id": submission_id, "expected_version": transaction["version"],
        "class_code": class_code, "reason": "Correct expense classification",
    }, headers={"X-CSRF": csrf})
    assert result.status_code == 422, result.get_json()
    assert result.get_json()["error"]["code"] == error_code
    assert client.get(url).get_json()["data"] == completed_expense
    assert conn.execute("SELECT ledger_revision FROM ledger_state").fetchone()[0] == revision
    assert conn.execute("SELECT 1 FROM submissions WHERE submission_id=?",
                        (submission_id,)).fetchone() is None


def test_completed_expense_history_allows_valid_class_corrections(
        conn, client, csrf, completed_expense):
    transaction = completed_expense["transaction"]
    url = f"/api/ledger/{transaction['txn_id']}"
    conn.execute("INSERT INTO classes (class_code, name) VALUES ('7.5','Program')")
    result = client.patch(url, json={
        "submission_id": sid(), "expected_version": transaction["version"],
        "class_code": "7.5", "reason": "Correct expense classification",
    }, headers={"X-CSRF": csrf})
    assert result.status_code == 200, result.get_json()
    detail = client.get(url).get_json()["data"]
    assert detail["transaction"]["class_code"] == "7.5"
    assert detail["transaction"]["incomplete"] is False
    assert detail["documents"] == completed_expense["documents"]
    assert len(detail["edits"]) == 1
    # Deactivating an existing historical code must not block unrelated edits.
    conn.execute("UPDATE classes SET active=0 WHERE class_code='7.5'")
    result = client.patch(url, json={
        "submission_id": sid(), "expected_version": detail["transaction"]["version"],
        "notes": "Receipt checked",
    }, headers={"X-CSRF": csrf})
    assert result.status_code == 200, result.get_json()


def test_pending_expense_history_does_not_require_class(client, conn, csrf):
    opening(conn)
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense", "date": "2026-08-02",
        "recipient": "Vendor", "purpose": "Supplies", "usd": "20",
    }).get_json()["data"]
    url = f"/api/ledger/{prepared['txn_ids'][0]}"
    result = client.patch(url, json={
        "submission_id": sid(), "expected_version": 1,
        "class_code": "", "notes": "Awaiting review",
    }, headers={"X-CSRF": csrf})
    assert result.status_code == 200, result.get_json()
    detail = client.get(url).get_json()["data"]["transaction"]
    assert detail["class_code"] is None
    assert detail["incomplete"] is True


@pytest.mark.parametrize("kind", ["signout", "positive", "return"])
@pytest.mark.parametrize("currency", ["usd", "mxn"])
def test_transfer_history_rejects_negative_amount_without_changes(
        conn, client, csrf, kind, currency):
    opening(conn)
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "signout", "date": "2026-08-02",
        "recipient": "Recipient", "purpose": "Supplies",
        "usd": "100", "mxn": "300",
    }).get_json()["data"]
    if kind != "signout":
        response = post(client, csrf, "/api/addenda/prepare", {
            "submission_id": sid(), "voucher_id": prepared["voucher_id"],
            "date": "2026-08-03", "kind": kind, "reason": "Cash handoff",
            "usd": "10", "mxn": "100",
        })
        assert response.status_code == 200, response.get_json()
        prepared = response.get_json()["data"]
    url = f"/api/ledger/{prepared['txn_ids'][0]}"
    before = client.get(url).get_json()["data"]
    balances = repo.replay_projected(conn).states
    revision = conn.execute("SELECT ledger_revision FROM ledger_state").fetchone()[0]
    submission_id = sid()
    response = client.patch(url, json={
        "submission_id": submission_id,
        "expected_version": before["transaction"]["version"],
        currency: "-300", "reason": "Correct recorded cash amount",
    }, headers={"X-CSRF": csrf})
    assert response.status_code == 422, response.get_json()
    assert response.get_json()["error"]["code"] == "negative_amount"
    assert client.get(url).get_json()["data"] == before
    assert repo.replay_projected(conn).states == balances
    assert conn.execute("SELECT ledger_revision FROM ledger_state").fetchone()[0] == revision
    assert conn.execute("SELECT 1 FROM submissions WHERE submission_id=?",
                        (submission_id,)).fetchone() is None

    # A positive correction still moves cash between these same accounts.
    response = client.patch(url, json={
        "submission_id": sid(),
        "expected_version": before["transaction"]["version"],
        currency: "50", "reason": "Correct recorded cash amount",
    }, headers={"X-CSRF": csrf})
    assert response.status_code == 200, response.get_json()
    after = repo.replay_projected(conn).states
    delta = 50_00 - int(Decimal(before["transaction"][currency]) * 100)
    source = before["transaction"]["from_account"]
    destination = before["transaction"]["to_account"]
    assert getattr(after[source], currency) == getattr(balances[source], currency) - delta
    assert getattr(after[destination], currency) == getattr(balances[destination], currency) + delta
    assert sum(s.mxn for s in after.values()) == sum(s.mxn for s in balances.values())
    assert sum(s.value_usd for s in after.values()) == sum(s.value_usd for s in balances.values())


@pytest.mark.parametrize("changes,fields", [
    ({"mxn": "310"}, {"mxn_centavos"}),
    ({"mxn": "0"}, {"mxn_centavos"}),
    ({"usd": "0"}, {"usd_cents"}),
    ({"usd": "-17"}, {"usd_cents"}),
    ({"usd": "0", "mxn": "0"}, {"usd_cents", "mxn_centavos"}),
])
def test_exchange_history_rejects_invalid_signs_and_allows_valid_correction(
        conn, client, csrf, changes, fields):
    opening(conn)
    response = post(client, csrf, "/api/entries/exchange", {
        "submission_id": sid(), "date": "2026-08-02",
        "source_account": "SAFE", "usd": "17", "mxn": "310",
    })
    assert response.status_code == 200, response.get_json()
    txn_id = response.get_json()["data"]["txn_ids"][0]
    url = f"/api/ledger/{txn_id}"
    before = client.get(url).get_json()["data"]
    balances = repo.replay_projected(conn).states
    revision = conn.execute("SELECT ledger_revision FROM ledger_state").fetchone()[0]
    submission_id = sid()
    response = client.patch(url, json={
        "submission_id": submission_id, "expected_version": 1,
        "reason": "Correct exchange amount", **changes,
    }, headers={"X-CSRF": csrf})
    assert response.status_code == 422, response.get_json()
    assert response.get_json()["error"]["code"] == "bad_exchange"
    assert set(response.get_json()["error"]["fields"]) == fields
    assert client.get(url).get_json()["data"] == before
    assert repo.replay_projected(conn).states == balances
    assert conn.execute("SELECT ledger_revision FROM ledger_state").fetchone()[0] == revision
    assert conn.execute("SELECT 1 FROM submissions WHERE submission_id=?",
                        (submission_id,)).fetchone() is None

    response = client.patch(url, json={
        "submission_id": sid(), "expected_version": 1,
        "usd": "20", "mxn": "-400", "reason": "Correct exchange amount",
    }, headers={"X-CSRF": csrf})
    assert response.status_code == 200, response.get_json()
    result = repo.replay_projected(conn)
    assert result.states["SAFE"].usd == 980_00
    assert result.states["SAFE"].mxn == 1950_00
    assert result.states["SAFE"].basis == 120
    assert result.external_in == sum(s.value_usd for s in result.states.values())


@pytest.mark.parametrize("invalid_existing", [False, True])
def test_startup_reports_all_invalid_active_signs_without_changing_history(
        conn, client, csrf, app, completed_expense, caplog, invalid_existing):
    signout = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "signout", "date": "2026-08-03",
        "recipient": "Recipient", "purpose": "Supplies", "mxn": "300",
    }).get_json()["data"]["txn_ids"][0]
    exchange_ids = []
    for _ in range(2):
        response = post(client, csrf, "/api/entries/exchange", {
            "submission_id": sid(), "date": "2026-08-04",
            "source_account": "SAFE", "usd": "17", "mxn": "310",
        })
        assert response.status_code == 200, response.get_json()
        exchange_ids.extend(response.get_json()["data"]["txn_ids"])
    # Model old stored data, including an inactive bad row and legitimate
    # negative receipt change. Only active violations belong in diagnostics.
    conn.execute("UPDATE transactions SET posting_state='void', mxn_centavos=31000"
                 " WHERE txn_id=?", (exchange_ids[1],))
    conn.execute("UPDATE transactions SET mxn_centavos=-10000,"
                 " receipt_face_usd_cents=NULL, receipt_face_mxn_centavos=40000"
                 " WHERE txn_id=?", (completed_expense["transaction"]["txn_id"],))
    if invalid_existing:
        conn.execute("UPDATE transactions SET mxn_centavos=-30000 WHERE txn_id=?",
                     (signout,))
        conn.execute("UPDATE transactions SET usd_cents=0 WHERE txn_id=?",
                     (exchange_ids[0],))
    tables = ("transactions", "transaction_edits", "ledger_state", "submissions")
    before = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
              for table in tables}
    caplog.clear()
    create_app(app.config["DATA_ROOT"], admin_password="x", testing=True)
    diagnostics = [record for record in caplog.records
                   if record.getMessage().startswith("Ledger amount-sign errors")]
    if invalid_existing:
        assert len(diagnostics) == 1
        assert diagnostics[0].levelname == "ERROR"
        message = diagnostics[0].getMessage()
        assert f"'{signout}':" in message
        assert f"'{exchange_ids[0]}':" in message
        assert f"'{exchange_ids[1]}':" not in message
        assert "No amounts were changed" in message
    else:
        assert diagnostics == []
        repo.replay_projected(conn)
    after = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
             for table in tables}
    assert after == before


def test_expense_history_allows_pesos_change(conn, client, csrf, completed_expense):
    transaction = completed_expense["transaction"]
    response = client.patch(f"/api/ledger/{transaction['txn_id']}", json={
        "submission_id": sid(), "expected_version": transaction["version"],
        "usd": "20", "mxn": "-100", "receipt_face_usd": "",
        "receipt_face_mxn": "400", "reason": "Record pesos change received",
    }, headers={"X-CSRF": csrf})
    assert response.status_code == 200, response.get_json()
    result = repo.replay_projected(conn)
    assert result.states["SAFE"].usd == 980_00
    assert result.states["SAFE"].mxn == 1650_00
    assert result.states["SAFE"].basis == 104
    assert result.expense_value == 16


def test_material_history_edit_requires_reason_and_preserves_id(
        conn, client, csrf):
    opening(conn)
    listed = client.get("/api/ledger").get_json()["data"]["transactions"]
    assert listed[0]["calc_usd"] is None
    transaction = conn.execute(
        "SELECT txn_id, version FROM transactions WHERE txn_type='opening'"
    ).fetchone()
    url = f"/api/ledger/{transaction['txn_id']}"
    response = client.patch(url, json={
        "submission_id": sid(), "expected_version": transaction["version"],
        "usd": "1200.00", "reason": ""}, headers={"X-CSRF": csrf})
    assert response.status_code == 422
    response = client.patch(url, json={
        "submission_id": sid(), "expected_version": transaction["version"],
        "usd": "1200.00", "reason": "Opening count evidence corrected"},
        headers={"X-CSRF": csrf})
    assert response.status_code == 200, response.get_json()
    updated = conn.execute("SELECT txn_id, usd_cents, version FROM transactions"
                           " WHERE txn_id=?", (transaction["txn_id"],)).fetchone()
    assert updated["txn_id"] == transaction["txn_id"]
    assert updated["usd_cents"] == 1200_00
    assert updated["version"] == transaction["version"] + 1
    edit = conn.execute("SELECT reason FROM transaction_edits WHERE txn_id=?",
                        (transaction["txn_id"],)).fetchone()
    assert edit["reason"] == "Opening count evidence corrected"

    missing_rate = client.patch(url, json={
        "submission_id": sid(), "expected_version": updated["version"],
        "assigned_rate": "", "reason": "Incorrectly remove starting rate"},
        headers={"X-CSRF": csrf})
    assert missing_rate.status_code == 422
    assert missing_rate.get_json()["error"]["code"] == "rate_required"


def test_balances_page_and_api_show_safe_and_signouts(
        client, conn, csrf):
    opening(conn)
    signout = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="School supplies", notes="",
            usd_cents=100_00, mxn_centavos=0))["voucher_id"]

    page = client.get("/balances")
    assert page.status_code == 200

    data = client.get("/api/balances").get_json()["data"]
    assert Decimal(data["safe"]["usd"]) == 900.00
    assert "bank" not in data
    entry = next(s for s in data["signouts"]
                 if s["voucher_id"] == signout)
    assert entry["open"] is True
    assert Decimal(entry["balance"]["usd"]) == 100.00
    assert "cash remaining" in entry["reasons"]
    assert Decimal(data["totals"]["signout_usd"]) == 100.00
    assert Decimal(data["totals"]["tracked_usd"]) == 1000.00
    assert data["totals"]["open_count"] == 1
    assert Decimal(data["totals"]["open_signout_value_usd"]) == 100


def test_signout_summary_includes_peso_basis_and_mixed_cash(client, conn, csrf):
    opening(conn)
    assert Decimal(client.get("/api/balances").json["data"]["totals"][
        "open_signout_value_usd"]) == 0
    for seq, (usd, mxn) in enumerate([(0, 155_00), (50_00, 775_00)]):
        run_command(conn, sid(), "prepare_signout", lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=seq,
            recipient="Ana", purpose="Supplies", notes="",
            usd_cents=usd, mxn_centavos=mxn))
    data = client.get("/api/balances").json["data"]
    assert data["totals"]["open_count"] == 2
    assert Decimal(data["totals"]["signout_usd"]) == 50
    assert Decimal(data["totals"]["signout_mxn"]) == 930
    assert Decimal(data["totals"]["open_signout_value_usd"]) == 110
    assert [Decimal(s["balance"]["basis_usd"]) for s in data["signouts"]] == [10, 50]
    assert Decimal(data["safe"]["usd"]) == 950
    assert Decimal(data["safe"]["mxn"]) == 620


def test_safe_count_reports_delta_and_persists_nothing(client, conn, csrf):
    opening(conn)
    before = conn.execute(
        "SELECT COUNT(*) FROM transactions").fetchone()[0]
    response = client.post("/api/balances/safe-count",
        json={"usd": "50.00", "mxn": "0"},
        headers={"X-CSRF": csrf})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert Decimal(data["book"]["usd"]) == 1000.00
    assert Decimal(data["count"]["usd"]) == 50.00
    assert Decimal(data["delta"]["usd"]) == -950.00
    after = conn.execute(
        "SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert before == after
    for invalid in ("1.234", "NaN", "Infinity", "-1.00"):
        rejected = client.post("/api/balances/safe-count",
            json={"usd": invalid}, headers={"X-CSRF": csrf})
        assert rejected.status_code == 422
        assert rejected.get_json()["error"]["code"] == "bad_amount"


def test_safe_count_correction_is_atomic_persists_context_and_is_idempotent(
        client, conn, csrf):
    opening(conn)
    document(conn, "count-sheet")
    comparison = post(client, csrf, "/api/balances/safe-count", {
        "usd": "990.00", "mxn": "1600.00"}).get_json()["data"]
    submission_id = sid()
    payload = {
        "submission_id": submission_id,
        "date": "2026-09-03",
        "expected_ledger_revision": comparison["ledger_revision"],
        "usd": "990.00", "mxn": "1600.00",
        "reason": "Counted after closing",
        "assigned_rate_milli": 16000,
        "document_id": "count-sheet",
    }

    response = post(client, csrf, "/api/balances/safe-count/correct", payload)
    assert response.status_code == 200, response.get_json()
    result = response.get_json()["data"]
    assert len(result["txn_ids"]) == 2
    assert {item["currency"] for item in result["movements"]} == {"USD", "MXN"}
    rows = conn.execute(
        "SELECT txn_id, date, description, notes FROM transactions"
        " WHERE txn_id IN (?,?) ORDER BY txn_id", result["txn_ids"]
    ).fetchall()
    assert [row["date"] for row in rows] == ["2026-09-03", "2026-09-03"]
    assert {row["description"] for row in rows} == {"Counted after closing"}
    assert {row["notes"] for row in rows} == {
        "Physical SAFE count · USD", "Physical SAFE count · MXN"}
    assert conn.execute(
        "SELECT ledger_revision FROM ledger_state WHERE singleton_id=1"
    ).fetchone()[0] == comparison["ledger_revision"] + 1
    assert conn.execute(
        "SELECT COUNT(*) FROM transaction_documents"
        " WHERE document_id='count-sheet'"
    ).fetchone()[0] == 2
    assert tuple(conn.execute(
        "SELECT state, doc_type FROM documents WHERE document_id='count-sheet'"
    ).fetchone()) == ("filed", "physical_count")
    safe = repo.replay_projected(conn).states["SAFE"]
    assert (safe.usd, safe.mxn) == (990_00, 1600_00)

    retry = post(client, csrf, "/api/balances/safe-count/correct", payload)
    assert retry.status_code == 200
    assert retry.get_json()["data"] == result
    assert conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE txn_type='adjustment'"
    ).fetchone()[0] == 2


def test_safe_count_correction_rejects_stale_comparison(client, conn, csrf):
    opening(conn)
    comparison = post(client, csrf, "/api/balances/safe-count", {
        "usd": "990.00", "mxn": "1550.00"}).get_json()["data"]
    assert post(client, csrf, "/api/entries/withdrawal", {
        "submission_id": sid(), "date": "2026-09-03", "usd": "10.00",
        "notes": "Cash received after the count"}).status_code == 200

    response = post(client, csrf, "/api/balances/safe-count/correct", {
        "submission_id": sid(), "date": "2026-09-03",
        "expected_ledger_revision": comparison["ledger_revision"],
        "usd": "990.00", "mxn": "1550.00", "reason": "Short"})
    assert response.status_code == 409
    assert conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE txn_type='adjustment'"
    ).fetchone()[0] == 0


def test_ledger_edits_are_limited_to_fields_of_that_transaction_type(conn, client, csrf):
    opening(conn)
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "signout", "date": "2026-08-02",
        "recipient": "Recipient", "purpose": "Supplies", "usd": "100",
    }).get_json()["data"]
    url = f"/api/ledger/{prepared['txn_ids'][0]}"
    before = client.get(url).get_json()["data"]["transaction"]
    moved = client.patch(url, json={
        "submission_id": sid(), "expected_version": before["version"],
        "from_account": "BANK", "reason": "Wrong source"}, headers={"X-CSRF": csrf})
    assert moved.status_code == 422
    assert moved.get_json()["error"]["code"] == "not_editable"
    described = client.patch(url, json={
        "submission_id": sid(), "expected_version": before["version"],
        "description": "Field supplies"}, headers={"X-CSRF": csrf})
    assert described.status_code == 200, described.get_json()
