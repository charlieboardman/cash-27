from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def upgrade_checkout(tmp_path: Path):
    checkout = tmp_path / "checkout"
    deploy = checkout / "deploy"
    config = tmp_path / "config"
    fake_bin = tmp_path / "bin"
    deploy.mkdir(parents=True)
    config.mkdir()
    fake_bin.mkdir()
    shutil.copy2(PROJECT_ROOT / "upgrade.sh", checkout / "upgrade.sh")
    shutil.copy2(
        PROJECT_ROOT / "deploy" / "upgrade-installed.sh",
        deploy / "upgrade-installed.sh",
    )
    executable(
        deploy / "image-ref.sh",
        "#!/bin/sh\n"
        "printf '%s\\n' localhost/cash-27:222222222222\n",
    )
    executable(
        deploy / "podman-build.sh",
        "#!/bin/sh\nprintf '%s\\n' build >>\"$CASH_TEST_LOG\"\n",
    )
    executable(
        deploy / "compose.sh",
        "#!/bin/sh\nprintf 'compose %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "case \"$*\" in *'local-backup once --kind pre-upgrade'*) exit \"${CASH_TEST_BACKUP_EXIT:-0}\";; esac\n"
        "exit 0\n",
    )
    compose_env = config / "compose.env"
    compose_env.write_text(
        "CASH_APP_IMAGE=localhost/cash-27:111111111111\n"
        "CASH_DATA_ROOT=/persistent/data\n"
    )
    compose_env.chmod(0o600)
    return checkout, config, fake_bin, compose_env


def base_env(tmp_path: Path, config: Path, fake_bin: Path):
    command_log = tmp_path / "commands.log"
    command_log.touch()
    executable(
        fake_bin / "git",
        "#!/bin/sh\n"
        "printf 'git %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "exit 0\n",
    )
    executable(
        fake_bin / "podman",
        "#!/bin/sh\n"
        "if test \"$1 $2\" = 'compose version'; then exit 0; fi\n"
        "exit 0\n",
    )
    executable(fake_bin / "podman-compose", "#!/bin/sh\nexit 0\n")
    executable(fake_bin / "curl", "#!/bin/sh\nexit 0\n")
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "CASH_CONFIG_DIR": str(config),
        "CASH_TEST_LOG": str(command_log),
        "CASH_UPGRADE_HEALTH_ATTEMPTS": "1",
        "CASH_UPGRADE_HEALTH_DELAY": "0",
    })
    return env, command_log


def test_upgrade_pulls_builds_switches_compose_images_and_checks_health(tmp_path):
    checkout, config, fake_bin, compose_env = upgrade_checkout(tmp_path)
    env, command_log = base_env(tmp_path, config, fake_bin)

    result = subprocess.run(
        [str(checkout / "upgrade.sh")], cwd=tmp_path, env=env,
        capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    installed = compose_env.read_text()
    assert "CASH_APP_IMAGE=localhost/cash-27:222222222222" in installed
    assert "CASH_DATA_ROOT=/persistent/data" in installed
    assert compose_env.stat().st_mode & 0o777 == 0o600
    logged = command_log.read_text()
    assert " pull --ff-only" in logged
    assert "compose run --rm --no-deps local-backup once --kind pre-upgrade" in logged
    assert "build\n" in logged
    assert logged.index("local-backup once --kind pre-upgrade") < logged.index(" pull --ff-only")
    assert logged.index(" pull --ff-only") < logged.index("build\n")
    assert "compose up --detach --remove-orphans" in logged
    assert "Previous application image: localhost/cash-27:111111111111" in result.stdout
    assert "Running application image:  localhost/cash-27:222222222222" in result.stdout


def test_upgrade_refuses_local_changes_before_backup_or_pull(tmp_path):
    checkout = tmp_path / "checkout"
    fake_bin = tmp_path / "bin"
    checkout.mkdir()
    fake_bin.mkdir()
    shutil.copy2(PROJECT_ROOT / "upgrade.sh", checkout / "upgrade.sh")
    command_log = tmp_path / "commands.log"
    executable(
        fake_bin / "git",
        "#!/bin/sh\n"
        "printf 'git %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "case \"$*\" in *'status --porcelain'*) printf '%s\\n' ' M setup.sh';; esac\n",
    )
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "CASH_TEST_LOG": str(command_log),
    })

    result = subprocess.run(
        [str(checkout / "upgrade.sh")], env=env,
        capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "local changes" in result.stderr
    assert "pull --ff-only" not in command_log.read_text()


def test_failed_health_check_restores_prior_compose_images(tmp_path):
    checkout, config, fake_bin, compose_env = upgrade_checkout(tmp_path)
    env, command_log = base_env(tmp_path, config, fake_bin)
    curl_count = tmp_path / "curl-count"
    executable(
        fake_bin / "curl",
        "#!/bin/sh\n"
        "count=0\n"
        "test ! -f \"$CASH_TEST_CURL_COUNT\" || count=$(sed -n '1p' \"$CASH_TEST_CURL_COUNT\")\n"
        "count=$((count + 1))\n"
        "printf '%s\\n' \"$count\" >\"$CASH_TEST_CURL_COUNT\"\n"
        "test \"$count\" -gt 1\n",
    )
    env["CASH_TEST_CURL_COUNT"] = str(curl_count)

    result = subprocess.run(
        [str(checkout / "upgrade.sh")], cwd=tmp_path, env=env,
        capture_output=True, text=True, check=False)

    assert result.returncode != 0
    installed = compose_env.read_text()
    assert "CASH_APP_IMAGE=localhost/cash-27:111111111111" in installed
    assert "prior images are running again" in result.stderr
    assert command_log.read_text().count(
        "compose up --detach --remove-orphans"
    ) == 2


def test_failed_pre_upgrade_backup_does_not_pull_or_build(tmp_path):
    checkout, config, fake_bin, _ = upgrade_checkout(tmp_path)
    env, command_log = base_env(tmp_path, config, fake_bin)
    env["CASH_TEST_BACKUP_EXIT"] = "1"

    result = subprocess.run(
        [str(checkout / "upgrade.sh")], cwd=tmp_path, env=env,
        capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "pre-upgrade backup failed" in result.stderr
    logged = command_log.read_text()
    assert "local-backup once --kind pre-upgrade" in logged
    assert "pull --ff-only" not in logged
    assert "build\n" not in logged
