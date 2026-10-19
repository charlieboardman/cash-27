"""The recipient's expense report page, sign-in, uploads, and cash activity."""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from cash import signout_recipients
from cash.commands import run_command
from cash.db import connect
from cash.prep import (create_opening, prepare_addendum, prepare_direct_expense,
                       prepare_signout)
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
    client = app.test_client()
    response = client.post(
        "/api/user-login", json={"password": "x", "next": f"/{voucher_id}"})
    assert response.status_code == 200
    return client, app, voucher_id


def test_signee_workspace_requires_shared_password(tmp_path):
    app = create_app(tmp_path, admin_password="admin", user_password="staff",
                     testing=True)
    conn = connect(tmp_path / "cash.db")
    run_command(conn, sid(), "opening", lambda db: create_opening(
        db, submission_id=sid(), date="2026-08-01", usd_cents=100_00))
    voucher_id = run_command(conn, sid(), "signout", lambda db: prepare_signout(
        db, submission_id=sid(), date="2026-08-02", seq_no=None,
        recipient="Ana", purpose="Travel", notes="", usd_cents=50_00
    ))["voucher_id"]
    conn.close()
    client = app.test_client()
    page = client.get(f"/{voucher_id}")
    assert page.status_code == 302
    assert page.location.endswith(f"/user-login?next=/{voucher_id}")
    assert client.get(f"/{voucher_id}/api/report").status_code == 401
    login = client.post("/api/user-login", json={
        "password": "staff", "next": f"/{voucher_id}"})
    assert login.status_code == 200
    assert login.get_json()["data"]["next_url"] == f"/{voucher_id}"
    assert client.get(f"/{voucher_id}").status_code == 200


def test_recipient_and_administrator_share_one_report_page(workflow):
    client, app, voucher_id = workflow
    page = client.get(f"/{voucher_id}").get_data(as_text=True)
    assert 'id="expense-report"' in page and 'data-admin="false"' in page
    assert 'expense-report.js' in page and "data-language" in page
    assert 'id="er-approve"' not in page and "Paperwork" not in page
    assert 'class="topnav"' not in page

    admin = app.test_client()
    admin.post("/api/login", json={"password": "x"})
    page = admin.get(f"/{voucher_id}").get_data(as_text=True)
    assert 'data-admin="true"' in page and 'id="er-approve"' in page
    assert "Paperwork" not in page and 'class="topnav"' in page
    assert 'data-language="es"' in page       # the same language toggle
    assert admin.get(f"/signouts/{voucher_id}").status_code == 404


def test_recipient_index_groups_aliases_and_links_from_the_report(workflow):
    client, app, voucher_id = workflow
    data_root = app.config["DATA_ROOT"]
    signout_recipients.save(data_root, [{
        "slug": "ana", "name": "Ana",
        "aliases": ["Ana Maria"],
    }])
    conn = connect(data_root / "cash.db")
    second = run_command(
        conn, sid(), "signout",
        lambda db: prepare_signout(
            db, submission_id=sid(), date="2026-08-03", seq_no=None,
            recipient="Ana Maria", purpose="Travel", notes="",
            mxn_centavos=100_00))["voucher_id"]
    direct = run_command(
        conn, sid(), "direct",
        lambda db: prepare_direct_expense(
            db, submission_id=sid(), date="2026-08-03", seq_no=None,
            recipient="Ana Maria", purpose="Vendor", notes="",
            mxn_centavos=50_00))["voucher_id"]
    conn.execute("UPDATE vouchers SET status='void' WHERE voucher_id=?", (second,))
    conn.close()

    page = client.get("/signouts/ana")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert voucher_id in text and second in text
    assert direct not in text
    assert "USD" not in text and "MXN" not in text
    assert 'data-i18n="Signouts"' in text and "data-language" in text
    assert 'data-i18n="Open"' in text and 'data-i18n="Void"' in text

    # Both languages are first-class URLs.
    spanish = client.get("/entregas/ana")
    assert spanish.status_code == 200
    assert voucher_id in spanish.get_data(as_text=True)

    # A typed alias or case redirects to the canonical slug, keeping the language.
    alias = client.get("/entregas/ana-maria")
    assert alias.status_code == 301
    assert alias.location == "/entregas/ana"
    upper = client.get("/signouts/ANA")
    assert upper.status_code == 301
    assert upper.location == "/signouts/ana"
    accented = client.get("/signouts/Ana-Mar%C3%ADa")
    assert accented.status_code == 301
    assert accented.location == "/signouts/ana"

    report = client.get(f"/{voucher_id}").get_data(as_text=True)
    assert 'href="/signouts/ana"' in report
    assert 'data-href-es="/entregas/ana"' in report
    assert 'data-i18n="All signouts"' in report


def test_recipient_index_requires_shared_password_and_preserves_the_prefix(
        workflow):
    client, app, voucher_id = workflow
    anonymous = app.test_client()
    page = anonymous.get("/signouts/ana")
    assert page.status_code == 302
    assert page.location.endswith("/user-login?next=/signouts/ana")

    login = anonymous.post("/api/user-login", json={
        "password": "x", "next": "/entregas/ana"})
    assert login.status_code == 200
    assert login.get_json()["data"]["next_url"] == "/entregas/ana"
    assert anonymous.get("/entregas/ana").status_code == 200
    assert anonymous.get("/signouts/ana").status_code == 200

    other = app.test_client()
    rejected = other.post("/api/user-login", json={
        "password": "x", "next": "//example.com/signouts/ana"})
    assert rejected.get_json()["data"]["next_url"] == "/"


def test_recipient_index_shows_an_empty_page_for_a_configured_person(workflow):
    client, app, voucher_id = workflow
    signout_recipients.save(app.config["DATA_ROOT"], [{
        "slug": "nobody", "name": "Nobody", "aliases": [],
    }])
    page = client.get("/signouts/nobody")
    assert page.status_code == 200
    assert "No signouts yet." in page.get_data(as_text=True)
    assert client.get("/signouts/unknown").status_code == 404


def test_a_corrupt_recipient_config_falls_back_to_derived_names(workflow):
    client, app, voucher_id = workflow
    path = app.config["DATA_ROOT"] / "config" / "signout-recipients.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    page = client.get("/signouts/ana")
    assert page.status_code == 200
    assert voucher_id in page.get_data(as_text=True)
    report = client.get(f"/{voucher_id}")
    assert report.status_code == 200
    assert 'href="/signouts/ana"' in report.get_data(as_text=True)


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

    state = client.get(f"/{voucher_id}/api/report").get_json()["data"]
    assert state["balance"]["mxn"] == "700.00"
    assert [(item["label"], item["mxn"]) for item in state["activity"]] == [
        ("Originally received", "500.00"),
        ("Additional cash received", "200.00"),
    ]
    assert state["activity"][1]["detail"] == "More supplies needed"


def test_ordered_receipt_photos_become_one_multipage_pdf(workflow):
    client, app, voucher_id = workflow
    csrf = client.get(f"/{voucher_id}/api/report").get_json()["data"]["csrf"]
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
        f"/{voucher_id}/documents/{document_id}/document.pdf", follow_redirects=True)
    assert streamed.headers['Content-Disposition'] == f'inline; filename={document_id}.pdf'
    assert client.get(f'/{voucher_id}/documents/{document_id}/stream').status_code == 404
    assert client.get(f'/{voucher_id}/documents/unknown/name.pdf').status_code == 404
    # A valid signee session still cannot read a different voucher's document.
    with connect(app.config['DATA_ROOT'] / 'cash.db') as db:
        db.execute('UPDATE documents SET voucher_id=NULL WHERE document_id=?', (document_id,))
    denied = client.get(f'/{voucher_id}/documents/{document_id}/name.pdf')
    assert denied.status_code == 404 and 'Location' not in denied.headers
    pages = PdfReader(io.BytesIO(streamed.data)).pages
    assert float(pages[0].mediabox.height) > float(pages[0].mediabox.width)
    assert float(pages[1].mediabox.width) > float(pages[1].mediabox.height)


def test_voucher_page_url_is_case_insensitive(workflow):
    client, app, voucher_id = workflow
    for typed in (voucher_id.lower(), "Fy" + voucher_id[2:]):
        response = client.get(f"/{typed}?next=1")
        assert response.status_code == 301
        assert response.location == f"/{voucher_id}?next=1"
    assert client.get(f"/{voucher_id}").status_code in (200, 302)
    assert client.get(f"/{voucher_id.lower()}/api/report").status_code == 404


def test_every_visible_word_on_the_report_page_has_a_spanish_translation():
    """Templates mark their words with data-i18n; each must be in the dictionary."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "cash"
    dictionary = (root / "static/js/i18n.js").read_text()
    known = {m.group(1) for m in re.finditer(r'^\s*"([^"\n]+)":', dictionary, re.M)}
    missing = []
    for name in ("expense_report.html", "signout_recipients.html",
                 "user_login.html"):
        text = (root / "templates" / name).read_text()
        for key in re.findall(r'data-i18n(?:-aria-label)?="([^"]+)"', text):
            if key not in known:
                missing.append((name, key))
    assert not missing, missing
