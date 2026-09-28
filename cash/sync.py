"""Offsite sync (the Google Apps Script in gas/): its access token and the
time of its last completed run, both small files under config/sync/.

Only a SHA-256 of the token is stored. Full backups copy named config files
only, so neither file travels with a backup; a restored server gets a new
token from Settings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets

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


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(data_root: Path) -> str:
    """Replace any existing token. The caller shows the result once."""
    token = secrets.token_urlsafe(32)
    _write(_dir(data_root) / "token.json", {"sha256": _digest(token), "created_at": _now()})
    return token


def revoke_token(data_root: Path) -> None:
    (_dir(data_root) / "token.json").unlink(missing_ok=True)


def token_valid(data_root: Path, presented: str) -> bool:
    stored = _read(_dir(data_root) / "token.json")
    if not stored or not presented or not isinstance(stored.get("sha256"), str):
        return False
    return hmac.compare_digest(stored["sha256"], _digest(presented))


def record_completion(data_root: Path) -> str:
    completed = _now()
    _write(_dir(data_root) / "last-sync.json", {"completed_at": completed})
    return completed


def status(data_root: Path) -> dict:
    """Whether sync is set up, when it last finished, and whether that is
    long enough ago to warn about. No token means no warning."""
    token = _read(_dir(data_root) / "token.json")
    last = (_read(_dir(data_root) / "last-sync.json") or {}).get("completed_at")
    since = token and (last or token.get("created_at"))
    try:
        stale = bool(since) and datetime.now(timezone.utc) - datetime.fromisoformat(since) > STALE_AFTER
    except (TypeError, ValueError):
        stale = True
    return {"configured": bool(token), "token_created_at": token and token.get("created_at"),
            "last_completed_at": last, "stale": stale}
