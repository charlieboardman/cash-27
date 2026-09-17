"""Fresh schema initialization and rejection of the pre-reset schema history."""

from pathlib import Path
import sqlite3

import pytest

from cash.db import apply_migrations, connect

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def test_fresh_schema_contains_only_current_money_fields(tmp_path):
    conn = connect(tmp_path / "cash.db")
    apply_migrations(conn, MIGRATIONS)
    voucher_fields = {r[1] for r in conn.execute("PRAGMA table_info(vouchers)")}
    assert {"authorized_usd_cents", "authorized_mxn_centavos"} <= voucher_fields
    assert not any("face" in name or "paid" in name or "change" in name for name in voucher_fields)
    for table in ("receipts", "signout_expense_claims", "signout_claim_allocations"):
        fields = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert {"face_usd_cents", "face_mxn_centavos"} <= fields
        assert not {"face_value_minor", "face_currency"} & fields
    fields = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    assert not {"receipt_face_minor", "receipt_face_currency"} & fields
    assert conn.execute("SELECT COUNT(*) FROM signout_closeouts").fetchone()[0] == 0
    assert conn.execute("SELECT last_value FROM document_id_sequence").fetchone()[0] == 0
    assert conn.execute("SELECT ledger_revision FROM ledger_state").fetchone()[0] == 0
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.execute("UPDATE document_id_sequence SET last_value=42")
    apply_migrations(conn, MIGRATIONS)
    assert conn.execute("SELECT last_value FROM document_id_sequence").fetchone()[0] == 42
    assert [r[0] for r in conn.execute("SELECT version FROM schema_migrations")] == [10, 11, 12, 13, 14]
    conn.close()


def test_pre_reset_schema_is_not_upgraded(tmp_path):
    conn = connect(tmp_path / "cash.db")
    conn.executescript("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);"
                       "INSERT INTO schema_migrations VALUES (9, 'before reset');")
    with pytest.raises(RuntimeError, match="fresh installation"):
        apply_migrations(conn, MIGRATIONS)
    assert [r[0] for r in conn.execute("SELECT version FROM schema_migrations")] == [9]
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='vouchers'").fetchone() is None
    conn.close()


def test_failed_schema_change_rolls_back_with_its_version(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0010_initial.sql").write_text("CREATE TABLE example (id INTEGER); SELECT missing;")
    conn = connect(tmp_path / "cash.db")
    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(conn, migrations)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='example'").fetchone() is None
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    conn.close()


def test_processing_migration_leaves_existing_documents_ready(tmp_path):
    old = tmp_path / 'old-migrations'
    old.mkdir()
    for version in ('0010_initial.sql', '0011_document_qr.sql', '0012_expense_reports.sql'):
        (old / version).write_text((MIGRATIONS / version).read_text())
    conn = connect(tmp_path / 'cash.db')
    apply_migrations(conn, old)
    conn.execute("INSERT INTO documents (document_id, rel_path, sha256, size_bytes, state)"
                 " VALUES ('D000001', 'documents/original.pdf', 'original-hash', 100, 'inbox')")
    before = tuple(conn.execute('SELECT * FROM documents').fetchone())
    apply_migrations(conn, MIGRATIONS)
    assert tuple(conn.execute('SELECT * FROM documents').fetchone()) == before
    assert conn.execute('SELECT COUNT(*) FROM document_processing').fetchone()[0] == 0
    conn.close()


def test_report_type_migration_preserves_existing_snapshot_metadata(tmp_path):
    old = tmp_path / 'old-migrations'
    old.mkdir()
    for path in MIGRATIONS.glob('*.sql'):
        if not path.name.startswith('0014'):
            (old / path.name).write_text(path.read_text())
    conn = connect(tmp_path / 'cash.db')
    apply_migrations(conn, old)
    conn.execute("INSERT INTO report_runs (period, revision, ledger_revision, generated_path, sha256)"
                 " VALUES ('FY2027', 1, 10, 'reports/FY2027-r1.csv', 'saved-hash')")
    before = dict(conn.execute('SELECT * FROM report_runs').fetchone())
    apply_migrations(conn, MIGRATIONS)
    after = dict(conn.execute('SELECT * FROM report_runs').fetchone())
    assert after.pop('export_type') == 'expenses'
    assert after == before
    conn.close()
