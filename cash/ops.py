"""Backup and restore of SQLite, immutable PDFs, and finalized reports."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

from .db import apply_migrations, connect, connect_readonly
from . import repo
from .document_jobs import object_refs


def backup(data_root: str | Path, backup_root: str | Path):
    """Online backup while the app runs (spec 17: no WAL file copy)."""
    data_root, backup_root = Path(data_root), Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    src = connect_readonly(data_root / "cash.db")
    dst = sqlite3.connect(str(backup_root / "cash.db"))
    dst.row_factory = sqlite3.Row
    try:
        src.backup(dst)
        # Copy exactly the evidence referenced by the database snapshot.
        doc_out = backup_root / "files"
        doc_out.mkdir(exist_ok=True)
        copied = 0
        for row in object_refs(dst):
            source = data_root / row["rel_path"]
            if not source.is_file():
                raise SystemExit(
                    f"backup document missing: {row['rel_path']}")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest != row["sha256"]:
                raise SystemExit(
                    f"backup document hash mismatch: {row['rel_path']}")
            target = doc_out / digest[:2] / digest
            if not target.exists():
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(source, target)
                copied += 1
        reports_copied = 0
        reports_verified = 0
        for report in dst.execute(
                "SELECT generated_path, sha256 FROM report_runs"
                " ORDER BY report_id"):
            report_path = data_root / report["generated_path"]
            if not report_path.exists():
                raise SystemExit(
                    f"finalized report missing: {report['generated_path']}")
            digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
            if digest != report["sha256"]:
                raise SystemExit(f"finalized report hash mismatch: "
                                 f"{report['generated_path']}")
            target = backup_root / report["generated_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(report_path, target)
            reports_copied += 1
            reports_verified += 1
    finally:
        dst.close()
        src.close()
    return {"backup": str(backup_root), "files_copied": copied,
            "reports_copied": reports_copied,
            "reports_verified": reports_verified}


def backup_archive(data_root: str | Path, archive_path: str | Path):
    """Create one portable, hash-manifested archive from a live data root."""
    data_root, archive_path = Path(data_root), Path(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    working = Path(tempfile.mkdtemp(
        prefix=".cash-27-backup-", dir=archive_path.parent))
    archive_temporary = archive_path.with_name(
        f".{archive_path.name}.{os.getpid()}.partial")
    try:
        result = backup(data_root, working)
        manifest_files = []
        for path in sorted(item for item in working.rglob("*")
                           if item.is_file()):
            contents = path.read_bytes()
            manifest_files.append({
                "path": path.relative_to(working).as_posix(),
                "size_bytes": len(contents),
                "sha256": hashlib.sha256(contents).hexdigest(),
            })
        snapshot = sqlite3.connect(working / "cash.db")
        try:
            schema_version = snapshot.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
        finally:
            snapshot.close()
        manifest = {
            "format": "cash-27-full-backup-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "application_revision": os.environ.get(
                "CASH_BUILD_REVISION", "unknown"),
            "schema_version": schema_version,
            "files": manifest_files,
        }
        (working / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        with tarfile.open(archive_temporary, "w:gz") as archive:
            for path in sorted(item for item in working.rglob("*")
                               if item.is_file()):
                archive.add(path, arcname=path.relative_to(working))
        os.chmod(archive_temporary, 0o600)
        os.replace(archive_temporary, archive_path)
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        return {
            **result,
            "archive": str(archive_path),
            "size_bytes": archive_path.stat().st_size,
            "sha256": digest,
            "manifest_files": len(manifest_files),
        }
    finally:
        archive_temporary.unlink(missing_ok=True)
        shutil.rmtree(working, ignore_errors=True)


def verify_backup_manifest(backup_root: str | Path) -> dict | None:
    """Verify every payload file when restoring an archive-backed backup."""
    backup_root = Path(backup_root)
    manifest_path = backup_root / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit("backup manifest is unreadable") from exc
    if manifest.get("format") != "cash-27-full-backup-v1" \
            or not isinstance(manifest.get("files"), list):
        raise SystemExit("backup manifest format is not supported")

    recorded: set[str] = set()
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise SystemExit("backup manifest contains an invalid file entry")
        relative = str(item.get("path", ""))
        relative_path = Path(relative)
        if not relative or relative_path.is_absolute() \
                or ".." in relative_path.parts:
            raise SystemExit("backup manifest contains an unsafe file path")
        if relative in recorded:
            raise SystemExit("backup manifest contains a duplicate file path")
        recorded.add(relative)
        path = backup_root / relative_path
        if not path.is_file():
            raise SystemExit(f"backup manifest file is missing: {relative}")
        contents = path.read_bytes()
        if len(contents) != item.get("size_bytes"):
            raise SystemExit(f"backup manifest size mismatch: {relative}")
        if hashlib.sha256(contents).hexdigest() != item.get("sha256"):
            raise SystemExit(f"backup manifest hash mismatch: {relative}")

    actual = {path.relative_to(backup_root).as_posix()
              for path in backup_root.rglob("*")
              if path.is_file() and path != manifest_path}
    if actual != recorded:
        raise SystemExit("backup payload does not match its manifest")
    return manifest


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.restore")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _materialize_backup(backup_root: Path, target_root: Path) -> dict:
    """Build and fully validate a restore tree away from live data."""
    verify_backup_manifest(backup_root)
    target_root.mkdir(parents=True, exist_ok=True)
    database = backup_root / "cash.db"
    if not database.is_file():
        raise SystemExit("backup database is missing")
    shutil.copy2(database, target_root / "cash.db")
    conn = connect(target_root / "cash.db")
    apply_migrations(conn, Path(__file__).parent.parent / "migrations")
    restored_files = 0
    restored_reports = 0
    try:
        for row in object_refs(conn):
            src = backup_root / "files" / row["sha256"][:2] / row["sha256"]
            if not src.is_file():
                raise SystemExit(
                    f"backup missing document payload: {row['rel_path']}")
            if hashlib.sha256(src.read_bytes()).hexdigest() != row["sha256"]:
                raise SystemExit(
                    f"backup document hash mismatch: {row['rel_path']}")
            dst = target_root / row["rel_path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored_files += 1
        for row in conn.execute(
                "SELECT generated_path, sha256 FROM report_runs"
                " ORDER BY report_id"):
            src = backup_root / row["generated_path"]
            if not src.is_file():
                raise SystemExit(
                    f"backup missing finalized report: {row['generated_path']}")
            digest = hashlib.sha256(src.read_bytes()).hexdigest()
            if digest != row["sha256"]:
                raise SystemExit(
                    f"backup report hash mismatch: {row['generated_path']}")
            dst = target_root / row["generated_path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            if hashlib.sha256(dst.read_bytes()).hexdigest() != row["sha256"]:
                raise SystemExit(
                    f"restored report hash mismatch: {row['generated_path']}")
            restored_reports += 1
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"integrity_check failed: {integrity}")
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise SystemExit(
                f"foreign_key_check failed: {len(foreign_keys)} violation(s)")
        res = repo.replay_projected(conn)
        from .web.documents import recover_documents
        findings = recover_documents(target_root, conn)
        if findings:
            raise SystemExit("restored document verification failed")
    finally:
        conn.close()
    return {"integrity": integrity, "accounts": len(res.states),
            "movements": len(res.movements), "findings": findings,
            "restored_files": restored_files,
            "restored_reports": restored_reports,
            "expense_value": str(res.expense_value)}


def restore(data_root: str | Path, backup_root: str | Path):
    """Stage and validate a backup, then commit files and database atomically.

    The caller must stop the application first. Evidence is installed before
    the database, so a failed copy cannot point the live database at absent
    files. The database itself is the final atomic replacement.
    """
    data_root, backup_root = Path(data_root), Path(backup_root)
    data_root.mkdir(parents=True, exist_ok=True)
    staging_parent = data_root / "staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".full-restore-", dir=staging_parent))
    try:
        result = _materialize_backup(backup_root, staged)
        for directory in ("documents", "reports"):
            source_root = staged / directory
            if not source_root.exists():
                continue
            for source in sorted(source_root.rglob("*")):
                if source.is_file():
                    _copy_atomic(source, data_root / source.relative_to(staged))
        for suffix in ("-wal", "-shm"):
            (data_root / f"cash.db{suffix}").unlink(missing_ok=True)
        _copy_atomic(staged / "cash.db", data_root / "cash.db")
        return result
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def _safe_extract(archive_path: Path, destination: Path) -> None:
    try:
        archive = tarfile.open(archive_path, "r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise SystemExit("backup archive is unreadable") from exc
    with archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise SystemExit("backup archive contains an unsafe path")
            if not (member.isfile() or member.isdir()):
                raise SystemExit("backup archive contains an unsafe entry")
        archive.extractall(destination, filter="data")


def verify_archive(archive_path: str | Path) -> dict:
    """Safely extract and exercise a full restore without touching live data."""
    archive_path = Path(archive_path)
    working = Path(tempfile.mkdtemp(
        prefix=".cash-27-verify-", dir=archive_path.parent))
    try:
        extracted = working / "extracted"
        restored = working / "restored"
        extracted.mkdir()
        _safe_extract(archive_path, extracted)
        return _materialize_backup(extracted, restored)
    finally:
        shutil.rmtree(working, ignore_errors=True)


def restore_archive(data_root: str | Path, archive_path: str | Path) -> dict:
    """Safely extract, validate, and restore a full archive while stopped."""
    data_root, archive_path = Path(data_root), Path(archive_path)
    staging_parent = data_root / "staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    working = Path(tempfile.mkdtemp(
        prefix=".cash-27-archive-", dir=staging_parent))
    try:
        extracted = working / "extracted"
        extracted.mkdir()
        _safe_extract(archive_path, extracted)
        return restore(data_root, extracted)
    finally:
        shutil.rmtree(working, ignore_errors=True)


def forensic_archive(data_root: str | Path, archive_path: str | Path) -> dict:
    """Preserve raw stopped-state files when the database cannot be backed up.

    This is deliberately not a verified Cash 27 backup and cannot be selected
    by the automatic restore path. It exists only to retain damaged source
    material for later investigation.
    """
    data_root, archive_path = Path(data_root), Path(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = archive_path.with_name(
        f".{archive_path.name}.{os.getpid()}.partial")
    candidates = [
        data_root / "cash.db",
        data_root / "cash.db-wal",
        data_root / "cash.db-shm",
    ]
    for directory in ("documents", "reports"):
        root = data_root / directory
        if root.exists():
            candidates.extend(
                path for path in sorted(root.rglob("*")) if path.is_file())
    try:
        with tarfile.open(temporary, "w:gz") as archive:
            for path in candidates:
                if path.is_file() and not path.is_symlink():
                    archive.add(path, arcname=path.relative_to(data_root))
        os.chmod(temporary, 0o600)
        os.replace(temporary, archive_path)
        return {
            "archive": str(archive_path),
            "size_bytes": archive_path.stat().st_size,
            "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        }
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "backup":
        print(backup(args[0], args[1]))
    elif cmd == "backup-archive":
        print(backup_archive(args[0], args[1]))
    elif cmd == "restore":
        print(restore(args[0], args[1]))
    elif cmd == "verify-archive":
        print(verify_archive(args[0]))
    elif cmd == "restore-archive":
        print(restore_archive(args[0], args[1]))
    elif cmd == "forensic-archive":
        print(forensic_archive(args[0], args[1]))
    else:
        raise SystemExit(
            "usage: python -m cash.ops backup|backup-archive|restore|"
            "verify-archive|restore-archive|forensic-archive ...")
