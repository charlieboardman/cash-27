"""Production WSGI entry point configured entirely through the environment."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cash.web.app import create_app


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


fy_start_month = int(os.environ.get("CASH_FY_START_MONTH", "9"))
if not 1 <= fy_start_month <= 12:
    raise RuntimeError("CASH_FY_START_MONTH must be between 1 and 12")

# Server-side dates with no browser involved, such as the Drive backup's.
timezone_name = os.environ.get("CASH_TIMEZONE", "America/Los_Angeles")
try:
    ZoneInfo(timezone_name)
except (ValueError, ZoneInfoNotFoundError) as exc:
    raise RuntimeError(f"CASH_TIMEZONE is not a known time zone: {timezone_name}") from exc

app = create_app(
    Path(required_environment("CASH_DATA_ROOT")),
    admin_password=required_environment("CASH_ADMIN_PASSWORD"),
    user_password=required_environment("CASH_USER_PASSWORD"),
    secret_key=required_environment("CASH_SECRET_KEY"),
    fy_start_month=fy_start_month,
    timezone_name=timezone_name,
    auth_env_file=os.environ.get("CASH_AUTH_ENV_FILE"),
)
