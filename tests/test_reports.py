"""Transient report previews and retained finalized revisions."""

from __future__ import annotations

import uuid
import csv
import io

import pytest

from cash.commands import run_command
from cash.db import connect
from cash.prep import (create_opening, create_withdrawal,
                       fiscal_year_for, prepare_direct_expense)
from cash.web.app import create_app


def sid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, admin_password="x", testing=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf(client):
    return client.post(
        "/api/login", json={"password": "x"}
    ).get_json()["data"]["csrf"]


def open_safe(app, *, usd_cents=100_00):
    conn = connect(app.config["DATA_ROOT"] / "cash.db")
    submission_id = sid()
    run_command(
        conn, submission_id, "opening",
        lambda db: create_opening(
            db, submission_id=submission_id, date="2026-09-01",
            usd_cents=usd_cents))
    conn.close()


def report_rows(app):
    conn = connect(app.config["DATA_ROOT"] / "cash.db")
    rows = conn.execute(
        "SELECT * FROM report_runs ORDER BY report_id").fetchall()
    conn.close()
    return rows


def report_files(app):
    return sorted((app.config["DATA_ROOT"] / "reports").glob("*.csv"))


def test_fiscal_year_uses_september_start_and_ending_year():
    assert fiscal_year_for("2026-08-31") == 2026
    assert fiscal_year_for("2026-09-01") == 2027
    assert fiscal_year_for("2027-08-31") == 2027
    assert fiscal_year_for("2027-09-01") == 2028


def test_reports_page_requires_login_and_renders(client, csrf):
    fresh_app = create_app(client.application.config["DATA_ROOT"] / "other",
                           admin_password="x", testing=True)
    assert fresh_app.test_client().get("/reports").status_code == 302
    response = client.get("/reports")
    assert response.status_code == 200
    assert b"Preview" in response.data
    assert b"Final reports" in response.data


def test_preview_is_returned_but_not_retained(app, client, csrf):
    open_safe(app)

    response = client.post(
        "/api/reports/2027/generate", json={},
        headers={"X-CSRF": csrf})

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert response.headers["X-Ledger-Revision"] == "1"
    assert response.headers["X-Report-Unallocated"] == "0"
    # Opening cash is replay input, not an expense for the bookkeeper.
    assert list(csv.DictReader(io.StringIO(response.data.decode()))) == []
    assert report_rows(app) == []
    assert report_files(app) == []


def test_final_report_is_retained_idempotently_and_becomes_outdated(
        app, client, csrf):
    open_safe(app)
    submission_id = sid()
    body = {"final": True, "submission_id": submission_id,
            "notes": "delivered"}

    first = client.post(
        "/api/reports/2027/generate", json=body,
        headers={"X-CSRF": csrf})
    assert first.status_code == 200
    first_data = first.get_json()["data"]
    assert first_data["revision"] == 1
    assert first_data["ledger_revision"] == 1
    assert len(report_rows(app)) == 1
    assert len(report_files(app)) == 1
    download = client.get(
        f"/api/reports/{first_data['report_id']}/download")
    assert download.status_code == 200
    assert download.data == report_files(app)[0].read_bytes()

    retry = client.post(
        "/api/reports/2027/generate", json=body,
        headers={"X-CSRF": csrf})
    assert retry.get_json()["data"] == first_data
    assert len(report_rows(app)) == 1
    assert len(report_files(app)) == 1

    listed = client.get("/api/reports").get_json()["data"]
    assert listed["ledger_revision"] == 1
    assert listed["reports"][0]["freshness"] == "current"

    conn = connect(app.config["DATA_ROOT"] / "cash.db")
    withdrawal_submission = sid()
    run_command(
        conn, withdrawal_submission, "withdrawal",
        lambda db: create_withdrawal(
            db, submission_id=withdrawal_submission,
            date="2026-09-02", seq_no=0, usd_cents=25_00))
    conn.close()

    listed = client.get("/api/reports").get_json()["data"]
    assert listed["ledger_revision"] == 2
    assert listed["reports"][0]["freshness"] == "outdated"


def test_preview_shows_blocker_and_finalization_refuses_it(
        app, client, csrf):
    open_safe(app)
    conn = connect(app.config["DATA_ROOT"] / "cash.db")
    expense_submission = sid()
    run_command(
        conn, expense_submission, "prepare_direct_expense",
        lambda db: prepare_direct_expense(
            db, submission_id=expense_submission, date="2026-09-02",
            seq_no=0, recipient="Vendor", purpose="Supplies", notes="",
            usd_cents=10_00))
    conn.close()

    preview = client.post(
        "/api/reports/2027/generate", json={},
        headers={"X-CSRF": csrf})
    assert preview.status_code == 200
    assert preview.headers["X-Report-Unallocated"] == "1"
    assert report_rows(app) == []
    assert report_files(app) == []

    final = client.post(
        "/api/reports/2027/generate",
        json={"final": True, "submission_id": sid()},
        headers={"X-CSRF": csrf})
    assert final.status_code == 422
    assert final.get_json()["error"]["code"] == "unallocated"
    assert report_rows(app) == []
    assert report_files(app) == []
