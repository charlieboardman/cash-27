# cash-27

`cash-27` is a small USD/MXN cash ledger for vouchers, sign-outs, receipts,
reconciliation, and revisioned fiscal-year reports. It is intended to replace a
spreadsheet while keeping the workflow understandable and correctable. The
project specification in [`spec-new.md`](spec-new.md) is the behavioral source
of truth.

## Workflow at a glance

- **Prepare** records a direct expense, signout, or addendum immediately and
  produces its printable paperwork. A direct expense captures the final tender
  and change before printing.
- **Capture** turns PDF/JPEG/PNG evidence into normalized PDFs in the inbox.
- **Review** keeps the open ledger rows, contextual accounting form, and
  supporting documents on one screen. Receipt-value splits determine expense
  allocations; the server distributes the original USD/MXN settlement
  proportionally in exact minor units, so a reviewer does not manually
  rebalance tender cents.
- A signout's `/FY##-NNNN` page lets the signee upload receipt drafts, submit a
  package, correct returned items, and resubmit the same package. Accepted
  packages become ledger transactions atomically.
- **Ledger** is the correctable authoritative history. Transaction IDs remain
  monotonic and stable; material edits and voids require reasons and retain
  before/after evidence. Physical-count corrections are posted separately for
  each nonzero currency delta.
- **Reports** downloads disposable previews or retains numbered finalized CSV
  revisions. Later ledger changes mark an old finalized report as outdated;
  they do not overwrite it.
- **Settings** manages the expense accounts and classes offered during review.
  Deactivating a code never rewrites historical transactions.

## Production assumptions

- Rootless Podman
- Network access to pull the Python base image and install Python packages
- Tailscale Serve, or another TLS-terminating reverse proxy
- One persistent directory owned by the account that runs the service

The production image contains Python 3.12, the application dependencies, and
Gunicorn. The host does not need a Python virtual environment. The container
runs one Gunicorn worker because the deployment is built around one SQLite
database.

## Persistent data is required

The database and uploaded evidence are runtime data, not source code. They must
live in a persistent directory that survives application deployments and
container rebuilds. Prefer a bind mount or a directory outside the checkout,
owned by the service account and inaccessible to other users.

The Podman launch helper deliberately requires the absolute data path as its
first argument:

```bash
./deploy/podman-run.sh /absolute/path/to/cash-27-data /path/to/cash-27.env
```

Calling it without a path prints its usage and exits with status `2`. Passing a
relative data path also exits with status `2`. This safeguard prevents
production records from silently appearing in an unexpected working directory.

The persistent directory contains:

- `cash.db` and its SQLite WAL sidecars while the application is running;
- `inbox/`, `documents/`, and `staging/` for evidence files;
- `reports/` for retained finalized reports; and
- `backups/` for backups stored locally, if that location is used.

These paths, Python bytecode, and environment files are ignored by Git. Do not
force-add them.

## First-time Podman deployment

Install Podman through the host operating system if it is not already present.
For this Linux Mint/Ubuntu host:

```bash
sudo apt update
sudo apt install podman
```

Build the image as the unprivileged operating-system account that will run the
application:

```bash
git clone git@github.com:charlieboardman/cash-27.git
cd cash-27
./deploy/podman-build.sh
```

Create a private environment file outside the repository:

```bash
mkdir -p ~/.config/cash-27
cp deploy/cash-27.env.example ~/.config/cash-27/cash-27.env
chmod 600 ~/.config/cash-27/cash-27.env
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Edit that file to set a strong administrator password and paste the generated
session secret. Keep the secret stable across restarts; changing it invalidates
existing browser sessions.

Start or replace the production container:

```bash
./deploy/podman-run.sh \
  ~/.local/share/cash-27 \
  ~/.config/cash-27/cash-27.env
```

The helper runs rootless Podman with a read-only container filesystem, a
private temporary directory, dropped Linux capabilities, the persistent data
bind mount at `/data`, and port `8000` bound only to host localhost. On every
container start, pending migrations are applied, standard accounts are seeded,
and SQLite integrity is checked before Gunicorn starts.

Expose the localhost service to the tailnet over HTTPS:

```bash
tailscale serve --bg 8000
```

Use the HTTPS URL printed by Tailscale. Production cookies are marked `Secure`,
so plain HTTP is intentionally not a supported production entry point.

Useful container commands:

```bash
podman logs --follow cash-27
podman inspect --format '{{.State.Health.Status}}' cash-27
podman stop cash-27
podman start cash-27
```

The image accepts these environment variables:

| Variable | Meaning |
| --- | --- |
| `CASH_DATA_ROOT` | Absolute path inside the container; the helper fixes this at `/data` |
| `CASH_ADMIN_PASSWORD` | A strong shared administrator password |
| `CASH_SECRET_KEY` | A strong, persistent Flask session-signing secret |
| `CASH_FY_START_MONTH` | Optional fiscal-year start month; defaults to `9` |

The first start creates an empty ledger with standard accounts. It does not
invent an opening balance, administrator password, or sample transactions.

## Upgrades

Take a backup, pull the new revision, rebuild, and rerun the launch helper. Its
`--replace` behavior replaces the old container while retaining the external
data directory:

```bash
git pull --ff-only
./deploy/podman-build.sh
./deploy/podman-run.sh \
  ~/.local/share/cash-27 \
  ~/.config/cash-27/cash-27.env
```

The older native-Python deployment remains available through `setup.sh` and
`run_prod:app`, but Podman is the documented production path.

## Backups and restore

The built-in live backup uses SQLite's online backup API, so it is safe to run
while the application is serving requests. Use a new destination for each run,
preferably on storage outside the application host:

```bash
data_root=~/.local/share/cash-27
backup_root=/path/to/off-host/archive/20260901T120000Z
mkdir -p "$backup_root"
podman run --rm --userns keep-id \
  --volume "$data_root:/data:Z" \
  --volume "$backup_root:/backup:Z" \
  --entrypoint python localhost/cash-27:latest \
  -m cash.ops backup /data /backup
```

The command backs up the database, current and filed PDF evidence, and every
retained finalized report. Report hashes are verified before copying; a missing
or altered finalized report fails the backup instead of silently producing an
incomplete archive.

Restore must be performed while the application is stopped and overwrites the
live database:

```bash
podman stop cash-27
podman run --rm --userns keep-id \
  --volume "$data_root:/data:Z" \
  --volume "$backup_root:/backup:Z" \
  --entrypoint python localhost/cash-27:latest \
  -m cash.ops restore /data /backup
podman start cash-27
```

Restore applies migrations, runs SQLite integrity checks, verifies document
state, restores and hash-verifies finalized reports, and replays the ledger.
Test restoration periodically instead of assuming that the existence of backup
files is sufficient.

## Local development

The development entry point uses `data-dev/`, the password `dev`, and port
`5051`. It is intentionally unsuitable for production:

```bash
python3.12 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python run_dev.py
```

It binds only to `127.0.0.1` by default. To reach it over a private tailnet,
listen on all interfaces and open the machine's tailnet IP in the browser:

```bash
CASH_DEV_HOST=0.0.0.0 .venv/bin/python run_dev.py
# Then visit http://<tailnet-ip>:5051/login
```

This changes only the listening interface; it does not make the development
server suitable for the public internet. `CASH_DEV_PORT` can override the
port. The development cookie is deliberately usable over tailnet HTTP, while
production cookies remain HTTPS-only.

Run the test suite with:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
```

For ledger rules, correction semantics, report finalization, security
boundaries, and acceptance criteria, read [`spec-new.md`](spec-new.md).
