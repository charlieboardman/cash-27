"""Offsite sync: token handling, database downloads, and run tracking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from cash import sync
from cash.web.app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, admin_password="x", testing=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf(client):
    return client.post("/api/login", json={"password": "x"}).get_json()["data"]["csrf"]


@pytest.fixture
def token(client, csrf):
    response = client.post("/api/settings/sync-token", json={}, headers={"X-CSRF": csrf})
    assert response.status_code == 200
    return response.get_json()["data"]["token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_token_is_shown_once_and_stored_only_as_a_hash(app, client, csrf, token):
    stored = (app.config["DATA_ROOT"] / "config" / "sync" / "token.json")
    assert token not in stored.read_text()
    assert stored.stat().st_mode & 0o077 == 0
    settings = client.get("/api/settings").get_json()["data"]["sync"]
    assert settings["configured"] is True
    assert "token" not in json.dumps(settings).replace("token_created_at", "")

    replaced = client.post("/api/settings/sync-token", json={}, headers={"X-CSRF": csrf})
    new_token = replaced.get_json()["data"]["token"]
    assert new_token != token
    anonymous = client.application.test_client()
    assert anonymous.get("/api/sync/database.sql", headers=bearer(token)).status_code == 401
    assert anonymous.get("/api/sync/database.sql", headers=bearer(new_token)).status_code == 200


def test_sync_endpoints_need_the_token_or_an_admin(app, token):
    anonymous = app.test_client()
    for path in ("/api/sync/database.db", "/api/sync/database.sql"):
        assert anonymous.get(path).status_code == 401
        assert anonymous.get(path, headers=bearer("wrong")).status_code == 401
        assert anonymous.get(path, headers={"Authorization": token}).status_code == 401
        assert anonymous.get(path, headers=bearer(token)).status_code == 200
    # The token opens only the sync endpoints.
    assert anonymous.get("/api/settings", headers=bearer(token)).status_code == 401
    assert anonymous.get("/api/export/transactions.csv", headers=bearer(token)).status_code == 401


def test_revoked_token_stops_working(client, csrf, token):
    assert client.delete("/api/settings/sync-token", headers={"X-CSRF": csrf}).status_code == 200
    anonymous = client.application.test_client()
    assert anonymous.get("/api/sync/database.db", headers=bearer(token)).status_code == 401
    assert client.get("/api/settings").get_json()["data"]["sync"]["configured"] is False


def test_database_file_is_standalone_and_includes_the_wal(app, client, csrf, token):
    data_root = Path(app.config["DATA_ROOT"])
    # An open reader keeps the next commit in the WAL instead of cash.db.
    holder = sqlite3.connect(data_root / "cash.db")
    try:
        holder.execute("SELECT 1 FROM accounts").fetchone()
        created = client.post("/api/settings/classes", headers={"X-CSRF": csrf},
                              json={"submission_id": "8a3c1f0e-4b7d-4c2e-9f1a-2b3c4d5e6f70",
                                    "class_code": "INWAL", "name": "Only in WAL"})
        assert created.status_code == 200
        assert (data_root / "cash.db-wal").stat().st_size > 0
        downloaded = app.test_client().get("/api/sync/database.db", headers=bearer(token))
    finally:
        holder.close()
    assert downloaded.status_code == 200
    assert downloaded.headers["Content-Disposition"].endswith(".db")
    # Header bytes 18-19 are 1 in rollback-journal mode and 2 in WAL mode.
    assert downloaded.data[:16] == b"SQLite format 3\x00"
    assert downloaded.data[18:20] == b"\x01\x01"
    assert not list((data_root / "staging").glob(".database-export-*"))
    copy = data_root.parent / "downloaded.db"
    copy.write_bytes(downloaded.data)
    with sqlite3.connect(copy) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("SELECT name FROM classes WHERE class_code='INWAL'"
                            ).fetchone() == ("Only in WAL",)


def test_admin_can_download_the_database_file_from_settings(client, csrf):
    assert b'href="/api/sync/database.db"' in client.get("/settings").data
    response = client.get("/api/sync/database.db")
    assert response.status_code == 200
    assert response.data[:16] == b"SQLite format 3\x00"


def test_completion_is_recorded_and_staleness_warns_admins(app, client, csrf, token):
    data_root = app.config["DATA_ROOT"]
    assert "Drive backup has not completed" not in client.get("/settings").get_data(as_text=True)
    # A session cannot report a run; only the script's token can.
    assert client.post("/api/sync/complete", json={}, headers={"X-CSRF": csrf}).status_code == 401
    done = app.test_client().post("/api/sync/complete", json={}, headers=bearer(token))
    assert done.status_code == 200
    status = client.get("/api/settings").get_json()["data"]["sync"]
    assert status["last_completed_at"] == done.get_json()["data"]["completed_at"]
    assert status["stale"] is False

    old = (datetime.now(timezone.utc) - timedelta(hours=37)).isoformat(timespec="seconds")
    (data_root / "config" / "sync" / "last-sync.json").write_text(json.dumps({"completed_at": old}))
    assert sync.status(data_root)["stale"] is True
    assert "Drive backup has not completed" in client.get("/settings").get_data(as_text=True)


def test_new_token_warns_only_after_a_day_and_a_half_without_a_run(tmp_path):
    sync.create_token(tmp_path)
    assert sync.status(tmp_path)["stale"] is False
    token_file = tmp_path / "config" / "sync" / "token.json"
    stored = json.loads(token_file.read_text())
    stored["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=40)).isoformat()
    token_file.write_text(json.dumps(stored))
    assert sync.status(tmp_path)["stale"] is True
    sync.revoke_token(tmp_path)
    assert sync.status(tmp_path) == {"configured": False, "token_created_at": None,
                                     "last_completed_at": None, "stale": False}


def test_sync_files_are_not_in_full_backups(app, token, tmp_path):
    from cash.ops import backup
    backup(app.config["DATA_ROOT"], tmp_path / "backup")
    assert not list((tmp_path / "backup").rglob("token.json"))
