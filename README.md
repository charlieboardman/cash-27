# cash-27

`cash-27` is a small USD/MXN cash ledger for vouchers, sign-outs, receipts,
reconciliation, and revisioned fiscal-year reports. It is intended to replace a
spreadsheet while keeping the workflow understandable and correctable. The
project specification in [`spec-new.md`](spec-new.md) is the behavioral source
of truth.

## Workflow at a glance

- **New transaction** records a direct expense, signout, currency exchange, or
  bank withdrawal immediately. Direct expenses and signouts produce printable
  vouchers; a direct expense captures the final tender and change before
  printing. Add cash or record a return from the original signout's
  administrator workspace. The page's **Other** tab holds the infrequent cash
  donation workflow.
- **Capture** turns PDF/JPEG/PNG evidence into normalized PDFs in the inbox.
- **Review** separates unfinished ledger transactions from employee receipt
  submissions. Its transaction view keeps rows, accounting controls, and
  supporting documents on one screen. Every receipt split explicitly records
  its USD/MXN cash and USD/MXN receipt-value portions; the server verifies all
  four totals rather than inventing a proportional currency split.
- A signout's shared-password-protected `/FY##-NNNN` page lets the signee upload
  receipt drafts, submit a package, correct returned items, and resubmit the
  same package. Accepted packages become ledger transactions atomically. Open
  signout state is derived from the ledger and current workflow facts on read.
- **Ledger** is the correctable authoritative history. Transaction IDs remain
  monotonic and stable; material edits and voids require reasons and retain
  before/after evidence. Physical-count corrections are posted separately for
  each nonzero currency delta.
- On first login to a new database, **Set starting cash** records the one
  genuine opening balance. It remains editable from Ledger with a warning and
  full-history replay; it is not combined with recurring donations.
- **Reports** downloads disposable previews or retains numbered finalized CSV
  revisions. Later ledger changes mark an old finalized report as outdated;
  they do not overwrite it.
- **Settings** manages the expense accounts and classes offered during review.
  Deactivating a code never rewrites historical transactions.

## Production assumptions

- A public, systemd-based Linux VM using `apt` or `dnf`
- A normal sudo-capable account (do not run the installer as `root`)
- A domain whose DNS points to the VM
- Inbound TCP ports 80 and 443 allowed by the cloud firewall
- Network access to install host packages and build the application image

The production image contains Python 3.12, the application dependencies, and
Gunicorn. The host does not need a Python virtual environment. The container
runs one Gunicorn worker because the deployment is built around one SQLite
database.

## Persistent data is required

The database and uploaded evidence are runtime data, not source code. They must
live in a persistent directory that survives application deployments and
container rebuilds. Prefer a bind mount or a directory outside the checkout,
owned by the service account and inaccessible to other users.

The installer uses `~/.local/share/cash-27` by default and bind-mounts it into
the container at `/data`. It prints that location at the beginning and end of
every run. The checkout and container image are disposable; this directory is
not.

The persistent directory contains:

- `cash.db` and its SQLite WAL sidecars while the application is running;
- `inbox/`, `documents/`, and `staging/` for evidence files;
- `reports/` for retained finalized reports; and
- `backups/` for backups stored locally, if that location is used.

These paths, Python bytecode, and environment files are ignored by Git. Do not
force-add them.

## First production deployment

First point the chosen domain at the VM and allow inbound TCP ports 80 and 443
in the provider's firewall. Then, as the normal account that will run the
application:

```bash
git clone git@github.com:charlieboardman/cash-27.git
cd cash-27
./setup.sh
```

`setup.sh` asks for the public domain, an administrator password, one shared
employee password, and paths to the account-code and class-code CSV files. The
employee password protects every receipt-submission link from random internet
traffic; it does not give employees administrator access. On later runs it offers the installed
values as defaults, so an upgrade does not rotate the password or session
secret or require finding the original CSV files again. The script then:

- installs Podman, Caddy, and curl when absent;
- opens ports 80 and 443 in an active UFW or firewalld host firewall;
- generates and retains a private session-signing secret;
- validates and stores private copies of the account and class CSV files;
- creates the persistent data and private configuration directories;
- builds the container image;
- installs a rootless Podman Quadlet so the container returns after reboot;
- binds the application only to host localhost on port 8000;
- configures Caddy as the public reverse proxy with automatic HTTPS; and
- starts the service, applies migrations, checks SQLite, and waits for health.

The script prints the final `https://.../login` URL. Certificate issuance is
automatic once public DNS and ports 80/443 are correct. Production cookies are
marked `Secure`, so public plain HTTP is intentionally unsupported.

The container has a read-only root filesystem, a private temporary directory,
dropped Linux capabilities, and the persistent bind mount at `/data`. On every
start, pending migrations are applied, standard accounts are seeded, and SQLite
integrity is checked before Gunicorn starts.

Useful diagnostics, if they are ever needed:

```bash
systemctl --user status cash-27
journalctl --user --unit cash-27 --follow
sudo systemctl status caddy
```

The installer stores private application settings at
`~/.config/cash-27/cash-27.env`. The image accepts these variables:

| Variable | Meaning |
| --- | --- |
| `CASH_DATA_ROOT` | Absolute path inside the container; the helper fixes this at `/data` |
| `CASH_ADMIN_PASSWORD` | A strong shared administrator password |
| `CASH_USER_PASSWORD` | The shared password for employee receipt pages |
| `CASH_SECRET_KEY` | A strong, persistent Flask session-signing secret |
| `CASH_FY_START_MONTH` | Optional fiscal-year start month; defaults to `9` |

The first start creates an empty ledger with standard accounts. It does not
invent an opening balance, administrator password, or sample transactions.

### Account and class codes

Keep the real CSV files wherever is convenient on the production VM. On the
first run, `setup.sh` prompts for the path to each file. Relative paths are
resolved from the repository checkout. Both files use exactly two columns:

```csv
name,code
Travel,EXP_TRAVEL
```

Tracked, empty templates are available as `accounts.example.csv` and
`classes.example.csv`. The real root-level `accounts.csv` and `classes.csv`
filenames are ignored by Git. Setup validates the headers, then copies the
files to `~/.config/cash-27/reference/` with private permissions and mounts
those copies read-only in the container. On a later run, press Enter to keep
the installed copies or answer `n` to select replacement files. For unattended
setup, provide `CASH_SETUP_ACCOUNTS_CSV` and `CASH_SETUP_CLASSES_CSV`.

Container startup adds new rows, refreshes names, and reactivates matching
codes. It never deletes database rows omitted from a CSV, so rerunning setup is
safe for historical records. Codes may also be managed later in Settings.

Project remains an optional free-text field rather than a separate reference
table. While entering a project, Review offers existing project values from
the ledger as selectable suggestions.

## Upgrades

Take a browser database export, pull the new revision, and rerun the same
installer. It rebuilds and replaces the container while retaining the external
data directory and existing secrets:

```bash
git pull --ff-only
./setup.sh
```

## Browser database export and import

Settings includes a database-maintenance section, so routine database transfer
does not require shell access:

- **Download database export** creates a portable SQL export from a consistent
  live SQLite snapshot.
- **Validate export** reconstructs the uploaded database away from the live
  ledger, applies current migrations, verifies the exact application schema,
  SQLite integrity, foreign keys, full ledger replay, and every referenced
  document and finalized-report hash.
- **Activate imported database** requires both the administrator password and
  the phrase `REPLACE DATABASE`. Other users must stop work during activation.
  The current database is retained automatically under `backups/` before the
  validated database is atomically activated.

Database export contains database records only. It intentionally does not
contain uploaded PDFs or finalized report files, so it complements rather than
replaces the full backup below.

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
systemctl --user stop cash-27
podman run --rm --userns keep-id \
  --volume "$data_root:/data:Z" \
  --volume "$backup_root:/backup:Z" \
  --entrypoint python localhost/cash-27:latest \
  -m cash.ops restore /data /backup
systemctl --user start cash-27
```

Restore applies migrations, runs SQLite integrity checks, verifies document
state, restores and hash-verifies finalized reports, and replays the ledger.
Test restoration periodically instead of assuming that the existence of backup
files is sufficient.

## Local development

The development entry point uses `data-dev/`, `dev` for both passwords, and port
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
