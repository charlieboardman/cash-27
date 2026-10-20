from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cash import local_backups
from cash.db import apply_migrations, connect
from cash.ops import restore_archive


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def live(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    connection = connect(data / "cash.db")
    apply_migrations(connection, PROJECT_ROOT / "migrations")
    document = data / "documents" / "receipt.pdf"
    document.parent.mkdir()
    document.write_bytes(b"%PDF-1.4 example")
    connection.execute(
        "INSERT INTO documents (document_id, rel_path, sha256, size_bytes,"
        " state) VALUES ('doc-1', 'documents/receipt.pdf', ?, ?, 'filed')",
        (hashlib.sha256(document.read_bytes()).hexdigest(), document.stat().st_size))
    # Keep this live WAL connection open during archive creation.
    yield data, connection
    connection.close()


def test_local_archive_restores_database_and_documents(live, tmp_path):
    data, connection = live
    connection.execute("INSERT INTO classes (class_code, name) VALUES ('NEW', 'New')")
    result = local_backups.create_local_archive(data)
    archive = Path(result["archive"])
    assert archive.stat().st_mode & 0o777 == 0o600
    assert result["integrity"] == "ok"
    restored = tmp_path / "restored"
    restore_archive(restored, archive)
    assert (restored / "documents/receipt.pdf").read_bytes() == \
        (data / "documents/receipt.pdf").read_bytes()
    conn = connect(restored / "cash.db")
    try:
        assert conn.execute("SELECT name FROM classes WHERE class_code='NEW'").fetchone()[0] == "New"
    finally:
        conn.close()


def test_retention_is_separate_for_each_job_and_preserves_other_recovery_files(live):
    data, _ = live
    first = Path(local_backups.create_local_archive(data, kind="scheduled", keep=2)["archive"])
    second = Path(local_backups.create_local_archive(data, kind="scheduled", keep=2)["archive"])
    upgrade = Path(local_backups.create_local_archive(data, kind="pre-upgrade", keep=1)["archive"])
    unrelated = first.parent / "keep-this-manually.tar.gz"
    unrelated.write_bytes(b"manual recovery material")
    safety = data / "backups" / "before-import.sql"
    safety.write_bytes(b"SQL")
    third = Path(local_backups.create_local_archive(data, kind="scheduled", keep=2)["archive"])
    assert not first.exists()
    assert second.exists() and third.exists() and upgrade.exists()
    assert unrelated.read_bytes() == b"manual recovery material"
    assert safety.exists()


def test_failed_verification_never_publishes_or_rotates(live, monkeypatch):
    data, _ = live
    previous = Path(local_backups.create_local_archive(data, kind="scheduled", keep=1)["archive"])

    def reject(pending):
        assert list(previous.parent.glob("*.tar.gz")) == [previous]
        raise SystemExit("verification failed")

    monkeypatch.setattr(local_backups, "verify_archive", reject)
    with pytest.raises(SystemExit, match="verification failed"):
        local_backups.create_local_archive(data, kind="scheduled", keep=1)
    assert list(previous.parent.glob("*.tar.gz")) == [previous]
    assert not list(previous.parent.parent.glob(".building-*"))


def test_loop_retries_when_database_not_ready_and_then_uses_normal_interval(live, monkeypatch):
    data, _ = live
    create = local_backups.create_local_archive
    sleeps = []

    def attempt(*args, **kwargs):
        if not sleeps:
            raise SystemExit("database not ready")
        return create(*args, **kwargs)

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(local_backups, "create_local_archive", attempt)
    monkeypatch.setattr(local_backups.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        local_backups.run_loop(data, interval=3600, retry=60, keep=1)
    assert sleeps == [60, 3600]
    assert len(list((data / "backups/local/scheduled").glob("*.tar.gz"))) == 1


def test_missing_database_is_not_silently_created(tmp_path):
    with pytest.raises(Exception):
        local_backups.create_local_archive(tmp_path)
    assert not (tmp_path / "cash.db").exists()
    assert not list((tmp_path / "backups/local/manual").glob("*.tar.gz"))


def test_health_requires_recent_scheduled_archive(live):
    data, _ = live
    env = {**os.environ, "CASH_DATA_ROOT": str(data)}

    def health():
        return subprocess.run(
            [sys.executable, "-m", "cash.local_backups", "health"],
            cwd=PROJECT_ROOT, env=env, capture_output=True).returncode

    assert health() == 1
    local_backups.create_local_archive(data, kind="pre-upgrade")
    assert health() == 1
    archive = Path(local_backups.create_local_archive(data, kind="scheduled")["archive"])
    assert health() == 0
    os.utime(archive, (1, 1))
    assert health() == 1


@pytest.mark.parametrize("value", ["0", "bad"])
def test_bad_retention_fails_before_archiving(live, value):
    data, _ = live
    result = subprocess.run(
        [sys.executable, "-m", "cash.local_backups", "once"],
        cwd=PROJECT_ROOT, env={**os.environ, "CASH_DATA_ROOT": str(data),
                              "CASH_LOCAL_BACKUP_MANUAL_KEEP": value},
        capture_output=True, text=True)
    assert result.returncode != 0
    assert "must be a positive integer" in result.stderr
    assert not (data / "backups/local").exists()
