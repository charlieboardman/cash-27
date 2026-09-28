"""Validated logical database export, staging, and atomic activation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

from . import repo
from .db import apply_migrations, connect


MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
IMPORT_PREFIX = "database-import-"


class DatabaseTransferError(Exception):
    """An export cannot be safely staged or activated."""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def dump_signature(lines) -> str:
    """SHA-256 of a database's SQL dump: equal exactly when the contents are,
    whichever form (.db or .sql) was downloaded."""
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode("utf-8") + b"\n")
    return digest.hexdigest()


def export_sql_signed(connection: sqlite3.Connection) -> tuple[bytes, str]:
    """A consistent logical SQL export from a live connection, and its
    content signature (which leaves out the export time)."""
    snapshot = sqlite3.connect(":memory:")
    try:
        connection.backup(snapshot)
        dump = list(snapshot.iterdump())
    finally:
        snapshot.close()
    lines = [
        "-- cash-27 logical database export",
        f"-- exported_at={datetime.now(timezone.utc).isoformat()}",
        *dump,
    ]
    return ("\n".join(lines) + "\n").encode("utf-8"), dump_signature(dump)


def export_sql(connection: sqlite3.Connection) -> bytes:
    """Return a consistent logical SQL export from a live connection."""
    return export_sql_signed(connection)[0]


def export_database(connection: sqlite3.Connection, staging: Path) -> tuple[bytes, str]:
    """Return a standalone SQLite file holding one consistent snapshot, and
    its content signature.

    The online backup includes committed WAL contents; switching the copy to
    DELETE mode lets it open without any -wal file beside it."""
    staging.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=".database-export-", suffix=".db",
                                    dir=staging)
    os.close(handle)
    path = Path(name)
    try:
        snapshot = sqlite3.connect(path)
        try:
            connection.backup(snapshot)
            snapshot.execute("PRAGMA journal_mode=DELETE")
            if snapshot.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise DatabaseTransferError(
                    "The database snapshot failed its integrity check.")
            signature = dump_signature(snapshot.iterdump())
        finally:
            snapshot.close()
        return path.read_bytes(), signature
    finally:
        path.unlink(missing_ok=True)


def _schema_signature(connection: sqlite3.Connection) -> list[tuple]:
    return [tuple(row) for row in connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'sqlite_%' AND type IN"
        " ('table','index','trigger','view') ORDER BY type, name")]


def _expected_schema() -> list[tuple]:
    reference = sqlite3.connect(":memory:", isolation_level=None)
    try:
        apply_migrations(reference, MIGRATIONS)
        return _schema_signature(reference)
    finally:
        reference.close()


def _inside(root: Path, rel_path: str) -> Path:
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise DatabaseTransferError(
            f"File path leaves the persistent data directory: {rel_path}") from exc
    return candidate


def _validate_files(data_root: Path,
                    connection: sqlite3.Connection) -> int:
    documents = 0
    for row in connection.execute(
            "SELECT rel_path, sha256, size_bytes, state FROM documents"
            " WHERE state!='missing'"):
        path = _inside(data_root, row["rel_path"])
        if not path.is_file():
            raise DatabaseTransferError(
                f"Imported database references a missing document: {row['rel_path']}")
        if path.stat().st_size != row["size_bytes"] or \
                hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise DatabaseTransferError(
                f"Imported database references a changed document: {row['rel_path']}")
        documents += 1

    from .document_jobs import object_refs
    for row in object_refs(connection):
        path = _inside(data_root, row["rel_path"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise DatabaseTransferError(
                f"Imported database references a missing or changed original: {row['rel_path']}")

    return documents


def _summary(connection: sqlite3.Connection, documents: int) -> dict:
    transaction = connection.execute(
        "SELECT COUNT(*) AS total,"
        " SUM(CASE WHEN posting_state='active' THEN 1 ELSE 0 END) AS active,"
        " MIN(date) AS first_date, MAX(date) AS last_date FROM transactions"
    ).fetchone()
    migration = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    return {
        "transactions": transaction["total"],
        "active_transactions": transaction["active"] or 0,
        "first_transaction_date": transaction["first_date"],
        "last_transaction_date": transaction["last_date"],
        "vouchers": connection.execute(
            "SELECT COUNT(*) FROM vouchers").fetchone()[0],
        "documents": documents,
        "schema_version": migration,
    }


def _validate_candidate(data_root: Path, candidate_path: Path) -> dict:
    connection = connect(candidate_path)
    try:
        apply_migrations(connection, MIGRATIONS)
        if _schema_signature(connection) != _expected_schema():
            raise DatabaseTransferError(
                "The imported database schema does not match cash-27")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise DatabaseTransferError(
                f"Imported database integrity check failed: {integrity}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise DatabaseTransferError(
                f"Imported database has {len(foreign_keys)} broken references")
        replayed = repo.replay_projected(connection)
        documents = _validate_files(data_root, connection)
        summary = _summary(connection, documents)
        summary.update({"cash_accounts": len(replayed.states),
                        "movements": len(replayed.movements)})
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return summary
    finally:
        connection.close()
        for suffix in ("-wal", "-shm"):
            Path(str(candidate_path) + suffix).unlink(missing_ok=True)


def stage_import(data_root: str | Path, sql_bytes: bytes) -> dict:
    """Build and fully validate an imported SQL dump without touching live DB."""
    data_root = Path(data_root).resolve()
    try:
        sql = sql_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DatabaseTransferError("Database export must be UTF-8 SQL") from exc
    if not sql.strip():
        raise DatabaseTransferError("Database export is empty")

    import_id = str(uuid.uuid4())
    candidate_path = data_root / "staging" / f"{IMPORT_PREFIX}{import_id}.db"
    manifest_path = candidate_path.with_suffix(".json")
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = sqlite3.connect(candidate_path, isolation_level=None)
        try:
            denied = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}
            raw.set_authorizer(
                lambda action, _one, _two, _database, _trigger:
                sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK)
            raw.executescript(sql)
        finally:
            raw.close()
    except sqlite3.Error as exc:
        candidate_path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(candidate_path) + suffix).unlink(missing_ok=True)
        raise DatabaseTransferError(f"Could not read database export: {exc}") from exc

    try:
        summary = _validate_candidate(data_root, candidate_path)
        digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        manifest = {"import_id": import_id, "sha256": digest,
                    "validated_at": datetime.now(timezone.utc).isoformat(),
                    "summary": summary}
        manifest_path.write_text(json.dumps(manifest, sort_keys=True))
        os.chmod(candidate_path, 0o600)
        os.chmod(manifest_path, 0o600)
        return manifest
    except Exception as exc:
        candidate_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        if isinstance(exc, DatabaseTransferError):
            raise
        raise DatabaseTransferError(
            f"Database export failed validation: {exc}") from exc


def _staged_paths(data_root: Path, import_id: str) -> tuple[Path, Path]:
    try:
        normalized = str(uuid.UUID(import_id))
    except (ValueError, TypeError) as exc:
        raise DatabaseTransferError("Invalid staged import identifier") from exc
    candidate = data_root / "staging" / f"{IMPORT_PREFIX}{normalized}.db"
    return candidate, candidate.with_suffix(".json")


def activate_import(data_root: str | Path, live_connection: sqlite3.Connection,
                    import_id: str) -> dict:
    # Database replacement must not strand a worker connection on the old inode.
    import fcntl
    with (Path(data_root) / "staging" / "document-worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatabaseTransferError(
                "A document is processing. Try importing again when it finishes.") from exc
        return _activate_import(data_root, live_connection, import_id)


def _activate_import(data_root: str | Path, live_connection: sqlite3.Connection,
                     import_id: str) -> dict:
    """Back up the live database and atomically activate a staged import.

    The caller must guarantee a single application worker and give this
    function ownership of ``live_connection``; it is always closed here.
    """
    data_root = Path(data_root).resolve()
    candidate, manifest_path = _staged_paths(data_root, import_id)
    if not candidate.is_file() or not manifest_path.is_file():
        raise DatabaseTransferError("Staged database import was not found")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DatabaseTransferError("Staged import manifest is invalid") from exc
    if hashlib.sha256(candidate.read_bytes()).hexdigest() != manifest.get("sha256"):
        raise DatabaseTransferError("Staged database changed after validation")
    try:
        summary = _validate_candidate(data_root, candidate)
    except Exception as exc:
        if isinstance(exc, DatabaseTransferError):
            raise
        raise DatabaseTransferError(
            f"Staged database failed final validation: {exc}") from exc

    backup_dir = (data_root / "backups" /
                  f"before-db-import-{_utc_stamp()}-{import_id[:8]}")
    backup_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    backup_path = backup_dir / "cash.db"
    backup_connection = sqlite3.connect(backup_path)
    try:
        live_connection.backup(backup_connection)
    finally:
        backup_connection.close()
    os.chmod(backup_path, 0o600)
    (backup_dir / "import.json").write_text(json.dumps({
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "import_id": import_id,
        "import_summary": summary,
    }, indent=2, sort_keys=True))

    live_path = data_root / "cash.db"
    try:
        live_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        live_connection.close()
    for suffix in ("-wal", "-shm"):
        Path(str(live_path) + suffix).unlink(missing_ok=True)

    os.replace(candidate, live_path)
    os.chmod(live_path, 0o600)
    try:
        verification = connect(live_path)
        try:
            integrity = verification.execute(
                "PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise DatabaseTransferError(
                    f"Activated database integrity check failed: {integrity}")
            repo.replay_projected(verification)
        finally:
            verification.close()
    except Exception:
        for suffix in ("-wal", "-shm"):
            Path(str(live_path) + suffix).unlink(missing_ok=True)
        rollback = data_root / "staging" / f"rollback-{uuid.uuid4()}.db"
        shutil.copy2(backup_path, rollback)
        os.replace(rollback, live_path)
        raise
    finally:
        manifest_path.unlink(missing_ok=True)

    return {
        "import_id": import_id,
        "summary": summary,
        "previous_database": str(backup_path.relative_to(data_root)),
    }
