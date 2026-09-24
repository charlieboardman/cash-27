"""SQLite connection and migration runner (spec 6: stdlib sqlite3,
WAL, synchronous=FULL, foreign keys, numbered migrations, busy
timeout, BEGIN IMMEDIATE for mutating commands)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


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


def apply_migrations(conn: sqlite3.Connection, migrations_dir: str | Path, *,
                     allow_newer: bool = False):
    """Run pending numbered SQL migrations in order. Each migration is
    one transaction; the migrations table records what ran.

    allow_newer lets application startup run against a database that a newer
    release has already migrated, so upgrade.sh can roll back to the prior
    image. That is safe only because every migration must keep the previous
    release working (see AGENTS.md). Imports and restores stay strict."""
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS} ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {row[0] for row in
               conn.execute(f"SELECT version FROM {SCHEMA_MIGRATIONS}")}
    paths = sorted(Path(migrations_dir).glob("*.sql"))
    available = {int(path.name.split("_")[0]) for path in paths}
    unknown = applied - available
    if unknown and min(unknown) <= max(available, default=0):
        raise RuntimeError(
            "This database uses a different schema history. This release requires "
            "a fresh installation; run powerwash.sh, then setup.sh as planned.")
    if unknown and not allow_newer:
        raise RuntimeError(
            f"This database was migrated by a newer cash-27 release "
            f"(migrations {sorted(unknown)}). Use that release, or restore the "
            "pre-upgrade archive with restore-local.sh.")
    if unknown:
        logger.warning(
            "Database has migrations %s from a newer release; starting with "
            "the older schema expectations.", sorted(unknown))
    for path in paths:
        version = int(path.name.split("_")[0])
        if version in applied:
            continue
        sql = path.read_text()
        # executescript commits any open transaction first; wrap the
        # migration body in its own BEGIN so it is atomic.
        try:
            conn.executescript(
                "BEGIN;\n" + sql +
                f"\nINSERT INTO {SCHEMA_MIGRATIONS} (version, applied_at) "
                f"VALUES ({version}, strftime('%Y-%m-%dT%H:%M:%fZ','now'));\nCOMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    seed_system_accounts(conn)
