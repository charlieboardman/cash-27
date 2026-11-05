"""Operational overview and global search coverage."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app_factory import create_app
from cash.commands import run_command
from cash.db import connect as connect_db
from cash.prep import (create_opening, create_withdrawal,
                       prepare_direct_expense, prepare_signout)
from cash.web import overview


def sid():
    return str(uuid.uuid4())


@pytest.fixture
def app(tmp_path):
    application = create_app(tmp_path, admin_password="x", testing=True)
    if overview.bp.name not in application.blueprints:
        application.register_blueprint(overview.bp)
    return application


@pytest.fixture
def conn(app):
    connection = connect_db(Path(app.config["DATA_ROOT"]) / "cash.db")
    yield connection
    connection.close()


def opening(conn):
    submission = sid()
    return run_command(
        conn, submission, "opening",
        lambda c: create_opening(
            c, submission_id=submission, date="2026-08-01",
            usd_cents=1000_00, mxn_centavos=1550_00,
            assigned_rate_milli=15500))


def withdrawal(conn, *, date="2026-08-02", usd_cents=50_00, notes="ATM"):
    submission = sid()
    return run_command(
        conn, submission, "withdrawal",
        lambda c: create_withdrawal(
            c, submission_id=submission, date=date, seq_no=None,
            usd_cents=usd_cents, notes=notes))


def direct_expense(conn):
    submission = sid()
    return run_command(
        conn, submission, "prepare_direct_expense",
        lambda c: prepare_direct_expense(
            c, submission_id=submission, date="2026-08-04", seq_no=None,
            recipient="Recipient Needle", purpose="Description Needle",
            notes="Notes Needle", usd_cents=20_00))


def signout(conn):
    submission = sid()
    return run_command(
        conn, submission, "prepare_signout",
        lambda c: prepare_signout(
            c, submission_id=submission, date="2026-08-03", seq_no=None,
            recipient="Ana", purpose="Field work", notes="", usd_cents=100_00))


def document(conn, document_id, display_name, *, created_at="2026-08-05T12:00:00Z"):
    conn.execute(
        "INSERT INTO documents (document_id, rel_path, sha256, size_bytes,"
        " page_count, state, display_name, created_at)"
        " VALUES (?, ?, ?, 100, 1, 'inbox', ?, ?)",
        (document_id, f"documents/{document_id}.pdf", f"hash-{document_id}",
         display_name, created_at))
    return document_id


def search_results(client, query):
    response = client.get("/api/search", query_string={"q": query})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["ok"] is True
    return response.get_json()["data"]["results"]


def test_overview_requires_admin_and_renders_empty_operational_home(app, client, csrf):
    anonymous = app.test_client()
    assert anonymous.get("/overview").location == "/login"
    assert anonymous.get("/api/search?q=test").status_code == 401

    response = client.get("/overview")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "/static/css/overview.css" in page
    assert "Needs attention" in page
    assert "Cash position" in page
    assert "Inbox documents" in page
    assert 'id="overview-attention-count">0</span>' in page
    assert 'id="overview-inbox-count">0</span>' in page
    assert 'id="overview-safe-usd">0.00</dd>' in page
    for href, label in (
            ("/transactions/new", "New transaction"),
            ("/capture", "Capture document"),
            ("/balances#count", "Count safe"),
            ("/review", "Open Review")):
        assert f'href="{href}"' in page
        assert label in page


def test_overview_links_worklist_and_shows_cash_and_visible_inbox(
        client, conn, csrf):
    opening(conn)
    pending_withdrawal = withdrawal(conn)
    pending_signout = signout(conn)
    pending_expense = direct_expense(conn)
    staged_id = document(conn, "D000100", "staged.pdf")
    document(conn, "D000101", "visible <receipt>.pdf")
    conn.execute(
        "INSERT INTO direct_expense_review_drafts (voucher_id, draft_json)"
        " VALUES (?, ?)",
        (pending_expense["voucher_id"], json.dumps({
            "format": 2, "primary_document_id": staged_id,
            "receipt_document_ids": [], "rows": []})))

    response = client.get("/overview")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'id="overview-attention-count">3</span>' in page
    for txn_id in (pending_withdrawal["txn_ids"][0],
                   pending_signout["txn_ids"][0],
                   pending_expense["txn_ids"][0]):
        assert f'href="/transactions/{txn_id}"' in page
    assert 'id="overview-safe-usd">930.00</dd>' in page
    assert 'id="overview-safe-mxn">1,550.00</dd>' in page
    assert 'id="overview-safe-basis">100.00</dd>' in page
    assert 'id="overview-open-signout-count">1</span>' in page
    assert 'id="overview-open-signout-usd">100.00</dd>' in page
    assert 'id="overview-open-signout-mxn">0.00</dd>' in page
    assert 'id="overview-open-signout-value">100.00</dd>' in page
    assert 'id="overview-inbox-count">1</span>' in page
    assert "staged.pdf" not in page
    assert "visible &lt;receipt&gt;.pdf" in page
    assert 'href="/documents/D000101/preview"' in page


def test_search_matches_every_transaction_field(client, conn, csrf):
    opening(conn)
    for _ in range(9):
        create_withdrawal(
            conn, submission_id=sid(), date="2026-08-02", seq_no=None,
            usd_cents=1_00, notes="filler")
    prepared = direct_expense(conn)
    txn_id = prepared["txn_ids"][0]
    conn.execute(
        "UPDATE transactions SET vendor='Vendor Needle', project='Project Needle'"
        " WHERE txn_id=?", (txn_id,))

    queries = (str(txn_id), prepared["voucher_id"], "recipient needle",
               "VENDOR NEEDLE", "description needle", "PROJECT NEEDLE",
               "notes needle", "SAFE", "UNALLOCATED_EXPENSE")
    for query in queries:
        results = search_results(client, query)
        assert txn_id in {item["id"] for item in results
                          if item["kind"] == "transaction"}, query
    result = next(item for item in search_results(client, "Vendor Needle")
                  if item["id"] == txn_id)
    assert result == {
        "kind": "transaction", "id": txn_id,
        "title": "Description Needle",
        "subtitle": f"Txn-{txn_id} · Expense · 2026-08-04 · "
                    f"{prepared['voucher_id']} · SAFE → UNALLOCATED_EXPENSE",
        "url": f"/transactions/{txn_id}"}


def test_search_matches_documents_and_puts_transactions_first(client, conn, csrf):
    opening(conn)
    conn.execute("UPDATE transactions SET notes='SharedNeedle'")
    document(conn, "D000110", "SharedNeedle.pdf")
    document(conn, "D000111", "<North & Co>.pdf",
             created_at="2026-08-06T12:00:00Z")

    shared = search_results(client, "sharedneedle")
    assert [item["kind"] for item in shared] == ["transaction", "document"]
    north = search_results(client, "north")
    assert north == [{
        "kind": "document", "id": "D000111", "title": "<North & Co>.pdf",
        "subtitle": "D000111 · Inbox · 1 page · 2026-08-06",
        "url": "/documents/D000111/preview"}]
    by_id = search_results(client, "d000111")
    assert [item["id"] for item in by_id] == ["D000111"]


@pytest.mark.parametrize("query", [None, "", " ", "a", " a \t"])
def test_search_short_queries_return_empty_results(client, conn, csrf, query):
    opening(conn)
    document(conn, "D000120", "anything.pdf")
    request = {} if query is None else {"q": query}
    response = client.get("/api/search", query_string=request)
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "data": {"results": []}}


def test_search_treats_like_wildcards_and_escape_character_literally(
        client, conn, csrf):
    opening(conn)
    notes = ("rate 100% cash", "rate 1000 cash", "under_score", "underXscore",
             "path C:\\new", "path C:Xnew")
    for index, note in enumerate(notes):
        create_withdrawal(
            conn, submission_id=sid(), date=f"2026-08-{index + 2:02d}",
            seq_no=None, usd_cents=1_00, notes=note)

    assert [item["title"] for item in search_results(client, "100%")] == [
        "Txn-2"]
    assert [item["title"] for item in search_results(client, "under_")] == [
        "Txn-4"]
    assert [item["title"] for item in search_results(client, "C:\\")] == [
        "Txn-6"]


def test_search_limits_results_to_twelve_recent_transactions(
        client, conn, csrf):
    opening(conn)
    created = []
    for number in range(14):
        created.extend(create_withdrawal(
            conn, submission_id=sid(), date=f"2026-09-{number + 1:02d}",
            seq_no=None, usd_cents=1_00, notes="BulkNeedle")["txn_ids"])
    document(conn, "D000130", "BulkNeedle.pdf")

    results = search_results(client, "BulkNeedle")
    assert len(results) == 12
    assert {item["kind"] for item in results} == {"transaction"}
    assert [item["id"] for item in results] == list(reversed(created))[:12]


def test_overview_and_search_run_with_query_only_connections(
        client, conn, csrf, monkeypatch):
    opening(conn)
    withdrawal(conn, notes="ReadOnlyNeedle")
    signout(conn)
    document(conn, "D000140", "ReadOnlyNeedle.pdf")
    before = list(conn.iterdump())

    real_connect = connect_db

    def read_only_connect(path):
        connection = real_connect(path)
        connection.execute("PRAGMA query_only=ON")
        return connection

    monkeypatch.setattr("cash.web.app.connect", read_only_connect)
    assert client.get("/overview").status_code == 200
    assert len(search_results(client, "ReadOnlyNeedle")) == 2
    assert list(conn.iterdump()) == before
