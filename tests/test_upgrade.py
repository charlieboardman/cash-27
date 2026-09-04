from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def test_upgrade_pulls_builds_switches_quadlet_and_checks_health(tmp_path):
    checkout = tmp_path / "checkout"
    deploy = checkout / "deploy"
    fake_bin = tmp_path / "bin"
    deploy.mkdir(parents=True)
    fake_bin.mkdir()

    shutil.copy2(PROJECT_ROOT / "upgrade.sh", checkout / "upgrade.sh")
    shutil.copy2(
        PROJECT_ROOT / "deploy" / "upgrade-installed.sh",
        deploy / "upgrade-installed.sh",
    )
    executable(
        deploy / "image-ref.sh",
        "#!/bin/sh\nprintf '%s\\n' localhost/cash-27:222222222222\n",
    )
    executable(
        deploy / "podman-build.sh",
        "#!/bin/sh\nprintf '%s\\n' build >>\"$CASH_TEST_LOG\"\n",
    )

    command_log = tmp_path / "commands.log"
    executable(
        fake_bin / "git",
        "#!/bin/sh\n"
        "printf 'git %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "exit 0\n",
    )
    executable(fake_bin / "podman", "#!/bin/sh\nexit 0\n")
    executable(
        fake_bin / "systemctl",
        "#!/bin/sh\n"
        "printf 'systemctl %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "exit 0\n",
    )
    executable(
        fake_bin / "curl",
        "#!/bin/sh\n"
        "printf 'curl %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "exit 0\n",
    )

    quadlet = tmp_path / "cash-27.container"
    quadlet.write_text(
        "[Container]\n"
        "Image=localhost/cash-27:111111111111\n"
        "Volume=/persistent/data:/data:Z\n"
        "EnvironmentFile=/private/cash-27.env\n"
    )
    quadlet.chmod(0o600)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CASH_TEST_LOG": str(command_log),
            "CASH_UPGRADE_QUADLET_FILE": str(quadlet),
            "CASH_UPGRADE_HEALTH_ATTEMPTS": "1",
            "CASH_UPGRADE_HEALTH_DELAY": "0",
        }
    )
    result = subprocess.run(
        [str(checkout / "upgrade.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Image=localhost/cash-27:222222222222" in quadlet.read_text()
    assert "Volume=/persistent/data:/data:Z" in quadlet.read_text()
    assert "EnvironmentFile=/private/cash-27.env" in quadlet.read_text()
    assert quadlet.stat().st_mode & 0o777 == 0o600

    logged = command_log.read_text()
    assert "git -C " in logged
    assert " pull --ff-only" in logged
    assert "build\n" in logged
    assert "systemctl --user daemon-reload" in logged
    assert "systemctl --user restart cash-27.service" in logged
    assert "curl --silent --show-error --fail --max-time 2" in logged
    assert "Previous image: localhost/cash-27:111111111111" in result.stdout
    assert "Running image:  localhost/cash-27:222222222222" in result.stdout


def test_upgrade_refuses_local_changes_before_pull(tmp_path):
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
        "case \"$*\" in\n"
        "  *'status --porcelain'*) printf '%s\\n' ' M setup.sh' ;;\n"
        "esac\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CASH_TEST_LOG": str(command_log),
        }
    )

    result = subprocess.run(
        [str(checkout / "upgrade.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "local changes" in result.stderr
    assert "pull --ff-only" not in command_log.read_text()


def test_failed_health_check_restores_prior_quadlet(tmp_path):
    checkout = tmp_path / "checkout"
    deploy = checkout / "deploy"
    fake_bin = tmp_path / "bin"
    deploy.mkdir(parents=True)
    fake_bin.mkdir()

    shutil.copy2(PROJECT_ROOT / "upgrade.sh", checkout / "upgrade.sh")
    shutil.copy2(
        PROJECT_ROOT / "deploy" / "upgrade-installed.sh",
        deploy / "upgrade-installed.sh",
    )
    executable(
        deploy / "image-ref.sh",
        "#!/bin/sh\nprintf '%s\\n' localhost/cash-27:new-image\n",
    )
    executable(deploy / "podman-build.sh", "#!/bin/sh\nexit 0\n")

    command_log = tmp_path / "commands.log"
    curl_count = tmp_path / "curl-count"
    executable(
        fake_bin / "git",
        "#!/bin/sh\nexit 0\n",
    )
    executable(fake_bin / "podman", "#!/bin/sh\nexit 0\n")
    executable(
        fake_bin / "systemctl",
        "#!/bin/sh\n"
        "printf 'systemctl %s\\n' \"$*\" >>\"$CASH_TEST_LOG\"\n"
        "exit 0\n",
    )
    executable(
        fake_bin / "curl",
        "#!/bin/sh\n"
        "count=0\n"
        "test ! -f \"$CASH_TEST_CURL_COUNT\" || count=$(cat \"$CASH_TEST_CURL_COUNT\")\n"
        "count=$((count + 1))\n"
        "printf '%s\\n' \"$count\" >\"$CASH_TEST_CURL_COUNT\"\n"
        "test \"$count\" -gt 1\n",
    )

    quadlet = tmp_path / "cash-27.container"
    original = (
        "[Container]\n"
        "Image=localhost/cash-27:known-good\n"
        "Volume=/persistent/data:/data:Z\n"
    )
    quadlet.write_text(original)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CASH_TEST_LOG": str(command_log),
            "CASH_TEST_CURL_COUNT": str(curl_count),
            "CASH_UPGRADE_QUADLET_FILE": str(quadlet),
            "CASH_UPGRADE_HEALTH_ATTEMPTS": "1",
            "CASH_UPGRADE_HEALTH_DELAY": "0",
        }
    )
    result = subprocess.run(
        [str(checkout / "upgrade.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert quadlet.read_text() == original
    assert "prior image is running again" in result.stderr
    assert command_log.read_text().count(
        "systemctl --user restart cash-27.service"
    ) == 2
