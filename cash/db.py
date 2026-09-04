"""SQLite connection and migration runner (spec 6: stdlib sqlite3,
WAL, synchronous=FULL, foreign keys, numbered migrations, busy
timeout, BEGIN IMMEDIATE for mutating commands)."""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_MIGRATIONS = "schema_migrations"


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None,
                           detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open a database snapshot source without requiring a writable mount."""
    path = Path(db_path).resolve()
    conn = sqlite3.connect(
        f"{path.as_uri()}?mode=ro", uri=True, isolation_level=None,
        detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


SYSTEM_ACCOUNTS = [
    ("SAFE", "Physical safe", "safe"),
    ("BANK", "Bank", "bank"),
    ("OPENING_EQUITY", "Opening equity", "opening_equity"),
    ("DONATION", "Donations", "donation"),
    ("RECONCILIATION", "Reconciliation", "reconciliation"),
    ("FX_COUNTERPARTY", "FX counterparty", "fx_counterparty"),
    ("UNALLOCATED_EXPENSE", "Unallocated expense", "expense"),
    ("EXP_SUPPLIES", "Supplies", "expense"),
    ("EXP_PROGRAMS", "Programs", "expense"),
]


def seed_system_accounts(conn: sqlite3.Connection):
    for aid, name, kind in SYSTEM_ACCOUNTS:
        conn.execute(
            "INSERT OR IGNORE INTO accounts (account_id, name, kind)"
            " VALUES (?,?,?)", (aid, name, kind))


def apply_migrations(conn: sqlite3.Connection, migrations_dir: str | Path):
    """Run pending numbered SQL migrations in order. Each migration is
    one transaction; the migrations table records what ran."""
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS} ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {row[0] for row in
               conn.execute(f"SELECT version FROM {SCHEMA_MIGRATIONS}")}
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        version = int(path.name.split("_")[0])
        if version in applied:
            continue
        sql = path.read_text()
        rebuilds_referenced_tables = "cash27: foreign_keys_off" in sql
        if rebuilds_referenced_tables:
            # SQLite cannot change a CHECK constraint in place. A marked
            # migration may rebuild a referenced table while foreign-key
            # enforcement is temporarily disabled; validate every reference
            # before returning the connection to normal use.
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("PRAGMA legacy_alter_table=ON")
        # executescript commits any open transaction first; wrap the
        # migration body in its own BEGIN so it is atomic.
        try:
            conn.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if rebuilds_referenced_tables:
                conn.execute("PRAGMA legacy_alter_table=OFF")
                conn.execute("PRAGMA foreign_keys=ON")
        if rebuilds_referenced_tables:
            broken = conn.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(
                    f"migration {version} left {len(broken)} broken references")
        conn.execute(
            f"INSERT INTO {SCHEMA_MIGRATIONS} (version, applied_at) "
            "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
            (version,))
    seed_system_accounts(conn)
