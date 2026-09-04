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
