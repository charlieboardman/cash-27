"""Review desk API and correctable-history integration coverage."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

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
        (name, f"inbox/{name}.pdf", f"hash-{name}"))
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


def test_review_page_is_document_rail_beside_expandable_sheet(client, csrf):
    response = client.get("/review")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    evidence = page.index('class="evidence-pane document-rail"')
    worklist = page.index('class="work-pane review-sheet-pane"')
    assert evidence < worklist
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
    ledger = client.get("/api/ledger").get_json()["data"]
    signout_row = next(
        row for row in ledger["transactions"]
        if row["voucher_id"] == signout and row["txn_type"] == "signout")
    addendum_row = next(
        row for row in ledger["transactions"]
        if row["addendum_id"] == addendum)
    assert signout_row["incomplete"] is True
    assert addendum_row["incomplete"] is True
    assert signout in ledger["open_signouts"]
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
    assert signout in ledger["open_signouts"]
    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert ("signout", signout) not in {(item["kind"], item["id"]) for item in work}
    assert ("addendum", addendum) not in {(item["kind"], item["id"]) for item in work}


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
    assert signout in ledger["open_signouts"]
    document(conn, "return-paper")
    assert post(client, csrf, f"/api/review/addendum/{addendum}/complete", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "return-paper"}).status_code == 200
    ledger = client.get("/api/ledger").get_json()["data"]
    assert signout not in ledger["open_signouts"]
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
