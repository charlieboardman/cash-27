"""Autosave must preserve edits and never approve an older, hidden value."""

import io
import uuid

from pypdf import PdfWriter

import pytest

from test_browser_workflows import (browser, web, prepare, recipient, saved,  # noqa: F401
                                    expect, answer_confirm)


def ready_row(page, post, *, usd="5", mxn="0", receipt_total=None):
    vid = prepare(post, kind="signout")["voucher_id"]
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


@pytest.mark.parametrize("admin", [True, False])
@pytest.mark.parametrize("invalid", ["local", "server"])
def test_rejected_edits_survive_collapse_and_block_approval_or_submission(web, admin, invalid):
    page, post, _ = web
    vid, row = ready_row(page, post, mxn="-1" if invalid == "server" else "0",
                         receipt_total="10" if invalid == "server" else None)
    # The leftover cash is already declared, so submitting sends nothing first.
    left = view(page, vid)["balance"]
    post(f"/{vid}/api/rows", kind="cash_return", usd=left["usd"], mxn=left["mxn"])
    if admin:
        page.goto(f"/{vid}")
        saved(page)
    else:
        recipient(page, vid)
    page.locator(".er-row-open").click()
    field = "usd" if invalid == "local" else "receipt_total"
    value = "12.345" if invalid == "local" else "-1"
    page.locator(f'.er-editor [name="{field}"]').fill(value)
    expect(page.locator("#er-save-state")).to_have_text("Changes not saved")
    expect(page.locator("#er-retry")).to_be_visible()
    page.locator(".er-row-open").click()
    page.locator(".er-row-open").click()
    expect(page.locator(f'.er-editor [name="{field}"]')).to_have_value(value)
    requests = []
    step = "approve" if admin else "submit"
    page.route(f"**/{vid}/api/report/{step}", lambda route: (
        requests.append(route.request.url), route.continue_()))
    page.locator(f"#er-{step}").click()
    if admin:
        answer_confirm(page)
    expect(page.locator("#er-save-state")).to_have_text("Changes not saved")
    assert requests == []
    assert view(page, vid)["rows"][0]["usd"] == "5.00"
    assert view(page, vid)["rows"][0]["posted"] is None
    page.locator(f'.er-editor [name="{field}"]').fill("12.34")
    saved(page)
    page.locator(f"#er-{step}").click()
    if admin:
        answer_confirm(page)
    expect(page.locator(f"#er-{step}")).to_be_hidden()
    after = view(page, vid)
    assert after["rows"][0][field] == "12.34"
    assert after["status"] == ("approved" if admin else "submitted")


def test_edits_while_a_save_is_pending_survive_collapse(web):
    page, post, _ = web
    vid, row = ready_row(page, post)
    page.goto(f"/{vid}")
    saved(page)
    page.locator(".er-row-open").click()
    requests = []

    def delayed_response(route):
        requests.append(route.request.post_data_json)
        response = route.fetch()
        if len(requests) == 1:
            # The first save is committed but still awaiting acknowledgement.
            page.evaluate('''() => {
                const input = document.querySelector('.er-editor [name="usd"]');
                input.value = '9';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                document.querySelector('.er-row-open').click();
            }''')
        route.fulfill(response=response)

    page.route(f"**/{vid}/api/rows/{row['id']}", delayed_response)
    page.locator('.er-editor [name="usd"]').fill("6")
    expect(page.locator(".er-editor")).to_have_count(0)
    saved(page)
    assert [request["usd"] for request in requests] == ["6", "9"]
    assert requests[0]["submission_id"] != requests[1]["submission_id"]
    assert requests[1]["rev"] > requests[0]["rev"]
    page.reload()
    saved(page)
    assert view(page, vid)["rows"][0]["usd"] == "9.00"


def test_older_row_response_cannot_replace_a_newer_report_snapshot(web):
    page, post, _ = web
    vid, row = ready_row(page, post)
    page.goto(f"/{vid}")
    saved(page)
    page.locator(".er-row-open").click()
    responses = []
    def hold_response(route):
        responses.append((route, route.fetch()))
        page.evaluate("window.reviewSaveCommitted = true")

    page.route(f"**/{vid}/api/rows/{row['id']}", hold_response)
    page.locator('.er-editor [name="usd"]').fill("6")
    page.wait_for_function("window.reviewSaveCommitted === true")
    # Opening a second row completes a newer request before the first response arrives.
    page.locator("#er-add").click()
    expect(page.locator(".er-row")).to_have_count(2)
    expect(page.locator(".er-row.open")).to_contain_text("New receipt")
    route, response = responses[0]
    route.fulfill(response=response)
    saved(page)
    assert len(view(page, vid)["rows"]) == 2
    expect(page.locator(".er-row")).to_have_count(2)
    # Force a re-render, which previously exposed the stale internal snapshot.
    page.locator(".er-row-open").first.click()
    expect(page.locator(".er-row")).to_have_count(2)
    expect(page.locator('.er-editor [name="usd"]')).to_have_value("6.00")


def test_failed_note_save_blocks_approval_and_retries_the_same_request(web):
    page, post, _ = web
    vid, _ = ready_row(page, post)
    page.goto(f"/{vid}")
    saved(page)
    requests = []
    allow_retry = False

    def fail_note(route):
        if route.request.method != "PATCH":
            return route.continue_()
        requests.append(route.request.post_data_json)
        if len(requests) == 1:
            route.fetch()  # Committed, but its acknowledgement is lost.
        if allow_retry:
            route.continue_()
        else:
            route.abort()

    page.route(f"**/{vid}/api/report", fail_note)
    page.locator("#er-note").fill("First explanation")
    expect(page.locator("#er-save-state")).to_have_text("Changes not saved")
    page.locator("#er-note").fill("Complete explanation")
    expect(page.locator("#er-save-state")).to_have_text("Changes not saved")
    page.locator("#er-approve").click()
    answer_confirm(page)
    expect(page.locator("#er-save-state")).to_have_text("Changes not saved")
    assert view(page, vid)["rows"][0]["posted"] is None
    allow_retry = True
    page.locator("#er-retry").click()
    saved(page)
    assert len({request["submission_id"] for request in requests[:-1]}) == 1
    assert requests[-1]["submission_id"] != requests[0]["submission_id"]
    page.reload()
    saved(page)
    expect(page.locator("#er-note")).to_have_value("Complete explanation")
    assert view(page, vid)["note"] == "Complete explanation"


def test_approval_waits_for_the_pending_note_save(web):
    page, post, _ = web
    vid, _ = ready_row(page, post)
    page.goto(f"/{vid}")
    saved(page)
    held = []
    approvals = []

    def hold_note(route):
        if route.request.method != "PATCH":
            return route.continue_()
        held.append((route, route.fetch()))
        page.evaluate("window.reviewNoteCommitted = true")

    page.route(f"**/{vid}/api/report", hold_note)
    page.route(f"**/{vid}/api/report/approve", lambda route: (
        approvals.append(route.request.url), route.continue_()))
    page.locator("#er-note").fill("Explanation to include with approval")
    page.wait_for_function("window.reviewNoteCommitted === true")
    expect(page.locator("#er-save-state")).to_have_text("Saving…")
    page.locator("#er-approve").click()
    answer_confirm(page)
    page.wait_for_timeout(300)
    assert approvals == []
    assert view(page, vid)["rows"][0]["posted"] is None
    route, response = held[0]
    route.fulfill(response=response)
    expect(page.locator("#er-approve")).to_be_hidden()
    assert view(page, vid)["note"] == "Explanation to include with approval"
    assert len(approvals) == 1


@pytest.mark.parametrize("admin", [True, False])
@pytest.mark.parametrize("delete_fails", [False, True])
def test_report_action_waits_for_delete_after_undo_expires(web, admin, delete_fails):
    page, post, _ = web
    vid, row = ready_row(page, post)
    post(f"/{vid}/api/rows", kind="cash_return", usd="1", date="2026-09-03")
    if admin:
        page.goto(f"/{vid}")
        saved(page)
    else:
        recipient(page, vid)
    held, actions = [], []

    def hold_delete(route):
        if route.request.method != "DELETE":
            return route.continue_()
        held.append(route)
        page.evaluate("window.deleteStarted = true")

    step = "approve" if admin else "submit"
    page.route(f"**/{vid}/api/rows/{row['id']}", hold_delete)
    page.route(f"**/{vid}/api/report/{step}", lambda route: (
        actions.append(route.request.url), route.continue_()))
    page.locator(".er-row-open").first.click()
    page.locator("[data-delete]").click()
    page.wait_for_function("window.deleteStarted === true")
    expect(page.locator(".er-row")).to_have_count(0)
    page.locator(f"#er-{step}").click()
    if admin:
        answer_confirm(page)
    page.wait_for_timeout(300)
    assert actions == []
    assert view(page, vid)["rows"][0]["posted"] is None
    expect(page.locator("#er-save-state")).to_have_text("Saving…")
    if delete_fails:
        held[0].fulfill(status=409, json={"ok": False, "error": {
            "code": "conflict", "message": "Deletion rejected"}})
        expect(page.locator("#er-message")).to_have_text("Deletion rejected")
        expect(page.locator(".er-row")).to_have_count(1)
        assert actions == []
        assert view(page, vid)["status"] == "draft"
    else:
        held[0].continue_()
        expect(page.locator(f"#er-{step}")).to_be_hidden()
        after = view(page, vid)
        assert row["id"] not in [item["id"] for item in after["rows"]]
        assert after["status"] == ("approved" if admin else "submitted")
        assert len(actions) == 1


def test_conflicting_edits_stay_unsaved_without_overwriting_the_other_screen(web):
    page, post, _ = web
    vid, row = ready_row(page, post)
    page.goto(f"/{vid}")
    saved(page)
    page.locator(".er-row-open").click()
    # Another screen saves a newer version while this editor still shows $5.
    response = page.request.patch(f"/{vid}/api/rows/{row['id']}", data={
        "submission_id": str(uuid.uuid4()), "rev": row["rev"], "usd": "8"})
    assert response.ok
    page.locator('.er-editor [name="usd"]').fill("9")
    expect(page.locator("#er-save-state")).to_have_text("Changes not saved")
    expect(page.locator("#er-message")).to_contain_text("changed on another screen")
    page.locator(".er-row-open").click()
    page.locator(".er-row-open").click()
    expect(page.locator('.er-editor [name="usd"]')).to_have_value("9")
    page.locator("#er-retry").click()
    expect(page.locator("#er-save-state")).to_have_text("Changes not saved")
    assert view(page, vid)["rows"][0]["usd"] == "8.00"
