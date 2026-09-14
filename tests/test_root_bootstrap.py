from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_IMAGE = "docker.io/library/python:3.12-slim"


def podman_can_run_smoke() -> bool:
    if not shutil.which("podman"):
        return False
    return subprocess.run(
        ["podman", "image", "exists", BASE_IMAGE],
        capture_output=True,
        check=False,
    ).returncode == 0


@pytest.mark.skipif(
    not podman_can_run_smoke(),
    reason="the disposable root-bootstrap image is not locally available",
)
def test_root_bootstrap_and_upgrade_delegate_runtime_work():
    script = r"""
set -ceu
cp -a /source /opt/cash-27
install -m 0755 \
  /opt/cash-27/tests/fixtures/fake-production-host-command \
  /usr/local/bin/fake-production-host-command
for name in git podman podman-compose curl systemctl loginctl sysctl; do
  ln -s /usr/local/bin/fake-production-host-command "/usr/local/bin/$name"
done
touch /tmp/cash-27-root-smoke.log
chmod 0666 /tmp/cash-27-root-smoke.log
cd /root
CASH_SETUP_DOMAIN=cash.example.org \
CASH_SETUP_ADMIN_PASSWORD=correct-horse-battery \
CASH_SETUP_USER_PASSWORD=shared-employee-pass \
CASH_SETUP_ACCOUNTS_CSV=/opt/cash-27/accounts.csv \
CASH_SETUP_CLASSES_CSV=/opt/cash-27/classes.csv \
/opt/cash-27/setup.sh
/opt/cash-27/deploy/compose.sh ps
/opt/cash-27/upgrade.sh
test "$(stat -c %U /opt/cash-27)" = root
test "$(stat -c %G /opt/cash-27)" = cash27
test "$(stat -c %U /home/cash27/.local/share/cash-27)" = cash27
grep -Fxq CASH_SERVICE_USER=cash27 /etc/cash-27/operator.conf
test "$(stat -c %U /etc/cash-27/operator.conf)" = root
test "$(stat -c %a /etc/cash-27/operator.conf)" = 644
grep -Fq "podman uid=$(id -u cash27) home=/home/cash27" \
  /tmp/cash-27-root-smoke.log
grep -Fq 'git uid=0 home=/root args=-C /opt/cash-27 pull --ff-only' \
  /tmp/cash-27-root-smoke.log
if grep -Fq 'podman uid=0' /tmp/cash-27-root-smoke.log; then exit 91; fi
"""
    result = subprocess.run(
        [
            "podman", "run", "--rm",
            "--volume", f"{PROJECT_ROOT}:/source:ro",
            BASE_IMAGE, "bash", "-c", script,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
