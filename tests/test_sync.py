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
def token(client, csrf):
    response = client.post("/api/settings/sync-token", json={}, headers={"X-CSRF": csrf})
    assert response.status_code == 200
    return response.get_json()["data"]["sync"]["token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_token_is_shown_to_admins_and_can_be_generated_or_chosen(app, client, csrf, token):
    stored = (app.config["DATA_ROOT"] / "config" / "sync" / "token.json")
    assert json.loads(stored.read_text())["token"] == token
    assert stored.stat().st_mode & 0o077 == 0
    settings = client.get("/api/settings").get_json()["data"]["sync"]
    assert settings["configured"] is True
    assert settings["token"] == token

    chosen = "short!"
    replaced = client.post("/api/settings/sync-token", json={"token": chosen},
                           headers={"X-CSRF": csrf})
    assert replaced.get_json()["data"]["sync"]["token"] == chosen
    anonymous = client.application.test_client()
    assert anonymous.get("/api/sync/database.sql", headers=bearer(token)).status_code == 401
    assert anonymous.get("/api/sync/database.sql", headers=bearer(chosen)).status_code == 200
    for bad in ("has spaces", "", "x" * 257, "line\nbreak"):
        refused = client.post("/api/settings/sync-token", json={"token": bad},
                              headers={"X-CSRF": csrf})
        assert refused.status_code == 422
        assert refused.get_json()["error"]["code"] == "bad_sync_token"
    assert client.get("/api/settings").get_json()["data"]["sync"]["token"] == chosen


def test_a_token_kept_only_as_a_hash_still_works_until_replaced(app, client, csrf):
    import hashlib
    data_root = Path(app.config["DATA_ROOT"])
    (data_root / "config" / "sync").mkdir(parents=True)
    (data_root / "config" / "sync" / "token.json").write_text(json.dumps(
        {"sha256": hashlib.sha256(b"older-token").hexdigest(), "created_at": "2026-09-27T00:00:00+00:00"}))
    assert app.test_client().get("/api/sync/database.sql",
                                 headers=bearer("older-token")).status_code == 200
    status = client.get("/api/settings").get_json()["data"]["sync"]
    assert status["configured"] is True and status["token"] is None


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
    sync.set_token(tmp_path)
    assert sync.status(tmp_path)["stale"] is False
    token_file = tmp_path / "config" / "sync" / "token.json"
    stored = json.loads(token_file.read_text())
    stored["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=40)).isoformat()
    token_file.write_text(json.dumps(stored))
    assert sync.status(tmp_path)["stale"] is True
    sync.revoke_token(tmp_path)
    assert sync.status(tmp_path) == {"configured": False, "token": None, "token_created_at": None,
                                     "last_completed_at": None, "stale": False}


def test_the_token_travels_with_full_backups_but_the_last_run_does_not(app, token, tmp_path):
    from cash.ops import backup, restore
    sync.record_completion(app.config["DATA_ROOT"])
    backup(app.config["DATA_ROOT"], tmp_path / "backup")
    assert not list((tmp_path / "backup").rglob("last-sync.json"))
    restore(tmp_path / "restored", tmp_path / "backup")
    assert sync.token_valid(tmp_path / "restored", token)
    assert sync.status(tmp_path / "restored")["last_completed_at"] is None


def add_document(data_root, conn, name, content, state="inbox"):
    import hashlib
    import uuid
    rel_path = f"documents/{name}.pdf"
    (data_root / rel_path).write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    conn.execute("INSERT INTO documents (document_id, rel_path, sha256, size_bytes, state)"
                 " VALUES (?,?,?,?,?)", (str(uuid.uuid4()), rel_path, digest, len(content), state))
    return rel_path, digest


def test_manifest_lists_the_full_backup_set(app, token):
    from cash.db import connect
    from cash import voucher_defaults
    data_root = Path(app.config["DATA_ROOT"])
    conn = connect(data_root / "cash.db")
    inbox = add_document(data_root, conn, "20260901T000000Z--D000001--inbox", b"%PDF inbox")
    filed = add_document(data_root, conn, "20260902T000000Z--D000002--filed", b"%PDF filed", "filed")
    add_document(data_root, conn, "20260903T000000Z--D000003--gone", b"x", "missing")
    (data_root / "documents" / "20260903T000000Z--D000003--gone.pdf").unlink()
    # A split scan keeps its original, which a restore also needs.
    original = data_root / "documents" / "20260904T000000Z--D000004--scan.pdf"
    original.write_bytes(b"%PDF original scan")
    import hashlib
    conn.execute("INSERT INTO document_processing (document_id, status, source_rel_path,"
                 " source_sha256, captured_at) SELECT document_id, 'complete', ?, ?, ''"
                 " FROM documents WHERE rel_path=?",
                 ("documents/" + original.name, hashlib.sha256(original.read_bytes()).hexdigest(),
                  filed[0]))
    conn.close()
    voucher_defaults.save_default(data_root, "Ana")

    response = app.test_client().get("/api/sync/manifest", headers=bearer(token))
    assert response.status_code == 200
    manifest = response.get_json()["data"]
    listed = {entry["path"]: entry for entry in manifest["files"]}
    assert set(listed) == {inbox[0], filed[0], "documents/" + original.name,
                           "config/voucher-defaults.json", "config/sync/token.json"}
    assert listed[inbox[0]]["sha256"] == inbox[1]
    assert listed[inbox[0]]["name"] == "20260901T000000Z--D000001--inbox.pdf"
    assert listed[inbox[0]]["size"] == len(b"%PDF inbox")
    assert listed["config/voucher-defaults.json"]["kind"] == "config"
    assert listed["config/sync/token.json"]["kind"] == "config"
    assert manifest["problems"] == []
    assert manifest["timezone"] == "America/Los_Angeles"
    local = datetime.now(app.config["TIMEZONE"]).date().isoformat()
    assert manifest["local_date"] == local
    from cash.prep import fiscal_year_for
    assert manifest["fiscal_year"] == fiscal_year_for(local)
    assert manifest["fiscal_label"] == f"FY{fiscal_year_for(local) % 100:02d}"

    (data_root / inbox[0]).unlink()
    manifest = app.test_client().get("/api/sync/manifest", headers=bearer(token)).get_json()["data"]
    assert manifest["problems"] == [f"missing document file: {inbox[0]}"]


def test_manifest_and_files_need_the_token_not_a_session(client, csrf, token):
    assert client.get("/api/sync/manifest").status_code == 401
    assert client.get("/api/sync/files/" + "0" * 64).status_code == 401


def test_listed_files_are_served_by_hash_and_checked(app, token):
    from cash.db import connect
    data_root = Path(app.config["DATA_ROOT"])
    conn = connect(data_root / "cash.db")
    rel_path, digest = add_document(data_root, conn, "20260901T000000Z--D000001--r", b"%PDF r")
    conn.close()
    anonymous = app.test_client()
    served = anonymous.get(f"/api/sync/files/{digest}", headers=bearer(token))
    assert served.status_code == 200
    assert served.data == b"%PDF r"
    assert served.mimetype == "application/pdf"
    assert anonymous.get("/api/sync/files/" + "a" * 64, headers=bearer(token)).status_code == 404
    assert anonymous.get("/api/sync/files/../cash.db", headers=bearer(token)).status_code == 404
    (data_root / rel_path).write_bytes(b"%PDF tampered")
    tampered = anonymous.get(f"/api/sync/files/{digest}", headers=bearer(token))
    assert tampered.status_code == 409
    assert tampered.get_json()["error"]["code"] == "hash_mismatch"


def test_database_signature_matches_across_formats_and_tracks_changes(app, client, csrf, token):
    anonymous = app.test_client()
    first_db = anonymous.get("/api/sync/database.db", headers=bearer(token))
    first_sql = anonymous.get("/api/sync/database.sql", headers=bearer(token))
    signature = first_db.headers["X-Database-Signature"]
    assert len(signature) == 64
    import hashlib
    assert first_db.headers["X-Content-SHA256"] == hashlib.sha256(first_db.data).hexdigest()
    assert first_sql.headers["X-Content-SHA256"] == hashlib.sha256(first_sql.data).hexdigest()
    assert first_sql.headers["X-Database-Signature"] == signature
    assert anonymous.get("/api/sync/database.db", headers=bearer(token)
                         ).headers["X-Database-Signature"] == signature
    client.post("/api/settings/classes", headers={"X-CSRF": csrf},
                json={"submission_id": "3f1c2b4a-5d6e-4f70-8a9b-0c1d2e3f4a5b",
                      "class_code": "NEW", "name": "New"})
    assert anonymous.get("/api/sync/database.db", headers=bearer(token)
                         ).headers["X-Database-Signature"] != signature


def test_timezone_is_configurable(tmp_path):
    app = create_app(tmp_path, admin_password="x", testing=True,
                     timezone_name="Pacific/Kiritimati")
    assert str(app.config["TIMEZONE"]) == "Pacific/Kiritimati"
    token = sync.set_token(tmp_path)
    manifest = app.test_client().get("/api/sync/manifest", headers=bearer(token)).get_json()["data"]
    assert manifest["timezone"] == "Pacific/Kiritimati"
    assert manifest["local_date"] == datetime.now(app.config["TIMEZONE"]).date().isoformat()
