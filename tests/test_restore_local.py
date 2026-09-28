from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP = "backups/local/scheduled/cash-27-full-20260904T120000123456Z.tar.gz"


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
    fake_bin.mkdir()
    (data / BACKUP).parent.mkdir(parents=True)
    (data / BACKUP).write_bytes(b"archive")
    shutil.copy2(PROJECT_ROOT / "restore-local.sh", checkout / "restore-local.sh")
    (config / "compose.env").write_text(
        f"CASH_DATA_ROOT={data}\nCASH_APP_IMAGE=localhost/cash-27:example\n")
    executable(deploy / "compose.sh", r'''#!/bin/bash
set -eu
printf 'compose %s\n' "$*" >>"$CASH_TEST_LOG"
if [[ "$*" == *'verify-archive /data/backups/local/'* ]]; then
  exit "${CASH_TEST_VERIFY_FAIL:-0}"
fi
if [[ "$*" == *'backup-archive'* ]]; then
  if test "${CASH_TEST_LOCAL_FAIL:-0}" = 1; then exit 1; fi
fi
if [[ "$*" == *'backup-archive'* || "$*" == *'forensic-archive'* ]]; then
  target="${@: -1}"
  relative="${target#/data/}"
  mkdir -p "$CASH_TEST_DATA/$(dirname "$relative")"
  printf safety >"$CASH_TEST_DATA/$relative"
fi
if [[ "$*" == *'restore-archive /data /data/backups/local/'* ]]; then
  exit "${CASH_TEST_RESTORE_FAIL:-0}"
fi
''')
    executable(fake_bin / "podman", "#!/bin/sh\nexit 0\n")
    executable(fake_bin / "curl", "#!/bin/sh\nexit 0\n")
    command_log = tmp_path / "commands.log"
    command_log.touch()
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
           "CASH_CONFIG_DIR": str(config),
           "CASH_RESTORE_CONFIRM": "RESTORE CASH 27",
           "CASH_TEST_DATA": str(data), "CASH_TEST_LOG": str(command_log),
           "CASH_RESTORE_HEALTH_ATTEMPTS": "1", "CASH_RESTORE_HEALTH_DELAY": "0"}
    return checkout, data, env, command_log


def run(checkout, env, backup=BACKUP):
    return subprocess.run([str(checkout / "restore-local.sh"), backup],
                          cwd=checkout, env=env, capture_output=True, text=True)


def test_restore_verifies_stops_preserves_and_restores(tmp_path):
    checkout, data, env, command_log = restore_fixture(tmp_path)
    result = run(checkout, env)
    assert result.returncode == 0, result.stderr
    logged = command_log.read_text()
    assert logged.index("stop local-backup") < logged.index("verify-archive")
    assert logged.index("verify-archive") < logged.index("stop app")
    assert logged.index("stop app") < logged.index("backup-archive")
    assert logged.index("backup-archive") < logged.index("restore-archive")
    assert f"restore-tool restore-archive /data /data/{BACKUP}" in logged
    assert "up --detach --remove-orphans" in logged
    assert (data / BACKUP).is_file()
    assert len(list((data / "backups/restore").glob("cash-27-full-*.tar.gz"))) == 1
    assert "Restore completed successfully." in result.stdout


@pytest.mark.parametrize("path", ["../../cash.db", "/tmp/archive.tar.gz",
                                   BACKUP.replace("20260904", "20260903")])
def test_restore_rejects_unsafe_or_missing_archive_before_stopping(tmp_path, path):
    checkout, _, env, command_log = restore_fixture(tmp_path)
    result = run(checkout, env, path)
    assert result.returncode != 0
    assert "stop " not in command_log.read_text()


def test_bad_archive_does_not_stop_app_or_replace_data(tmp_path):
    checkout, _, env, command_log = restore_fixture(tmp_path)
    env["CASH_TEST_VERIFY_FAIL"] = "1"
    result = run(checkout, env)
    assert result.returncode != 0
    logged = command_log.read_text()
    assert "stop app" not in logged
    assert "restore-archive" not in logged
    assert "up --detach --remove-orphans" in logged


def test_restore_preserves_raw_files_when_current_database_is_broken(tmp_path):
    checkout, data, env, command_log = restore_fixture(tmp_path)
    env["CASH_TEST_LOCAL_FAIL"] = "1"
    result = run(checkout, env)
    assert result.returncode == 0, result.stderr
    logged = command_log.read_text()
    assert "forensic-archive /data /data/backups/restore/" in logged
    assert "current database cannot produce a verified safety archive" in result.stderr
    assert len(list((data / "backups/restore").glob("cash-27-forensic-*.tar.gz"))) == 1


def test_failed_restore_reinstates_safety_archive(tmp_path):
    checkout, _, env, command_log = restore_fixture(tmp_path)
    env["CASH_TEST_RESTORE_FAIL"] = "1"
    result = run(checkout, env)
    assert result.returncode != 0
    assert "prior data was reinstated" in result.stderr
    logged = command_log.read_text()
    assert logged.count("restore-tool restore-archive") == 2
    assert "restore-archive /data /data/backups/restore/" in logged
    assert "up --detach --remove-orphans" in logged


def test_failed_restore_without_safety_archive_stays_stopped(tmp_path):
    checkout, _, env, command_log = restore_fixture(tmp_path)
    env.update(CASH_TEST_RESTORE_FAIL="1", CASH_TEST_LOCAL_FAIL="1")
    result = run(checkout, env)
    assert result.returncode != 0
    assert "application remains stopped" in result.stderr
    assert "up --detach" not in command_log.read_text()


def drive_fixture(tmp_path):
    checkout, data, env, command_log = restore_fixture(tmp_path)
    shutil.copy2(PROJECT_ROOT / "restore-drive.sh", checkout / "restore-drive.sh")
    compose = checkout / "deploy" / "compose.sh"
    compose.rename(checkout / "deploy" / "compose-base.sh")
    executable(compose, r'''#!/bin/bash
set -eu
if [[ "$*" == *'drive-archive'* ]]; then
  printf 'compose %s\n' "$*" >>"$CASH_TEST_LOG"
  for arg in "$@"; do
    if [[ "$arg" == /data/staging/* ]]; then test -s "$CASH_TEST_DATA/${arg#/data/}"; fi
  done
  if test "${CASH_TEST_DRIVE_FAIL:-0}" = 1; then exit 1; fi
  target=""
  for arg in "$@"; do if [[ "$arg" == /data/backups/* ]]; then target="$arg"; fi; done
  mkdir -p "$CASH_TEST_DATA/$(dirname "${target#/data/}")"
  printf archive >"$CASH_TEST_DATA/${target#/data/}"
  exit 0
fi
exec "$(dirname "$0")/compose-base.sh" "$@"
''')
    zips = [tmp_path / "drive-download-1.zip", tmp_path / "drive-download-2.zip"]
    for path in zips:
        path.write_bytes(b"zip")
    return checkout, data, env, command_log, zips


def test_drive_restore_builds_an_archive_then_restores_it(tmp_path):
    checkout, data, env, command_log, zips = drive_fixture(tmp_path)
    result = subprocess.run([str(checkout / "restore-drive.sh"), *map(str, zips),
                             "--database", "cash-27-20260927T093000Z.db"],
                            cwd=checkout, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    logged = command_log.read_text()
    [drive] = [line for line in logged.splitlines() if "drive-archive" in line]
    assert "restore-tool drive-archive /data/backups/restore/cash-27-full-" in drive
    assert drive.count("/data/staging/drive-restore-") == 2
    assert drive.endswith("--database=cash-27-20260927T093000Z.db")
    archive = drive.split("drive-archive /data/")[1].split(" ")[0]
    assert f"restore-tool restore-archive /data /data/{archive}" in logged
    assert not list((data / "staging").glob("drive-restore-*"))
    assert "Restore completed successfully." in result.stdout


def test_drive_restore_changes_nothing_when_the_archive_fails(tmp_path):
    checkout, data, env, command_log, zips = drive_fixture(tmp_path)
    env["CASH_TEST_DRIVE_FAIL"] = "1"
    result = subprocess.run([str(checkout / "restore-drive.sh"), str(zips[0])],
                            cwd=checkout, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "nothing was changed" in result.stderr
    logged = command_log.read_text()
    assert "stop " not in logged and "restore-archive" not in logged
    assert not list((data / "staging").glob("drive-restore-*"))


@pytest.mark.parametrize("args", [[], ["--database", "../cash.db", "x.zip"], ["missing.zip"]])
def test_drive_restore_rejects_bad_arguments(tmp_path, args):
    checkout, _, env, command_log, _ = drive_fixture(tmp_path)
    result = subprocess.run([str(checkout / "restore-drive.sh"), *args],
                            cwd=checkout, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert command_log.read_text() == ""
