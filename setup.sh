#!/usr/bin/env bash
set -euo pipefail

umask 027

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DATA_ROOT="${1:-}"
readonly PYTHON_BIN="${CASH_PYTHON:-python3}"
readonly VENV_ROOT="${CASH_VENV:-${PROJECT_ROOT}/.venv}"

cd -- "${PROJECT_ROOT}"

if [[ -z "${DATA_ROOT}" ]]; then
    echo "Usage: $0 /absolute/path/to/persistent-data" >&2
    exit 2
fi

if [[ "${DATA_ROOT}" != /* ]]; then
    echo "The persistent data path must be absolute." >&2
    exit 2
fi

"${PYTHON_BIN}" - <<'PY'
import sys

if sys.version_info < (3, 12):
    raise SystemExit("cash-27 requires Python 3.12 or newer")
PY

if [[ ! -x "${VENV_ROOT}/bin/python" ]]; then
    "${PYTHON_BIN}" -m venv "${VENV_ROOT}"
fi

"${VENV_ROOT}/bin/python" - <<'PY'
import sys

if sys.version_info < (3, 12):
    raise SystemExit(
        "the configured virtual environment must use Python 3.12 or newer"
    )
PY

"${VENV_ROOT}/bin/python" -m ensurepip --upgrade
"${VENV_ROOT}/bin/python" -m pip install \
    --disable-pip-version-check \
    --requirement "${PROJECT_ROOT}/requirements.txt"

CASH_SETUP_DATA_ROOT="${DATA_ROOT}" \
CASH_SETUP_PROJECT_ROOT="${PROJECT_ROOT}" \
"${VENV_ROOT}/bin/python" - <<'PY'
import os
from pathlib import Path

from cash.db import apply_migrations, connect
from cash.web.app import DATA_LAYOUT

data_root = Path(os.environ["CASH_SETUP_DATA_ROOT"])
project_root = Path(os.environ["CASH_SETUP_PROJECT_ROOT"])

data_root.mkdir(parents=True, exist_ok=True, mode=0o750)
for name in DATA_LAYOUT:
    (data_root / name).mkdir(parents=True, exist_ok=True, mode=0o750)

connection = connect(data_root / "cash.db")
try:
    apply_migrations(connection, project_root / "migrations")
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise SystemExit(f"database integrity check failed: {integrity}")
finally:
    connection.close()

print(f"Initialized cash-27 data at {data_root}")
PY

cat <<EOF

Setup complete.

Set these environment variables in the production service:
  CASH_DATA_ROOT=${DATA_ROOT}
  CASH_ADMIN_PASSWORD=<strong password>
  CASH_SECRET_KEY=<persistent random secret>
  CASH_FY_START_MONTH=9

Run one worker behind the TLS-terminating reverse proxy:
  ${VENV_ROOT}/bin/gunicorn --chdir ${PROJECT_ROOT} --workers 1 --bind 127.0.0.1:8000 run_prod:app
EOF
