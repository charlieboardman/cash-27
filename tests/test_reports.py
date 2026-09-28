"""The Reports page and its downloads. Nothing is kept on the server."""

from __future__ import annotations

import csv
import io
import re
import uuid

import pytest

from cash.commands import run_command
from cash.db import connect
from cash.prep import create_opening, fiscal_year_for, prepare_direct_expense
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


def test_fiscal_year_uses_september_start_and_ending_year():
    assert fiscal_year_for("2026-08-31") == 2026
    assert fiscal_year_for("2026-09-01") == 2027
    assert fiscal_year_for("2027-08-31") == 2027
    assert fiscal_year_for("2027-09-01") == 2028


def test_reports_page_requires_login_and_offers_years_from_first_transaction(
        app, client, csrf):
    fresh_app = create_app(client.application.config["DATA_ROOT"] / "other",
                           admin_password="x", testing=True)
    assert fresh_app.test_client().get("/reports").status_code == 302
    response = client.get("/reports")
    assert response.status_code == 200
    assert b'data-first-fy=""' in response.data
    assert b'data-fy-start-month="9"' in response.data
    assert b"Save final report" not in response.data
    assert b"awaiting review" in response.data
    open_safe(app)  # 2026-09-01 is the first day of FY2027.
    assert b'data-first-fy="2027"' in client.get("/reports").data


def test_download_is_timestamped_and_nothing_is_kept(app, client, csrf):
    open_safe(app)

    response = client.get("/api/export/transactions.csv?fy=2027")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert re.search(r'filename=FY2027-expenses-\d{8}T\d{6}Z\.csv',
                     response.headers["Content-Disposition"])
    assert response.headers["X-Ledger-Revision"] == "1"
    assert response.headers["X-Through-Txn-Id"] == "1"
    assert response.headers["X-Report-Unallocated"] == "0"
    # Opening cash is replay input, not an expense for the bookkeeper.
    assert list(csv.DictReader(io.StringIO(response.data.decode()))) == []
    assert list((app.config["DATA_ROOT"] / "reports").iterdir()) == []
    everything = client.get("/api/export/transactions.csv")
    assert re.search(r'filename=all-years-expenses-\d{8}T\d{6}Z\.csv',
                     everything.headers["Content-Disposition"])


def test_download_warns_about_unallocated_expense(app, client, csrf):
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

    response = client.get("/api/export/transactions.csv?fy=2027")
    assert response.status_code == 200
    assert response.headers["X-Report-Unallocated"] == "1"
    assert client.get("/api/export/transactions.csv?fy=2028"
                      ).headers["X-Report-Unallocated"] == "0"


@pytest.mark.parametrize("fy", ["27", "abc", "1999"])
def test_download_rejects_bad_fiscal_year(client, csrf, fy):
    response = client.get(f"/api/export/transactions.csv?fy={fy}")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "bad_fy"


def test_finalized_report_endpoints_are_gone(client, csrf):
    assert client.post("/api/reports/2027/generate", json={},
                       headers={"X-CSRF": csrf}).status_code == 404
    assert client.get("/api/reports").status_code == 404
