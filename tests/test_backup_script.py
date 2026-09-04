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
                   copy_fails: bool = False,
                   mode: tuple[str, ...] = ("once",),
                   remote_files: str = ""):
    tmp_path.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "data"
    state = data / "backups" / "drive"
    fake_bin = tmp_path / "bin"
    state.mkdir(parents=True)
    fake_bin.mkdir()
    data.joinpath("cash.db").touch()
    if disabled:
        state.joinpath("automatic-backups-disabled").write_text("yes\n")

    command_log = tmp_path / "commands.log"
    command_log.touch()
    executable(
        fake_bin / "python",
        "#!/bin/sh\n"
        "printf 'python %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "if test \"$2\" = cash.backup_client && test \"$3\" = create; then\n"
        "  mkdir -p \"$CASH_BACKUP_STATE_ROOT/$4\"\n"
        "  printf 'archive' >\"$CASH_BACKUP_STATE_ROOT/$4/$5\"\n"
        "fi\n"
        "exit 0\n",
    )
    rclone_exit = "exit 1" if rclone_fails else "exit 0"
    copy_exit = "exit 1" if copy_fails else "exit 0"
    executable(
        fake_bin / "rclone",
        "#!/bin/sh\n"
        "printf 'rclone %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "if test \"$1\" = listremotes; then printf 'drive:\\n'; exit 0; fi\n"
        "if test \"$1 $2\" = 'config show'; then printf 'type = drive\\n'; exit 0; fi\n"
        "if test \"$1\" = lsf; then\n"
        "  case \"$*\" in *--include*) printf '%s' \"$CASH_TEST_REMOTE_FILES\" | grep '^cash-27-full-' || true;;\n"
        "    *) printf '%s' \"$CASH_TEST_REMOTE_FILES\";; esac\n"
        f"  {rclone_exit}\n"
        "fi\n"
        "if test \"$1\" = copyto && printf '%s' \"$2\" | grep -q '^drive:'; then\n"
        "  printf 'archive' >\"$3\"\n"
        "fi\n"
        f"if test \"$1\" = copyto; then {copy_exit}; fi\n"
        "exit 0\n",
    )
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "CASH_DATA_ROOT": str(data),
        "CASH_BACKUP_STATE_ROOT": str(state),
        "CASH_BACKUP_CONFIGURED": "1",
        "CASH_BACKUP_REMOTE": "drive",
        "CASH_BACKUP_PATH": "Cash 27 Backups",
        "CASH_TEST_LOG": str(command_log),
        "CASH_TEST_REMOTE_FILES": remote_files,
    })
    result = subprocess.run(
        [str(PROJECT_ROOT / "deploy" / "backup-to-drive.sh"), *mode],
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
    assert status["scheduler_ok"] == "1"
    assert status["backup_name"].startswith("cash-27-full-")
    assert (state / "latest-local.tar.gz").read_bytes() == b"archive"
    assert "python -m cash.backup_client create outbox" in \
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


def test_drive_adapter_lists_and_fetches_only_named_full_backups(tmp_path):
    name = "cash-27-full-20260904T120000123456Z.tar.gz"
    listed, _, _ = configured_job(
        tmp_path / "list", mode=("list",),
        remote_files=f"notes.txt\n{name}\n")
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.strip() == name

    fetched, state, log = configured_job(
        tmp_path / "fetch", mode=("fetch", name),
        remote_files=f"{name}\n")
    assert fetched.returncode == 0, fetched.stderr
    assert (state / "restore" / name).read_bytes() == b"archive"
    assert "python -m cash.ops verify-archive" in log.read_text()


def test_drive_adapter_rejects_unsafe_restore_name(tmp_path):
    result, _, log = configured_job(
        tmp_path, mode=("fetch", "../../cash.db"))
    assert result.returncode != 0
    assert "valid Cash 27 backup name" in result.stderr
    assert log.read_text() == ""
