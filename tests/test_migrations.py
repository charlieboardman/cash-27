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
    assert [r[0] for r in conn.execute("SELECT version FROM schema_migrations")] == [10, 11, 12, 13, 14, 15]
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


def _newer_database(tmp_path):
    conn = connect(tmp_path / "cash.db")
    apply_migrations(conn, MIGRATIONS)
    newest = max(int(path.name.split("_")[0]) for path in MIGRATIONS.glob("*.sql"))
    conn.execute("CREATE TABLE added_by_newer_release (id INTEGER)")
    conn.execute("INSERT INTO schema_migrations VALUES (?, 'newer release')", (newest + 1,))
    return conn, newest + 1


def test_startup_tolerates_migrations_from_a_newer_release(tmp_path):
    """upgrade.sh rolls back to the prior image after a new migration ran."""
    conn, newer = _newer_database(tmp_path)
    apply_migrations(conn, MIGRATIONS, allow_newer=True)
    assert newer in {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    conn.close()


def test_imports_and_restores_reject_a_newer_release_without_powerwash_advice(tmp_path):
    conn, newer = _newer_database(tmp_path)
    with pytest.raises(RuntimeError, match="newer cash-27 release") as raised:
        apply_migrations(conn, MIGRATIONS)
    assert "powerwash" not in str(raised.value)
    assert str(newer) in str(raised.value)
    conn.close()


def test_expense_report_drafts_are_additive_and_reject_invalid_json(tmp_path):
    conn = connect(tmp_path / "cash.db")
    apply_migrations(conn, MIGRATIONS)
    columns = {r[1]: r for r in conn.execute("PRAGMA table_info(expense_report_drafts)")}
    assert set(columns) == {"voucher_id", "version", "draft_json", "updated_at"}
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute("INSERT INTO expense_report_drafts (voucher_id, draft_json)"
                     " VALUES ('FY27-0001', 'not json')")
    conn.close()
