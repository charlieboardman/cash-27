"""Signee receipt correction and projected-balance workflow."""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from cash import packages
from cash.commands import run_command
from cash.db import connect
from cash.prep import create_opening, prepare_addendum, prepare_signout
from cash.web.app import create_app


def sid():
    return str(uuid.uuid4())


def png(width=20, height=20):
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, "PNG")
    output.seek(0)
    return output


@pytest.fixture
def workflow(tmp_path):
    app = create_app(tmp_path, admin_password="x", testing=True)
    conn = connect(tmp_path / "cash.db")
    run_command(
        conn, sid(), "opening",
        lambda db: create_opening(
            db, submission_id=sid(), date="2026-08-01",
            mxn_centavos=1000_00, assigned_rate_milli=17000))
    voucher_id = run_command(
        conn, sid(), "signout",
        lambda db: prepare_signout(
            db, submission_id=sid(), date="2026-08-02", seq_no=0,
            recipient="Ana", purpose="Program supplies", notes="",
            mxn_centavos=500_00))["voucher_id"]
    conn.close()
    return app.test_client(), app, voucher_id


def test_returned_claim_can_be_corrected_and_resubmitted(workflow):
    client, app, voucher_id = workflow
    initial = client.get(f"/{voucher_id}/state").get_json()["data"]
    csrf = initial["csrf"]
    uploaded = client.post(
        f"/{voucher_id}/documents",
        data={"files": (png(), "receipt.png")},
        headers={"X-CSRF": csrf}, content_type="multipart/form-data")
    assert uploaded.status_code == 200
    document_id = uploaded.get_json()["data"]["documents"][0]["document_id"]
    body = {
        "submission_id": sid(), "document_id": document_id,
        "date": "2026-08-03", "vendor": "Store",
        "description": "Materials", "face_value": "100.00",
        "face_currency": "MXN", "usd_paid": "0",
        "usd_change": "0", "mxn_paid": "100.00",
        "mxn_change": "0", "notes": ""}
    created = client.post(
        f"/{voucher_id}/claims", json=body, headers={"X-CSRF": csrf})
    assert created.status_code == 200, created.get_json()
    claim_id = created.get_json()["data"]["claim_id"]
    state = client.get(f"/{voucher_id}/state").get_json()["data"]
    assert state["projected_after_pending"]["mxn"] == "400.00"

    submitted = client.post(
        f"/{voucher_id}/packages",
        json={"submission_id": sid(), "claim_ids": [claim_id]},
        headers={"X-CSRF": csrf})
    package_id = submitted.get_json()["data"]["package_id"]
    conn = connect(app.config["DATA_ROOT"] / "cash.db")
    run_command(
        conn, sid(), "return_package",
        lambda db: packages.return_package(
            db, package_id=package_id, admin_summary="Correct the total",
            item_notes={claim_id: "Receipt shows MXN 120"}))
    conn.close()
    returned = client.get(f"/{voucher_id}/state").get_json()["data"]
    assert returned["claims"][0]["state"] == "flagged"
    assert returned["projected_after_pending"]["mxn"] == "400.00"

    correction = dict(body)
    correction.update({
        "submission_id": sid(), "face_value": "120.00",
        "mxn_paid": "120.00", "expected_content_version": 1})
    edited = client.patch(
        f"/{voucher_id}/claims/{claim_id}", json=correction,
        headers={"X-CSRF": csrf})
    assert edited.status_code == 200, edited.get_json()
    corrected = client.get(f"/{voucher_id}/state").get_json()["data"]
    assert corrected["projected_after_pending"]["mxn"] == "380.00"
    assert corrected["claims"][0]["content_version"] == 2

    resubmitted = client.post(
        f"/{voucher_id}/packages/{package_id}/resubmit",
        json={"submission_id": sid()}, headers={"X-CSRF": csrf})
    assert resubmitted.status_code == 200, resubmitted.get_json()
    final = client.get(f"/{voucher_id}/state").get_json()["data"]
    assert final["packages"][0]["status"] == "submitted"
    assert final["claims"][0]["state"] == "submitted"
    assert final["projected_after_pending"]["mxn"] == "380.00"
    assert client.get(
        f"/{voucher_id}/documents/{document_id}/stream").status_code == 200


def test_activity_explains_additional_cash_even_before_paperwork_is_filed(
        workflow):
    client, app, voucher_id = workflow
    conn = connect(app.config["DATA_ROOT"] / "cash.db")
    run_command(
        conn, sid(), "more_cash",
        lambda db: prepare_addendum(
            db, submission_id=sid(), voucher_id=voucher_id,
            date="2026-08-03", seq_no=None, kind="positive",
            reason="More supplies needed", mxn_centavos=200_00))
    conn.close()

    state = client.get(f"/{voucher_id}/state").get_json()["data"]
    assert state["authoritative"]["mxn"] == "700.00"
    assert [(item["label"], item["mxn_change_centavos"])
            for item in state["activity"]] == [
                ("Originally received", 500_00),
                ("Additional cash received", 200_00),
            ]
    assert state["activity"][1]["detail"] == "More supplies needed"


def test_ordered_receipt_photos_become_one_multipage_pdf(workflow):
    client, app, voucher_id = workflow
    csrf = client.get(f"/{voucher_id}/state").get_json()["data"]["csrf"]
    uploaded = client.post(
        f"/{voucher_id}/documents",
        data={"files": [
            (png(20, 40), "front.png"),
            (png(40, 20), "back.png"),
        ]}, headers={"X-CSRF": csrf}, content_type="multipart/form-data")
    assert uploaded.status_code == 200, uploaded.get_json()
    documents = uploaded.get_json()["data"]["documents"]
    assert len(documents) == 1
    assert documents[0]["pages"] == 2

    document_id = documents[0]["document_id"]
    conn = connect(app.config["DATA_ROOT"] / "cash.db")
    row = conn.execute(
        "SELECT voucher_id, page_count, state FROM documents"
        " WHERE document_id=?", (document_id,)).fetchone()
    conn.close()
    assert dict(row) == {"voucher_id": voucher_id, "page_count": 2,
                         "state": "signee_draft"}

    from pypdf import PdfReader
    streamed = client.get(
        f"/{voucher_id}/documents/{document_id}/stream")
    pages = PdfReader(io.BytesIO(streamed.data)).pages
    assert float(pages[0].mediabox.height) > float(pages[0].mediabox.width)
    assert float(pages[1].mediabox.width) > float(pages[1].mediabox.height)
