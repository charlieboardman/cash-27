"""Calculated settlement paper and composite logs share the real report lifecycle."""

import uuid

import test_closeouts as _closeout_fixtures
from test_closeouts import data, document, receipt, voucher

from cash import expense_drafts, repo, report_rows
from cash.commands import run_command
from cash.web import qr_capture

desk = _closeout_fixtures.desk

def command(conn, fn):
    return run_command(conn, str(uuid.uuid4()), "test_report_row", fn)


def signed_papers(desk, vid):
    conn, _, post = desk
    if not report_rows.review_started(expense_drafts.load(conn,vid)[0]):
        data(post(f"/{vid}/api/report/submit-calculated"))
    for stage in report_rows.settlement_stages(conn, vid):
        data(post(f"/{vid}/api/settlement/prepare/{stage['kind']}", date="2026-09-04"))
        data(
            post(
                f"/{vid}/api/settlement/file/{stage['kind']}",
                document_id=document(conn),
            )
        )


def test_prepare_does_not_move_cash_and_approval_files_the_same_number(desk):
    conn, _client, post = desk
    vid = voucher(desk, usd="20")
    receipt(desk, vid, mxn=0, usd_paid=3000, mxn_paid=0)
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    signed_papers(desk, vid)
    stage = report_rows.settlement_stages(conn, vid)[0]
    report = data(_client.get(f"/{vid}/api/report"))
    assert report["cash_rows"]["stages"][0]["filename"] == stage["paper_id"] + ".pdf"
    assert stage["usd"] == "10.00" and stage["status"] == "filed"
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == count
    result = data(post(f"/{vid}/api/report/approve-calculated", date="2026-09-04"))
    assert result["balance"]["balanced"]
    row = conn.execute(
        "SELECT * FROM addenda WHERE addendum_id=?", (stage["paper_id"],)
    ).fetchone()
    assert row["status"] == "complete" and row["document_id"] == stage["document_id"]
    assert repo.replay_projected(conn).states[vid].usd == 0


def test_staged_settlement_paper_is_hidden_from_inbox(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="20")
    receipt(desk, vid, mxn=0, usd_paid=3000)
    data(post(f"/{vid}/api/report/submit-calculated"))
    data(post(f"/{vid}/api/settlement/prepare/positive", date="2026-09-04"))
    doc = document(conn)
    data(post(f"/{vid}/api/settlement/file/positive", document_id=doc))
    inbox = client.get("/api/documents/inbox").get_json()["data"]["documents"]
    assert doc not in [item["document_id"] for item in inbox]
    assert client.get("/api/nav-counts").get_json()["data"]["inbox"] == 0
    report = data(client.get(f"/{vid}/api/report"))
    assert report["cash_rows"]["stages"][0]["document"]["document_id"] == doc


def test_zero_and_direction_changes_drop_the_paperwork_requirement(desk):
    conn, _client, post = desk
    vid = voucher(desk, usd="20")
    receipt(desk, vid, mxn=0, usd_paid=3000, mxn_paid=0)
    signed_papers(desk, vid)
    row = expense_drafts.load(conn, vid)[0]["rows"][0]
    command(
        conn,
        lambda c: expense_drafts.update_row(
            c,
            voucher_id=vid,
            row_id=row["id"],
            expected_rev=row["rev"],
            fields={"usd": 1000},
            admin=True,
        ),
    )
    assert report_rows.settlement_stages(conn, vid)[0]["kind"] == "return"
    row = expense_drafts.load(conn, vid)[0]["rows"][0]
    command(
        conn,
        lambda c: expense_drafts.update_row(
            c,
            voucher_id=vid,
            row_id=row["id"],
            expected_rev=row["rev"],
            fields={"usd": 2000},
            admin=True,
        ),
    )
    assert report_rows.settlement_stages(conn, vid) == []
    data(post(f"/{vid}/api/report/approve-calculated", date="2026-09-04"))
    assert not conn.execute(
        "SELECT 1 FROM addenda WHERE voucher_id=?", (vid,)
    ).fetchone()


def test_reserved_paper_number_is_not_used_for_another_advance_and_qr_matches(desk):
    conn, _, post = desk
    vid = voucher(desk, usd="20")
    receipt(desk, vid, mxn=0, usd_paid=3000, mxn_paid=0)
    data(post(f"/{vid}/api/report/submit-calculated"))
    data(post(f"/{vid}/api/settlement/prepare/positive", date="2026-09-04"))
    paper = report_rows.settlement_stages(conn, vid)[0]["paper_id"]
    doc = document(conn)
    match = qr_capture.record_match(conn, doc, {"status": "detected", "ids": [paper]})
    assert match["status"] == "matched" and match["review_url"] == f"/{vid}"
    advance = data(
        post(
            "/api/addenda/prepare",
            voucher_id=vid,
            kind="positive",
            date="2026-09-04",
            reason="More cash",
            usd="1",
        )
    )
    assert advance["addendum_id"] != paper
    assert report_rows.settlement_stages(conn, vid)[0]["status"] == "outdated"


def test_log_purchases_total_once_prefill_print_and_match_signed_scan(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="20")
    view = data(post(f"/{vid}/api/purchase-log"))
    items = [
        {
            "date": "2026-09-03",
            "vendor": "Taxi",
            "purpose": "Trip",
            "usd": "5",
            "mxn": "0",
        },
        {
            "date": "2026-09-03",
            "vendor": "Store",
            "purpose": "Supplies",
            "usd": "15",
            "mxn": "0",
        },
    ]
    csrf = client.get("/api/session").get_json()["data"]["csrf"]
    view = data(
        client.patch(
            f"/{vid}/api/purchase-log",
            json={
                "items": items,
                "rev": view["cash_rows"]["log"]["rev"],
                "class_code": "GENERAL",
                "expense_account": "EXP_SUPPLIES",
            },
            headers={"X-CSRF": csrf},
        )
    )
    assert view["balance"]["balanced"] and len(view["rows"]) == 1
    assert view["rows"][0]["usd"] == "20.00"
    printed = client.get(f"/{vid}/purchase-log").get_data(as_text=True)
    assert "Taxi" in printed and "Supplies" in printed and "20.00" in printed
    doc = document(conn)
    assert (
        qr_capture.record_match(
            conn, doc, {"status": "detected", "ids": [f"{vid}-log"]}
        )["status"]
        == "matched"
    )
    data(post(f"/{vid}/api/purchase-log", attach_matched=True))
    data(post(f"/{vid}/api/report/submit-calculated"))
    data(post(f"/{vid}/api/report/approve-calculated", date="2026-09-04"))
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM receipts WHERE voucher_id=?", (vid,)
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "SELECT doc_type FROM documents WHERE document_id=?", (doc,)
        ).fetchone()[0]
        == "purchase_log"
    )


def test_signee_can_edit_expenses_but_cannot_generate_cash_rows(desk):
    _conn, client, _post = desk
    vid = voucher(desk)
    client.get("/logout")
    token = data(client.post("/api/user-login", json={"password": "test"}))["csrf"]
    response = client.post(
        f"/{vid}/api/rows",
        json={"submission_id": str(uuid.uuid4()), "kind": "cash_return", "usd": "20"},
        headers={"X-CSRF": token},
    )
    assert (
        response.status_code == 422
        and response.get_json()["error"]["code"] == "forbidden"
    )
    response = client.get(f"/{vid}/api/report")
    assert response.status_code == 200 and "cash_rows" in response.get_json()["data"]
    response = client.post(
        f"/{vid}/api/settlement/prepare/return", json={}, headers={"X-CSRF": token}
    )
    assert response.status_code in (302, 401, 403)


def test_unused_signout_can_close_with_only_its_signed_cash_return(desk):
    conn, _client, post = desk
    vid = voucher(desk, usd="20")
    signed_papers(desk, vid)
    result = data(post(f"/{vid}/api/report/approve-calculated", date="2026-09-04"))
    assert result["balance"]["balanced"]
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM receipts WHERE voucher_id=?", (vid,)
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT status FROM addenda WHERE voucher_id=?", (vid,)
        ).fetchone()[0]
        == "complete"
    )


def test_new_approval_cannot_override_the_calculated_amount(desk):
    conn, _client, post = desk
    vid = voucher(desk, usd="20")
    response = post(f"/{vid}/api/report/approve-calculated", reimburse_usd="100")
    assert response.status_code in (400, 422)
    assert not conn.execute("SELECT 1 FROM signout_closeouts").fetchone()
