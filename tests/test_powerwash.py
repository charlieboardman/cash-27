from __future__ import annotations

import os
import pty
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def executable(path: Path, body: str):
    path.write_text(body)
    path.chmod(0o755)


def create_ledger(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE entries (description TEXT)")
        conn.execute("INSERT INTO entries VALUES ('old ledger')")


def assert_ledger(path):
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT description FROM entries").fetchall() == [("old ledger",)]


@pytest.fixture
def installation(tmp_path):
    checkout = tmp_path / "checkout"
    home = tmp_path / "home" / "cash27"
    config = home / ".config" / "cash-27"
    data = home / ".local" / "share" / "cash-27"
    fake_bin = tmp_path / "bin"
    (checkout / "deploy").mkdir(parents=True)
    config.mkdir(parents=True)
    data.mkdir(parents=True)
    fake_bin.mkdir()
    shutil.copy2(PROJECT_ROOT / "powerwash.sh", checkout / "powerwash.sh")
    script = checkout / "powerwash.sh"
    script.write_text(script.read_text().replace('/cash27-backups', str(tmp_path / 'backups')))
    shutil.copy2(PROJECT_ROOT / "deploy" / "powerwash-backup.py",
                 checkout / "deploy" / "powerwash-backup.py")
    for name in ("accounts.csv", "classes.csv"):
        (checkout / name).write_text("name,code\nKeep me,KEEP\n")
    (config / "cash-27.env").write_text("private settings")
    (config / "compose.env").write_text(f"CASH_DATA_ROOT={data}\n")
    create_ledger(data / "cash.db")
    for name in ("documents", "backups", "reports", "caddy"):
        (data / name).mkdir()
        (data / name / "old-file").write_text("old data")
    (home / "unrelated-home-file").write_text("also erased")
    account = tmp_path / "account-exists"
    account.touch()
    operator = tmp_path / "operator.conf"
    operator.write_text(
        f"CASH_SERVICE_USER=cash27\nCASH_SERVICE_HOME={home}\n"
        f"CASH_PROJECT_ROOT={checkout}\n")
    log = tmp_path / "commands.log"
    log.touch()
    executable(fake_bin / "id", '#!/bin/sh\nprintf "%s\\n" "${CASH_TEST_CALLER_UID:-0}"\n')
    executable(fake_bin / "getent", '''#!/bin/sh
test -f "$CASH_TEST_ACCOUNT" || exit 2
printf 'cash27:x:1001:1001::%s:/bin/bash\n' "$CASH_TEST_HOME"
''')
    executable(fake_bin / "stat", '#!/bin/sh\nprintf "%s\\n" "${CASH_TEST_OWNER_UID:-1001}"\n')
    command = '''#!/bin/bash
set -eu
name="${0##*/}"
printf '%s %s\n' "$name" "$*" >>"$CASH_TEST_LOG"
[[ "${CASH_TEST_FAIL:-}" != "$name" ]] || exit 1
if [[ "$name" == userdel ]]; then
    [[ "$*" == '--remove -- cash27' ]] || exit 99
    rm -rf -- "$CASH_TEST_HOME"
    rm -- "$CASH_TEST_ACCOUNT"
fi
'''
    for name in ("systemctl", "loginctl", "podman", "userdel"):
        executable(fake_bin / name, command)
    executable(fake_bin / "ps", '#!/bin/sh\nexit 1\n')
    executable(checkout / "deploy" / "as-runtime-user.sh", '#!/bin/bash\nexec "$@"\n')
    executable(checkout / "deploy" / "compose.sh", '''#!/bin/bash
printf 'compose %s\n' "$*" >>"$CASH_TEST_LOG"
[[ "${CASH_TEST_FAIL:-}" != compose ]]
''')
    executable(checkout / "setup.sh", '#!/bin/sh\nprintf "unexpected setup\\n" >>"$CASH_TEST_LOG"\n')
    env = {**os.environ,
           "PATH": f"{fake_bin}:{os.environ['PATH']}",
           "CASH_OPERATOR_CONFIG": str(operator),
           "CASH_CONFIG_DIR": str(config),
           "CASH_COMPOSE_ENV": str(config / "compose.env"),
           "CASH_TEST_HOME": str(home), "CASH_TEST_ACCOUNT": str(account),
           "CASH_TEST_LOG": str(log)}
    return checkout, home, data, config, operator, account, env, log


def run(installation, answers="yes\nyes\n", *, terminal=True):
    checkout, _, _, _, _, _, env, _ = installation
    if not terminal:
        return subprocess.run([str(checkout / "powerwash.sh")], env=env,
                              input=answers, capture_output=True, text=True)
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen([str(checkout / "powerwash.sh")], env=env,
                                   stdin=slave, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        os.close(slave)
        slave = None
        os.write(master, answers.encode())
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        return subprocess.CompletedProcess(process.args, process.returncode, output, "")
    finally:
        os.close(master)
        if slave is not None:
            os.close(slave)


def test_powerwash_removes_account_home_and_data_after_stopping_everything(installation):
    checkout, home, data, config, operator, account, _, log = installation
    result = run(installation)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("Type yes") == 2
    assert str(home) in result.stdout and str(data) in result.stdout
    assert not home.exists() and not data.exists()
    assert not account.exists() and not operator.exists()
    assert (checkout / "accounts.csv").read_text() == "name,code\nKeep me,KEEP\n"
    assert (checkout / "classes.csv").is_file()
    assert log.read_text().splitlines() == [
        "systemctl --user disable --now cash-27-compose.service",
        "compose down --remove-orphans",
        "podman system reset --force",
        "loginctl disable-linger cash27",
        "loginctl terminate-user cash27",
        "systemctl stop user@1001.service",
        "userdel --remove -- cash27",
    ]
    assert "Run ./setup.sh as root" in result.stdout
    backups = list((checkout.parent / "backups").iterdir())
    assert len(backups) == 1
    saved = backups[0]
    assert saved.name.endswith("Z") and not saved.name.startswith(".")
    assert {p.name for p in saved.iterdir()} == {"data.db", "documents"}
    assert saved.stat().st_mode & 0o777 == 0o700
    assert (saved / "data.db").stat().st_mode & 0o777 == 0o600
    assert_ledger(saved / "data.db")
    assert (saved / "documents" / "old-file").read_text() == "old data"
    assert str(saved) in result.stdout


@pytest.mark.parametrize("answers", ["no\n", "YES\n", " yes\n", "yes \n",
                                     "yes\nno\n", "yes\n\x04", "\x04"])
def test_both_literal_confirmations_are_required_before_any_changes(installation, answers):
    _, home, data, _, operator, account, _, log = installation
    result = run(installation, answers)
    assert result.returncode != 0
    assert "cancelled" in result.stdout
    assert_ledger(data / "cash.db")
    assert home.exists() and operator.exists() and account.exists()
    assert log.read_text() == ""
    assert not (installation[0].parent / "backups").exists()


def test_piped_yes_cannot_bypass_interactive_confirmation(installation):
    result = run(installation, terminal=False)
    assert result.returncode != 0
    assert "interactive terminal" in result.stderr
    assert installation[-1].read_text() == ""


def test_non_root_caller_is_rejected(installation):
    installation[-2]["CASH_TEST_CALLER_UID"] = "1000"
    result = run(installation)
    assert result.returncode != 0
    assert "as root" in result.stdout
    assert installation[-1].read_text() == ""


@pytest.mark.parametrize("failure", ["systemctl", "compose", "podman", "loginctl"])
def test_shutdown_failure_prevents_account_and_ledger_deletion(installation, failure):
    _, home, data, _, operator, account, env, log = installation
    env["CASH_TEST_FAIL"] = failure
    result = run(installation)
    assert result.returncode != 0
    assert (data / "cash.db").is_file()
    assert home.exists() and operator.exists() and account.exists()
    assert "userdel" not in log.read_text()


@pytest.mark.parametrize("unsafe", ["/", "/home", "home", "checkout", "config", "relative", "missing", "symlink", "noncanonical"])
def test_unsafe_data_paths_are_rejected_before_shutdown(installation, tmp_path, unsafe):
    checkout, home, data, config, _, _, _, log = installation
    link = tmp_path / "data-link"
    link.symlink_to(data, target_is_directory=True)
    paths = {"home": home, "checkout": checkout, "config": config,
             "relative": "relative/data", "missing": tmp_path / "missing",
             "symlink": link, "noncanonical": str(data) + "/../cash-27"}
    (config / "compose.env").write_text(f"CASH_DATA_ROOT={paths.get(unsafe, unsafe)}\n")
    result = run(installation)
    assert result.returncode != 0
    assert (data / "cash.db").is_file()
    assert log.read_text() == ""


def test_configured_data_outside_home_is_also_removed(installation, tmp_path):
    _, home, _, config, _, _, _, _ = installation
    external = tmp_path / "external data"
    external.mkdir()
    create_ledger(external / "cash.db")
    (config / "compose.env").write_text(f"CASH_DATA_ROOT={external}\n")
    result = run(installation)
    assert result.returncode == 0, result.stdout
    assert not external.exists() and not home.exists()


def test_foreign_checkout_is_rejected(installation):
    _, _, data, _, operator, _, _, log = installation
    operator.write_text(operator.read_text().replace("CASH_PROJECT_ROOT=", "OTHER_ROOT="))
    result = run(installation)
    assert result.returncode != 0
    assert "installed checkout" in result.stdout
    assert (data / "cash.db").exists() and log.read_text() == ""


def test_foreign_directory_owner_is_rejected(installation):
    installation[-2]["CASH_TEST_OWNER_UID"] = "1002"
    result = run(installation)
    assert result.returncode != 0
    assert "not owned" in result.stdout
    assert installation[-1].read_text() == ""


def test_account_removal_failure_is_reported_without_claiming_success(installation):
    _, home, data, _, operator, account, env, _ = installation
    env["CASH_TEST_FAIL"] = "userdel"
    result = run(installation)
    assert result.returncode != 0
    assert "account removal failed" in result.stdout
    assert "Powerwash complete" not in result.stdout
    assert not data.exists()
    assert home.exists() and operator.exists() and account.exists()


@pytest.mark.parametrize("behavior", ["delayed_exit", "stuck", "inspection_error", "manager_stop_error"])
def test_runtime_shutdown_is_complete_before_data_deletion(installation, behavior):
    checkout, home, data, _, operator, account, _, log = installation
    fake_bin = checkout.parent / "bin"
    executable(fake_bin / "sleep", '#!/bin/sh\nexit 0\n')
    executable(fake_bin / "ps", '''#!/bin/bash
[[ "$*" == '-u 1001 -U 1001 -o pid=,args=' ]] || exit 2
printf 'process-check\n' >>"$CASH_TEST_LOG"
'''+ {
        "delayed_exit": '''if [[ ! -f "$CASH_TEST_LOG.exiting" ]]; then
    touch "$CASH_TEST_LOG.exiting"
    printf '4562 podman-helper\n'
    exit 0
fi
exit 1
''',
        "stuck": "printf '4562 podman-helper\\n'\nexit 0\n",
        "inspection_error": "exit 2\n",
        "manager_stop_error": "exit 1\n",
    }[behavior])
    if behavior == "manager_stop_error":
        manager = fake_bin / "systemctl"
        manager.write_text(manager.read_text().replace(
            'if [[ "$name" == userdel ]]; then',
            '[[ "$*" != "stop user@1001.service" ]] || exit 1\n'
            'if [[ "$name" == userdel ]]; then'))
    result = run(installation)
    commands = log.read_text()
    if behavior == "delayed_exit":
        assert result.returncode == 0, result.stdout
        assert commands.count("process-check") == 2
        assert commands.rindex("process-check") < commands.index("userdel")
        assert not data.exists() and not account.exists()
    else:
        assert result.returncode != 0
        assert "account and application data were not deleted" in result.stdout
        assert home.exists() and operator.exists() and account.exists()
        assert_ledger(data / "cash.db")
        assert "userdel" not in commands
        if behavior == "stuck":
            assert "4562 podman-helper" in result.stdout


@pytest.mark.parametrize("failure", ["corrupt_database", "missing_database", "document_symlink",
                                     "backup_symlink", "copy_error"])
def test_backup_failure_prevents_deletion(installation, tmp_path, failure):
    checkout, home, data, _, operator, account, _, log = installation
    if failure == "corrupt_database":
        (data / "cash.db").write_bytes(b"not a SQLite database")
    elif failure == "missing_database":
        (data / "cash.db").unlink()
    elif failure == "document_symlink":
        (data / "documents" / "link.pdf").symlink_to(data / "documents" / "old-file")
    elif failure == "backup_symlink":
        target = tmp_path / "elsewhere"
        target.mkdir()
        (tmp_path / "backups").symlink_to(target, target_is_directory=True)
    else:
        helper = checkout / "deploy" / "powerwash-backup.py"
        helper.write_text(helper.read_text().replace(
            "shutil.copyfile(source, destination)", "raise OSError('simulated disk full')"))
    result = run(installation)
    assert result.returncode != 0
    assert "backup failed" in result.stdout
    assert "Powerwash complete" not in result.stdout
    assert home.exists() and data.exists() and operator.exists() and account.exists()
    assert "podman system reset" not in log.read_text()
    assert "userdel" not in log.read_text()


def test_backup_preserves_wal_and_nested_documents_without_overwriting(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    docs = data / "documents" / "nested"
    docs.mkdir(parents=True)
    (docs / "receipt.pdf").write_bytes(b"%PDF-example")
    backups = tmp_path / "backups"
    conn = sqlite3.connect(data / "cash.db")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE entries (description TEXT)")
        conn.execute("INSERT INTO entries VALUES ('old ledger')")
        conn.commit()
        assert (data / "cash.db-wal").stat().st_size > 0
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "deploy" / "powerwash-backup.py"),
                 str(data), str(backups)], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
            saved = Path(result.stdout.strip())
            assert_ledger(saved / "data.db")
            assert {p.name for p in saved.iterdir()} == {"data.db", "documents"}
            assert (saved / "documents" / "nested" / "receipt.pdf").read_bytes() == b"%PDF-example"
        assert len(list(backups.iterdir())) == 2
    finally:
        conn.close()
