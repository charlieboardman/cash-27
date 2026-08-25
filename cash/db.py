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
        # executescript commits any open transaction first; wrap the
        # migration body in its own BEGIN so it is atomic.
        conn.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
        conn.execute(
            f"INSERT INTO {SCHEMA_MIGRATIONS} (version, applied_at) "
            "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
            (version,))
