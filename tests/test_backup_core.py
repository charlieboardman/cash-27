from __future__ import annotations

from http.server import HTTPServer
from pathlib import Path
import threading

import pytest

from cash.backup_client import create_archive
from cash.backup_core_service import BackupHandler
from cash.db import apply_migrations, connect


MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def test_private_backup_core_builds_verified_archive(monkeypatch, tmp_path):
    data = tmp_path / "data"
    state = data / "backups" / "drive"
    data.mkdir()
    state.mkdir(parents=True)
    connection = connect(data / "cash.db")
    apply_migrations(connection, MIGRATIONS)
    connection.close()

    server = HTTPServer(("127.0.0.1", 0), BackupHandler)
    server.token = "a" * 64
    server.data_root = data
    server.state_root = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "CASH_BACKUP_CORE_URL",
        f"http://127.0.0.1:{server.server_port}/archive")
    monkeypatch.setenv("CASH_BACKUP_CORE_TOKEN", "a" * 64)
    monkeypatch.setenv("CASH_BACKUP_CORE_ATTEMPTS", "1")
    name = "cash-27-full-20260904T120000123456Z.tar.gz"
    try:
        result = create_archive("outbox", name)
        assert result["integrity"] == "ok"
        assert (state / "outbox" / name).is_file()

        monkeypatch.setenv("CASH_BACKUP_CORE_TOKEN", "wrong")
        with pytest.raises(SystemExit, match="HTTP 403"):
            create_archive("outbox", name)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
