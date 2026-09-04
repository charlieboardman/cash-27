from __future__ import annotations

import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def configured_job(tmp_path: Path, *, disabled: bool = False,
                   rclone_fails: bool = False,
                   copy_fails: bool = False):
    config = tmp_path / "config"
    data = tmp_path / "data"
    state = data / "backups" / "drive"
    fake_bin = tmp_path / "bin"
    config.mkdir()
    state.mkdir(parents=True)
    fake_bin.mkdir()
    config.joinpath("backup.conf").write_text(
        f"CASH_DATA_ROOT={data}\n"
        "CASH_BACKUP_CONFIGURED=1\n"
        "CASH_BACKUP_REMOTE=drive\n"
        "CASH_BACKUP_PATH=Cash 27 Backups\n")
    if disabled:
        state.joinpath("automatic-backups-disabled").write_text("yes\n")

    command_log = tmp_path / "commands.log"
    command_log.touch()
    executable(
        fake_bin / "systemctl",
        "#!/bin/sh\n"
        "printf 'systemctl %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "exit 0\n",
    )
    executable(
        fake_bin / "podman",
        "#!/bin/sh\n"
        "printf 'podman %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "if test \"$1\" = container; then printf 'true\\n'; exit 0; fi\n"
        "if test \"$1\" = exec; then\n"
        "  for last do :; done\n"
        "  relative=${last#/data/}\n"
        "  mkdir -p \"$CASH_TEST_DATA_ROOT/$(dirname \"$relative\")\"\n"
        "  printf 'archive' >\"$CASH_TEST_DATA_ROOT/$relative\"\n"
        "fi\n",
    )
    rclone_exit = "exit 1" if rclone_fails else "exit 0"
    copy_exit = "exit 1" if copy_fails else "exit 0"
    executable(
        fake_bin / "rclone",
        "#!/bin/sh\n"
        "printf 'rclone %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "if test \"$1\" = listremotes; then printf 'drive:\\n'; exit 0; fi\n"
        f"if test \"$1\" = lsf; then {rclone_exit}; fi\n"
        f"if test \"$1\" = copyto; then {copy_exit}; fi\n"
        "exit 0\n",
    )
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "CASH_BACKUP_CONFIG_DIR": str(config),
        "CASH_TEST_LOG": str(command_log),
        "CASH_TEST_DATA_ROOT": str(data),
    })
    result = subprocess.run(
        [str(PROJECT_ROOT / "deploy" / "backup-to-drive.sh")],
        env=env, capture_output=True, text=True, check=False)
    return result, state, command_log


def values(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def test_backup_job_creates_uploads_and_records_success(tmp_path):
    result, state, command_log = configured_job(tmp_path)

    assert result.returncode == 0, result.stderr
    status = values(state / "status.conf")
    assert status["state"] == "success"
    assert status["rclone_ok"] == "1"
    assert status["timer_ok"] == "1"
    assert status["backup_name"].startswith("cash-27-full-")
    assert (state / "latest-local.tar.gz").read_bytes() == b"archive"
    assert "podman exec cash-27 python -m cash.ops backup-archive" in \
        command_log.read_text()
    assert "rclone copyto" in command_log.read_text()
    assert "\tsuccess\tBackup uploaded to Google Drive.\t" in \
        (state / "activity.tsv").read_text()


def test_disabled_job_records_heartbeat_without_external_commands(tmp_path):
    result, state, command_log = configured_job(tmp_path, disabled=True)

    assert result.returncode == 0
    assert values(state / "status.conf")["state"] == "disabled"
    assert command_log.read_text() == ""


def test_failed_drive_check_is_visible_in_status(tmp_path):
    result, state, _ = configured_job(tmp_path, rclone_fails=True)

    assert result.returncode != 0
    status = values(state / "status.conf")
    assert status["state"] == "failure"
    assert status["rclone_ok"] == "0"
    assert status["message"] == "Google Drive could not be reached."


def test_failed_upload_keeps_only_one_diagnostic_archive(tmp_path):
    result, state, _ = configured_job(tmp_path, copy_fails=True)

    assert result.returncode != 0
    assert values(state / "status.conf")["state"] == "failure"
    assert (state / "failed-latest.tar.gz").read_bytes() == b"archive"
    assert not list((state / "outbox").glob("*.tar.gz"))
