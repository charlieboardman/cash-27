"""Every coded form prints on exactly one page carrying its code (paper.py)."""

import io

from pypdf import PdfReader
import pytest

from cash import paper

W = lambda n: "W" * n  # the widest letter, the worst case for wrapping


def notes_at_limit():
    lines = paper.VOUCHER_NOTES_LINES
    return "\n".join([W(paper.VOUCHER_NOTES // lines - 1)] * lines)


def filled_forms(post):
    """Every coded form, with every printed field at its limit."""
    post("/api/settings/voucher-defaults", approver_name=W(paper.NAME))
    voucher = dict(date="2026-09-02", recipient=W(paper.NAME), purpose=W(paper.VOUCHER_PURPOSE),
                   notes=notes_at_limit(), usd="500.00", mxn="5000.00")
    signout = post("/api/vouchers/prepare", type="signout", **voucher)["voucher_id"]
    direct = post("/api/vouchers/prepare", type="direct_expense", **voucher)["voucher_id"]
    addendum = post("/api/addenda/prepare", voucher_id=signout, kind="positive", date="2026-09-03",
                    usd="100.00", mxn="1000.00", reason=W(paper.REASON))["addendum_id"]
    post(f"/{signout}/api/rows", date="2026-09-03", vendor=W(paper.ROW_VENDOR),
         purpose=W(paper.ROW_PURPOSE), usd="1000000.00", mxn="1000000.00",
         no_receipt_reason=W(paper.REASON))
    compared = post("/api/balances/safe-count", usd="1", mxn="1")
    count = post("/api/balances/safe-count/correct", date="2026-09-03", usd="1", mxn="1",
                 expected_ledger_revision=compared["ledger_revision"],
                 reason=W(paper.REASON))["count_id"]
    return [(f"/voucher/{signout}/print", signout), (f"/voucher/{direct}/print", direct),
            (f"/voucher/{signout}/addendum/{addendum}/print", addendum),
            (f"/{signout}/statements", f"{signout}-R1x"),
            (f"/{signout}/purchase-log", f"{signout}-log"),
            (f"/balances/count/{count}/print", f"Txn-{count}")]


def test_every_coded_form_prints_on_one_page_with_its_code(web):
    page, post, _ = web
    for url, code in filled_forms(post):
        page.goto(url)
        page.wait_for_load_state("networkidle")
        printed = PdfReader(io.BytesIO(page.pdf(format="Letter")))
        assert len(printed.pages) == 1, url
        assert code in printed.pages[0].extract_text(), url


@pytest.mark.parametrize("field,limit", [
    ("recipient", paper.NAME), ("purpose", paper.VOUCHER_PURPOSE),
    ("notes", paper.VOUCHER_NOTES)])
def test_voucher_text_longer_than_its_limit_is_refused(web, field, limit):
    page, _, _ = web
    body = {"type": "signout", "date": "2026-09-02", "recipient": "Ana", "purpose": "Supplies",
            "usd": "10", field: "a" * (limit + 1)}
    response = page.request.post("/api/vouchers/prepare", data={
        "submission_id": "6f0b7e1e-1111-4111-8111-111111111111", **body})
    assert response.status == 422 and response.json()["error"]["code"] == "too_long"


def test_voucher_notes_are_limited_to_five_lines(web):
    page, _, _ = web
    response = page.request.post("/api/vouchers/prepare", data={
        "submission_id": "6f0b7e1e-2222-4222-8222-222222222222", "type": "signout",
        "date": "2026-09-02", "recipient": "Ana", "purpose": "Supplies", "usd": "10",
        "notes": "\n".join(["a"] * (paper.VOUCHER_NOTES_LINES + 1))})
    assert response.status == 422 and response.json()["error"]["code"] == "too_long"
