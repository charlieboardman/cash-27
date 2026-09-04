from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def current_image(component: str = "app") -> str:
    revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    repository = "cash-27" if component == "app" else "cash-27-drive-backup"
    return f"localhost/{repository}:{revision}"


def render_setup(tmp_path: Path, *, domain: str = "https://cash.example.org/",
                 admin_password: str = "correct-horse-battery",
                 user_password: str = "shared-employee-pass",
                 backup_remote: str | None = None,
                 backup_path: str | None = None):
    config_dir = tmp_path / "config"
    data_root = tmp_path / "data"
    user_systemd_dir = tmp_path / "systemd-user"
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
            "CASH_SETUP_ADMIN_PASSWORD": admin_password,
            "CASH_SETUP_USER_PASSWORD": user_password,
            "CASH_SETUP_CONFIG_DIR": str(config_dir),
            "CASH_SETUP_DATA_ROOT": str(data_root),
            "CASH_SETUP_USER_SYSTEMD_DIR": str(user_systemd_dir),
            "CASH_SETUP_CADDY_FILE": str(caddy_file),
            "CASH_SETUP_ACCOUNTS_CSV": str(accounts_csv),
            "CASH_SETUP_CLASSES_CSV": str(classes_csv),
        }
    )
    if backup_remote is not None:
        env["CASH_SETUP_RCLONE_REMOTE"] = backup_remote
    if backup_path is not None:
        env["CASH_SETUP_RCLONE_PATH"] = backup_path
    result = subprocess.run(
        [str(PROJECT_ROOT / "setup.sh")],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, config_dir, data_root, user_systemd_dir, caddy_file


def test_setup_renders_private_persistent_public_vm_configuration(tmp_path):
    result, config_dir, data_root, user_systemd_dir, caddy_file = render_setup(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "correct-horse-battery" not in result.stdout
    env_file = config_dir / "cash-27.env"
    env_text = env_file.read_text()
    assert "CASH_ADMIN_PASSWORD=correct-horse-battery" in env_text
    assert "CASH_USER_PASSWORD=shared-employee-pass" in env_text
    assert re.search(r"^CASH_SECRET_KEY=[0-9a-f]{64}$", env_text, re.MULTILINE)
    assert "CASH_FY_START_MONTH=9" in env_text
    assert "CASH_BACKUP_EXPECTED=1" in env_text
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

    compose_env = (config_dir / "compose.env").read_text()
    assert f"CASH_APP_IMAGE={current_image()}" in compose_env
    assert f"CASH_BACKUP_IMAGE={current_image('backup')}" in compose_env
    assert f"CASH_DATA_ROOT={data_root}" in compose_env
    assert f"CASH_ACCOUNTS_CSV={installed_accounts}" in compose_env
    assert f"CASH_CLASSES_CSV={installed_classes}" in compose_env
    assert f"CASH_CADDY_FILE={caddy_file}" in compose_env
    assert (config_dir / "compose.env").stat().st_mode & 0o777 == 0o600

    compose_text = (PROJECT_ROOT / "compose.yaml").read_text()
    assert "drive-backup:" in compose_text
    assert "backup-core:" in compose_text
    assert "caddy:" in compose_text
    assert "${CASH_RCLONE_CONFIG}:/config/rclone.conf:ro,z" in compose_text
    assert "${CASH_DATA_ROOT}:/data:rw,z" in compose_text

    backup_config = (config_dir / "backup.env").read_text()
    assert "CASH_BACKUP_CONFIGURED=0" in backup_config
    assert re.search(
        r"^CASH_BACKUP_CORE_TOKEN=[0-9a-f]{64}$",
        backup_config, re.MULTILINE)
    service_text = (user_systemd_dir / "cash-27-compose.service").read_text()
    assert str(PROJECT_ROOT / "deploy" / "compose.sh") in service_text
    assert "up --detach --remove-orphans" in service_text
    host_status = (data_root / "backups" / "drive" / "host.conf").read_text()
    assert "rclone_configured=0" in host_status
    assert "service_installed=1" in host_status

    caddy_text = caddy_file.read_text()
    assert "cash.example.org {" in caddy_text
    assert "reverse_proxy app:8000" in caddy_text
    assert "tailscale" not in caddy_text.lower()


def test_setup_rerun_keeps_the_session_secret(tmp_path):
    first, config_dir, *_ = render_setup(tmp_path)
    assert first.returncode == 0, first.stderr
    first_secret = re.search(
        r"^CASH_SECRET_KEY=(.+)$",
        (config_dir / "cash-27.env").read_text(),
        re.MULTILINE,
    ).group(1)
    first_backup_token = re.search(
        r"^CASH_BACKUP_CORE_TOKEN=(.+)$",
        (config_dir / "backup.env").read_text(),
        re.MULTILINE,
    ).group(1)

    second, *_ = render_setup(tmp_path, domain="ledger.example.org")
    assert second.returncode == 0, second.stderr
    second_text = (config_dir / "cash-27.env").read_text()
    assert f"CASH_SECRET_KEY={first_secret}" in second_text
    assert f"CASH_BACKUP_CORE_TOKEN={first_backup_token}" in \
        (config_dir / "backup.env").read_text()


def test_setup_accepts_short_nonempty_passwords(tmp_path):
    result, config_dir, *_ = render_setup(
        tmp_path, admin_password="a", user_password="b")

    assert result.returncode == 0, result.stderr
    env_text = (config_dir / "cash-27.env").read_text()
    assert "CASH_ADMIN_PASSWORD=a\n" in env_text
    assert "CASH_USER_PASSWORD=b\n" in env_text


def test_setup_renders_configured_google_drive_backup(tmp_path):
    result, config_dir, data_root, *_ = render_setup(
        tmp_path, backup_remote="organization-drive",
        backup_path="Finance/Cash 27 Backups")

    assert result.returncode == 0, result.stderr
    backup_config = (config_dir / "backup.env").read_text()
    assert "CASH_BACKUP_CONFIGURED=1" in backup_config
    assert "CASH_BACKUP_REMOTE=organization-drive" in backup_config
    assert "CASH_BACKUP_PATH=Finance/Cash 27 Backups" in backup_config
    host = (data_root / "backups" / "drive" / "host.conf").read_text()
    assert "rclone_configured=1" in host
    assert "destination=organization-drive:Finance/Cash 27 Backups" in host


def test_image_reference_uses_head_commit_and_allows_override():
    script = PROJECT_ROOT / "deploy" / "image-ref.sh"
    automatic = subprocess.run(
        [str(script)], cwd=PROJECT_ROOT, capture_output=True, text=True,
        check=True,
    )
    assert automatic.stdout.strip() == current_image()

    env = os.environ.copy()
    env["CASH_IMAGE"] = "registry.example/cash-27:release-candidate"
    overridden = subprocess.run(
        [str(script)], cwd=PROJECT_ROOT, env=env, capture_output=True,
        text=True, check=True,
    )
    assert overridden.stdout.strip() == \
        "registry.example/cash-27:release-candidate"


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
            "CASH_SETUP_USER_SYSTEMD_DIR": str(tmp_path / "systemd-user"),
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
