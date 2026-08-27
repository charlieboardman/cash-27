"""Signee receipt correction and projected-balance workflow."""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from cash import packages
from cash.commands import run_command
from cash.db import connect
from cash.prep import create_opening, prepare_signout
from cash.web.app import create_app


def sid():
    return str(uuid.uuid4())


def png():
    output = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(output, "PNG")
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
