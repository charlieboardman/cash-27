"""Administrator-managed allocation settings."""

from __future__ import annotations

import io
from pathlib import Path
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


def test_backup_health_and_confirmed_disable_control(tmp_path):
    app = create_app(tmp_path, admin_password="x", backup_expected=True,
                     testing=True)
    client = app.test_client()
    csrf = client.post(
        "/api/login", json={"password": "x"}
    ).get_json()["data"]["csrf"]

    settings = client.get("/api/settings").get_json()["data"]
    assert settings["backups"]["state"] == "not_configured"
    assert b"Backup warning" in client.get("/ledger").data

    rejected = client.post(
        "/api/settings/backups/automatic",
        json={"enabled": False}, headers={"X-CSRF": csrf})
    assert rejected.status_code == 422

    disabled = client.post(
        "/api/settings/backups/automatic",
        json={"enabled": False,
              "confirmation": "TURN OFF AUTOMATIC BACKUPS"},
        headers={"X-CSRF": csrf})
    assert disabled.status_code == 200
    assert disabled.get_json()["data"]["state"] == "disabled"

    enabled = client.post(
        "/api/settings/backups/automatic",
        json={"enabled": True}, headers={"X-CSRF": csrf})
    assert enabled.status_code == 200
    assert enabled.get_json()["data"]["enabled"] is True


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


def test_database_export_validate_and_activate_roundtrip(client, csrf):
    before = post(client, csrf, "/api/settings/classes",
                  class_code="BEFORE", name="Before export")
    assert before.status_code == 200

    exported = client.post(
        "/api/settings/database/export", headers={"X-CSRF": csrf})
    assert exported.status_code == 200
    assert exported.headers["Content-Disposition"].endswith(".sql")
    assert b"cash-27 logical database export" in exported.data
    assert b"Before export" in exported.data

    after = post(client, csrf, "/api/settings/classes",
                 class_code="AFTER", name="After export")
    assert after.status_code == 200

    validated = client.post(
        "/api/settings/database/import/validate",
        data={"database_export": (io.BytesIO(exported.data), "ledger.sql")},
        content_type="multipart/form-data", headers={"X-CSRF": csrf})
    assert validated.status_code == 200, validated.get_json()
    staged = validated.get_json()["data"]
    assert staged["summary"]["schema_version"] == 7

    rejected = client.post(
        "/api/settings/database/import/activate",
        json={"import_id": staged["import_id"],
              "confirmation": "REPLACE DATABASE",
              "admin_password": "wrong"}, headers={"X-CSRF": csrf})
    assert rejected.status_code == 422
    assert rejected.get_json()["error"]["code"] == "password_required"

    activated = client.post(
        "/api/settings/database/import/activate",
        json={"import_id": staged["import_id"],
              "confirmation": "REPLACE DATABASE",
              "admin_password": "x"}, headers={"X-CSRF": csrf})
    assert activated.status_code == 200, activated.get_json()
    result = activated.get_json()["data"]
    data_root = Path(client.application.config["DATA_ROOT"])
    assert (data_root / result["previous_database"]).is_file()
    assert (data_root / result["previous_database"]).with_name(
        "import.json").is_file()

    settings = client.get("/api/settings").get_json()["data"]
    codes = {item["class_code"] for item in settings["classes"]}
    assert "BEFORE" in codes
    assert "AFTER" not in codes


def test_database_import_rejects_changed_schema(client, csrf):
    exported = client.post(
        "/api/settings/database/export", headers={"X-CSRF": csrf})
    changed = exported.data.replace(
        b"CREATE TABLE classes", b"CREATE TABLE altered_classes", 1)
    validated = client.post(
        "/api/settings/database/import/validate",
        data={"database_export": (io.BytesIO(changed), "changed.sql")},
        content_type="multipart/form-data", headers={"X-CSRF": csrf})
    assert validated.status_code == 422
    assert validated.get_json()["error"]["code"] == "invalid_database_export"
    staging = Path(client.application.config["DATA_ROOT"]) / "staging"
    assert not list(staging.glob("database-import-*"))


def test_database_import_rejects_malformed_sql_without_leaving_staging(client,
                                                                        csrf):
    validated = client.post(
        "/api/settings/database/import/validate",
        data={"database_export": (io.BytesIO(b"this is not SQL"), "bad.sql")},
        content_type="multipart/form-data", headers={"X-CSRF": csrf})
    assert validated.status_code == 422
    assert validated.get_json()["error"]["code"] == "invalid_database_export"
    staging = Path(client.application.config["DATA_ROOT"]) / "staging"
    assert not list(staging.glob("database-import-*"))
