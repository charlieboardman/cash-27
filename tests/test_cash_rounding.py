"""Whole-peso cash settlement: smear the difference or declare it missing."""

import io
import uuid

from pypdf import PdfWriter

from cash import repo
from test_browser_workflows import expect, saved
from test_closeouts import approve, data, desk, document, receipt, voucher  # noqa: F401


def report(client, vid):
    return data(client.get(f"/{vid}/api/report"))


def patch(client, url, body):
    csrf = client.get("/api/session").get_json()["data"]["csrf"]
    return client.patch(url, json={"submission_id": str(uuid.uuid4()), **body},
                        headers={"X-CSRF": csrf})


def paid(desk, vid, **body):
    _, _, post = desk
    return post(f"/{vid}/api/report/cash-difference", **body)


def test_reimbursement_rounding_smears_receipts_to_the_paid_peso(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="0", mxn="100")
    receipt(desk, vid, mxn=5000)
    receipt(desk, vid, mxn=5050)
    data(post(f"/{vid}/api/report/submit-calculated"))

    difference = report(client, vid)["cash_difference"]
    assert difference == {"direction": "reimbursement", "exact": "0.50",
                          "suggested": "1.00", "can_missing_cash": False}

    view = data(paid(desk, vid, actual_mxn="1.00", method="rounding"))
    assert view["cash_difference"] is None
    assert [row["mxn"] for row in view["rows"]] == ["50.00", "51.00"]
    assert view["cash_rows"]["stages"][0]["mxn"] == "1.00"

    result = data(approve(desk, vid))
    assert result["balance"]["balanced"]
    after = repo.replay_projected(conn).states[vid]
    assert (after.usd, after.mxn) == (0, 0)


def test_return_rounding_uses_the_counted_cash(desk):
    conn, client, post = desk
    vid = voucher(desk, usd="0", mxn="200")
    receipt(desk, vid, mxn=5000)
    receipt(desk, vid, mxn=5050)
    data(post(f"/{vid}/api/report/submit-calculated"))

    difference = report(client, vid)["cash_difference"]
    assert difference["direction"] == "return"
    assert difference["exact"] == "99.50"
    assert difference["suggested"] == "100.00"

    view = data(paid(desk, vid, actual_mxn="99.00", method="rounding"))
    assert view["cash_difference"] is None
    assert [row["mxn"] for row in view["rows"]] == ["50.00", "51.00"]
    stage = next(stage for stage in view["cash_rows"]["stages"] if stage["kind"] == "return")
    assert stage["mxn"] == "99.00"

    result = data(approve(desk, vid))
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


def test_underpaid_reimbursement_needs_an_explanation(desk):
    _conn, client, post = desk
    vid = voucher(desk, usd="0", mxn="100")
    receipt(desk, vid, mxn=5000)
    receipt(desk, vid, mxn=5050)
    data(post(f"/{vid}/api/report/submit-calculated"))

    refused = paid(desk, vid, actual_mxn="0.00", method="rounding")
    assert refused.status_code == 422
    assert refused.get_json()["error"]["code"] == "resolution_required"

    view = data(paid(desk, vid, actual_mxn="0.00", method="rounding",
                     reason="Recipient agreed to absorb the difference"))
    assert [row["mxn"] for row in view["rows"]] == ["50.00", "50.00"]


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
    expect(page.locator("#er-cash-difference-exact")).to_have_value("99.50")
    expect(page.locator("#er-cash-difference-actual")).to_have_value("100.00")
    page.locator("#er-cash-difference-actual").fill("99.00")
    expect(page.locator("#er-cash-difference-delta")).to_have_text("MXN 0.50")
    page.locator("#er-cash-rounding").click()
    expect(panel).to_be_hidden()
    saved(page)

    amounts = [row["mxn"] for row in
               page.request.get(f"/{vid}/api/report").json()["data"]["rows"]]
    assert amounts == ["50.00", "51.00"]
    assert page.evaluate("[...cashI18n.missing]") == []
    page.reload()
    expect(panel).to_be_hidden()

