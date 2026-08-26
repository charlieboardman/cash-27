# cash-27

`cash-27` is a small USD/MXN cash ledger for vouchers, sign-outs, receipts,
reconciliation, and revisioned fiscal-year reports. It is intended to replace a
spreadsheet while keeping the workflow understandable and correctable. The
project specification in [`spec-new.md`](spec-new.md) is the behavioral source
of truth.

## Production assumptions

- Python 3.12 or newer
- A POSIX shell with Python's `venv` module available
- Network access to a Python package index during installation
- One Gunicorn worker behind a TLS-terminating reverse proxy
- One persistent directory owned by the account that runs the service

On Debian or Ubuntu, the `python3.12-venv` package may need to be installed by
the system administrator before running setup. This repository does not yet
include a container image, reverse-proxy configuration, or service-unit file.

## Persistent data is required

The database and uploaded evidence are runtime data, not source code. They must
live in a persistent directory that survives application deployments and
container rebuilds. Prefer a bind mount or a directory outside the checkout,
owned by the service account and inaccessible to other users.

`setup.sh` deliberately requires the absolute path as its first argument:

```bash
./setup.sh /srv/cash-27-data
```

Calling it without a path prints its usage and exits with status `2`. Passing a
relative path also exits with status `2`. In either case it exits before
creating a virtual environment, database, or data directories. This safeguard
prevents production records from silently appearing in an unexpected working
directory.

The persistent directory contains:

- `cash.db` and its SQLite WAL sidecars while the application is running;
- `inbox/`, `documents/`, and `staging/` for evidence files;
- `reports/` for retained finalized reports; and
- `backups/` for backups stored locally, if that location is used.

These paths, local virtual environments, Python bytecode, and `.env` files are
ignored by Git. Do not force-add them.

## First-time setup and upgrades

Run setup as the same operating-system account that will run the service:

```bash
git clone git@github.com:charlieboardman/cash-27.git
cd cash-27
./setup.sh /srv/cash-27-data
```

Setup:

1. validates both the requested Python and virtual-environment Python as 3.12+;
2. creates `.venv` and installs `requirements.txt`;
3. creates the persistent directory layout;
4. creates or upgrades `cash.db` through every numbered migration;
5. seeds the standard ledger accounts; and
6. runs SQLite's integrity check.

The script is idempotent and may be rerun for an existing installation. Because
that also applies new database migrations, take a current backup before using a
new application revision against production data.

The interpreter and virtual-environment locations can be overridden when
needed:

```bash
CASH_PYTHON=/usr/bin/python3.12 \
CASH_VENV=/opt/cash-27-venv \
./setup.sh /srv/cash-27-data
```

## Production configuration and launch

The production entry point is `run_prod:app`. It requires these environment
variables:

| Variable | Meaning |
| --- | --- |
| `CASH_DATA_ROOT` | The same absolute persistent path passed to `setup.sh` |
| `CASH_ADMIN_PASSWORD` | A strong shared administrator password |
| `CASH_SECRET_KEY` | A strong, persistent Flask session-signing secret |
| `CASH_FY_START_MONTH` | Optional fiscal-year start month; defaults to `9` |

Generate a session secret, for example, with:

```bash
python3.12 -c 'import secrets; print(secrets.token_hex(32))'
```

Keep the password and session secret in a service manager, secret store, or
root/service-readable environment file outside Git. Keep `CASH_SECRET_KEY`
stable across restarts; changing it invalidates existing browser sessions.

From the repository root, a direct launch looks like:

```bash
export CASH_DATA_ROOT=/srv/cash-27-data
export CASH_ADMIN_PASSWORD='<strong password>'
export CASH_SECRET_KEY='<persistent random secret>'
export CASH_FY_START_MONTH=9

.venv/bin/gunicorn \
  --chdir "$(pwd)" \
  --workers 1 \
  --bind 127.0.0.1:8000 \
  run_prod:app
```

Use exactly one worker because the deployment is built around one SQLite
database. Put Gunicorn behind an HTTPS reverse proxy; production cookies are
marked `Secure`. The unauthenticated health check is `GET /api/health`.

Setup creates an empty ledger with standard accounts. It does not invent an
opening balance, administrator password, or sample transactions.

## Backups and restore

The built-in live backup uses SQLite's online backup API, so it is safe to run
while the application is serving requests. Use a new destination for each run,
preferably on storage outside the application host:

```bash
backup_root="/srv/cash-27-archive/$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/python -m cash.ops backup "$CASH_DATA_ROOT" "$backup_root"
mkdir -p "$backup_root/reports"
cp -a "$CASH_DATA_ROOT/reports/." "$backup_root/reports/"
```

At present, that command backs up the database plus current and filed PDF
evidence. It does **not** copy retained CSV files from `reports/`. Until report
backup is added to `cash.ops`, copy the report directory to the same protected
backup destination and preserve its relative filenames. Finalized reports are
immutable, so copying those files while the service runs is safe.

Restore must be performed while the application is stopped and overwrites the
live database:

```bash
.venv/bin/python -m cash.ops restore "$CASH_DATA_ROOT" "$backup_root"
mkdir -p "$CASH_DATA_ROOT/reports"
cp -a "$backup_root/reports/." "$CASH_DATA_ROOT/reports/"
```

Restore applies migrations, runs SQLite integrity checks, verifies document
state, and replays the ledger. Restore the corresponding `reports/` files as
well before restarting the service. Test restoration periodically instead of
assuming that the existence of backup files is sufficient.

## Local development

The development entry point uses `data-dev/`, the password `dev`, and port
`5051`. It is intentionally unsuitable for production:

```bash
python3.12 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python run_dev.py
```

Run the test suite with:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
```

For ledger rules, correction semantics, report finalization, security
boundaries, and acceptance criteria, read [`spec-new.md`](spec-new.md).
