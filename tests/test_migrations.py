"""Populated-database migration coverage."""

from __future__ import annotations

from pathlib import Path
import shutil

from cash import repo
from cash.db import apply_migrations, connect


MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def test_superseded_state_migrates_populated_version_four_database(tmp_path):
    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    for version in range(1, 5):
        source = next(MIGRATIONS.glob(f"{version:04d}_*.sql"))
        shutil.copy2(source, old_migrations / source.name)

    connection = connect(tmp_path / "cash.db")
    apply_migrations(connection, old_migrations)
    connection.execute(
        "INSERT INTO transactions (movement_id, submission_id, date,"
        " same_day_seq, txn_type, from_account, to_account, usd_cents,"
        " mxn_centavos) VALUES"
        " ('open','s1','2026-08-01',0,'opening','OPENING_EQUITY',"
        " 'SAFE',10000,0)")
    connection.execute(
        "INSERT INTO transactions (movement_id, submission_id, date,"
        " same_day_seq, txn_type, from_account, to_account, usd_cents,"
        " mxn_centavos, posting_state, void_reason, voided_at) VALUES"
        " ('expense','s2','2026-08-02',0,'expense','SAFE',"
        " 'UNALLOCATED_EXPENSE',1000,0,'void',"
        " 'superseded by final allocations','2026-08-02T00:00:00Z')")
    connection.execute(
        "INSERT INTO transactions (movement_id, submission_id, date,"
        " same_day_seq, allocation_line, txn_type, from_account, to_account,"
        " usd_cents, mxn_centavos, related_txn_id) VALUES"
        " ('expense','s3','2026-08-02',0,1,'expense','SAFE',"
        " 'EXP_SUPPLIES',1000,0,2)")
    connection.execute(
        "INSERT INTO documents (document_id, rel_path, sha256, size_bytes,"
        " state) VALUES ('doc','documents/doc.pdf','hash',1,'filed')")
    connection.execute(
        "INSERT INTO transaction_documents (txn_id, document_id, role)"
        " VALUES (3,'doc','support')")
    connection.execute(
        "INSERT INTO transaction_edits (txn_id, submission_id, reason,"
        " before_json, after_json) VALUES (2,'edit','reason','{}','{}')")

    apply_migrations(connection, MIGRATIONS)
    migrated = connection.execute(
        "SELECT posting_state, void_reason, voided_at FROM transactions"
        " WHERE txn_id=2").fetchone()
    assert tuple(migrated) == ("superseded", None, None)
    assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    assert connection.execute(
        "SELECT txn_id FROM transaction_documents").fetchone()[0] == 3
    assert connection.execute(
        "SELECT txn_id FROM transaction_edits").fetchone()[0] == 2
    assert repo.replay_projected(connection).states["SAFE"].usd == 90_00
    inserted = connection.execute(
        "INSERT INTO transactions (movement_id, submission_id, date,"
        " same_day_seq, txn_type, from_account, to_account, usd_cents,"
        " mxn_centavos) VALUES"
        " ('donation','s4','2026-08-03',0,'donation','DONATION',"
        " 'SAFE',100,0)")
    assert inserted.lastrowid == 4
    connection.close()
