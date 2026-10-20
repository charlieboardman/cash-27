"""The approved row-centered design, including compact purchase subrows."""

import io

import pytest
from pypdf import PdfWriter
from test_browser_workflows import expect, recipient, saved, signed_signout


def signed_pdf():
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    stream = io.BytesIO()
    writer.write(stream)
    return {
        "name": "signed.pdf",
        "mimeType": "application/pdf",
        "buffer": stream.getvalue(),
    }


@pytest.mark.parametrize("width,language", [(1440, "en"), (390, "es")])
def test_log_subrows_edit_one_purchase_and_sum_once(web, width, language):
    page, post, artifacts = web
    vid = signed_signout(page, post)["voucher_id"]
    page.set_viewport_size({"width": width, "height": 844})
    recipient(page, vid)
    if language == "es":
        page.get_by_role("button", name="Español", exact=True).click()
    page.locator("#er-print-log").click()
    for vendor, amount in [("Mercado", "125.50"), ("Taxi", "74.50")]:
        page.locator("[data-add-purchase]").click()
        expect(page.locator("[data-purchase]")).to_have_count(1)
        expect(page.locator("[data-open-purchase]")).to_have_count(0)
        page.locator('[data-purchase] [name="vendor"]').fill(vendor)
        page.locator('[data-purchase] [name="purpose"]').fill("Supplies")
        page.locator('[data-purchase] [name="mxn"]').fill(amount)
        if vendor == "Mercado":
            page.locator('[data-purchase] [name="usd"]').fill("50")
        page.locator("[data-log-back]").click()
    expect(page.locator("#er-print-statements")).to_be_hidden()
    expect(page.locator("[data-open-purchase]")).to_have_count(2)
    expect(page.locator("[data-purchase]")).to_have_count(0)
    expect(page.locator("[data-log-total]")).to_contain_text("200.00")
    expect(page.locator('[data-cash-row="log"] .er-thumb')).to_have_text("☷")
    expect(page.locator('[data-cash-row="positive"]')).to_have_count(0)
    expect(page.locator("#er-balance")).to_contain_text("100.00")
    expect(page.locator(".er-row[data-row]")).to_have_count(0)
    page.locator('[data-open-purchase="0"]').click()
    page.locator('[name="mxn"]').fill("150.50")
    page.locator("[data-log-back]").click()
    expect(page.locator("[data-log-total]")).to_contain_text("225.00")
    page.locator('[data-open-purchase="1"]').click()
    page.locator("[data-remove-purchase]").click()
    expect(page.locator("[data-open-purchase]")).to_have_count(1)
    expect(page.locator("[data-log-total]")).to_contain_text("150.50")
    assert page.locator(".er-tray-body").evaluate("(e)=>e.scrollWidth<=e.clientWidth")
    page.screenshot(
        path=str(artifacts / f"purchase-subrows-{language}-{width}.png"),
        animations="disabled",
    )
    page.locator("#er-tray-done").click()
    page.reload()
    page.locator('[data-cash-row="log"]').click()
    expect(page.locator("[data-open-purchase]")).to_have_count(1)
    printed = page.request.get(f"/{vid}/purchase-log").text()
    assert "Mercado" in printed and "150.50" in printed
    assert page.evaluate("[...cashI18n.missing]") == []


def test_voucher_and_addenda_are_dated_and_ordered_with_receipts(web):
    page, post, _ = web
    vid = signed_signout(page, post)["voucher_id"]
    addendum = post(
        "/api/addenda/prepare",
        voucher_id=vid,
        kind="positive",
        date="2026-09-02",
        usd="0",
        mxn="10",
        reason="More cash",
    )
    for date, purpose in [
        ("2026-09-05", "Later"),
        ("2026-09-03", "Earlier"),
        ("", "Undated"),
    ]:
        post(
            f"/{vid}/api/rows",
            date=date,
            purpose=purpose,
            mxn="10",
            class_code="7.5",
            expense_account="1000",
        )
    recipient(page, vid)
    rows = page.locator("#er-rows .er-row")
    expect(rows).to_have_count(5)
    expect(rows.nth(0)).to_contain_text(vid)
    expect(rows.nth(0)).to_contain_text("2026-09-02")
    expect(rows.nth(1)).to_contain_text(addendum["addendum_id"])
    expect(rows.nth(1)).to_contain_text("2026-09-02")
    expect(rows.nth(2)).to_contain_text("Earlier")
    expect(rows.nth(2)).to_contain_text("2026-09-03")
    expect(rows.nth(3)).to_contain_text("Later")
    expect(rows.nth(3)).to_contain_text("2026-09-05")
    expect(rows.nth(4)).to_contain_text("Undated")


def test_cash_rows_switch_direction_and_require_signed_paper_before_approval(web):
    page, post, _ = web
    vid = signed_signout(page, post)["voucher_id"]
    captured = page.request.post(
        "/api/documents/capture-merge", multipart={"files": signed_pdf()}
    ).json()["data"]
    post(
        f"/{vid}/api/rows",
        date="2026-09-03",
        purpose="Supplies",
        usd="50",
        mxn="150",
        document_id=captured["document_id"],
        class_code="7.5",
        expense_account="1000",
    )
    page.goto(f"/{vid}")
    page.locator("#er-submit").click()
    expect(page.locator("#er-submit")).to_be_hidden()
    expect(page.locator('[data-cash-row="positive"] .er-thumb')).to_have_text("→")
    expect(page.locator("#er-approve")).to_be_disabled()
    page.locator('[data-cash-row="positive"]').click()
    page.locator("[data-generate]").click()
    page.locator("[data-signed]").set_input_files(signed_pdf())
    expect(page.locator(".er-row-document")).to_be_visible()
    page.locator("#er-tray-done").click()
    expect(page.locator("#er-approve")).to_be_enabled()
    page.locator(".er-row[data-row] .er-row-open").click()
    page.locator('.er-editor [name="mxn"]').fill("75")
    saved(page)
    page.locator("#er-tray-done").click()
    expect(page.locator('[data-cash-row="return"] .er-thumb')).to_have_text("←")
    expect(page.locator('[data-cash-row="positive"]')).to_have_count(0)
    page.locator(".er-row[data-row] .er-row-open").click()
    page.locator('.er-editor [name="mxn"]').fill("100")
    saved(page)
    page.locator("#er-tray-done").click()
    expect(
        page.locator('[data-cash-row="return"],[data-cash-row="positive"]')
    ).to_have_count(0)
    page.locator("#er-approve").click()
    expect(page.locator('[name="confirmed"],[name="reimburse_mxn"]')).to_have_count(0)
    page.locator("#er-approve-confirm").click()
    expect(page.locator("#er-status")).to_have_text("Closed")


@pytest.mark.parametrize("width", [390, 1440])
def test_settlement_appears_after_submission_survives_corrections_and_becomes_history(
    web, width
):
    from test_browser_workflows import (
        add_row,
        administrator,
        close_row,
        code_row,
        file_cash_papers,
        submit_report,
    )

    page, post, _ = web
    vid = signed_signout(page, post)["voucher_id"]
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(f"/{vid}")
    expect(
        page.locator('[data-cash-row="return"],[data-cash-row="positive"]')
    ).to_have_count(0)
    expect(page.locator("#er-submit")).to_be_visible()
    expect(page.locator("#er-approve")).to_be_hidden()
    refused = page.request.post(f"/{vid}/api/settlement/prepare/return", data={})
    assert (
        refused.status == 422
        and refused.json()["error"]["code"] == "report_not_submitted"
    )
    editor = add_row(page, usd="50", mxn="150")
    code_row(editor)
    saved(page)
    close_row(page)
    recipient(page, vid)
    expect(page.locator("#er-balance-label")).to_have_text("Reimbursement due")
    expect(page.locator("#er-balance")).to_have_text("USD 0.00 · MXN 50.00")
    expect(page.locator('[data-cash-row="positive"]')).to_have_count(0)
    submit_report(page)
    expect(page.locator('[data-cash-row="positive"]')).to_have_count(0)
    administrator(page)
    page.goto(f"/{vid}")
    expect(page.locator('[data-cash-row="positive"]')).to_contain_text("50.00")
    file_cash_papers(page)
    page.reload()
    page.locator("#er-corrections").click()
    page.locator("#er-corrections-summary").fill("Check the amount")
    page.locator("#er-corrections-send").click()
    expect(page.locator("#er-status")).to_have_text("Draft")
    expect(page.locator('[data-cash-row="positive"]')).to_contain_text("50.00")
    recipient(page, vid)
    page.locator(".er-row[data-row] .er-row-open").click()
    page.locator('.er-editor [name="mxn"]').fill("200")
    saved(page)
    close_row(page)
    expect(page.locator("#er-balance")).to_contain_text("100.00")
    expect(page.locator('[data-cash-row="positive"]')).to_have_count(0)
    submit_report(page)
    administrator(page)
    page.goto(f"/{vid}")
    expect(page.locator('[data-cash-row="positive"]')).to_contain_text("100.00")
    expect(page.locator("#er-approve")).to_be_disabled()
    file_cash_papers(page)
    page.reload()
    page.locator("#er-approve").click()
    page.locator("#er-approve-confirm").click()
    expect(page.locator("#er-status")).to_have_text("Closed")
    view = page.request.get(f"/{vid}/api/report").json()["data"]
    aid = view["cash_rows"]["advances"][-1]["addendum_id"]
    recipient(page, vid)
    expect(page.locator(f'[data-cash-row="{aid}"]')).to_contain_text("Reimbursement")
    expect(
        page.locator('[data-cash-row="positive"],[data-cash-row="return"]')
    ).to_have_count(0)


def test_admin_submits_an_unused_signout_before_the_return_appears(web):
    page, post, _ = web
    vid = signed_signout(page, post)["voucher_id"]
    page.goto(f"/{vid}")
    expect(page.locator('[data-cash-row="return"]')).to_have_count(0)
    page.locator("#er-submit").click()
    expect(page.locator('[data-cash-row="return"]')).to_be_visible()
    expect(page.locator("#er-submit")).to_be_hidden()
    expect(page.locator("#er-approve")).to_be_disabled()
