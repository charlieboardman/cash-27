"""Administrator-managed allocation settings."""

from __future__ import annotations

import uuid

import pytest

from cash.web.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, admin_password="x", testing=True)
    return app.test_client()


@pytest.fixture
def csrf(client):
    return client.post(
        "/api/login", json={"password": "x"}
    ).get_json()["data"]["csrf"]


def post(client, csrf, path, **data):
    return client.post(
        path, json={"submission_id": str(uuid.uuid4()), **data},
        headers={"X-CSRF": csrf})


def test_settings_page_and_account_lifecycle(client, csrf):
    assert client.get("/settings").status_code == 200
    created = post(
        client, csrf, "/api/settings/expense-accounts",
        account_id="exp_travel", name="Travel")
    assert created.status_code == 200
    assert created.get_json()["data"]["account_id"] == "EXP_TRAVEL"

    duplicate = post(
        client, csrf, "/api/settings/expense-accounts",
        account_id="EXP_TRAVEL", name="Duplicate")
    assert duplicate.status_code == 422
    assert duplicate.get_json()["error"]["code"] == "code_exists"

    toggled = post(
        client, csrf,
        "/api/settings/expense-account/EXP_TRAVEL/toggle")
    assert toggled.get_json()["data"]["active"] is False
    settings = client.get("/api/settings").get_json()["data"]
    account = next(
        item for item in settings["expense_accounts"]
        if item["account_id"] == "EXP_TRAVEL")
    assert account["active"] == 0


def test_class_lifecycle_and_reserved_account_protection(client, csrf):
    created = post(
        client, csrf, "/api/settings/classes",
        class_code="program", name="Program services")
    assert created.status_code == 200
    assert created.get_json()["data"]["class_code"] == "PROGRAM"

    duplicate = post(
        client, csrf, "/api/settings/classes",
        class_code="PROGRAM", name="Duplicate")
    assert duplicate.status_code == 422

    reserved = post(
        client, csrf,
        "/api/settings/expense-account/SAFE_USD/toggle")
    assert reserved.status_code == 422
    assert reserved.get_json()["error"]["code"] == "not_found"
