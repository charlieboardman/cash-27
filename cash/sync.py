"""Offsite sync (the Google Apps Script in gas/): its access token and the
time of its last completed run, both small files under config/sync/.

The token opens nothing an administrator cannot already download, so it is
kept as plain text and shown in Settings. Full backups and the Drive copy
carry it, so a restored server keeps working with the script; they leave out
the last-run time, which describes only this server.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets

from .commands import ValidationFailure

STALE_AFTER = timedelta(hours=36)


def _dir(data_root: Path) -> Path:
    return Path(data_root) / "config" / "sync"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


# It travels in an HTTP header, so no spaces or line breaks.
TOKEN = re.compile(r"[\x21-\x7e]{1,256}")


def token_file(data_root: Path) -> Path:
    return _dir(data_root) / "token.json"


def set_token(data_root: Path, token: str | None = None) -> str:
    """Replace any existing token with the one given, or a random one."""
    if token is None:
        token = secrets.token_urlsafe(32)
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValidationFailure(
            "bad_sync_token",
            "Enter a token without spaces.",
            {"token": "1 to 256 characters, no spaces"})
    _write(token_file(data_root), {"token": token, "created_at": _now()})
    return token


def revoke_token(data_root: Path) -> None:
    token_file(data_root).unlink(missing_ok=True)


def token_valid(data_root: Path, presented: str) -> bool:
    stored = _read(token_file(data_root)) or {}
    if not presented:
        return False
    if isinstance(stored.get("token"), str):
        return hmac.compare_digest(stored["token"].encode(), presented.encode())
    # Tokens made before they were shown were kept only as a hash.
    if isinstance(stored.get("sha256"), str):
        return hmac.compare_digest(stored["sha256"],
                                   hashlib.sha256(presented.encode()).hexdigest())
    return False


def record_completion(data_root: Path) -> str:
    completed = _now()
    _write(_dir(data_root) / "last-sync.json", {"completed_at": completed})
    return completed


def status(data_root: Path) -> dict:
    """Whether sync is set up, when it last finished, and whether that is
    long enough ago to warn about. No token means no warning."""
    token = _read(token_file(data_root))
    last = (_read(_dir(data_root) / "last-sync.json") or {}).get("completed_at")
    since = token and (last or token.get("created_at"))
    try:
        stale = bool(since) and datetime.now(timezone.utc) - datetime.fromisoformat(since) > STALE_AFTER
    except (TypeError, ValueError):
        stale = True
    return {"configured": bool(token), "token": token and token.get("token"),
            "token_created_at": token and token.get("created_at"),
            "last_completed_at": last, "stale": stale}


def fiscal_label(fy: int) -> str:
    """Drive folder names use the same two digits as voucher numbers."""
    return f"FY{fy % 100:02d}"


def manifest(conn, data_root: Path, *, fy_start_month: int, zone) -> dict:
    """Every file a full restore needs, as a full backup would copy it:
    documents (inbox and processing originals included) and print settings.

    Fetch it after the database, so it lists every document that snapshot
    names. Documents never change, so their recorded hash is listed; files
    that are missing on disk are reported as problems, not left out quietly.
    """
    from .document_jobs import object_refs
    from .ops import config_files
    from .prep import fiscal_year_for

    data_root = Path(data_root)
    local_date = datetime.now(zone).date().isoformat()
    fy = fiscal_year_for(local_date, fy_start_month)
    conn.execute("BEGIN")
    try:
        refs = sorted({(row["rel_path"], row["sha256"]) for row in object_refs(conn)})
        revision = conn.execute(
            "SELECT ledger_revision FROM ledger_state WHERE singleton_id=1").fetchone()[0]
    finally:
        conn.rollback()
    files, problems = [], []
    for rel_path, digest in refs:
        path = data_root / rel_path
        if not path.is_file():
            problems.append(f"missing document file: {rel_path}")
            continue
        files.append({"kind": "document", "path": rel_path, "name": path.name,
                      "sha256": digest, "size": path.stat().st_size})
    for path in config_files(data_root):
        content = path.read_bytes()
        files.append({"kind": "config", "path": path.relative_to(data_root).as_posix(),
                      "name": path.name, "sha256": hashlib.sha256(content).hexdigest(),
                      "size": len(content)})
    return {"generated_at": _now(), "timezone": str(zone), "local_date": local_date,
            "fiscal_year": fy, "fiscal_label": fiscal_label(fy),
            "ledger_revision": revision, "files": files, "problems": problems}


def manifest_file(conn, data_root: Path, sha256: str) -> tuple[dict, bytes] | None:
    """One listed file's entry and bytes, checked against its listed hash."""
    from .document_jobs import object_refs
    from .ops import config_files

    data_root = Path(data_root)
    for row in object_refs(conn):
        path = data_root / row["rel_path"]
        if row["sha256"] == sha256 and path.is_file():
            return {"path": row["rel_path"], "name": path.name}, path.read_bytes()
    for path in config_files(data_root):
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() == sha256:
            return {"path": path.relative_to(data_root).as_posix(), "name": path.name}, content
    return None
