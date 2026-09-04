from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def render_setup(tmp_path: Path, *, domain: str = "https://cash.example.org/"):
    config_dir = tmp_path / "config"
    data_root = tmp_path / "data"
    quadlet_dir = tmp_path / "quadlet"
    caddy_file = tmp_path / "caddy" / "cash-27.caddy"
    accounts_csv = tmp_path / "source-accounts.csv"
    classes_csv = tmp_path / "source-classes.csv"
    accounts_csv.write_text("name,code\nTravel,EXP_TRAVEL\n")
    classes_csv.write_text("name,code\nProgram services,PROGRAM\n")
    env = os.environ.copy()
    env.update(
        {
            "CASH_SETUP_RENDER_ONLY": "1",
            "CASH_SETUP_DOMAIN": domain,
            "CASH_SETUP_ADMIN_PASSWORD": "correct-horse-battery",
            "CASH_SETUP_USER_PASSWORD": "shared-employee-pass",
            "CASH_SETUP_CONFIG_DIR": str(config_dir),
            "CASH_SETUP_DATA_ROOT": str(data_root),
            "CASH_SETUP_QUADLET_DIR": str(quadlet_dir),
            "CASH_SETUP_CADDY_FILE": str(caddy_file),
            "CASH_SETUP_ACCOUNTS_CSV": str(accounts_csv),
            "CASH_SETUP_CLASSES_CSV": str(classes_csv),
        }
    )
    result = subprocess.run(
        [str(PROJECT_ROOT / "setup.sh")],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, config_dir, data_root, quadlet_dir, caddy_file


def test_setup_renders_private_persistent_public_vm_configuration(tmp_path):
    result, config_dir, data_root, quadlet_dir, caddy_file = render_setup(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "correct-horse-battery" not in result.stdout
    env_file = config_dir / "cash-27.env"
    env_text = env_file.read_text()
    assert "CASH_ADMIN_PASSWORD=correct-horse-battery" in env_text
    assert "CASH_USER_PASSWORD=shared-employee-pass" in env_text
    assert re.search(r"^CASH_SECRET_KEY=[0-9a-f]{64}$", env_text, re.MULTILINE)
    assert "CASH_FY_START_MONTH=9" in env_text
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert data_root.stat().st_mode & 0o777 == 0o700

    reference_dir = config_dir / "reference"
    installed_accounts = reference_dir / "accounts.csv"
    installed_classes = reference_dir / "classes.csv"
    assert reference_dir.stat().st_mode & 0o777 == 0o700
    assert installed_accounts.read_text() == "name,code\nTravel,EXP_TRAVEL\n"
    assert installed_classes.read_text() == "name,code\nProgram services,PROGRAM\n"
    assert installed_accounts.stat().st_mode & 0o777 == 0o600
    assert installed_classes.stat().st_mode & 0o777 == 0o600

    quadlet_text = (quadlet_dir / "cash-27.container").read_text()
    assert "Image=localhost/cash-27:latest" in quadlet_text
    assert "PublishPort=127.0.0.1:8000:8000" in quadlet_text
    assert f"Volume={data_root}:/data:Z" in quadlet_text
    assert f"Volume={installed_accounts}:/config/accounts.csv:ro,Z" in quadlet_text
    assert f"Volume={installed_classes}:/config/classes.csv:ro,Z" in quadlet_text
    assert "Environment=CASH_ACCOUNTS_CSV=/config/accounts.csv" in quadlet_text
    assert "Environment=CASH_CLASSES_CSV=/config/classes.csv" in quadlet_text
    assert "ReadOnly=true" in quadlet_text
    assert "DropCapability=all" in quadlet_text
    assert "WantedBy=default.target" in quadlet_text

    caddy_text = caddy_file.read_text()
    assert "cash.example.org {" in caddy_text
    assert "reverse_proxy 127.0.0.1:8000" in caddy_text
    assert "tailscale" not in caddy_text.lower()


def test_setup_rerun_keeps_the_session_secret(tmp_path):
    first, config_dir, *_ = render_setup(tmp_path)
    assert first.returncode == 0, first.stderr
    first_secret = re.search(
        r"^CASH_SECRET_KEY=(.+)$",
        (config_dir / "cash-27.env").read_text(),
        re.MULTILINE,
    ).group(1)

    second, *_ = render_setup(tmp_path, domain="ledger.example.org")
    assert second.returncode == 0, second.stderr
    second_text = (config_dir / "cash-27.env").read_text()
    assert f"CASH_SECRET_KEY={first_secret}" in second_text


def test_setup_rejects_non_domain_public_address(tmp_path):
    result, *_ = render_setup(tmp_path, domain="100.101.52.77:5051")

    assert result.returncode != 0
    assert "public domain name" in result.stderr


def test_setup_rejects_reference_csv_with_wrong_header(tmp_path):
    accounts_csv = tmp_path / "bad-accounts.csv"
    classes_csv = tmp_path / "classes.csv"
    accounts_csv.write_text("code,name\nEXP_TRAVEL,Travel\n")
    classes_csv.write_text("name,code\nProgram services,PROGRAM\n")
    env = os.environ.copy()
    env.update(
        {
            "CASH_SETUP_RENDER_ONLY": "1",
            "CASH_SETUP_DOMAIN": "cash.example.org",
            "CASH_SETUP_ADMIN_PASSWORD": "correct-horse-battery",
            "CASH_SETUP_USER_PASSWORD": "shared-employee-pass",
            "CASH_SETUP_CONFIG_DIR": str(tmp_path / "config"),
            "CASH_SETUP_DATA_ROOT": str(tmp_path / "data"),
            "CASH_SETUP_QUADLET_DIR": str(tmp_path / "quadlet"),
            "CASH_SETUP_CADDY_FILE": str(tmp_path / "caddy" / "cash-27.caddy"),
            "CASH_SETUP_ACCOUNTS_CSV": str(accounts_csv),
            "CASH_SETUP_CLASSES_CSV": str(classes_csv),
        }
    )

    result = subprocess.run(
        [str(PROJECT_ROOT / "setup.sh")],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Accounts CSV must begin with exactly: name,code" in result.stderr
