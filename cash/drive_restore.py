"""Turn the Drive backup, downloaded as one or more zip files, into a standard
cash-27-full-backup-v1 archive that restore-local.sh accepts.

Files are found by their SHA-256, never by name or folder, so renamed or
duplicated Drive files do not matter. Sheets are ignored: everything comes from
a database snapshot and the documents and settings it needs.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
from zipfile import BadZipFile, ZipFile

from .document_jobs import object_refs
from .ops import verify_archive, write_archive

SNAPSHOT = re.compile(r"cash-27-(\d{8}T\d{6}Z)\.db")


class DriveRestoreError(SystemExit):
    """The zip files cannot produce a complete, valid archive."""


def _index(zips: list[Path]) -> tuple[dict, dict, dict]:
    """By SHA-256, every file; by name, every database snapshot; by path
    under config/, every settings file."""
    by_sha, snapshots, config = {}, {}, {}
    for zip_path in zips:
        try:
            archive = ZipFile(zip_path)
        except (OSError, BadZipFile) as exc:
            raise DriveRestoreError(f"{zip_path} is not a readable zip file") from exc
        with archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                digest = hashlib.sha256()
                with archive.open(info) as handle:
                    for block in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(block)
                where = (zip_path, info.filename)
                by_sha.setdefault(digest.hexdigest(), where)
                parts = PurePosixPath(info.filename).parts
                if SNAPSHOT.fullmatch(parts[-1]):
                    snapshots.setdefault(parts[-1], where)
                if "Config" in parts:
                    relative = "/".join(parts[parts.index("Config") + 1:])
                    config[relative] = where
    return by_sha, snapshots, config


def _extract(where: tuple[Path, str], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(where[0]) as archive, archive.open(where[1]) as source, \
            target.open("wb") as destination:
        shutil.copyfileobj(source, destination)


def build_archive(zips: list[Path], archive_path: Path, *, database: str | None = None) -> dict:
    """Use the newest database snapshot in the zips, or the one named."""
    zips = [Path(path) for path in zips]
    archive_path = Path(archive_path)
    by_sha, snapshots, config = _index(zips)
    if not snapshots:
        raise DriveRestoreError("no database snapshot (cash-27-<time>.db) was found in the zip files")
    name = database or max(snapshots, key=lambda n: SNAPSHOT.fullmatch(n).group(1))
    if name not in snapshots:
        raise DriveRestoreError(f"{name} is not in the zip files; found {', '.join(sorted(snapshots))}")

    archive_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    working = Path(tempfile.mkdtemp(prefix=".cash-27-drive-", dir=archive_path.parent))
    try:
        _extract(snapshots[name], working / "cash.db")
        conn = sqlite3.connect(f"{(working / 'cash.db').resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DriveRestoreError(f"{name} failed its integrity check")
            documents = sorted({(row["rel_path"], row["sha256"]) for row in object_refs(conn)})
        except sqlite3.DatabaseError as exc:
            raise DriveRestoreError(f"{name} is not a usable Cash 27 database: {exc}") from exc
        finally:
            conn.close()
        missing = [path for path, digest in documents if digest not in by_sha]
        if missing:
            raise DriveRestoreError(
                f"{len(missing)} file(s) that {name} needs are not in the zip files: "
                + ", ".join(missing[:10]) + (" …" if len(missing) > 10 else ""))
        for _, digest in documents:
            _extract(by_sha[digest], working / "files" / digest[:2] / digest)
        for relative, where in sorted(config.items()):
            _extract(where, working / "config" / relative)
        result = write_archive(working, archive_path)
    finally:
        shutil.rmtree(working, ignore_errors=True)
    # Prove it restores (migrations, integrity, documents, full replay) now;
    # an archive that does not is removed rather than offered for restore.
    try:
        verification = verify_archive(archive_path)
    except BaseException:
        archive_path.unlink(missing_ok=True)
        raise
    return {**result, "database": name, "documents": len(documents),
            "config_files": len(config),
            "integrity": verification["integrity"]}
