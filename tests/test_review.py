"""Review desk API and correctable-history integration coverage."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

import pytest

from cash import packages, repo
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
    new_transaction = client.get("/voucher").get_data(as_text=True)
    assert "Record a cash donation" not in new_transaction
    assert "Cash donation" in new_transaction
    assert "What happened?" not in new_transaction
    assert "Who and why" not in new_transaction
    assert "Notes for staff" not in new_transaction
    assert "Exchange currency" in new_transaction
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


def test_exchange_review_files_required_receipt_without_changing_cash(
        client, conn, csrf):
    opening(conn)
    created = post(client, csrf, "/api/entries/exchange", {
        "submission_id": sid(), "date": "2026-09-01",
        "source_account": "SAFE", "usd": "20", "mxn": "400",
        "notes": "Exchange counter",
    }).get_json()["data"]
    txn_id = created["txn_ids"][0]
    safe_before = repo.replay_projected(conn).states["SAFE"]
    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert len(work) == 1
    assert (work[0]["kind"], work[0]["id"], work[0]["badges"]) == (
        "exchange", str(txn_id), ["review needed", "receipt needed"])
    assert client.get(f"/transactions/{txn_id}").location == (
        f"/review?item=exchange:{txn_id}")
    detail = client.get(f"/api/review/item/exchange/{txn_id}").get_json()["data"]
    version = detail["transaction"]["version"]
    assert client.get(f"/api/ledger/{txn_id}").get_json()["data"][
        "transaction"]["incomplete"] is True
    ledger = client.get("/api/ledger?type=exchange").get_json()["data"]
    assert ledger["transactions"][0]["incomplete"] is True
    url = f"/api/review/exchange/{txn_id}/complete"
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
        ("exchange-receipt", "support", "exchange_receipt")]
    assert repo.replay_projected(conn).states["SAFE"] == safe_before


@pytest.mark.parametrize("case,code", [
    ("missing_document", "document_unavailable"),
    ("filed_document", "document_unavailable"),
    ("stale_version", "conflict"),
    ("void_exchange", "not_pending"),
    ("wrong_type", "not_exchange"),
    ("already_reviewed", "not_pending"),
])
def test_exchange_review_rejects_invalid_filing_atomically(
        client, conn, csrf, case, code):
    opening(conn)
    created = post(client, csrf, "/api/entries/exchange", {
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
    elif case == "void_exchange":
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
        assert post(client, csrf, f"/api/review/exchange/{txn_id}/complete", {
            "submission_id": sid(), "expected_version": version,
            "document_id": "first-receipt"}).status_code == 200
        version += 1
    before = conn.execute("SELECT COUNT(*) FROM transaction_documents").fetchone()[0]
    result = post(client, csrf, f"/api/review/exchange/{txn_id}/complete", {
        "submission_id": sid(), "expected_version": version,
        "document_id": "exchange-receipt"})
    assert result.status_code in (422, 409), result.get_json()
    assert result.get_json()["error"]["code"] == code
    assert conn.execute("SELECT COUNT(*) FROM transaction_documents").fetchone()[0] == before
    if case not in ("missing_document", "filed_document"):
        assert conn.execute("SELECT state FROM documents WHERE document_id=?",
                            ("exchange-receipt",)).fetchone()[0] == "inbox"


def test_exchange_can_be_created_and_reviewed_from_inbox(client, conn, csrf):
    opening(conn)
    document(conn, "exchange-receipt")
    body = {"submission_id": sid(), "date": "2026-09-01", "usd": "20",
            "mxn": "400", "document_id": "exchange-receipt"}
    response = post(client, csrf, "/api/entries/exchange", body)
    assert response.status_code == 200, response.get_json()
    assert post(client, csrf, "/api/entries/exchange", body).get_json() == response.get_json()
    txn_id = response.get_json()["data"]["txn_ids"][0]
    assert client.get(f"/api/ledger/{txn_id}").get_json()["data"][
        "transaction"]["incomplete"] is False
    assert client.get("/api/review/worklist").get_json()["data"]["items"] == []
    body["submission_id"] = sid()
    rejected = post(client, csrf, "/api/entries/exchange", body)
    assert rejected.get_json()["error"]["code"] == "document_unavailable"
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE txn_type='exchange'").fetchone()[0] == 1


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
    assert "Person / purpose" in page
    assert "Receipt value" in page
    assert "Transactions" in page
    assert "Receipt submissions" in page
    assert "New transaction" in page
    assert "Review transactions" in page
    assert "Capture documents" in page
    assert "View ledger" in page
    assert "Generate reports" in page
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


def test_direct_voucher_accepts_mixed_usd_and_mxn_acknowledgement(
        conn, client, csrf):
    opening(conn)
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense",
        "date": "2026-08-02", "recipient": "Contractor",
        "purpose": "Repairs", "notes": "",
        "receipt_usd": "600.00", "receipt_mxn": "400.00",
        "usd_paid": "600.00", "mxn_paid": "400.00",
        "usd_change": "0", "mxn_change": "0",
    })
    assert prepared.status_code == 200, prepared.get_json()
    voucher_id = prepared.get_json()["data"]["voucher_id"]
    voucher = conn.execute(
        "SELECT authorized_usd_cents, authorized_mxn_centavos,"
        " face_value_minor, face_currency, face_usd_cents,"
        " face_mxn_centavos FROM vouchers WHERE voucher_id=?",
        (voucher_id,)).fetchone()
    assert dict(voucher) == {"authorized_usd_cents": 600_00,
                             "authorized_mxn_centavos": 400_00,
                             "face_value_minor": None,
                             "face_currency": None,
                             "face_usd_cents": 600_00,
                             "face_mxn_centavos": 400_00}
    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    item = next(row for row in work if row["id"] == voucher_id)
    assert item["receipt"] == "USD 600.00 + MXN 400.00"
    printed = client.get(f"/voucher/{voucher_id}/print")
    assert printed.status_code == 200
    text = printed.get_data(as_text=True)
    assert "600.00" in text
    assert "400.00" in text
    tokens = printed_tokens(printed)
    assert tokens["VENDOR"] == "Contractor"
    assert tokens["SIGNOUT_RECIPIENT"] == ""


def test_direct_voucher_treats_blank_currency_and_change_boxes_as_zero(
        conn, client, csrf):
    opening(conn)
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense",
        "date": "2026-08-02", "recipient": "Papeleria",
        "purpose": "Supplies", "notes": "",
        "receipt_usd": "", "receipt_mxn": "150.00",
        "usd_paid": "20.00", "mxn_paid": "",
        "usd_change": "", "mxn_change": "",
    })
    assert prepared.status_code == 200, prepared.get_json()
    voucher = conn.execute(
        "SELECT authorized_usd_cents, authorized_mxn_centavos,"
        " face_usd_cents, face_mxn_centavos, usd_change_cents,"
        " mxn_change_centavos FROM vouchers WHERE voucher_id=?",
        (prepared.get_json()["data"]["voucher_id"],)).fetchone()
    assert dict(voucher) == {
        "authorized_usd_cents": 20_00,
        "authorized_mxn_centavos": None,
        "face_usd_cents": None,
        "face_mxn_centavos": 150_00,
        "usd_change_cents": 0,
        "mxn_change_centavos": 0,
    }


@pytest.mark.parametrize("receipt_usd,receipt_mxn", [
    ("20.00", ""), ("", "150.00"), ("600.00", "400.00"),
])
@pytest.mark.parametrize("tender", [{}, {"usd_paid": "", "mxn_paid": ""},
                                   {"usd_paid": None, "mxn_paid": "  "}])
def test_blank_tender_means_exact_receipt_amounts(
        conn, client, csrf, receipt_usd, receipt_mxn, tender):
    opening(conn)
    result = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense",
        "date": "2026-08-02", "recipient": "Vendor", "purpose": "Supplies",
        "receipt_usd": receipt_usd, "receipt_mxn": receipt_mxn,
        "usd_change": "", "mxn_change": "", **tender,
    })
    assert result.status_code == 200, result.get_json()
    voucher_id = result.get_json()["data"]["voucher_id"]
    voucher = conn.execute("SELECT * FROM vouchers WHERE voucher_id=?",
                           (voucher_id,)).fetchone()
    for paid, face, authorized in (
        ("usd_paid_cents", "face_usd_cents", "authorized_usd_cents"),
        ("mxn_paid_centavos", "face_mxn_centavos", "authorized_mxn_centavos"),
    ):
        assert voucher[paid] == (voucher[face] or 0) == (voucher[authorized] or 0)
    tokens = printed_tokens(client.get(result.get_json()["data"]["print_url"]))
    assert tokens["USD_PAID"] == receipt_usd
    assert tokens["MXN_PAID"] == receipt_mxn


@pytest.mark.parametrize("extra,status", [
    ({"usd_change": "1"}, 400), ({"mxn_change": "5"}, 400),
    ({"usd_paid": "0", "mxn_paid": "0"}, 422),
    ({"usd_paid": "0", "mxn_paid": ""}, 422),
    ({"receipt_usd": "", "receipt_mxn": ""}, 400),
])
def test_exact_payment_shortcut_does_not_hide_invalid_tender(
        conn, client, csrf, extra, status):
    opening(conn)
    result = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense",
        "date": "2026-08-02", "recipient": "Vendor", "purpose": "Supplies",
        "receipt_usd": "20.00", "receipt_mxn": "150.00",
        "usd_paid": "", "mxn_paid": "", **extra,
    })
    assert result.status_code == status, result.get_json()
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
        "receipt_usd": "600.00", "receipt_mxn": "400.00",
        "usd_paid": "600.00", "mxn_paid": "400.00",
        "usd_change": "0", "mxn_change": "0",
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
        "allocations": [{"expense_account": "EXP_SUPPLIES",
                         "class_code": "GENERAL", "usd": "600.00",
                         "mxn": "400.00", "face_usd": "600.00",
                         "face_mxn": "400.00", "description": "Repairs"}],
    })
    assert completed.status_code == 200, completed.get_json()
    row = conn.execute(
        "SELECT receipt_face_usd_cents, receipt_face_mxn_centavos,"
        " receipt_face_minor, receipt_face_currency FROM transactions"
        " WHERE voucher_id=? AND posting_state='active'", (voucher_id,)
    ).fetchone()
    assert dict(row) == {"receipt_face_usd_cents": 600_00,
                         "receipt_face_mxn_centavos": 400_00,
                         "receipt_face_minor": None,
                         "receipt_face_currency": None}
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


def test_direct_expense_can_be_completed_as_two_receipt_movements(
        conn, client, csrf):
    opening(conn)
    conn.execute(
        "INSERT INTO classes (class_code, name) VALUES ('GENERAL','General')")
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense",
        "date": "2026-08-02", "recipient": "Two vendors",
        "purpose": "Mixed purchases", "notes": "",
        "receipt_usd": "10.00", "receipt_mxn": "150.00",
        "usd_paid": "20.00", "mxn_paid": "0",
        "usd_change": "0", "mxn_change": "0",
    }).get_json()["data"]
    for name in ("voucher", "receipt-one", "receipt-two"):
        document(conn, name)
    response = post(
        client, csrf,
        f"/api/review/direct-expense/{prepared['voucher_id']}/complete", {
            "submission_id": sid(), "expected_version": 1,
            "voucher_document_id": "voucher",
            "receipts": [
                {"document_id": "receipt-one", "date": "2026-08-02",
                 "vendor": "Vendor one", "description": "First",
                 "usd": "10.00", "mxn": "0", "face_usd": "10.00",
                 "face_mxn": "0", "allocations": [{
                     "expense_account": "EXP_SUPPLIES",
                     "class_code": "GENERAL", "usd": "10.00", "mxn": "0",
                     "face_usd": "10.00", "face_mxn": "0",
                     "project": "ALPHA"}]},
                {"document_id": "receipt-two", "date": "2026-08-02",
                 "vendor": "Vendor two", "description": "Second",
                 "usd": "10.00", "mxn": "0", "face_usd": "0",
                 "face_mxn": "150.00", "allocations": [{
                     "expense_account": "EXP_PROGRAMS",
                     "class_code": "GENERAL", "usd": "10.00", "mxn": "0",
                     "face_usd": "0", "face_mxn": "150.00",
                     "project": "BETA"}]},
            ],
        })
    assert response.status_code == 200, response.get_json()
    active = conn.execute(
        "SELECT movement_id, receipt_id, usd_cents,"
        " receipt_face_usd_cents, receipt_face_mxn_centavos, project"
        " FROM transactions WHERE voucher_id=? AND posting_state='active'"
        " ORDER BY txn_id", (prepared["voucher_id"],)).fetchall()
    assert len(active) == 2
    assert len({row["movement_id"] for row in active}) == 2
    assert [row["receipt_id"] for row in active] == [
        f"{prepared['voucher_id']}-R1", f"{prepared['voucher_id']}-R2"]
    assert [row["project"] for row in active] == ["ALPHA", "BETA"]
    assert conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE voucher_id=?"
        " AND posting_state='superseded'",
        (prepared["voucher_id"],)).fetchone()[0] == 1


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


def test_signout_closes_from_facts_after_final_return_addendum_is_filed(
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
    ledger = client.get("/api/ledger").get_json()["data"]
    open_signouts = {s["voucher_id"] for s in client.get(
        "/api/balances").get_json()["data"]["signouts"] if s["open"]}
    assert signout not in open_signouts
    workspace = client.get(f"/api/signouts/{signout}").get_json()["data"]
    assert workspace["summary"]["status"] == "closed"
    assert workspace["summary"]["reasons"] == []


def test_signout_voucher_can_also_serve_as_the_receipt(
        conn, client, csrf):
    opening(conn)
    signout = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="School supplies", notes="",
            usd_cents=100_00, mxn_centavos=200_00))["voucher_id"]
    document(conn, "signed-signout")

    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    item = next(row for row in work if row["kind"] == "signout")
    assert item["badges"] == ["voucher needed", "receipt needed"]

    response = post(client, csrf, f"/api/review/signout/{signout}/attach-voucher", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "signed-signout", "use_as_receipt": True})
    assert response.status_code == 200, response.get_json()

    roles = [row["role"] for row in conn.execute(
        "SELECT td.role FROM transaction_documents td JOIN transactions t"
        " ON t.txn_id=td.txn_id WHERE t.voucher_id=? AND"
        " t.txn_type='signout' ORDER BY td.role", (signout,))]
    assert roles == ["support", "voucher"]

    detail = client.get(
        f"/api/review/item/signout/{signout}").get_json()["data"]
    assert detail["documents"][0]["roles"] == ["support", "voucher"]


def test_signout_can_file_a_separate_receipt(conn, client, csrf):
    opening(conn)
    signout = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="School supplies", notes="",
            usd_cents=100_00, mxn_centavos=200_00))["voucher_id"]
    document(conn, "signed-signout")
    document(conn, "vendor-receipt")

    response = post(client, csrf, f"/api/review/signout/{signout}/attach-voucher", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "signed-signout",
        "receipt_document_id": "vendor-receipt"})
    assert response.status_code == 200, response.get_json()

    documents = [tuple(row) for row in conn.execute(
        "SELECT td.document_id, td.role FROM transaction_documents td"
        " JOIN transactions t ON t.txn_id=td.txn_id"
        " WHERE t.voucher_id=? AND t.txn_type='signout'"
        " ORDER BY td.role", (signout,))]
    assert documents == [("vendor-receipt", "support"),
                         ("signed-signout", "voucher")]
    filed = {row["document_id"]: (row["state"], row["doc_type"])
             for row in conn.execute(
                 "SELECT document_id, state, doc_type FROM documents"
                 " WHERE document_id IN ('signed-signout','vendor-receipt')")}
    assert filed == {"signed-signout": ("filed", "voucher"),
                     "vendor-receipt": ("filed", "receipt")}


def test_direct_expense_preserves_tender_and_conserves_face_shares(
        conn, client, csrf):
    opening(conn)
    conn.execute(
        "INSERT INTO classes (class_code, name) VALUES ('GENERAL','General')")
    prepared = run_command(
        conn, sid(), "prepare_direct",
        lambda c: prepare_direct_expense(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Vendor", purpose="Supplies", notes="",
            usd_cents=40_00, mxn_centavos=0,
            face_value_minor=40_00, face_currency="USD",
            usd_paid_cents=50_00, usd_change_cents=10_00,
            mxn_paid_centavos=0, mxn_change_centavos=0))
    voucher_id = prepared["voucher_id"]
    voucher = conn.execute("SELECT * FROM vouchers WHERE voucher_id=?",
                           (voucher_id,)).fetchone()
    assert voucher["face_value_minor"] == 40_00
    assert voucher["usd_paid_cents"] == 50_00
    document(conn, "signed-voucher")
    response = post(
        client, csrf, f"/api/review/direct-expense/{voucher_id}/complete", {
            "submission_id": sid(), "expected_version": 1,
            "voucher_document_id": "signed-voucher",
            "allocations": [
                {"expense_account": "EXP_SUPPLIES", "usd": "30.00",
                 "mxn": "0", "face_value": "25.00",
                 "class_code": "GENERAL", "project": "OFFICE",
                 "description": "Paper"},
                {"expense_account": "EXP_PROGRAMS", "usd": "10.00",
                 "mxn": "0", "face_value": "15.00",
                 "class_code": "GENERAL", "project": "PROGRAM",
                 "description": "Markers"},
            ]})
    assert response.status_code == 200, response.get_json()
    rows = conn.execute(
        "SELECT usd_cents, receipt_face_minor, receipt_face_currency,"
        " project, description FROM transactions WHERE voucher_id=? AND"
        " posting_state='active' ORDER BY allocation_line", (voucher_id,)).fetchall()
    assert [row["usd_cents"] for row in rows] == [30_00, 10_00]
    assert [row["receipt_face_minor"] for row in rows] == [25_00, 15_00]
    assert all(row["receipt_face_currency"] == "USD" for row in rows)
    assert [row["project"] for row in rows] == ["OFFICE", "PROGRAM"]
    assert [row["description"] for row in rows] == ["Paper", "Markers"]


def test_package_allocations_can_be_saved_and_verified_through_api(
        conn, client, csrf):
    opening(conn)
    conn.execute("INSERT INTO classes (class_code, name) VALUES ('GENERAL','General')")
    voucher_id = run_command(
        conn, sid(), "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="Program", notes="",
            mxn_centavos=500_00))["voucher_id"]
    document(conn, "receipt")
    claim_id = run_command(
        conn, sid(), "claim",
        lambda c: packages.create_claim(
            c, voucher_id=voucher_id, document_id="receipt",
            claim_date="2026-08-03", vendor="Shop", description="Materials",
            face_value_minor=100_00, face_currency="MXN",
            usd_paid_cents=0, mxn_paid_centavos=100_00,
            mxn_change_centavos=0, usd_change_cents=0, notes=""))["claim_id"]
    package_id = run_command(
        conn, sid(), "submit",
        lambda c: packages.submit_package(
            c, voucher_id=voucher_id, claim_ids=[claim_id]))["package_id"]
    response = post(client, csrf, f"/api/review/claims/{claim_id}/allocations", {
        "submission_id": sid(), "expected_content_version": 1,
        "allocations": [{"expense_account": "EXP_SUPPLIES",
                         "class_code": "GENERAL", "usd": "0",
                         "mxn": "100.00", "face_value": "100.00",
                         "description": "Materials"}]})
    assert response.status_code == 200, response.get_json()
    response = post(client, csrf, f"/api/review/claims/{claim_id}/verify", {
        "submission_id": sid(), "expected_content_version": 1,
        "admin_note": "Matches receipt"})
    assert response.status_code == 200
    detail = client.get(f"/api/review/item/package/{package_id}").get_json()["data"]
    assert detail["claims"][0]["state"] == "verified"
    assert detail["claims"][0]["allocations"][0]["face_value_minor"] == 100_00


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
        "recipient": "Vendor", "purpose": "Supplies", "receipt_usd": "20",
    }).get_json()["data"]
    voucher_id = prepared["voucher_id"]
    document(conn, "signed-voucher")
    completed = post(
        client, csrf, f"/api/review/direct-expense/{voucher_id}/complete", {
            "submission_id": sid(), "expected_version": 1,
            "voucher_document_id": "signed-voucher",
            "allocations": [{"expense_account": "EXP_SUPPLIES",
                             "class_code": "GENERAL", "usd": "20",
                             "mxn": "0", "face_value": "20"}],
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
        "recipient": "Vendor", "purpose": "Supplies", "receipt_usd": "20",
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
    assert data["safe"]["usd"] == 900.00
    assert "bank" not in data
    entry = next(s for s in data["signouts"]
                 if s["voucher_id"] == signout)
    assert entry["open"] is True
    assert entry["balance"]["usd"] == 100.00
    assert "cash remaining" in entry["reasons"]
    assert data["totals"]["signout_usd"] == 100.00
    assert data["totals"]["tracked_usd"] == 1000.00
    assert data["totals"]["open_count"] == 1


def test_safe_count_reports_delta_and_persists_nothing(client, conn, csrf):
    opening(conn)
    before = conn.execute(
        "SELECT COUNT(*) FROM transactions").fetchone()[0]
    response = client.post("/api/balances/safe-count",
        json={"usd": "50.00", "mxn": "0"},
        headers={"X-CSRF": csrf})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["book"]["usd"] == 1000.00
    assert data["count"]["usd"] == 50.00
    assert data["delta"]["usd"] == -950.00
    after = conn.execute(
        "SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert before == after
    for invalid in ("1.234", "NaN", "Infinity", "-1.00"):
        rejected = client.post("/api/balances/safe-count",
            json={"usd": invalid}, headers={"X-CSRF": csrf})
        assert rejected.status_code == 400
        assert rejected.get_json()["error"]["code"] == "bad_count"


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
