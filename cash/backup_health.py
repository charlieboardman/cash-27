"""Read backup-service health and control the application-owned disable flag.

The rclone credentials remain isolated in the Drive adapter. It publishes only
non-secret status under the persistent data root, which Flask can safely
display without access to the credential or container controls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path


STATE_RELATIVE = Path("backups/drive")
HOST_CONFIG = "host.conf"
RUNTIME_STATUS = "status.conf"
ACTIVITY_LOG = "activity.tsv"
DISABLED_FLAG = "automatic-backups-disabled"
STALE_AFTER = timedelta(hours=36)
MAX_ACTIVITY_ROWS = 30


def _read_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeError):
        return values
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key:
            values[key] = value
    return values


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _activity(path: Path) -> list[dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeError):
        return []
    events = []
    for line in reversed(lines[-MAX_ACTIVITY_ROWS:]):
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        events.append({
            "at": parts[0], "state": parts[1],
            "message": parts[2], "backup_name": parts[3],
        })
    return events


def get_backup_health(data_root: str | Path, *, expected: bool,
                      now: datetime | None = None) -> dict:
    root = Path(data_root) / STATE_RELATIVE
    host = _read_values(root / HOST_CONFIG)
    runtime = _read_values(root / RUNTIME_STATUS)
    disabled = (root / DISABLED_FLAG).exists()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    last_attempt = _timestamp(runtime.get("last_attempt_at"))
    last_success = _timestamp(runtime.get("last_success_at"))
    configured = host.get("rclone_configured") == "1"
    service_installed = host.get("service_installed") == "1"
    rclone_ok = runtime.get("rclone_ok") == "1"
    scheduler_ok = runtime.get("scheduler_ok") == "1"
    warning: str | None = None
    state = "healthy"

    if not expected:
        state = "not_expected"
    elif disabled:
        state = "disabled"
        warning = "Automatic Google Drive backups are turned off."
    elif not configured:
        state = "not_configured"
        warning = "Google Drive backups have not been configured."
    elif not service_installed:
        state = "service_missing"
        warning = "The automatic backup service is not installed."
    elif runtime.get("state") == "failure":
        state = "failed"
        warning = runtime.get("message") or "The latest backup failed."
    elif not last_attempt:
        state = "never_run"
        warning = "No automatic backup attempt has been recorded."
    elif now - last_attempt > STALE_AFTER:
        state = "overdue"
        warning = "The automatic backup service has not checked in recently."
    elif runtime.get("state") == "running":
        state = "running"
    elif not last_success:
        state = "never_succeeded"
        warning = "No Google Drive backup has succeeded yet."
    elif not rclone_ok:
        state = "rclone_failed"
        warning = "The latest Google Drive connection check failed."
    elif not scheduler_ok:
        state = "scheduler_failed"
        warning = "The latest backup could not verify its scheduler."

    return {
        "expected": expected,
        "enabled": not disabled,
        "state": state,
        "warning": warning,
        "rclone_configured": configured,
        "rclone_ok": rclone_ok,
        "service_installed": service_installed,
        "scheduler_ok": scheduler_ok,
        "destination": host.get("destination", ""),
        "schedule": host.get("schedule", "Nightly"),
        "last_attempt_at": runtime.get("last_attempt_at"),
        "last_success_at": runtime.get("last_success_at"),
        "backup_name": runtime.get("backup_name", ""),
        "message": runtime.get("message", ""),
        "activity": _activity(root / ACTIVITY_LOG),
    }


def set_automatic_backups(data_root: str | Path, *, enabled: bool) -> None:
    root = Path(data_root) / STATE_RELATIVE
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    flag = root / DISABLED_FLAG
    if enabled:
        flag.unlink(missing_ok=True)
        return
    temporary = root / f".{DISABLED_FLAG}.{os.getpid()}"
    temporary.write_text(
        f"disabled_at={datetime.now(timezone.utc).isoformat()}\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, flag)
