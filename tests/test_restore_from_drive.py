from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP = "cash-27-full-20260904T120000123456Z.tar.gz"


def executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def restore_fixture(tmp_path: Path):
    checkout = tmp_path / "checkout"
    deploy = checkout / "deploy"
    config = tmp_path / "config"
    data = tmp_path / "data"
    fake_bin = tmp_path / "bin"
    deploy.mkdir(parents=True)
    config.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    shutil.copy2(
        PROJECT_ROOT / "restore-from-drive.sh",
        checkout / "restore-from-drive.sh")
    (checkout / "restore-from-drive.sh").chmod(0o755)
    (config / "compose.env").write_text(
        f"CASH_DATA_ROOT={data}\n"
        "CASH_APP_IMAGE=localhost/cash-27:example\n"
        "CASH_BACKUP_IMAGE=localhost/cash-27-drive-backup:example\n")
    executable(
        deploy / "compose.sh",
        "#!/bin/bash\n"
        "set -eu\n"
        "printf 'compose %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        f"if [[ \"$*\" == *'drive-backup list'* ]]; then printf '%s\\n' '{BACKUP}'; fi\n"
        "if [[ \"$*\" == *'drive-backup fetch'* ]]; then\n"
        "  name=\"${@: -1}\"\n"
        "  mkdir -p \"$CASH_TEST_DATA/backups/drive/restore\"\n"
        "  printf archive >\"$CASH_TEST_DATA/backups/drive/restore/$name\"\n"
        "  printf '/state/restore/%s\\n' \"$name\"\n"
        "fi\n"
        "if [[ \"$*\" == *'drive-backup local'* ]]; then\n"
        "  if test \"${CASH_TEST_LOCAL_FAIL:-0}\" = 1; then exit 1; fi\n"
        "  name=\"${@: -1}\"\n"
        "  mkdir -p \"$CASH_TEST_DATA/backups/drive/restore\"\n"
        "  printf rollback >\"$CASH_TEST_DATA/backups/drive/restore/$name\"\n"
        "fi\n"
        "if [[ \"$*\" == *'forensic-archive'* ]]; then\n"
        "  target=\"${@: -1}\"\n"
        "  relative=\"${target#/data/}\"\n"
        "  mkdir -p \"$CASH_TEST_DATA/$(dirname \"$relative\")\"\n"
        "  printf forensic >\"$CASH_TEST_DATA/$relative\"\n"
        "fi\n",
    )
    executable(fake_bin / "podman", "#!/bin/sh\nexit 0\n")
    executable(fake_bin / "curl", "#!/bin/sh\nexit 0\n")
    command_log = tmp_path / "commands.log"
    command_log.touch()
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "CASH_CONFIG_DIR": str(config),
        "CASH_RESTORE_CONFIRM": "RESTORE CASH 27",
        "CASH_TEST_DATA": str(data),
        "CASH_TEST_LOG": str(command_log),
        "CASH_RESTORE_HEALTH_ATTEMPTS": "1",
        "CASH_RESTORE_HEALTH_DELAY": "0",
    })
    return checkout, data, env, command_log


def test_restore_from_drive_downloads_verifies_stops_and_restores(tmp_path):
    checkout, data, env, command_log = restore_fixture(tmp_path)

    result = subprocess.run(
        [str(checkout / "restore-from-drive.sh"), BACKUP],
        cwd=checkout, env=env, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    logged = command_log.read_text()
    assert "drive-backup list" in logged
    assert f"drive-backup fetch {BACKUP}" in logged
    assert "stop drive-backup" in logged
    assert "drive-backup local cash-27-full-" in logged
    assert "stop app" in logged
    assert "restore-tool restore-archive /data /data/backups/drive/restore/" in logged
    assert "up --detach --remove-orphans" in logged
    assert logged.index("drive-backup fetch") < logged.index("stop drive-backup")
    assert logged.index("stop drive-backup") < logged.index("stop app")
    assert (data / "backups" / "drive" / "restore" / BACKUP).is_file()
    assert "Restore completed successfully." in result.stdout


def test_restore_from_drive_rejects_unlisted_name_before_stopping(tmp_path):
    checkout, _, env, command_log = restore_fixture(tmp_path)
    result = subprocess.run(
        [str(checkout / "restore-from-drive.sh"),
         "cash-27-full-20260903T120000123456Z.tar.gz"],
        cwd=checkout, env=env, capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "not found in Google Drive" in result.stderr
    assert "stop " not in command_log.read_text()


def test_restore_preserves_raw_files_when_current_database_is_broken(tmp_path):
    checkout, data, env, command_log = restore_fixture(tmp_path)
    env["CASH_TEST_LOCAL_FAIL"] = "1"

    result = subprocess.run(
        [str(checkout / "restore-from-drive.sh"), BACKUP],
        cwd=checkout, env=env, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    logged = command_log.read_text()
    assert "forensic-archive /data /data/backups/drive/restore/" in logged
    assert "current database cannot produce a verified safety archive" in \
        result.stderr
    forensic = list(
        (data / "backups" / "drive" / "restore").glob(
            "cash-27-forensic-*.tar.gz"))
    assert len(forensic) == 1
    assert "Raw pre-restore files:" in result.stdout
