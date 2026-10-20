"""Exact receipts and signed settlement rounding."""

import pytest
from cash import expense_drafts, report_rows
from test_report_rows import signed_papers

import io
import uuid

from pypdf import PdfWriter

from cash import repo
from test_browser_workflows import expect, saved
from test_closeouts import approve, data, desk, document, receipt, voucher  # noqa: F401


def calculated_approve(desk, vid):
    conn, _, post = desk
    signed_papers(desk, vid)
    result = data(post(f"/{vid}/api/report/approve-calculated", date="2026-10-20"))
    draft, _ = expense_drafts.load(conn, vid)
    from cash import closeouts
    assert conn.execute("SELECT facts_sha256 FROM signout_closeouts WHERE voucher_id=? ORDER BY rowid DESC", (vid,)).fetchone()[0] == closeouts.facts(conn, vid)[1]
    for row in draft["rows"]:
        if not row.get("rounding"):
            continue
        doc = draft["settlement_papers"][row["rounding"]]["document_id"]
        assert row["document_id"] == doc
        assert conn.execute("SELECT document_id FROM receipts WHERE receipt_id=?", (row["posted"]["receipt_id"],)).fetchone()[0] == doc
        assert conn.execute("SELECT doc_type FROM documents WHERE document_id=?", (doc,)).fetchone()[0] == "addendum"
        for txn in row["posted"]["txn_ids"]:
            assert conn.execute("SELECT 1 FROM transaction_documents WHERE txn_id=? AND document_id=? AND role='support'", (txn, doc)).fetchone()
    return result


def report(client, vid):
    return data(client.get(f"/{vid}/api/report"))


def patch(client, url, body):
    csrf = client.get("/api/session").get_json()["data"]["csrf"]
    return client.patch(url, json={"submission_id": str(uuid.uuid4()), **body},
                        headers={"X-CSRF": csrf})


def paid(desk, vid, **body):
    _, _, post = desk
    return post(f"/{vid}/api/report/cash-difference", **body)


def signout_with_receipts(page, post, *, cash, receipts):
    voucher = post("/api/vouchers/prepare", type="signout", date="2026-09-02",
                   recipient="Ana", purpose="Supplies", usd="0", mxn=cash)
    vid = voucher["voucher_id"]
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    signed = page.request.post("/api/documents/capture-merge", multipart={
        "files": {"name": "voucher.pdf", "mimeType": "application/pdf",
                  "buffer": buffer.getvalue()}}).json()["data"]
    post(f"/api/review/signout/{vid}/attach-voucher", expected_version=1,
         document_id=signed["document_id"])
    for mxn in receipts:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        buffer = io.BytesIO()
        writer.write(buffer)
        document_id = page.request.post(
            f"/{vid}/documents",
            multipart={"files": {"name": "receipt.pdf", "mimeType": "application/pdf",
                                 "buffer": buffer.getvalue()}},
        ).json()["data"]["documents"][0]["document_id"]
        post(f"/{vid}/api/rows", date="2026-09-03", usd="0", mxn=mxn,
             purpose="Supplies", document_id=document_id,
             class_code="7.5", expense_account="1000")
    post(f"/{vid}/api/report/submit-calculated")
    return vid


def test_reimbursement_rounding_preserves_receipts(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="0", mxn="100")
    receipt(desk, vid, mxn=5000)
    receipt(desk, vid, mxn=5050)
    data(post(f"/{vid}/api/report/submit-calculated"))

    difference = report(client, vid)["cash_difference"]["mxn"]
    assert difference == {"currency": "MXN", "direction": "reimbursement", "exact": "0.50",
                          "suggested": "1.00", "can_missing_cash": False}

    view = data(paid(desk, vid, actual_mxn="1.00", method="rounding"))
    assert view["cash_difference"] is None
    assert [row["mxn"] for row in view["rows"]] == ["50.00", "50.50", "0.50"]
    assert view["cash_rows"]["stages"][0]["mxn"] == "1.00"

    result = calculated_approve(desk, vid)
    assert result["balance"]["balanced"]
    after = repo.replay_projected(conn).states[vid]
    assert (after.usd, after.mxn) == (0, 0)


def test_return_rounding_uses_the_counted_cash(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="0", mxn="200")
    receipt(desk, vid, mxn=5000)
    receipt(desk, vid, mxn=5050)
    data(post(f"/{vid}/api/report/submit-calculated"))

    difference = report(client, vid)["cash_difference"]["mxn"]
    assert difference["direction"] == "return"
    assert difference["exact"] == "99.50"
    assert difference["suggested"] == "99.00"

    view = data(paid(desk, vid, actual_mxn="99.00", method="rounding"))
    assert view["cash_difference"] is None
    assert [row["mxn"] for row in view["rows"]] == ["50.00", "50.50", "0.50"]
    stage = next(stage for stage in view["cash_rows"]["stages"] if stage["kind"] == "return")
    assert stage["mxn"] == "99.00"

    result = calculated_approve(desk, vid)
    assert result["balance"]["balanced"]
    assert repo.replay_projected(conn).states[vid].mxn == 0


def test_missing_cash_keeps_receipts_exact_and_posts_the_shortfall(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="0", mxn="200")
    receipt(desk, vid, mxn=5000)
    receipt(desk, vid, mxn=5050)
    data(post(f"/{vid}/api/report/submit-calculated"))

    view = data(paid(desk, vid, actual_mxn="98.00", method="missing_cash",
                     reason="Counted short"))
    assert view["cash_difference"] is None
    expenses = [row["mxn"] for row in view["rows"] if row["kind"] == "expense"]
    assert expenses == ["50.00", "50.50"]
    lost = next(row for row in view["rows"] if row["kind"] == "discrepancy")
    assert lost["mxn"] == "1.50" and lost["purpose"] == "Counted short"
    stage = next(stage for stage in view["cash_rows"]["stages"] if stage["kind"] == "return")
    assert stage["mxn"] == "98.00"

    statement = document(conn)
    attached = patch(client, f"/{vid}/api/rows/{lost['id']}",
                     {"rev": lost["rev"], "document_id": statement,
                      "document_kind": "statement"})
    assert attached.status_code == 200, attached.get_json()

    result = data(approve(desk, vid, acknowledged=True))
    assert result["balance"]["balanced"]
    txn = conn.execute(
        "SELECT txn_type, from_account, to_account, mxn_centavos FROM transactions"
        " WHERE voucher_id=? AND txn_type='adjustment'", (vid,)).fetchone()
    assert (txn["txn_type"], txn["from_account"], txn["to_account"],
            txn["mxn_centavos"]) == ("adjustment", vid, "RECONCILIATION", 150)
    assert repo.replay_projected(conn).states[vid].mxn == 0


def test_missing_cash_is_refused_when_the_org_owes_money(desk):
    _conn, _client, post = desk
    vid = voucher(desk, usd="0", mxn="100")
    receipt(desk, vid, mxn=5000)
    receipt(desk, vid, mxn=5050)
    data(post(f"/{vid}/api/report/submit-calculated"))

    refused = paid(desk, vid, actual_mxn="1.00", method="missing_cash",
                   reason="Counted short")
    assert refused.status_code == 422
    assert refused.get_json()["error"]["code"] == "missing_cash_not_applicable"


def test_underpaid_reimbursement_is_refused_even_with_reason(desk):
    _, _, post = desk
    vid = voucher(desk, usd="0", mxn="100")
    receipt(desk, vid, mxn=10050)
    data(post(f"/{vid}/api/report/submit-calculated"))
    refused = paid(desk, vid, actual_mxn="0", method="rounding", reason="Agreed")
    assert refused.status_code == 422
    assert refused.get_json()["error"]["code"] == "recipient_unfavorable"


def test_cash_difference_requires_whole_pesos(desk):
    _conn, _client, post = desk
    vid = voucher(desk, usd="0", mxn="100")
    receipt(desk, vid, mxn=5000)
    receipt(desk, vid, mxn=5050)
    data(post(f"/{vid}/api/report/submit-calculated"))

    refused = paid(desk, vid, actual_mxn="0.75", method="rounding")
    assert refused.status_code == 422
    assert refused.get_json()["error"]["code"] == "bad_amount"


def test_administrator_resolves_the_cash_difference_from_the_page(web):
    page, post, _ = web
    voucher = post("/api/vouchers/prepare", type="signout", date="2026-09-02",
                   recipient="Ana", purpose="Supplies", usd="0", mxn="200")
    vid = voucher["voucher_id"]
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    signed = page.request.post("/api/documents/capture-merge", multipart={
        "files": {"name": "voucher.pdf", "mimeType": "application/pdf",
                  "buffer": buffer.getvalue()}}).json()["data"]
    post(f"/api/review/signout/{vid}/attach-voucher", expected_version=1,
         document_id=signed["document_id"])

    def attach(mxn):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        buffer = io.BytesIO()
        writer.write(buffer)
        document_id = page.request.post(
            f"/{vid}/documents",
            multipart={"files": {"name": "receipt.pdf", "mimeType": "application/pdf",
                                 "buffer": buffer.getvalue()}},
        ).json()["data"]["documents"][0]["document_id"]
        post(f"/{vid}/api/rows", date="2026-09-03", usd="0", mxn=mxn,
             purpose="Supplies", document_id=document_id,
             class_code="7.5", expense_account="1000")

    attach("50.00")
    attach("50.50")
    post(f"/{vid}/api/report/submit-calculated")
    page.goto(f"/{vid}")
    saved(page)

    panel = page.locator("#er-cash-difference")
    expect(panel).to_be_visible()
    expect(page.locator("#er-cash-difference-heading")).to_have_text("Cash settlement")
    expect(page.locator("#er-cash-difference-direction")).to_have_text("Cash to return (MXN)")
    expect(page.locator("#er-cash-difference-actual-label")).to_have_text("Cash returned")
    expect(page.locator("#er-cash-difference-delta-label")).to_have_text("Shortfall")
    expect(page.locator("#er-cash-rounding")).to_have_text("Record cash rounding")
    expect(page.locator("#er-missing-cash")).to_have_text("Declare missing cash")
    expect(page.locator("#er-cash-difference-exact")).to_have_value("99.50")
    expect(page.locator("#er-cash-difference-actual")).to_have_value("99.00")
    page.locator("#er-cash-difference-actual").fill("")
    expect(page.locator("#er-cash-rounding")).to_be_disabled()
    page.locator("#er-cash-difference-actual").fill("100.00")
    expect(page.locator("#er-cash-rounding")).to_be_disabled()
    page.locator("#er-cash-difference-actual").fill("99.25")
    expect(page.locator("#er-cash-rounding")).to_be_disabled()
    page.locator("#er-cash-difference-actual").fill("99.00")
    expect(page.locator("#er-cash-difference-delta")).to_have_text("MXN 0.50")
    page.get_by_role("button", name="Español", exact=True).click()
    expect(page.locator("#er-cash-difference-heading")).to_have_text("Cierre de efectivo")
    expect(page.locator("#er-cash-difference-direction")).to_have_text("Efectivo por devolver (MXN)")
    expect(page.locator("#er-cash-difference-actual-label")).to_have_text("Efectivo devuelto")
    expect(page.locator("#er-cash-difference-delta-label")).to_have_text("Faltante")
    expect(page.locator("#er-cash-rounding")).to_have_text("Registrar redondeo de efectivo")
    expect(page.locator("#er-missing-cash")).to_have_text("Declarar efectivo faltante")
    assert page.evaluate("[...cashI18n.missing]") == []
    page.locator("#er-cash-rounding").click()
    expect(panel).to_be_hidden()
    saved(page, "es")

    amounts = [row["mxn"] for row in
               page.request.get(f"/{vid}/api/report").json()["data"]["rows"]]
    assert amounts == ["50.00", "50.50", "0.50"]
    page.reload()
    expect(panel).to_be_hidden()


def test_reimbursement_copy_names_the_direction(web):
    page, post, _ = web
    vid = signout_with_receipts(page, post, cash="100",
                                receipts=("50.00", "50.50"))
    page.goto(f"/{vid}")
    saved(page)

    panel = page.locator("#er-cash-difference")
    expect(panel).to_be_visible()
    expect(page.locator("#er-cash-difference-heading")).to_have_text("Cash settlement")
    expect(page.locator("#er-cash-difference-direction")).to_have_text("Reimbursement due (MXN)")
    expect(page.locator("#er-cash-difference-actual-label")).to_have_text("Cash paid")
    expect(page.locator("#er-cash-difference-delta-label")).to_have_text("Rounding")
    expect(page.locator("#er-cash-rounding")).to_have_text("Record cash rounding")
    expect(page.locator("#er-missing-cash")).to_be_hidden()
    assert page.evaluate("[...cashI18n.missing]") == []



@pytest.mark.parametrize("actual", ["", "100", "99.25"])
def test_invalid_return(desk, actual):
    _, _, post = desk
    vid = voucher(desk, usd="0", mxn="200")
    receipt(desk, vid, mxn=10050)
    data(post(f"/{vid}/api/report/submit-calculated"))
    assert paid(desk, vid, actual_mxn=actual, method="rounding").status_code == 422


@pytest.mark.parametrize("reason", ["x" * 121, "one\ntwo"])
def test_missing_reason_paper_limit(desk, reason):
    _, _, post = desk
    vid = voucher(desk, usd="0", mxn="200")
    receipt(desk, vid, mxn=10050)
    data(post(f"/{vid}/api/report/submit-calculated"))
    assert paid(desk, vid, actual_mxn="99", method="missing_cash", reason=reason).status_code == 422


@pytest.mark.parametrize("step", ["reopen", "corrections"])
def test_corrections_clear_rounding(desk, step):
    conn, _, post = desk
    vid = voucher(desk, usd="0", mxn="200")
    receipt(desk, vid, mxn=10050)
    data(post(f"/{vid}/api/report/submit-calculated"))
    data(paid(desk, vid, actual_mxn="99", method="rounding"))
    data(post(f"/{vid}/api/report/{step}", **({"summary": "Fix"} if step == "corrections" else {})))
    draft, _ = expense_drafts.load(conn, vid)
    assert not any(r.get("rounding") for r in draft["rows"])
    assert "cash_resolution" not in draft


def test_repeat_and_zero_difference(desk):
    _, client, post = desk
    vid = voucher(desk, usd="0", mxn="200")
    receipt(desk, vid, mxn=10000)
    data(post(f"/{vid}/api/report/submit-calculated"))
    first = data(paid(desk, vid, actual_mxn="99", method="rounding"))["rows"][-1]
    second = data(paid(desk, vid, actual_mxn="98", method="rounding"))["rows"][-1]
    assert first["id"] == second["id"] and second["rev"] == first["rev"] + 1
    assert second["mxn"] == "2.00"
    data(paid(desk, vid, actual_mxn="100", method="rounding"))
    assert len(report(client, vid)["rows"]) == 1


@pytest.mark.parametrize("currency", ["usd", "mxn"])
def test_currency_approval_requires_paper_and_prints_breakdown(desk, currency):
    _, client, post = desk
    vid = voucher(desk, usd="200" if currency == "usd" else "0", mxn="200" if currency == "mxn" else "0")
    receipt(desk, vid, mxn=10050 if currency == "mxn" else 0, usd_paid=10050 if currency == "usd" else 0)
    data(post(f"/{vid}/api/report/submit-calculated"))
    data(paid(desk, vid, actual="99", currency=currency, method="rounding"))
    assert post(f"/{vid}/api/report/approve-calculated", date="2026-10-20").status_code == 422
    assert approve(desk, vid).status_code == 422
    signed_papers(desk, vid)
    html = client.get(f"/{vid}/settlement/print/return").text
    assert "rounding" in html and "99.50" in html and "Recipient signature:" in html
    calculated_approve(desk, vid)


def test_zero_return_has_signed_rounding_receipt(desk):
    _, _, post = desk
    vid = voucher(desk, usd="0", mxn="100")
    receipt(desk, vid, mxn=9950)
    data(post(f"/{vid}/api/report/submit-calculated"))
    data(paid(desk, vid, actual_mxn="0", method="rounding"))
    calculated_approve(desk, vid)


def test_codes_largest_split_and_earliest_tie(desk):
    _, client, post = desk
    vid = voucher(desk, usd="0", mxn="200")
    receipt(desk, vid, mxn=6000, split=True)
    receipt(desk, vid, mxn=3000)
    rows = report(client, vid)["rows"]
    parts = rows[0]["splits"]
    parts[0]["expense_account"] = "EXP_PROGRAMS"
    data(patch(client, f"/{vid}/api/rows/{rows[0]['id']}", {"rev": rows[0]["rev"], "splits": parts}))
    data(post(f"/{vid}/api/report/submit-calculated"))
    view = data(paid(desk, vid, actual_mxn="109", method="rounding"))
    rounding = view["rows"][-1]
    assert rounding["expense_account"] == "EXP_PROGRAMS"
    assert rounding["class_code"] == "GENERAL"
    assert rounding["document_id"] is None and not rounding["splits"]
    assert patch(client, f"/{vid}/api/rows/{rounding['id']}", {"rev": rounding["rev"], "mxn": "5"}).status_code == 422
    assert client.get(f"/{vid}/statements?row={rounding['id']}").status_code == 404


def test_bookkeeper_rounding_uses_addendum(desk, tmp_path):
    from cash.web.bookkeeper import build_report
    conn, _, post = desk
    vid = voucher(desk, usd="0", mxn="200")
    receipt(desk, vid, mxn=10050)
    data(post(f"/{vid}/api/report/submit-calculated"))
    data(paid(desk, vid, actual_mxn="99", method="rounding"))
    calculated_approve(desk, vid)
    draft, _ = expense_drafts.load(conn, vid)
    row = draft["rows"][-1]
    paper = draft["settlement_papers"]["return"]
    package = build_report(conn, tmp_path, 9, {"export_type": "expenses", "fy": "", "start": "", "end": ""})
    group = next(g for g in package["groups"] if g["reference"] == row["posted"]["receipt_id"])
    assert not group["missing_references"]
    assert any(d["id"] == paper["document_id"] and d["reference"] == paper["paper_id"] for d in group["documents"])


def test_other_receipt_kinds_and_dollars_stay_exact(desk):
    _, client, post = desk
    vid = voucher(desk, usd="10", mxn="200")
    receipt(desk, vid, mxn=0, usd_paid=1000)
    data(post(f"/{vid}/api/rows", date="2026-09-03", purpose="No receipt", mxn="20.50", no_receipt_reason="Lost"))
    data(post(f"/{vid}/api/purchase-log", items=[{"date": "2026-09-03", "vendor": "Market", "purpose": "Food", "usd": "0", "mxn": "10"}]))
    data(post(f"/{vid}/api/report/submit-calculated"))
    before = report(client, vid)["rows"]
    after = data(paid(desk, vid, actual_mxn="169", method="rounding"))["rows"]
    assert [(r["usd"], r["mxn"]) for r in after[:-1]] == [(r["usd"], r["mxn"]) for r in before]


def test_seventh_receipt_and_independent_currency_resolutions(desk):
    _, client, post = desk
    vid = voucher(desk, usd="100", mxn="100")
    for _ in range(5):
        receipt(desk, vid, mxn=2000)
    receipt(desk, vid, mxn=50, usd_paid=10050, mxn_paid=50)
    data(post(f"/{vid}/api/report/submit-calculated"))
    view = data(paid(desk, vid, actual_mxn="1", method="rounding"))
    assert view["rows"][-1]["receipt_reference"] == f"{vid}-R7"
    assert set(view["cash_difference"]) == {"usd"}
    view = data(paid(desk, vid, actual="1", currency="usd", method="rounding"))
    assert view["cash_difference"] is None
    assert view["rows"][-1]["receipt_reference"] == f"{vid}-R8"
    calculated_approve(desk, vid)


def test_implied_exchange_refuses_rounding(desk):
    _, client, post = desk
    vid = voucher(desk, usd="100", mxn="0")
    receipt(desk, vid, mxn=5050)
    data(post(f"/{vid}/api/report/submit-calculated"))
    assert report(client, vid)["cash_difference"] is None
    assert paid(desk, vid, actual_mxn="51", method="rounding").status_code == 422


def test_admin_can_change_codes_but_cannot_attach_receipt(desk):
    _, client, post = desk
    vid = voucher(desk, usd="0", mxn="200")
    receipt(desk, vid, mxn=10050)
    data(post(f"/{vid}/api/report/submit-calculated"))
    row = data(paid(desk, vid, actual_mxn="99", method="rounding"))["rows"][-1]
    view = data(patch(client, f"/{vid}/api/rows/{row['id']}", {"rev": row["rev"], "expense_account": "EXP_PROGRAMS"}))
    changed = next(r for r in view["rows"] if r["id"] == row["id"])
    assert changed["expense_account"] == "EXP_PROGRAMS"
    assert patch(client, f"/{vid}/api/rows/{row['id']}", {"rev": changed["rev"], "no_receipt_reason": "Lost"}).status_code == 422


def test_missing_codes_remain_for_approval(desk):
    _, client, post = desk
    vid = voucher(desk, usd="0", mxn="200")
    receipt(desk, vid, mxn=10050)
    row = report(client, vid)["rows"][0]
    data(patch(client, f"/{vid}/api/rows/{row['id']}", {"rev": row["rev"], "class_code": None, "expense_account": None}))
    data(post(f"/{vid}/api/report/submit-calculated"))
    rounding = data(paid(desk, vid, actual_mxn="99", method="rounding"))["rows"][-1]
    assert rounding["class_code"] is None and rounding["expense_account"] is None
