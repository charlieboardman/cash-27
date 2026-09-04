from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cash.backup_health import get_backup_health, set_automatic_backups


NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def write_status(tmp_path, *, runtime_state="success", age_hours=1):
    root = tmp_path / "backups" / "drive"
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("host.conf").write_text(
        "rclone_configured=1\n"
        "timer_installed=1\n"
        "destination=drive:Cash 27 Backups\n"
        "schedule=Nightly\n")
    timestamp = (NOW - timedelta(hours=age_hours)).isoformat()
    root.joinpath("status.conf").write_text(
        f"state={runtime_state}\n"
        f"last_attempt_at={timestamp}\n"
        f"last_success_at={timestamp if runtime_state == 'success' else ''}\n"
        "rclone_ok=1\ntimer_ok=1\n"
        "backup_name=cash-27-full-example.tar.gz\n"
        "message=Backup uploaded to Google Drive.\n")
    root.joinpath("activity.tsv").write_text(
        f"{timestamp}\tsuccess\tBackup uploaded to Google Drive.\t"
        "cash-27-full-example.tar.gz\n")


def test_healthy_backup_status_and_activity(tmp_path):
    write_status(tmp_path)
    health = get_backup_health(tmp_path, expected=True, now=NOW)

    assert health["state"] == "healthy"
    assert health["warning"] is None
    assert health["rclone_ok"] is True
    assert health["timer_ok"] is True
    assert health["activity"][0]["state"] == "success"


def test_missing_stale_and_failed_statuses_warn(tmp_path):
    missing = get_backup_health(tmp_path, expected=True, now=NOW)
    assert missing["state"] == "not_configured"

    write_status(tmp_path, age_hours=40)
    stale = get_backup_health(tmp_path, expected=True, now=NOW)
    assert stale["state"] == "overdue"

    write_status(tmp_path, runtime_state="failure")
    failed = get_backup_health(tmp_path, expected=True, now=NOW)
    assert failed["state"] == "failed"


def test_disable_flag_is_application_owned_and_reversible(tmp_path):
    write_status(tmp_path)
    set_automatic_backups(tmp_path, enabled=False)
    disabled = get_backup_health(tmp_path, expected=True, now=NOW)
    assert disabled["state"] == "disabled"
    assert disabled["enabled"] is False

    set_automatic_backups(tmp_path, enabled=True)
    enabled = get_backup_health(tmp_path, expected=True, now=NOW)
    assert enabled["state"] == "healthy"
    assert enabled["enabled"] is True


def test_development_environment_does_not_warn(tmp_path):
    health = get_backup_health(tmp_path, expected=False, now=NOW)
    assert health["state"] == "not_expected"
    assert health["warning"] is None


def test_active_backup_does_not_create_a_transient_warning(tmp_path):
    write_status(tmp_path, runtime_state="running")
    health = get_backup_health(tmp_path, expected=True, now=NOW)
    assert health["state"] == "running"
    assert health["warning"] is None
