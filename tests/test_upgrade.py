from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


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
        "#!/bin/sh\nprintf '%s\\n' build >>\"$CASH_TEST_LOG\"\n"
        "touch \"$CASH_TEST_BUILT\"\n",
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
        "printf 'podman %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "if test \"$1\" = images; then\n"
        "  test \"${CASH_TEST_LIST_FAIL:-0}\" = 0 || exit 1\n"
        "  if test -e \"$CASH_TEST_BUILT\"; then\n"
        "    printf '%s\\n' \"${CASH_TEST_IMAGES_AFTER:-}\"\n"
        "  else printf '%s\\n' \"${CASH_TEST_IMAGES_BEFORE:-}\"; fi\n"
        "fi\n"
        "if test \"$1\" = rmi && test \"$2\" = \"${CASH_TEST_REMOVE_FAIL:-}\"; then exit 1; fi\n"
        "exit 0\n",
    )
    executable(fake_bin / "podman-compose", "#!/bin/sh\nexit 0\n")
    executable(fake_bin / "curl", "#!/bin/sh\nexit 0\n")
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "CASH_CONFIG_DIR": str(config),
        "CASH_TEST_LOG": str(command_log),
        "CASH_TEST_BUILT": str(tmp_path / "built"),
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
    # A failed release never triggers the post-success retention pass.
    assert command_log.read_text().count("podman images ") == 1


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


@pytest.mark.parametrize("same_release", [False, True])
def test_upgrade_retains_three_images_and_protects_older_deployed_release(tmp_path, same_release):
    checkout, config, fake_bin, _ = upgrade_checkout(tmp_path)
    env, command_log = base_env(tmp_path, config, fake_bin)
    current = "localhost/cash-27:111111111111"
    newest = "localhost/cash-27:aaaaaaaaaaaa"
    second = "localhost/cash-27:bbbbbbbbbbbb"
    oldest = "localhost/cash-27:cccccccccccc"
    new = current if same_release else "localhost/cash-27:222222222222"
    if same_release:
        executable(checkout / "deploy" / "image-ref.sh", f"#!/bin/sh\necho {current}\n")
    # The deployed release can be older than other locally built images.
    env["CASH_TEST_IMAGES_BEFORE"] = "\n".join([newest, second, current, oldest])
    env["CASH_TEST_IMAGES_AFTER"] = "\n".join(dict.fromkeys([new, newest, second, current]))

    result = subprocess.run([str(checkout / "upgrade.sh")], env=env,
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    logged = command_log.read_text()
    before, after = logged.split("build\n")
    assert f"podman rmi {oldest}\n" in before
    assert f"podman rmi {second}\n" not in before
    assert (f"podman rmi {second}\n" in after) is not same_release
    assert f"podman rmi {current}\n" not in logged
    assert f"podman rmi {newest}\n" not in logged
    assert f"podman rmi {new}\n" not in logged
    assert "--force" not in logged
    if not same_release:
        assert after.index("document_worker health") < after.index(f"podman rmi {second}")


@pytest.mark.parametrize("failure", ["list", "remove"])
def test_image_cleanup_failure_does_not_fail_healthy_upgrade(tmp_path, failure):
    checkout, config, fake_bin, compose_env = upgrade_checkout(tmp_path)
    env, command_log = base_env(tmp_path, config, fake_bin)
    images = [f"localhost/cash-27:{letter * 12}" for letter in "abcd"]
    env["CASH_TEST_IMAGES_BEFORE"] = "\n".join(images)
    env["CASH_TEST_IMAGES_AFTER"] = "\n".join(images)
    if failure == "list":
        env["CASH_TEST_LIST_FAIL"] = "1"
    else:
        env["CASH_TEST_REMOVE_FAIL"] = images[-1]

    result = subprocess.run([str(checkout / "upgrade.sh")], env=env,
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "Warning:" in result.stderr
    assert "upgraded successfully" in result.stdout
    assert "CASH_APP_IMAGE=localhost/cash-27:222222222222" in compose_env.read_text()
    if failure == "list":
        assert "podman rmi" not in command_log.read_text()
