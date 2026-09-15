"""Reported facts stay separate from approval, cash settlement, and evidence."""
from decimal import Decimal

import pytest
from test_closeouts import desk, data, voucher, document, sid
from cash import expense_reports, packages, repo, signouts
from cash.commands import run_command


def claim(desk, vid, usd="100", mxn="-100", notes="School supplies"):
    conn, _, post = desk
    doc = document(conn)
    conn.execute("UPDATE documents SET voucher_id=?, state='signee_draft' WHERE document_id=?", (vid,doc))
    return data(post(f"/{vid}/claims", document_id=doc, date="2026-09-03", simple_entry=True,
                     usd_paid=str(max(Decimal(usd),0)), usd_change=str(max(-Decimal(usd),0)),
                     mxn_paid=str(max(Decimal(mxn),0)), mxn_change=str(max(-Decimal(mxn),0)), notes=notes))["claim_id"]


def submit(desk, vid, **fields):
    conn, client, post = desk
    previous = expense_reports.get(conn, vid)
    report = data(post(f"/{vid}/report/save", expected_version=previous["version"] if previous else 0,
                       cash_usd="0", cash_mxn="100", **fields))
    versions = {c["claim_id"]:c["content_version"] for c in client.get(f"/{vid}/state").get_json()["data"]["claims"]}
    return post(f"/{vid}/report/submit", expected_version=report["version"], claim_versions=versions)


def approve(desk, cid, usd=10000, mxn=-10000, face=150000):
    conn, _, post = desk
    c = conn.execute("SELECT * FROM signout_expense_claims WHERE claim_id=?", (cid,)).fetchone()
    saved = data(post(f"/api/review/claims/{cid}/allocations", expected_content_version=c["content_version"], allocations=[{
        "expense_account":"EXP_SUPPLIES", "class_code":"GENERAL", "usd":str(Decimal(usd)/100), "mxn":str(Decimal(mxn)/100), "face_mxn":str(Decimal(face)/100), "notes":c["notes"]}]))
    data(post(f"/api/review/claims/{cid}/verify", expected_content_version=saved["content_version"]))
    return data(post(f"/api/review/packages/{c['package_id']}/accept"))


def test_signed_change_report_to_admin_settlement(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="100")
    cid = claim(desk,vid)
    data(submit(desk,vid))
    before = repo.replay_projected(conn)
    assert before.states[vid].usd == 10000
    assert client.get(f"/api/signouts/{vid}").get_json()["data"]["report"]["cash_mxn"] == 10000
    approve(desk,cid)
    assert repo.replay_projected(conn).expense_value == Decimal("93.75")
    reviewed = data(post(f"/api/signouts/{vid}/closeout/preview",date="2026-09-04",return_mxn="100"))
    assert reviewed["exchange_usd_cents"] == 0
    result = data(post(f"/api/signouts/{vid}/closeout",date="2026-09-04",return_mxn="100",confirmed=True,preview_token=reviewed["preview_token"]))
    assert result["summary"]["reasons"] == ["addendum needed"]
    aid = result["summary"]["closeout"]["return_addendum_id"]
    data(post(f"/api/review/addendum/{aid}/complete",expected_version=1,document_id=document(conn)))
    assert signouts.summary(conn,vid)["status"] == "closed"


def test_incomplete_drafts_notes_and_cash_count(desk):
    conn, client, post = desk
    vid = voucher(desk)
    cid = claim(desk,vid,usd="0",mxn="0",notes="")
    assert conn.execute("SELECT simple_entry FROM signout_expense_claims WHERE claim_id=?", (cid,)).fetchone()[0] == 1
    response = submit(desk,vid)
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "notes_required"
    assert not conn.execute("SELECT 1 FROM signout_reconciliation_packages").fetchone()
    result = post(f"/{vid}/packages",claim_ids=[cid])
    assert result.status_code == 422  # Old endpoint cannot bypass mandatory notes.


def test_discrepancy_is_saved_and_blocks_unexplained_confirmation(desk):
    conn, _, post = desk
    vid = voucher(desk,usd="100")
    cid = claim(desk,vid)
    data(submit(desk,vid,discrepancy="Vendor may have kept some change"))
    approve(desk,cid)
    url=f"/api/signouts/{vid}/closeout/preview"
    assert post(url,date="2026-09-04",return_mxn="100").get_json()["error"]["code"] == "resolution_required"
    reviewed=data(post(url,date="2026-09-04",return_mxn="100",resolution_note="Counted with recipient and verified receipt"))
    assert reviewed["resolution_note"]
    assert expense_reports.get(conn,vid)["discrepancy"] == "Vendor may have kept some change"


def test_personal_spending_reimbursement_is_not_booked_until_confirmation(desk):
    conn, _, post = desk
    vid = voucher(desk,usd="20")
    cid = claim(desk,vid,usd="30",mxn="0")
    report=data(post(f"/{vid}/report/save",expected_version=0,cash_usd="0",cash_mxn="0",personal_usd="10"))
    data(post(f"/{vid}/report/submit",expected_version=report["version"],claim_versions={cid:1}))
    approve(desk,cid,usd=3000,mxn=0,face=0)
    assert repo.replay_projected(conn).states[vid].usd == -1000
    reviewed=data(post(f"/api/signouts/{vid}/closeout/preview",date="2026-09-04",reimburse_usd="10"))
    assert reviewed["requires_reimbursement_addendum"]
    assert not conn.execute("SELECT 1 FROM addenda").fetchone()
    result=data(post(f"/api/signouts/{vid}/closeout",date="2026-09-04",reimburse_usd="10",confirmed=True,preview_token=reviewed["preview_token"]))
    assert result["summary"]["usd_cents"] == 0
    assert result["summary"]["closeout"]["reimbursement_addendum_id"]
    assert len(result["print_urls"]) == 1


def test_report_versions_and_locked_receipts(desk):
    conn, client, post = desk
    vid=voucher(desk,usd="100"); cid=claim(desk,vid)
    report=data(submit(desk,vid))
    assert post(f"/{vid}/report/save",expected_version=report["version"],cash_usd="0",cash_mxn="5").status_code == 422
    assert post(f"/{vid}/report/reopen",expected_version=report["version"]-1).status_code == 409
    data(post(f"/{vid}/report/reopen",expected_version=report["version"]))
    assert conn.execute("SELECT state FROM signout_expense_claims WHERE claim_id=?",(cid,)).fetchone()[0] == "draft"
    saved=expense_reports.get(conn,vid)
    result=post(f"/{vid}/report/submit",expected_version=saved["version"],claim_versions={cid:0})
    assert result.status_code == 409
    assert post(f"/api/signouts/{vid}/closeout/preview",date="2026-09-04",return_usd="100").status_code == 422


def test_zero_receipt_return_can_be_submitted_and_reviewed(desk):
    conn, _, post=desk
    vid=voucher(desk,usd="20")
    report=data(post(f"/{vid}/report/save",expected_version=0,cash_usd="20",cash_mxn="0"))
    data(post(f"/{vid}/report/submit",expected_version=report["version"],claim_versions={}))
    assert not conn.execute("SELECT 1 FROM signout_reconciliation_packages").fetchone()
    result=data(post(f"/api/signouts/{vid}/closeout/preview",date="2026-09-04",return_usd="20"))
    assert result["requires_return_addendum"]


def test_returned_report_keeps_verified_receipts_and_includes_new_evidence(desk):
    conn,client,post=desk
    vid=voucher(desk,usd="100")
    first=claim(desk,vid,usd="10",mxn="0")
    second=claim(desk,vid,usd="20",mxn="0")
    data(submit(desk,vid))
    pid=conn.execute("SELECT package_id FROM signout_expense_claims WHERE claim_id=?",(first,)).fetchone()[0]
    data(post(f"/api/review/claims/{first}/verify",expected_content_version=1))
    data(post(f"/api/review/packages/{pid}/return",admin_summary="Explain second purchase",item_notes={second:"More detail please"}))
    third=claim(desk,vid,usd="5",mxn="0",notes="Forgot these pencils")
    data(submit(desk,vid,discrepancy="Exchange details to confirm"))
    rows=conn.execute("SELECT claim_id,state,package_id,verified_version FROM signout_expense_claims").fetchall()
    assert {r["package_id"] for r in rows} == {pid}
    assert next(r for r in rows if r["claim_id"]==first)["state"] == "verified"
    assert next(r for r in rows if r["claim_id"]==third)["state"] == "submitted"
    assert client.get(f"/{vid}/state").get_json()["data"]["report"]["discrepancy"] == "Exchange details to confirm"


def test_simultaneous_reimbursement_and_return_prints_both_and_conserves_money(desk):
    conn,_,post=desk
    vid=voucher(desk,usd="20",mxn="100")
    cid=claim(desk,vid,usd="30",mxn="0")
    data(submit(desk,vid,personal_usd="10"))
    approve(desk,cid,usd=3000,mxn=0,face=0)
    body={"date":"2026-09-04","return_mxn":"100","reimburse_usd":"10"}
    reviewed=data(post(f"/api/signouts/{vid}/closeout/preview",**body))
    result=data(post(f"/api/signouts/{vid}/closeout",**body,confirmed=True,preview_token=reviewed["preview_token"]))
    assert len(result["print_urls"])==2
    after=repo.replay_projected(conn)
    assert (after.states[vid].usd,after.states[vid].mxn,after.states[vid].basis)==(0,0,0)
    assert after.external_in == sum(s.value_usd for s in after.states.values()) + after.expense_value
    for aid in (result["summary"]["closeout"]["return_addendum_id"],result["summary"]["closeout"]["reimbursement_addendum_id"]):
        data(post(f"/api/review/addendum/{aid}/complete",expected_version=1,document_id=document(conn)))
    assert signouts.summary(conn,vid)["status"]=="closed"
