"""Expense reports as draft rows: edited freely, posted only on approval."""

from decimal import Decimal

import pytest

from cash import repo, signouts
from test_closeouts import desk, data, document, sid, voucher  # noqa: F401  (fixture)


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


def test_rows_are_drafts_until_approval_posts_them_atomically(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="100", mxn="0")
    before = repo.replay_projected(conn).states[vid]
    row = expense(desk, vid, usd="100", mxn="-100", receipt_total="1500", **coded())
    assert row["receipt_total_currency"] == "MXN"
    assert repo.replay_projected(conn).states[vid] == before
    assert signouts.summary(conn, vid)["reasons"].count("receipts in progress") == 1
    assert report(desk, vid)["balance"] == {"usd": "0.00", "mxn": "100.00",
                                            "balanced": False, "implied_exchange": False}

    view = data(post(f"/{vid}/api/report/approve"))
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
    assert data(post(f"/{vid}/api/report/submit"))["status"] == "submitted"


def test_rows_without_a_receipt_need_an_explanation(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="20")
    row = add(desk, vid, date="2026-09-03", purpose="Taxi", usd="5", **coded())
    failed = post(f"/{vid}/api/report/submit")
    assert "attach the receipt or explain why there is none" in failed.get_json()["error"]["message"]
    data(update(desk, vid, row, no_receipt_reason="Driver had no receipts"))
    posted = data(post(f"/{vid}/api/report/approve"))["rows"][0]["posted"]
    txn_id = posted["txn_ids"][0]
    assert conn.execute("SELECT notes FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()[0] \
        == "No receipt: Driver had no receipts"
    assert not conn.execute("SELECT 1 FROM transaction_documents WHERE txn_id=?", (txn_id,)).fetchone()


def test_splits_must_add_up_and_post_as_one_receipt(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="0", mxn="1000")
    row = expense(desk, vid, mxn="350", splits=[
        {"mxn": "150", "purpose": "Food", **coded()},
        {"mxn": "150", "purpose": "Paint", **coded(expense_account="EXP_PROGRAMS")}])
    assert "must add up" in post(f"/{vid}/api/report/approve").get_json()["error"]["message"]
    data(update(desk, vid, row, splits=[
        {"mxn": "150", "purpose": "Food", **coded()},
        {"mxn": "200", "purpose": "Paint", **coded(expense_account="EXP_PROGRAMS")}]))
    posted = data(post(f"/{vid}/api/report/approve"))["rows"][0]["posted"]
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
    early = post(f"/api/signouts/{vid}/closeout/preview", date="2026-09-04", return_usd="5")
    assert early.get_json()["error"]["code"] == "receipts_in_progress"
    data(post(f"/{vid}/api/report/approve"))

    changed = post(f"/api/signouts/{vid}/closeout/preview", date="2026-09-04", return_usd="4")
    assert changed.get_json()["error"]["code"] == "resolution_required"
    preview = data(post(f"/api/signouts/{vid}/closeout/preview", date="2026-09-04", return_usd="5"))
    data(post(f"/api/signouts/{vid}/closeout", date="2026-09-04", return_usd="5",
              confirmed=True, preview_token=preview["preview_token"]))
    after = report(desk, vid)
    assert after["settlement_confirmed"] and after["balance"]["balanced"]

    add(desk, vid, date="2026-09-05", purpose="Late receipt", usd="1", **coded())
    assert not report(desk, vid)["settlement_confirmed"]


def test_reports_in_progress_at_upgrade_keep_their_receipts(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="100")
    doc = receipt_document(desk, vid)
    claim = data(post(f"/{vid}/claims", document_id=doc, date="2026-09-03", simple_entry=True,
                      usd_paid="30", notes="Old receipt"))["claim_id"]
    data(post(f"/{vid}/report/save", expected_version=0, cash_usd="70"))

    legacy = report(desk, vid)
    assert legacy["version"] == 0
    assert [(r["id"], r["kind"], r["usd"]) for r in legacy["rows"]] == [
        (claim, "expense", "30.00"), ("legacy-cash_return", "cash_return", "70.00")]
    assert legacy["rows"][0]["purpose"] == "Old receipt"

    data(update(desk, vid, legacy["rows"][0], **coded()))
    assert report(desk, vid)["version"] == 1
    refused = post(f"/{vid}/claims", document_id=receipt_document(desk, vid),
                   date="2026-09-03", simple_entry=True, usd_paid="1", notes="x")
    assert refused.get_json()["error"]["code"] == "report_moved"
    data(post(f"/{vid}/api/report/approve"))
    assert conn.execute("SELECT state FROM signout_expense_claims WHERE claim_id=?",
                        (claim,)).fetchone()[0] == "draft"      # legacy rows untouched
    assert repo.replay_projected(conn).states[vid].usd == 7000


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
