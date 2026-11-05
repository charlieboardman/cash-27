"""Calculated settlement paper and composite logs share the real report lifecycle."""

import uuid

import pytest

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


@pytest.mark.parametrize("kind,spent", [("return", 500), ("positive", 3000)])
def test_review_can_attach_prepared_settlement_without_posting_cash(desk, kind, spent):
    conn, client, post = desk
    vid = voucher(desk, usd="20")
    receipt(desk, vid, mxn=0, usd_paid=spent)
    data(post(f"/{vid}/api/report/submit-calculated"))
    data(post(f"/{vid}/api/settlement/prepare/{kind}", date="2026-09-04"))
    paper = report_rows.settlement_stages(conn, vid)[0]["paper_id"]
    doc = document(conn)
    match = qr_capture.record_match(conn, doc, {"status": "detected", "ids": [paper]})
    assert match["status"] == "matched"
    before = [tuple(row) for row in conn.execute("SELECT * FROM transactions")]

    work = data(client.get("/api/review/worklist"))
    assert [item["kind"] for item in work["items"]] == ["expense_report"]
    assert len(work["attachment_targets"]) == 1
    target = work["attachment_targets"][0]
    assert (target["kind"], target["id"], target["voucher_id"], target["settlement_kind"]) == (
        "settlement", paper, vid, kind)
    assert target["label"] == "Ana" and target["date"] == "2026-09-04"
    assert target["usd"] == ("15.00" if kind == "return" else "10.00")
    assert data(client.get("/api/nav-counts")) == {"review": 1, "inbox": 1}

    url = f"/{vid}/api/settlement/file/{kind}"
    submission = str(uuid.uuid4())
    result = data(post(url, submission_id=submission, document_id=doc, paper_id=paper))
    assert data(post(url, submission_id=submission, document_id=doc, paper_id=paper)) == result
    stage = result["cash_rows"]["stages"][0]
    assert stage["status"] == "filed" and stage["document_id"] == doc
    assert result["status"] == "submitted"
    assert data(client.get("/api/review/worklist"))["attachment_targets"] == []
    assert data(client.get("/api/nav-counts")) == {"review": 1, "inbox": 0}
    assert data(client.get("/api/documents/inbox"))["documents"] == []
    assert before == [tuple(row) for row in conn.execute("SELECT * FROM transactions")]
    assert conn.execute("SELECT COUNT(*) FROM addenda").fetchone()[0] == 0

    data(post(f"/{vid}/api/report/approve-calculated", date="2026-09-04"))
    filed = conn.execute("SELECT status, document_id FROM addenda WHERE addendum_id=?",
                         (paper,)).fetchone()
    assert tuple(filed) == ("complete", doc)


def test_review_excludes_outdated_settlement_and_rejects_replaced_paper(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="20")
    receipt(desk, vid, mxn=0, usd_paid=500)
    data(post(f"/{vid}/api/report/submit-calculated"))
    data(post(f"/{vid}/api/settlement/prepare/return", date="2026-09-04"))
    paper = report_rows.settlement_stages(conn, vid)[0]["paper_id"]
    doc = document(conn)
    qr_capture.record_match(conn, doc, {"status": "detected", "ids": [paper]})
    row = expense_drafts.load(conn, vid)[0]["rows"][0]
    command(conn, lambda c: expense_drafts.update_row(
        c, voucher_id=vid, row_id=row["id"], expected_rev=row["rev"],
        fields={"usd": 800}, admin=True))
    assert data(client.get("/api/review/worklist"))["attachment_targets"] == []
    url = f"/{vid}/api/settlement/file/return"
    rejected = post(url, document_id=doc, paper_id=paper)
    assert rejected.status_code == 422
    data(post(f"/{vid}/api/settlement/prepare/return", date="2026-09-04"))
    replacement = report_rows.settlement_stages(conn, vid)[0]
    assert replacement["paper_id"] != paper
    rejected = post(url, document_id=doc, paper_id=paper)
    assert rejected.status_code == 409 and rejected.json["error"]["code"] == "conflict"
    assert report_rows.settlement_stages(conn, vid)[0]["document_id"] is None
    assert [item["document_id"] for item in data(client.get("/api/documents/inbox"))["documents"]] == [doc]


def test_review_cannot_replace_an_addendum_filed_since_it_was_selected(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="20")
    receipt(desk, vid, mxn=0, usd_paid=500)
    data(post(f"/{vid}/api/report/submit-calculated"))
    data(post(f"/{vid}/api/settlement/prepare/return", date="2026-09-04"))
    paper = report_rows.settlement_stages(conn, vid)[0]["paper_id"]
    first, second = document(conn), document(conn)
    url = f"/{vid}/api/settlement/file/return"
    data(post(url, document_id=first, paper_id=paper))
    assert post(url, document_id=second, paper_id=paper).status_code == 409
    assert report_rows.settlement_stages(conn, vid)[0]["document_id"] == first
    assert [item["document_id"] for item in data(client.get("/api/documents/inbox"))["documents"]] == [second]


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


def test_settlement_reprint_keeps_preparer_when_another_admin_approves(desk):
    from test_voucher_approvers import tokens
    conn, client, post = desk
    vid = voucher(desk, usd='20')
    receipt(desk, vid, mxn=0, usd_paid=500)
    data(post(f'/{vid}/api/report/submit-calculated'))
    data(post(f'/{vid}/api/settlement/prepare/return', date='2026-09-04'))
    paper = report_rows.settlement_stages(conn, vid)[0]['paper_id']
    data(post(f'/{vid}/api/settlement/file/return', document_id=document(conn)))
    assert tokens(client, f'/{vid}/settlement/print/return')['APPROVER'] == 'Test Admin'
    current = data(client.get('/api/settings/admins'))['current_id']
    data(post(f'/api/settings/admins/{current}', name='Changed Name'))
    data(post(f'/{vid}/api/report/approve-calculated', date='2026-09-04'))
    assert tokens(client, f'/voucher/{vid}/addendum/{paper}/print')['APPROVER'] == 'Test Admin'
