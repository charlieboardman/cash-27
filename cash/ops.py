"""Backup and restore (spec 17): SQLite online backup API, filed PDFs
backed by hash, restore runs migrations + integrity + document
verification + full replay."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

from .db import apply_migrations, connect
from . import repo


def backup(data_root: str | Path, backup_root: str | Path):
    """Online backup while the app runs (spec 17: no WAL file copy)."""
    data_root, backup_root = Path(data_root), Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    src = connect(data_root / "cash.db")
    dst = sqlite3.connect(str(backup_root / "cash.db"))
    src.backup(dst)
    dst.close()
    # documents by hash: idempotent deduped copy
    doc_out = backup_root / "files"
    doc_out.mkdir(exist_ok=True)
    copied = 0
    for sub in ("documents", "inbox"):
        d = data_root / sub
        if not d.exists():
            continue
        for f in d.rglob("*.pdf"):
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            target = doc_out / digest[:2] / digest
            if not target.exists():
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(f, target)
                copied += 1
    src.close()
    return {"backup": str(backup_root), "files_copied": copied}


def restore(data_root: str | Path, backup_root: str | Path):
    """Restore while stopped. Runs migrations, integrity check,
    document verification, full replay."""
    data_root, backup_root = Path(data_root), Path(backup_root)
    data_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_root / "cash.db", data_root / "cash.db")
    # restore hash-deduped PDFs to their recorded relative paths
    conn = connect(data_root / "cash.db")
    apply_migrations(conn, Path(__file__).parent.parent / "migrations")
    restored_files = 0
    for row in conn.execute(
            "SELECT rel_path, sha256 FROM documents WHERE state IN"
            " ('filed','staged','inbox','signee_draft')"):
        src = backup_root / "files" / row["sha256"][:2] / row["sha256"]
        if not src.exists():
            continue
        dst = data_root / row["rel_path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        restored_files += 1
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise SystemExit(f"integrity_check failed: {integrity}")
    res = repo.replay_projected(conn)
    from .web.documents import recover_documents
    findings = recover_documents(data_root, conn)
    conn.close()
    return {"integrity": integrity, "accounts": len(res.states),
            "movements": len(res.movements), "findings": findings,
            "restored_files": restored_files,
            "expense_value": str(res.expense_value)}


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "backup":
        print(backup(args[0], args[1]))
    elif cmd == "restore":
        print(restore(args[0], args[1]))
    else:
        raise SystemExit("usage: python -m cash.ops backup|restore "
                         "<data_root> <backup_root>")
