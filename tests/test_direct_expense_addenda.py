"""Direct-expense payment and refund addenda, end to end."""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal

import pytest

from cash import prep, repo
from cash.db import connect


def sid():
    return str(uuid.uuid4())


@pytest.fixture
def conn(app):
    connection = connect(f"{app.config['DATA_ROOT']}/cash.db")
    yield connection
    connection.close()


def post(client, csrf, url, body):
    return client.post(url, json=body, headers={"X-CSRF": csrf})


def document(conn, name):
    conn.execute(
        "INSERT INTO documents (document_id, rel_path, sha256, size_bytes,"
        " page_count, state) VALUES (?, ?, ?, 100, 1, 'inbox')",
        (name, f"documents/{name}.pdf", f"hash-{name}"))
    return name


def opening(client, csrf, usd="1000", mxn="0", rate=15500):
    response = post(client, csrf, "/api/entries/opening", {
        "submission_id": sid(), "date": "2026-08-01", "usd": usd,
        "mxn": mxn, "assigned_rate_milli": rate})
    assert response.status_code == 200, response.get_json()


def completed_expense(client, csrf, conn, *, usd="0", mxn="0", splits=None,
                      receipt=False):
    conn.execute(
        "INSERT OR IGNORE INTO classes (class_code, name) VALUES ('GENERAL','General')")
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense", "date": "2026-08-02",
        "recipient": "Contractor", "purpose": "Service", "notes": "Job notes",
        "usd": usd, "mxn": mxn}).get_json()["data"]
    voucher_id = prepared["voucher_id"]
    document(conn, f"voucher-{voucher_id}")
    receipt_ids = []
    if receipt:
        receipt_ids = [document(conn, f"quote-{voucher_id}")]
    row = {"purpose": "Service", "project": "Job 42", "usd": usd, "mxn": mxn}
    if splits:
        row["splits"] = [{"purpose": "Service", **part} for part in splits]
    else:
        row.update(expense_account="EXP_SUPPLIES", class_code="GENERAL")
    response = post(client, csrf, f"/api/review/direct-expense/{voucher_id}/complete", {
        "submission_id": sid(), "expected_version": 1,
        "voucher_document_id": f"voucher-{voucher_id}",
        "receipt_document_ids": receipt_ids, "rows": [row]})
    assert response.status_code == 200, response.get_json()
    return prepared, response.get_json()["data"]


def print_tokens(response):
    html = response.get_data(as_text=True)
    match = re.search(r'<script id="tokens" type="application/json">(.*?)</script>',
                      html, re.DOTALL)
    return json.loads(match[1])


def create_addendum(client, csrf, voucher_id, kind="positive", *, usd="0", mxn="0",
                    date="2026-08-03", source_txn_id=None, submission_id=None):
    body = {"submission_id": submission_id or sid(), "voucher_id": voucher_id,
            "kind": kind, "date": date, "reason": "Finiquito" if kind == "positive"
            else "Vendor refund", "usd": usd, "mxn": mxn,
            "approver_name": "Browser must not choose this name"}
    if source_txn_id is not None:
        body["source_txn_id"] = source_txn_id
    return post(client, csrf, "/api/addenda/prepare", body)


def test_completed_direct_expense_takes_signed_payment_addendum(
        client, conn, csrf):
    opening(client, csrf, usd="2000")
    prepared, completed = completed_expense(
        client, csrf, conn, usd="500", receipt=True)
    voucher_id = prepared["voucher_id"]
    original_id = completed["txn_ids"][0]

    response = create_addendum(client, csrf, voucher_id, usd="500")
    assert response.status_code == 200, response.get_json()
    result = response.get_json()["data"]
    addendum_id = result["addendum_id"]
    assert addendum_id == f"{voucher_id}-A1"
    assert result["movement_id"] != prepared["movement_id"]
    voucher = conn.execute(
        "SELECT status, authorized_usd_cents FROM vouchers WHERE voucher_id=?",
        (voucher_id,)).fetchone()
    assert tuple(voucher) == ("complete", 500_00)
    original = conn.execute(
        "SELECT usd_cents, to_account FROM transactions WHERE txn_id=?",
        (original_id,)).fetchone()
    assert tuple(original) == (500_00, "EXP_SUPPLIES")
    added = conn.execute(
        "SELECT * FROM transactions WHERE addendum_id=?", (addendum_id,)).fetchone()
    assert (added["txn_type"], added["from_account"], added["to_account"],
            added["usd_cents"], added["class_code"], added["project"],
            added["description"], added["notes"]) == (
                "expense", "SAFE", "EXP_SUPPLIES", 500_00, "GENERAL",
                "Job 42", "Service", "Job notes")
    assert repo.replay_projected(conn).states["SAFE"].usd == 1000_00
    assert conn.execute(
        "SELECT 1 FROM transaction_documents WHERE txn_id=? AND document_id=?"
        " AND role='support'",
        (added["txn_id"], f"quote-{voucher_id}")).fetchone()

    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    [item] = [item for item in work if item["kind"] == "addendum"]
    assert (item["id"], item["secondary"], item["usd"]) == (
        addendum_id, "Additional payment", "500.00")
    assert not [item for item in work if item["kind"] == "direct_expense"]
    detail = client.get(
        f"/api/review/item/addendum/{addendum_id}").get_json()["data"]["addendum"]
    assert detail["parent_type"] == "direct_expense"
    assert detail["usd"] == "500.00"
    printed_response = client.get(
        f"/voucher/{voucher_id}/addendum/{addendum_id}/print")
    printed = printed_response.get_data(as_text=True)
    tokens = print_tokens(printed_response)
    assert tokens["KIND_LABEL"] == "Additional payment / Pago adicional"
    assert (tokens["REASON"], tokens["RECIPIENT"], tokens["USD"]) == (
        "Finiquito", "Contractor", "500.00")
    assert "Vendor signature / Firma del proveedor" in printed
    original_tokens = print_tokens(client.get(f"/voucher/{voucher_id}/print"))
    assert original_tokens["USD"] == "500.00"
    assert client.get(f"/transactions/{added['txn_id']}").location == (
        f"/review?item=addendum:{addendum_id}")

    document(conn, "signed-addendum")
    from cash.web.qr_capture import record_match
    matched = record_match(conn, "signed-addendum", {
        "status": "detected", "ids": [addendum_id]})
    assert matched["status"] == "matched"
    assert matched["review_url"] == f"/review?item=addendum:{addendum_id}"
    assert conn.execute(
        "SELECT COUNT(*) FROM transaction_documents WHERE document_id='signed-addendum'"
    ).fetchone()[0] == 0
    filed = post(client, csrf, f"/api/review/addendum/{addendum_id}/complete", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "signed-addendum"})
    assert filed.status_code == 200, filed.get_json()
    assert prep.direct_voucher_document(conn, voucher_id) == f"voucher-{voucher_id}"
    assert client.get(f"/transactions/{added['txn_id']}").location == (
        f"/ledger?txn={added['txn_id']}")
    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert not [item for item in work if item["id"] == addendum_id]

    document(conn, "late-receipt")
    late = post(client, csrf, f"/api/ledger/{original_id}/receipts", {
        "submission_id": sid(), "document_id": "late-receipt"})
    assert late.status_code == 200, late.get_json()
    assert set(late.get_json()["data"]["txn_ids"]) == {original_id, added["txn_id"]}
    assert conn.execute(
        "SELECT 1 FROM transaction_documents WHERE txn_id=?"
        " AND document_id='late-receipt' AND role='support'",
        (added["txn_id"],)).fetchone()


def test_payment_addendum_copies_splits_and_is_idempotent(client, conn, csrf):
    opening(client, csrf, usd="2000")
    prepared, _ = completed_expense(
        client, csrf, conn, usd="300", splits=[
            {"usd": "100", "expense_account": "EXP_SUPPLIES", "class_code": "GENERAL"},
            {"usd": "200", "expense_account": "EXP_PROGRAMS", "class_code": "GENERAL"},
        ])
    voucher_id = prepared["voucher_id"]
    submission = sid()
    first = create_addendum(
        client, csrf, voucher_id, usd="150", submission_id=submission)
    second = create_addendum(
        client, csrf, voucher_id, usd="150", submission_id=submission)
    assert first.status_code == second.status_code == 200
    assert first.get_json()["data"] == second.get_json()["data"]
    result = first.get_json()["data"]
    rows = conn.execute(
        "SELECT * FROM transactions WHERE addendum_id=?"
        " ORDER BY allocation_line", (result["addendum_id"],)).fetchall()
    assert [(row["to_account"], row["usd_cents"]) for row in rows] == [
        ("EXP_SUPPLIES", 50_00), ("EXP_PROGRAMS", 100_00)]
    assert {row["addendum_id"] for row in rows} == {result["addendum_id"]}
    assert {row["related_txn_id"] for row in rows} == {None}
    work = client.get("/api/review/worklist").get_json()["data"]["items"]
    assert len([item for item in work if item["id"] == result["addendum_id"]]) == 1
    detail = client.get(
        f"/api/review/item/addendum/{result['addendum_id']}").get_json()["data"]
    assert detail["addendum"]["usd"] == "150.00"
    assert print_tokens(client.get(result["print_url"]))["USD"] == "150.00"
    document(conn, "signed-split-addendum")
    filed = post(client, csrf, f"/api/review/addendum/{result['addendum_id']}/complete", {
        "submission_id": sid(), "expected_version": 1,
        "document_id": "signed-split-addendum"})
    assert filed.status_code == 200, filed.get_json()
    assert conn.execute(
        "SELECT COUNT(*) FROM transaction_documents WHERE document_id=?"
        " AND role='voucher'", ("signed-split-addendum",)).fetchone()[0] == 2

    other_currency = create_addendum(
        client, csrf, voucher_id, mxn="300", date="2026-08-04")
    assert other_currency.status_code == 200, other_currency.get_json()
    other = other_currency.get_json()["data"]
    assert other["addendum_id"] == f"{voucher_id}-A2"
    assert [tuple(row) for row in conn.execute(
        "SELECT to_account, usd_cents, mxn_centavos FROM transactions"
        " WHERE addendum_id=? ORDER BY allocation_line",
        (other["addendum_id"],))] == [
            ("EXP_SUPPLIES", 0, 100_00), ("EXP_PROGRAMS", 0, 200_00)]


def test_refund_is_linked_capped_and_can_be_voided_alone(client, conn, csrf):
    opening(client, csrf, usd="2000")
    prepared, completed = completed_expense(client, csrf, conn, usd="500")
    voucher_id = prepared["voucher_id"]
    original_id = completed["txn_ids"][0]
    added = create_addendum(client, csrf, voucher_id, usd="500").get_json()["data"]
    response = create_addendum(
        client, csrf, voucher_id, "return", usd="200", date="2026-08-04",
        source_txn_id=original_id)
    assert response.status_code == 200, response.get_json()
    refund = response.get_json()["data"]
    row = conn.execute(
        "SELECT * FROM transactions WHERE addendum_id=?", (refund["addendum_id"],)).fetchone()
    assert row["usd_cents"] == -200_00
    assert row["related_txn_id"] == original_id
    assert repo.replay_projected(conn).states["SAFE"].usd == 1200_00
    detail = client.get(
        f"/api/review/item/addendum/{refund['addendum_id']}").get_json()["data"]["addendum"]
    assert detail["usd"] == "200.00"
    refund_tokens = print_tokens(client.get(refund["print_url"]))
    assert refund_tokens["KIND_LABEL"] == "Vendor refund / Reembolso del proveedor"
    assert refund_tokens["USD"] == "200.00"
    assert refund_tokens["REASON"] == "Vendor refund"

    excessive = create_addendum(
        client, csrf, voucher_id, "return", usd="300.01", date="2026-08-05",
        source_txn_id=original_id)
    assert excessive.status_code == 422
    assert excessive.get_json()["error"]["code"] == "refund_exceeds_payment"
    assert conn.execute("SELECT COUNT(*) FROM addenda").fetchone()[0] == 2

    positive_edit = client.patch(
        f"/api/ledger/{row['txn_id']}", headers={"X-CSRF": csrf}, json={
            "submission_id": sid(), "expected_version": row["version"],
            "usd": "200", "reason": "Try to change the direction"})
    assert positive_edit.status_code == 422
    assert positive_edit.get_json()["error"]["code"] == "invalid_replay"
    assert conn.execute(
        "SELECT usd_cents FROM transactions WHERE txn_id=?",
        (row["txn_id"],)).fetchone()[0] == -200_00

    voided = post(client, csrf, f"/api/ledger/{row['txn_id']}/void", {
        "submission_id": sid(), "expected_version": row["version"],
        "reason": "Entered twice"})
    assert voided.status_code == 200, voided.get_json()
    assert conn.execute(
        "SELECT status FROM addenda WHERE addendum_id=?",
        (refund["addendum_id"],)).fetchone()[0] == "void"
    assert conn.execute(
        "SELECT status FROM vouchers WHERE voucher_id=?", (voucher_id,)).fetchone()[0] == "complete"
    assert conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE voucher_id=? AND posting_state='active'",
        (voucher_id,)).fetchone()[0] == 2
    assert repo.replay_projected(conn).states["SAFE"].usd == 1000_00


def test_payment_with_linked_refund_cannot_be_voided_first(client, conn, csrf):
    opening(client, csrf)
    prepared, _ = completed_expense(client, csrf, conn, usd="100")
    added = create_addendum(
        client, csrf, prepared["voucher_id"], usd="50").get_json()["data"]
    refund = create_addendum(
        client, csrf, prepared["voucher_id"], "return", usd="10",
        date="2026-08-04", source_txn_id=added["txn_ids"][0]).get_json()["data"]
    response = post(client, csrf, f"/api/ledger/{added['txn_ids'][0]}/void", {
        "submission_id": sid(), "expected_version": 1, "reason": "Wrong payment"})
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "refunds_exist"
    assert conn.execute(
        "SELECT posting_state FROM transactions WHERE txn_id=?",
        (added["txn_ids"][0],)).fetchone()[0] == "active"
    assert conn.execute(
        "SELECT status FROM addenda WHERE addendum_id=?",
        (refund["addendum_id"],)).fetchone()[0] == "pending"


def test_mxn_refund_restores_source_payment_basis(client, conn, csrf):
    opening(client, csrf, usd="0", mxn="2000", rate=20000)
    prepared, completed = completed_expense(client, csrf, conn, mxn="2000")
    voucher_id = prepared["voucher_id"]
    original_id = completed["txn_ids"][0]
    donated = post(client, csrf, "/api/entries/donation", {
        "submission_id": sid(), "date": "2026-08-03", "usd": "0",
        "mxn": "1000", "assigned_rate_milli": 10000, "notes": "New pesos"})
    assert donated.status_code == 200, donated.get_json()
    added = create_addendum(
        client, csrf, voucher_id, mxn="500", date="2026-08-04").get_json()["data"]
    response = create_addendum(
        client, csrf, voucher_id, "return", mxn="500", date="2026-08-05",
        source_txn_id=original_id)
    assert response.status_code == 200, response.get_json()
    refund = response.get_json()["data"]

    replayed = repo.replay_projected(conn)
    safe = replayed.states["SAFE"]
    assert safe.mxn == 1000_00
    assert safe.basis == Decimal(75)
    assert replayed.expense_value == Decimal(125)
    movements = replayed.movements
    assert movements[prepared["movement_id"]].calc_usd_by_row[str(original_id)] == Decimal(100)
    assert movements[added["movement_id"]].calc_usd_by_row[str(added["txn_ids"][0])] == Decimal(50)
    assert movements[refund["movement_id"]].calc_usd_by_row[str(refund["txn_ids"][0])] == Decimal(-25)
    assert not replayed.open_claims

    from cash.web.export import _row_values, export_rows
    exported = [_row_values(row, replayed) for row in export_rows(conn, "expenses")
                if row["voucher_id"] == voucher_id]
    assert [(row["Activity"], row["ER"], row["calc_usd"]) for row in exported] == [
        ("Expense", Decimal("20.000"), Decimal("100.00")),
        ("Additional payment", Decimal("10.000"), Decimal("50.00")),
        ("Vendor refund", Decimal("20.000"), Decimal("-25.00"))]

    detail = client.get(f"/api/ledger/{original_id}").get_json()["data"]["transaction"]
    sources = detail["direct_addendum"]["refund_sources"]
    original_source = next(source for source in sources if source["txn_id"] == original_id)
    assert original_source["mxn"] == "1500.00"


def test_unresolved_source_basis_blocks_refund_atomically(client, conn, csrf):
    opening(client, csrf, usd="0", mxn="100", rate=10000)
    prepared, completed = completed_expense(client, csrf, conn, mxn="200")
    response = create_addendum(
        client, csrf, prepared["voucher_id"], "return", mxn="50",
        source_txn_id=completed["txn_ids"][0])
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "basis_unresolved"
    assert conn.execute("SELECT COUNT(*) FROM addenda").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE voucher_id=?",
        (prepared["voucher_id"],)).fetchone()[0] == 1


def test_pending_direct_expense_cannot_take_addendum(client, conn, csrf):
    opening(client, csrf)
    prepared = post(client, csrf, "/api/vouchers/prepare", {
        "submission_id": sid(), "type": "direct_expense", "date": "2026-08-02",
        "recipient": "Contractor", "purpose": "Service", "notes": "",
        "usd": "10", "mxn": "0"}).get_json()["data"]
    response = create_addendum(client, csrf, prepared["voucher_id"], usd="5")
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "not_complete"
    assert conn.execute("SELECT COUNT(*) FROM addenda").fetchone()[0] == 0


def test_voiding_original_direct_expense_voids_its_family(client, conn, csrf):
    opening(client, csrf)
    prepared, completed = completed_expense(client, csrf, conn, usd="100")
    voucher_id = prepared["voucher_id"]
    create_addendum(client, csrf, voucher_id, usd="50")
    original = conn.execute(
        "SELECT version FROM transactions WHERE txn_id=?",
        (completed["txn_ids"][0],)).fetchone()
    response = post(client, csrf, f"/api/ledger/{completed['txn_ids'][0]}/void", {
        "submission_id": sid(), "expected_version": original["version"],
        "reason": "Duplicate voucher"})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["voucher_void"] is True
    assert conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE voucher_id=? AND posting_state='active'",
        (voucher_id,)).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM addenda WHERE voucher_id=? AND status='void'",
        (voucher_id,)).fetchone()[0] == 1
    assert conn.execute(
        "SELECT status FROM vouchers WHERE voucher_id=?", (voucher_id,)).fetchone()[0] == "void"
