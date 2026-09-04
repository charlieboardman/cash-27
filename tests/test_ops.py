"""Backup/restore integration: online backup of a live db, restore,
integrity + replay verification (spec 17)."""

import hashlib
import json
import tarfile
import uuid
from pathlib import Path

import pytest

from cash.commands import run_command
from cash.db import connect, apply_migrations
from cash.ops import backup, backup_archive, restore, verify_backup_manifest
from cash.prep import create_opening, prepare_signout
from cash import repo

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def test_backup_restore_roundtrip(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    conn = connect(live / "cash.db")
    apply_migrations(conn, MIGRATIONS)
    run_command(conn, str(uuid.uuid4()), "open",
                lambda c: create_opening(c, submission_id=str(uuid.uuid4()),
                                         date="2026-08-01",
                                         usd_cents=500_00,
                                         mxn_centavos=1550_00,
                                         assigned_rate_milli=15500))
    run_command(conn, str(uuid.uuid4()), "prep",
                lambda c: prepare_signout(c, submission_id=str(uuid.uuid4()),
                                          date="2026-08-02", seq_no=0,
                                          recipient="Ana", purpose="",
                                          notes="", usd_cents=100_00))
    report_path = live / "reports" / "FY2026-r1.csv"
    report_path.parent.mkdir()
    report_path.write_text("txn_id,date\n1,2026-08-01\n")
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    ledger_revision = conn.execute(
        "SELECT ledger_revision FROM ledger_state WHERE singleton_id=1"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO report_runs (period, revision, ledger_revision,"
        " generated_path, sha256) VALUES ('FY2026', 1, ?, ?, ?)",
        (ledger_revision, "reports/FY2026-r1.csv", digest))
    # still live with the connection open: online backup
    out = backup(live, tmp_path / "bak")
    assert out["files_copied"] == 0  # no PDFs yet
    assert out["reports_copied"] == 1
    assert out["reports_verified"] == 1
    conn.close()

    r = restore(tmp_path / "restored", tmp_path / "bak")
    assert r["integrity"] == "ok"
    assert r["movements"] == 2
    assert r["restored_reports"] == 1
    assert hashlib.sha256(
        (tmp_path / "restored" / "reports" / "FY2026-r1.csv").read_bytes()
    ).hexdigest() == digest

    # restored db replays identically
    live_c = connect(live / "cash.db")
    live_res = repo.replay_projected(live_c)
    rest_c = connect(tmp_path / "restored" / "cash.db")
    rest_res = repo.replay_projected(rest_c)
    assert live_res.states == rest_res.states
    assert live_res.expense_value == rest_res.expense_value


def test_backup_archive_contains_database_files_reports_and_manifest(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    conn = connect(live / "cash.db")
    apply_migrations(conn, MIGRATIONS)
    document = live / "documents" / "receipt.pdf"
    document.parent.mkdir()
    document.write_bytes(b"%PDF-1.4 example")
    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    conn.execute(
        "INSERT INTO documents (document_id, rel_path, sha256, size_bytes,"
        " state) VALUES ('doc-1', 'documents/receipt.pdf', ?, ?, 'filed')",
        (digest, document.stat().st_size))
    conn.close()

    archive_path = tmp_path / "out" / "cash-27-full.tar.gz"
    result = backup_archive(live, archive_path)

    assert result["archive"] == str(archive_path)
    assert result["sha256"] == hashlib.sha256(
        archive_path.read_bytes()).hexdigest()
    with tarfile.open(archive_path, "r:gz") as archive:
        names = archive.getnames()
        assert "cash.db" in names
        assert any(name.startswith("files/") for name in names)
        manifest = json.load(archive.extractfile("manifest.json"))
    assert manifest["format"] == "cash-27-full-backup-v1"
    assert {item["path"] for item in manifest["files"]} >= {
        "cash.db", f"files/{digest[:2]}/{digest}"}

    extracted = tmp_path / "extracted"
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(extracted)
    assert verify_backup_manifest(extracted)["format"] == \
        "cash-27-full-backup-v1"
    restored = restore(tmp_path / "restored", extracted)
    assert restored["integrity"] == "ok"

    (extracted / "cash.db").write_bytes(b"damaged")
    with pytest.raises(SystemExit, match="mismatch"):
        verify_backup_manifest(extracted)


def test_backup_rejects_missing_or_changed_referenced_document(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    conn = connect(live / "cash.db")
    apply_migrations(conn, MIGRATIONS)
    conn.execute(
        "INSERT INTO documents (document_id, rel_path, sha256, size_bytes,"
        " state) VALUES ('missing', 'documents/missing.pdf', 'abc', 10,"
        " 'filed')")
    conn.close()

    with pytest.raises(SystemExit, match="backup document missing"):
        backup(live, tmp_path / "missing-backup")
