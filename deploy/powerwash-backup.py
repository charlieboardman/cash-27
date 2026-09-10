"""Preserve a stopped installation without depending on its schema version."""

from datetime import datetime, timezone
import filecmp
import os
from pathlib import Path
import shutil
import sqlite3
import sys


def copy_verified(source, destination):
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Document is not a regular file: {source}")
    shutil.copyfile(source, destination)
    if not filecmp.cmp(source, destination, shallow=False):
        raise OSError(f"Document copy verification failed: {source}")
    return str(destination)


def backup(data_root: Path, backup_root: Path) -> Path:
    if not backup_root.is_absolute() or backup_root.resolve() != backup_root:
        raise ValueError("Backup directory must be an absolute, non-symlink path")
    source_root = data_root.resolve(strict=True)
    if (source_root == backup_root or source_root in backup_root.parents
            or backup_root in source_root.parents):
        raise ValueError("Backup directory overlaps the data directory")
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if backup_root.stat().st_uid != os.geteuid():
        raise ValueError("Backup directory must be owned by the caller (root)")
    backup_root.chmod(0o700)

    database = data_root / "cash.db"
    if database.is_symlink() or not database.is_file():
        raise ValueError(f"Database is missing or not a regular file: {database}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = backup_root / timestamp
    staging = backup_root / f".{timestamp}.partial"
    staging.mkdir(mode=0o700)
    try:
        # SQLite's backup API includes committed WAL contents in a standalone DB.
        # A raw copy of cash.db alone could silently lose recent transactions.
        source = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            target = sqlite3.connect(staging / "data.db")
            try:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("Database backup failed SQLite integrity check")
                if target.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("Database backup failed SQLite foreign-key check")
                # Make the backup self-contained even when the source uses WAL.
                target.execute("PRAGMA journal_mode=DELETE")
            finally:
                target.close()
        finally:
            source.close()

        documents = data_root / "documents"
        if documents.is_symlink():
            raise ValueError("Documents directory must not be a symlink")
        if documents.exists():
            for path in documents.rglob("*"):
                if path.is_symlink():
                    raise ValueError(f"Document path must not be a symlink: {path}")
            shutil.copytree(documents, staging / "documents", copy_function=copy_verified)
        else:
            (staging / "documents").mkdir(mode=0o700)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging)
        raise
    return destination


if __name__ == "__main__":
    os.umask(0o077)
    try:
        result = backup(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception as exc:
        print(f"Powerwash backup failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(result)
