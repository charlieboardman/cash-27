"""Expense reports as draft rows: edited freely, posted only on approval."""

from decimal import Decimal

import pytest

from cash import repo, signouts
from test_closeouts import approve, desk, data, document, sid, voucher  # noqa: F401  (fixture)


def report(desk, vid):
    _, client, _ = desk
    return data(client.get(f"/{vid}/api/report"))


def call(desk, method, url, **body):
    _, client, post = desk
    if method == "post":
        return post(url, **body)
    csrf = client.get("/api/session").get_json()["data"]["csrf"]
    return getattr(client, method)(url, json={"submission_id": sid(), **body},
                                   headers={"X-CSRF": csrf})


def add(desk, vid, **fields):
    view = data(call(desk, "post", f"/{vid}/api/rows", **fields))
    return view["rows"][-1]


def update(desk, vid, row, **fields):
    return call(desk, "patch", f"/{vid}/api/rows/{row['id']}", rev=row["rev"], **fields)


def receipt_document(desk, vid):
    conn = desk[0]
    doc = document(conn)
    conn.execute("UPDATE documents SET voucher_id=?, state='signee_draft' WHERE document_id=?",
                 (vid, doc))
    return doc


def coded(**fields):
    return {"class_code": "GENERAL", "expense_account": "EXP_SUPPLIES", **fields}


def expense(desk, vid, **fields):
    return add(desk, vid, **{"date": "2026-09-03", "purpose": "Supplies",
                             "document_id": receipt_document(desk, vid), **fields})


@pytest.mark.parametrize("kind,suffix", [(None, "-R1"), ("statement", "-R1x"), ("log", "-log")])
def test_draft_document_labels_use_report_references(desk, kind, suffix):
    vid = voucher(desk, usd="20")
    row = expense(desk, vid, usd="5", **({"document_kind": kind} if kind else {}))
    assert row["document"]["display_name"] == f"{vid}{suffix}.pdf"


def test_rows_are_drafts_until_approval_posts_them_atomically(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="100", mxn="0")
    before = repo.replay_projected(conn).states[vid]
    row = expense(desk, vid, usd="100", mxn="-100", receipt_total="1500", **coded())
    assert repo.replay_projected(conn).states[vid] == before
    assert signouts.summary(conn, vid)["reasons"].count("receipts in progress") == 1
    assert report(desk, vid)["balance"] == {"usd": "0.00", "mxn": "100.00",
                                            "balanced": False, "implied_exchange": False}

    view = data(approve(desk, vid))
    assert view["status"] == "approved" and view["rows"][0]["status"] == "approved"
    posted = view["rows"][0]["posted"]
    assert repo.replay_projected(conn).expense_value == Decimal("93.75")
    txn = conn.execute("SELECT * FROM transactions WHERE txn_id=?", (posted["txn_ids"][0],)).fetchone()
    assert (txn["to_account"], txn["class_code"], txn["receipt_face_mxn_centavos"]) == (
        "EXP_SUPPLIES", "GENERAL", 150000)
    assert conn.execute("SELECT state FROM documents WHERE document_id=?",
                        (row["document_id"],)).fetchone()["state"] == "filed"
    assert "receipts in progress" not in signouts.summary(conn, vid)["reasons"]


def test_receipt_total_is_asked_for_only_when_signs_differ(desk):
    _, _, post = desk
    vid = voucher(desk, usd="50", mxn="500")
    expense(desk, vid, usd="", mxn="100", **coded())       # blank USD is zero
    expense(desk, vid, usd="10", mxn="20", **coded())       # same signs
    mixed = expense(desk, vid, usd="20", mxn="-100", **coded())
    failed = post(f"/{vid}/api/report/submit")
    assert failed.status_code == 422
    assert failed.get_json()["error"]["message"] == "Row 3: enter the receipt total in MXN"
    data(update(desk, vid, mixed, receipt_total="250"))
    assert post(f"/{vid}/api/report/submit").get_json()["error"]["code"] == "totals_do_not_add_up"
    add(desk, vid, kind="cash_return", usd="20", mxn="480")
    assert data(post(f"/{vid}/api/report/submit"))["status"] == "submitted"


def test_rows_without_a_receipt_need_an_explanation_then_a_signed_statement(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="20")
    row = add(desk, vid, date="2026-09-03", purpose="Taxi", usd="5", **coded())
    add(desk, vid, kind="cash_return", usd="15")
    failed = post(f"/{vid}/api/report/submit")
    assert "attach the receipt or explain why there is none" in failed.get_json()["error"]["message"]
    row = data(update(desk, vid, row, no_receipt_reason="Driver had no receipts"))["rows"][0]
    data(post(f"/{vid}/api/report/submit"))
    refused = post(f"/{vid}/api/report/approve")
    assert refused.get_json()["error"]["code"] == "statement_required"
    statement = receipt_document(desk, vid)
    row = data(update(desk, vid, row, document_id=statement, document_kind="statement"))["rows"][0]
    assert row["document_kind"] == "statement"
    posted = data(approve(desk, vid, return_usd="15"))["rows"][0]["posted"]
    txn_id = posted["txn_ids"][0]
    assert conn.execute("SELECT notes FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()[0] \
        == "No receipt: Driver had no receipts"
    assert conn.execute("SELECT doc_type FROM documents WHERE document_id=?",
                        (statement,)).fetchone()[0] == "statement"
    assert repo.receipt_numbers(conn)[statement] == f"{vid}-R1x"


def test_splits_must_add_up_and_post_as_one_receipt(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="0", mxn="1000")
    row = expense(desk, vid, mxn="350", splits=[
        {"mxn": "150", "purpose": "Food", **coded()},
        {"mxn": "150", "purpose": "Paint", **coded(expense_account="EXP_PROGRAMS")}])
    assert "must add up" in approve(desk, vid).get_json()["error"]["message"]
    data(update(desk, vid, row, splits=[
        {"mxn": "150", "purpose": "Food", **coded()},
        {"mxn": "200", "purpose": "Paint", **coded(expense_account="EXP_PROGRAMS")}]))
    posted = data(approve(desk, vid))["rows"][0]["posted"]
    rows = conn.execute("SELECT to_account, mxn_centavos, description, movement_id FROM transactions"
                        " WHERE txn_id IN (?, ?) ORDER BY allocation_line", posted["txn_ids"]).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [("EXP_SUPPLIES", 15000, "Food"),
                                                    ("EXP_PROGRAMS", 20000, "Paint")]
    assert rows[0]["movement_id"] == rows[1]["movement_id"]


def test_codes_can_be_set_on_many_rows_at_once(desk):
    vid = voucher(desk, usd="0", mxn="1000")
    plain = expense(desk, vid, mxn="100")
    split = expense(desk, vid, mxn="300", splits=[{"mxn": "100"}, {"mxn": "200"}])
    view = data(call(desk, "post", f"/{vid}/api/rows/codes", row_ids=[plain["id"], split["id"]],
                     class_code="GENERAL", expense_account="EXP_PROGRAMS"))
    rows = {row["id"]: row for row in view["rows"]}
    assert rows[plain["id"]]["expense_account"] == "EXP_PROGRAMS"
    assert {s["class_code"] for s in rows[split["id"]]["splits"]} == {"GENERAL"}
    only_class = data(call(desk, "post", f"/{vid}/api/rows/codes", row_ids=[plain["id"]],
                           class_code=None))
    kept = next(r for r in only_class["rows"] if r["id"] == plain["id"])
    assert (kept["class_code"], kept["expense_account"]) == (None, "EXP_PROGRAMS")


def test_submitted_reports_lock_for_the_recipient_but_not_the_admin(desk, tmp_path):
    conn, admin, post = desk
    vid = voucher(desk, usd="20")
    row = expense(desk, vid, usd="5")
    add(desk, vid, kind="cash_return", usd="15")
    data(post(f"/{vid}/api/report/submit"))

    recipient = admin.application.test_client()
    csrf = recipient.post("/api/user-login", json={"password": "test"}).get_json()["data"]["csrf"]
    headers = {"X-CSRF": csrf}
    locked = recipient.patch(f"/{vid}/api/rows/{row['id']}", headers=headers,
                             json={"submission_id": sid(), "rev": row["rev"], "usd": "6"})
    assert locked.status_code == 422
    assert recipient.post(f"/{vid}/api/report/approve", headers=headers,
                          json={"submission_id": sid()}).status_code == 403
    data(update(desk, vid, row, **coded()))                      # admin may still edit

    view = data(post(f"/{vid}/api/report/corrections", summary="Wrong amount",
                     row_notes={row["id"]: "The receipt says 6"}))
    assert view["status"] == "draft" and view["rows"][0]["admin_note"] == "The receipt says 6"
    fresh = view["rows"][0]
    assert recipient.patch(f"/{vid}/api/rows/{row['id']}", headers=headers,
                           json={"submission_id": sid(), "rev": fresh["rev"], "usd": "6"}).status_code == 200


def test_stale_row_edits_conflict_instead_of_overwriting(desk):
    vid = voucher(desk, usd="20")
    row = expense(desk, vid, usd="5")
    data(update(desk, vid, row, usd="6"))
    stale = update(desk, vid, row, usd="7")
    assert stale.status_code == 409
    assert report(desk, vid)["rows"][0]["usd"] == "6.00"


def test_unknown_fields_are_rejected(desk):
    vid = voucher(desk, usd="20")
    response = call(desk, "post", f"/{vid}/api/rows", color="blue")
    assert response.status_code == 422


def test_declared_return_feeds_the_settlement(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="20")
    expense(desk, vid, usd="15", **coded())
    add(desk, vid, kind="cash_return", usd="5")
    assert report(desk, vid)["balance"]["balanced"]
    assert report(desk, vid)["paperwork"]["proposed_settlement"]["return_usd"] == "5.00"
    changed = approve(desk, vid, return_usd="4")
    assert changed.get_json()["error"]["code"] == "resolution_required"
    data(approve(desk, vid, return_usd="5"))
    after = report(desk, vid)
    assert after["balance"]["balanced"] and after["paperwork"]["closeout"]

    add(desk, vid, date="2026-09-05", purpose="Late receipt", usd="1", **coded())
    assert report(desk, vid)["balance"]["usd"] == "-1.00"


def test_removed_receipts_are_discarded(desk):
    conn, _, _ = desk
    vid = voucher(desk, usd="20")
    row = expense(desk, vid, usd="5")
    data(call(desk, "delete", f"/{vid}/api/rows/{row['id']}", rev=row["rev"]))
    assert conn.execute("SELECT state FROM documents WHERE document_id=?",
                        (row["document_id"],)).fetchone()[0] == "discarded"
    assert report(desk, vid)["rows"] == []


@pytest.mark.parametrize("amounts", [{"usd": "-5"}, {"usd": "0"}, {"usd": "-1", "mxn": "-1"}])
def test_expense_rows_must_spend_something(desk, amounts):
    _, _, post = desk
    vid = voucher(desk, usd="20")
    expense(desk, vid, **amounts, **coded())
    assert post(f"/{vid}/api/report/submit").get_json()["error"]["code"] == "bad_amount"


def test_a_report_note_needs_an_explanation_at_settlement(desk):
    vid = voucher(desk, usd="20")
    expense(desk, vid, usd="20", **coded())
    data(call(desk, "patch", f"/{vid}/api/report", note="I lost one receipt"))
    assert approve(desk, vid).get_json()["error"]["code"] == "resolution_required"
    data(approve(desk, vid, resolution_note="Receipt confirmed with the vendor"))


def test_reimbursement_is_not_booked_until_approval(desk):
    conn, client, _ = desk
    vid = voucher(desk, usd="20")
    expense(desk, vid, usd="30", **coded())
    add(desk, vid, kind="reimbursement", usd="10")
    assert report(desk, vid)["balance"]["balanced"]
    assert not conn.execute("SELECT 1 FROM addenda WHERE voucher_id=?", (vid,)).fetchone()
    data(approve(desk, vid, reimburse_usd="10"))
    aid = signouts.summary(conn, vid)["closeout"]["reimbursement_addendum_id"]
    page = client.get(f"/voucher/{vid}/addendum/{aid}/print").get_data(as_text=True)
    assert '"KIND_LABEL": "Reimbursement"' in page and "Recipient signature" in page
    assert repo.replay_projected(conn).states[vid].usd == 0


def test_a_report_can_be_only_a_cash_return(desk):
    conn, client, _ = desk
    vid = voucher(desk, usd="20")
    add(desk, vid, kind="cash_return", usd="20")
    data(approve(desk, vid, return_usd="20"))
    aid = signouts.summary(conn, vid)["closeout"]["return_addendum_id"]
    page = client.get(f"/voucher/{vid}/addendum/{aid}/print").get_data(as_text=True)
    assert '"KIND_LABEL": "Cash returned"' in page and "(cash received)" in page
    assert "Recipient signature" not in page


def test_reimbursement_and_return_together_print_both_and_conserve_money(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="20", mxn="100")
    expense(desk, vid, usd="30", **coded())
    add(desk, vid, kind="cash_return", mxn="100")
    add(desk, vid, kind="reimbursement", usd="10")
    view = data(approve(desk, vid, return_mxn="100", reimburse_usd="10"))
    assert len(view["paperwork"]["addenda"]) == 2
    after = repo.replay_projected(conn)
    assert (after.states[vid].usd, after.states[vid].mxn, after.states[vid].basis) == (0, 0, 0)
    assert after.external_in == sum(s.value_usd for s in after.states.values()) + after.expense_value
    closeout = signouts.summary(conn, vid)["closeout"]
    for aid in (closeout["return_addendum_id"], closeout["reimbursement_addendum_id"]):
        data(post(f"/api/review/addendum/{aid}/complete", expected_version=1,
                  document_id=document(conn)))
    assert signouts.summary(conn, vid)["status"] == "closed"


def test_lost_cash_needs_an_explanation_a_signed_statement_and_an_acknowledgement(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="20")
    expense(desk, vid, usd="15", **coded())
    add(desk, vid, kind="cash_return", usd="3")
    # 15 spent and 3 returned leave 2 unaccounted for.
    assert post(f"/{vid}/api/report/submit").get_json()["error"]["code"] == "totals_do_not_add_up"
    lost = add(desk, vid, kind="discrepancy", usd="2")
    assert report(desk, vid)["balance"]["balanced"]
    assert "explain what happened" in post(f"/{vid}/api/report/submit").get_json()["error"]["message"]
    lost = data(update(desk, vid, lost, purpose="Dropped on the bus"))["rows"][-1]
    assert lost["receipt_reference"] == f"{vid}-R2"
    data(post(f"/{vid}/api/report/submit"))

    printed = client.get(f"/{vid}/statements?row={lost['id']}").get_data(as_text=True)
    assert "Missing cash statement" in printed and "Dropped on the bus" in printed
    assert f"{vid}-R2x" in printed
    assert approve(desk, vid, return_usd="3", acknowledged=True).get_json()["error"]["code"] == "statement_required"
    statement = receipt_document(desk, vid)
    data(update(desk, vid, lost, document_id=statement, document_kind="statement"))
    refused = approve(desk, vid, return_usd="3")
    assert refused.get_json()["error"]["code"] == "discrepancy_unacknowledged"
    assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0     # nothing was posted

    view = data(approve(desk, vid, return_usd="3", acknowledged=True))
    posted = next(row for row in view["rows"] if row["kind"] == "discrepancy")["posted"]
    txn = conn.execute("SELECT * FROM transactions WHERE txn_id=?", (posted["txn_ids"][0],)).fetchone()
    assert (txn["txn_type"], txn["from_account"], txn["to_account"], txn["usd_cents"],
            txn["class_code"], txn["description"]) == (
        "adjustment", vid, "RECONCILIATION", 200, None, "Dropped on the bus")
    assert conn.execute("SELECT role FROM transaction_documents WHERE document_id=?",
                        (statement,)).fetchone()[0] == "support"
    assert conn.execute("SELECT state, doc_type FROM documents WHERE document_id=?",
                        (statement,)).fetchone()[:] == ("filed", "statement")
    after = repo.replay_projected(conn).states[vid]
    assert (after.usd, after.mxn) == (0, 0)
    assert view["balance"]["balanced"]
    # Approved, missing cash cannot be edited.
    row = next(row for row in view["rows"] if row["kind"] == "discrepancy")
    assert update(desk, vid, row, usd="1").status_code == 422


def test_only_additional_cash_addenda_ask_for_the_recipient_signature(desk):
    _, client, post = desk
    vid = voucher(desk, usd="20")
    added = data(post("/api/addenda/prepare", voucher_id=vid, kind="positive",
                      date="2026-09-04", usd="5"))
    page = client.get(added["print_url"]).get_data(as_text=True)
    assert '"KIND_LABEL": "Additional cash authorized"' in page
    assert "Recipient signature" in page


def test_receipt_numbers_are_given_when_rows_are_saved_and_kept_at_approval(desk):
    _, _, post = desk
    vid = voucher(desk, usd="20")
    first = expense(desk, vid, usd="5", **coded())
    second = expense(desk, vid, usd="5", **coded())
    returned = add(desk, vid, kind="cash_return", usd="10")
    assert [first["receipt_reference"], second["receipt_reference"],
            returned["receipt_reference"]] == [f"{vid}-R1", f"{vid}-R2", None]
    data(call(desk, "delete", f"/{vid}/api/rows/{first['id']}", rev=first["rev"]))
    third = expense(desk, vid, usd="5", **coded())
    assert third["receipt_reference"] == f"{vid}-R3"    # a deleted number is not reused
    view = data(approve(desk, vid, return_usd="10"))
    assert [row["posted"]["receipt_id"] for row in view["rows"] if row["posted"]] == [
        f"{vid}-R2", f"{vid}-R3"]
    assert [row["receipt_reference"] for row in view["rows"]] == [f"{vid}-R2", None, f"{vid}-R3"]


def pdf(pages=1):
    import io
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    return io.BytesIO(buffer.getvalue())


def upload_to(desk, url):
    _, client, _ = desk
    csrf = client.get("/api/session").get_json()["data"]["csrf"]
    return client.post(url, data={"submission_id": sid(), "files": (pdf(), "page.pdf")},
                       headers={"X-CSRF": csrf}, content_type="multipart/form-data")


def approved_without_receipt(conn, vid, row_id):
    """Rows approved before statements were required have no document."""
    import json
    draft = json.loads(conn.execute("SELECT draft_json FROM expense_report_drafts"
                                    " WHERE voucher_id=?", (vid,)).fetchone()[0])
    row = next(row for row in draft["rows"] if row["id"] == row_id)
    conn.execute("DELETE FROM transaction_documents WHERE document_id=?", (row["document_id"],))
    conn.execute("UPDATE receipts SET document_id=NULL WHERE receipt_id=?",
                 (row["posted"]["receipt_id"],))
    row.update(document_id=None)
    row.pop("document_kind", None)
    conn.execute("UPDATE expense_report_drafts SET draft_json=? WHERE voucher_id=?",
                 (json.dumps(draft), vid))


def with_statement(desk, vid, **fields):
    return add(desk, vid, document_id=receipt_document(desk, vid), document_kind="statement",
               **fields)


def test_approved_rows_take_their_receipt_later_without_reopening_settlement(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="20")
    uploaded = data(upload_to(desk, f"/{vid}/documents"))["documents"][0]["document_id"]
    with_receipt = add(desk, vid, date="2026-09-03", purpose="Paint", usd="10",
                       document_id=uploaded, **coded())
    without = with_statement(desk, vid, date="2026-09-03", purpose="Market", usd="10",
                             no_receipt_reason="Stall had none", **coded())
    attach = lambda row, document_id: post(f"/{vid}/api/rows/{row['id']}/documentation",
                                           document_id=document_id)
    late = data(upload_to(desk, f"/{vid}/documents"))["documents"][0]["document_id"]
    assert attach(without, late).get_json()["error"]["code"] == "row_not_approved"
    data(approve(desk, vid))
    approved_without_receipt(conn, vid, without["id"])
    assert signouts.summary(conn, vid)["status"] == "closed"

    # Uploading and attaching still work once the signout is settled.
    view = data(attach(without, data(upload_to(desk, f"/{vid}/documents"))["documents"][0]["document_id"]))
    filed = next(row for row in view["rows"] if row["id"] == without["id"])
    assert filed["document"] and filed["receipt_reference"] == filed["posted"]["receipt_id"]
    assert conn.execute("SELECT document_id FROM receipts WHERE receipt_id=?",
                        (filed["posted"]["receipt_id"],)).fetchone()[0] == filed["document_id"]
    assert conn.execute(
        "SELECT COUNT(*) FROM transaction_documents WHERE document_id=? AND role='support'",
        (filed["document_id"],)).fetchone()[0] == len(filed["posted"]["txn_ids"])
    assert signouts.summary(conn, vid)["closeout"]
    assert signouts.summary(conn, vid)["status"] == "closed"

    # A filed receipt is never added to or replaced this way.
    refused = attach(with_receipt, late)
    assert refused.get_json()["error"]["code"] == "receipt_already_filed"
    assert conn.execute("SELECT state FROM documents WHERE document_id=?",
                        (uploaded,)).fetchone()[0] == "filed"


def test_the_administrator_can_file_a_late_receipt_from_the_inbox(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="20")
    row = with_statement(desk, vid, date="2026-09-03", purpose="Market", usd="20",
                         no_receipt_reason="Lost", **coded())
    data(approve(desk, vid))
    approved_without_receipt(conn, vid, row["id"])
    captured = document(conn)
    view = data(post(f"/{vid}/api/rows/{row['id']}/documentation", document_id=captured))
    assert view["rows"][0]["document_id"] == captured
    assert tuple(conn.execute("SELECT state, doc_type, voucher_id FROM documents"
                              " WHERE document_id=?", (captured,)).fetchone()) == (
        "filed", "receipt", vid)


def test_statements_print_for_rows_without_a_receipt(desk):
    _, client, _ = desk
    vid = voucher(desk, usd="20")
    expense(desk, vid, usd="5", **coded())
    second = add(desk, vid, date="2026-09-03", purpose="Taxi", vendor="Driver", usd="5",
                 no_receipt_reason="No paper")
    add(desk, vid, date="2026-09-03", purpose="Market", usd="5", no_receipt_reason="Stall")
    page = client.get(f"/{vid}/statements").get_data(as_text=True)
    assert page.count('class="statement"') == 2
    assert f"{vid}-R2x" in page and f"{vid}-R3x" in page and f"{vid}-R1x" not in page
    assert "I requested documentation for this expense and none was provided." in page
    one = client.get(f"/{vid}/statements?row={second['id']}").get_data(as_text=True)
    assert one.count('class="statement"') == 1 and "Driver" in one and "No paper" in one


def test_scanned_statements_are_suggested_on_their_row(desk):
    from cash.web.qr_capture import record_match
    conn, _, _ = desk
    vid = voucher(desk, usd="20")
    expense(desk, vid, usd="5", **coded())
    missing = add(desk, vid, date="2026-09-03", purpose="Taxi", usd="5", no_receipt_reason="None")
    match = lambda code: record_match(conn, document(conn), {"status": "detected", "ids": [code]})
    assert match(f"{vid}-R1x")["status"] == "already_filed"
    assert match(f"{vid}-R9x")["status"] == "unknown"
    scanned = match(f"{vid}-R2x")
    assert (scanned["status"], scanned["voucher_id"], scanned["review_url"]) == (
        "matched", vid, f"/{vid}")
    assert match(f"{vid}-R2x")["status"] == "duplicate"
    scan_id = conn.execute("SELECT document_id FROM document_qr WHERE detected_id=?"
                           " AND status='matched'", (f"{vid}-R2x",)).fetchone()[0]
    row = next(row for row in report(desk, vid)["rows"] if row["id"] == missing["id"])
    assert row["statement_scan"]["document_id"] == scan_id
    assert not any(doc.get("doc_type") == "Awaiting signed-paper review"
                   for doc in report(desk, vid)["paperwork"]["documents"])
    attached = data(update(desk, vid, row, document_id=scan_id, document_kind="statement"))
    assert attached["rows"][1]["statement_scan"] is None


def test_a_receipt_found_after_a_statement_takes_the_next_number(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="20")
    stated = with_statement(desk, vid, date="2026-09-03", purpose="Market", usd="10",
                            no_receipt_reason="Stall had none", **coded())
    expense(desk, vid, usd="10", **coded())
    data(approve(desk, vid))
    statement = stated["document_id"]
    found = data(upload_to(desk, f"/{vid}/documents"))["documents"][0]["document_id"]
    view = data(post(f"/{vid}/api/rows/{stated['id']}/documentation", document_id=found))
    row = next(row for row in view["rows"] if row["id"] == stated["id"])
    assert row["document_id"] == statement and row["document_kind"] == "statement"
    assert [item["receipt_id"] for item in row["later_receipts"]] == [f"{vid}-R3"]
    names = repo.receipt_numbers(conn)
    assert (names[statement], names[found]) == (f"{vid}-R1x", f"{vid}-R3")
    assert tuple(conn.execute(
        "SELECT usd_paid_cents, mxn_paid_centavos, face_usd_cents FROM receipts"
        " WHERE receipt_id=?", (f"{vid}-R3",)).fetchone()) == (0, 0, None)
    txn_ids = row["posted"]["txn_ids"]
    assert {r[0] for r in conn.execute(
        "SELECT document_id FROM transaction_documents WHERE txn_id=?", (txn_ids[0],))} == {
        statement, found}
    assert signouts.summary(conn, vid)["closeout"]
    assert signouts.summary(conn, vid)["status"] == "closed"
    assert report(desk, vid)["rows"][0]["later_receipts"][0]["document"]["document_id"] == found


def test_a_signout_has_one_purchase_log_named_log(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="15")
    log = lambda **fields: add(desk, vid, date="2026-09-03", purpose="Purchase log",
                               document_id=receipt_document(desk, vid), document_kind="log", **fields)
    row = log(usd="12", **coded())
    assert row["document_kind"] == "log"
    second = call(desk, "post", f"/{vid}/api/rows", date="2026-09-03", purpose="Again",
                  document_id=receipt_document(desk, vid), document_kind="log")
    assert second.get_json()["error"]["code"] == "log_exists"
    # More sheets before approval: the whole log is scanned again and replaces it.
    rescanned = receipt_document(desk, vid)
    row = data(update(desk, vid, row, document_id=rescanned, document_kind="log", usd="15"))["rows"][0]
    assert (row["document_id"], row["document_kind"]) == (rescanned, "log")
    data(approve(desk, vid))
    names = repo.receipt_numbers(conn)
    assert names[rescanned] == f"{vid}-log"
    assert conn.execute("SELECT doc_type FROM documents WHERE document_id=?",
                        (rescanned,)).fetchone()[0] == "purchase_log"
    # After approval a missing sheet is documentation: the next receipt number.
    sheet = data(upload_to(desk, f"/{vid}/documents"))["documents"][0]["document_id"]
    view = data(post(f"/{vid}/api/rows/{row['id']}/documentation", document_id=sheet))
    assert view["rows"][0]["later_receipts"][0]["receipt_id"] == f"{vid}-R2"
    assert repo.receipt_numbers(conn)[sheet] == f"{vid}-R2"
    assert signouts.summary(conn, vid)["closeout"]
    page = client.get(f"/{vid}/purchase-log").get_data(as_text=True)
    assert f"{vid}-log" in page and "<svg" in page and "Vendor initials" in page


def test_scanned_purchase_log_sheets_are_offered_on_the_report(desk):
    from cash.web.qr_capture import record_match
    conn, _, _ = desk
    vid = voucher(desk, usd="20")
    match = lambda: record_match(conn, document(conn), {"status": "detected", "ids": [f"{vid}-log"]})
    scanned = match()
    assert (scanned["status"], scanned["voucher_id"], scanned["review_url"]) == (
        "matched", vid, f"/{vid}")
    assert match()["status"] == "duplicate"
    scans = report(desk, vid)["log_scans"]
    assert len(scans) == 1
    add(desk, vid, date="2026-09-03", purpose="Purchase log", usd="5",
        document_id=scans[0]["document_id"], document_kind="log")
    assert report(desk, vid)["log_scans"] == []
    assert record_match(conn, document(conn), {"status": "detected", "ids": ["FY99-0001-log"]}
                        )["status"] == "unknown"
