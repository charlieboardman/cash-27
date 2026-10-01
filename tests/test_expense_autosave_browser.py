"""Autosave: the server holds the draft, and an action saves pending edits first."""

import io
import json
import uuid

from pypdf import PdfWriter

import pytest

from test_browser_workflows import (browser, web, prepare, recipient, saved,  # noqa: F401
                                    expect, declare_left, signed_signout)


def ready_row(page, post, *, usd="5", mxn="0", receipt_total=None):
    vid = signed_signout(page, post)["voucher_id"]
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    statement = page.request.post(f"/{vid}/documents", multipart={"files": {
        "name": "statement.pdf", "mimeType": "application/pdf", "buffer": buffer.getvalue()}}
    ).json()["data"]["documents"][0]["document_id"]
    row = post(f"/{vid}/api/rows", date="2026-09-03", usd=usd, mxn=mxn,
               receipt_total=receipt_total, purpose="Taxi", no_receipt_reason="No paper receipt",
               document_id=statement, document_kind="statement",
               class_code="7.5", expense_account="1000")["rows"][0]
    return vid, row


def view(page, vid):
    return page.request.get(f"/{vid}/api/report").json()["data"]


def left_over(page, vid):
    """The cash the report shows is left, as the amounts to return."""
    balance = view(page, vid)["balance"]
    return {"return_usd": balance["usd"], "return_mxn": balance["mxn"]}


def attempt_approval(page, **amounts):
    """Open the approval panel, confirm, and press Approve without waiting for the outcome."""
    page.locator("#er-approve").click()
    form = page.locator("#er-approval-form")
    for name, amount in amounts.items():
        form.locator(f'[name="{name}"]').fill(amount)
    form.locator('[name="confirmed"]').check()
    page.locator("#er-approve-confirm").click()


def test_typing_saves_after_a_pause_and_a_reload_shows_it(web):
    page, post, _ = web
    vid, row = ready_row(page, post)
    recipient(page, vid)
    page.locator(".er-row-open").click()
    page.locator('.er-editor [name="usd"]').fill("6")
    saved(page)
    assert view(page, vid)["rows"][0]["usd"] == "6.00"
    page.locator("#er-note").fill("Cab fare was higher")
    saved(page)
    page.reload()
    expect(page.locator("#er-note")).to_have_value("Cab fare was higher")
    page.locator(".er-row-open").click()
    expect(page.locator('.er-editor [name="usd"]')).to_have_value("6.00")


def test_edits_to_several_fields_in_a_row_do_not_conflict_with_each_other(web):
    page, post, _ = web
    vid, row = ready_row(page, post)
    recipient(page, vid)
    page.locator(".er-row-open").click()
    editor = page.locator(".er-editor")
    for value in ("7", "8", "9"):
        editor.locator('[name="usd"]').fill(value)
        editor.locator('[name="purpose"]').fill(f"Taxi {value}")
        page.wait_for_timeout(300)
    saved(page)
    expect(page.locator("#er-message")).to_be_hidden()
    saved_row = view(page, vid)["rows"][0]
    assert (saved_row["usd"], saved_row["purpose"]) == ("9.00", "Taxi 9")


def test_summary_and_balance_follow_a_save_without_touching_the_editor(web):
    page, post, _ = web
    vid, row = ready_row(page, post)
    recipient(page, vid)
    page.locator(".er-row-open").click()
    typing = page.locator('.er-editor [name="purpose"]')
    typing.fill("Taxi to the market")
    page.locator('.er-editor [name="usd"]').fill("12")
    saved(page)
    expect(page.locator(".er-row-text strong")).to_have_text("Taxi to the market")
    expect(page.locator("#er-balance")).to_have_text("USD 38.00 · MXN 100.00")
    expect(typing).to_have_value("Taxi to the market")
    expect(typing).to_be_visible()


def test_submit_saves_what_was_just_typed_first(web):
    page, post, _ = web
    vid, row = ready_row(page, post)
    recipient(page, vid)
    page.locator(".er-row-open").click()
    page.locator('.er-editor [name="usd"]').fill("12")
    declare = page.get_by_role("group", name="Cash remaining to return")
    declare.get_by_label("USD").fill("38")
    declare.get_by_label("MXN").fill("100")
    page.locator("#er-submit").click()       # before the pause that would have saved it
    expect(page.locator("#er-status")).to_have_text("Submitted")
    after = view(page, vid)
    assert after["rows"][0]["usd"] == "12.00" and after["status"] == "submitted"


def test_a_failed_save_blocks_the_action_and_a_later_edit_saves(web):
    page, post, _ = web
    vid, row = ready_row(page, post)
    page.goto(f"/{vid}")
    actions = []
    allow = False

    def fail_note(route):
        if allow:
            return route.continue_()
        route.abort()
    page.route(f"**/{vid}/ui/note", fail_note)
    page.route(f"**/{vid}/ui/report/*", lambda route: (actions.append(route.request.url), route.continue_()))
    page.locator("#er-note").fill("Complete explanation")
    expect(page.locator("#er-save-state")).to_have_text("Changes not saved")
    page.locator("#er-approve").click()
    expect(page.locator("#er-message")).to_contain_text("Changes not saved")
    expect(page.locator("#er-approval-form")).to_have_count(0)
    page.wait_for_timeout(300)
    assert actions == [] and view(page, vid)["rows"][0]["posted"] is None
    allow = True
    page.locator("#er-note").fill("Complete explanation, again")
    saved(page)
    assert view(page, vid)["note"] == "Complete explanation, again"


def test_approval_waits_for_the_pending_note_save(web):
    page, post, _ = web
    vid, row = ready_row(page, post)
    page.goto(f"/{vid}")
    page.locator("#er-note").fill("Explanation to include with approval")
    attempt_approval(page, resolution_note="Checked with Ana", **left_over(page, vid))
    expect(page.locator("#er-approve")).to_be_hidden()
    after = view(page, vid)
    assert after["note"] == "Explanation to include with approval" and after["status"] == "approved"


def test_a_row_changed_on_another_screen_is_not_overwritten(web):
    page, post, _ = web
    vid, row = ready_row(page, post)
    recipient(page, vid)
    page.locator(".er-row-open").click()
    # Another screen saves a newer version while this editor still shows 5.
    csrf = json.loads(page.locator("#expense-report").get_attribute("hx-headers"))["X-CSRF"]
    response = page.request.patch(f"/{vid}/api/rows/{row['id']}", headers={"X-CSRF": csrf}, data={
        "submission_id": str(uuid.uuid4()), "rev": row["rev"], "usd": "8"})
    assert response.ok, response.text()
    page.locator('.er-editor [name="usd"]').fill("9")
    expect(page.locator("#er-save-state")).to_have_text("Changes not saved")
    expect(page.locator("#er-message")).to_be_visible()
    assert view(page, vid)["rows"][0]["usd"] == "8.00"
    page.reload()
    page.locator(".er-row-open").click()
    expect(page.locator('.er-editor [name="usd"]')).to_have_value("8.00")


def test_a_row_can_be_added_while_its_neighbour_is_saving(web):
    page, post, _ = web
    vid, row = ready_row(page, post)
    recipient(page, vid)
    page.locator(".er-row-open").click()
    page.locator('.er-editor [name="usd"]').fill("6")
    page.locator("#er-add").click()
    expect(page.locator(".er-row")).to_have_count(2)
    saved(page)
    assert len(view(page, vid)["rows"]) == 2 and view(page, vid)["rows"][0]["usd"] == "6.00"
