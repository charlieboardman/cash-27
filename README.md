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
- **Capture** turns PDF/JPEG/PNG evidence into immutable normalized PDFs and
  places their database records in the inbox.
- **Review** separates unfinished ledger transactions from employee receipt
  submissions. On desktop it presents the document queue, a stable transaction
  table, and the selected transaction's workspace from left to right. On a
  phone, the table, workspace, and document picker become separate full-screen
  layers. Every receipt split explicitly records its USD/MXN cash and USD/MXN
  receipt-value portions; the server verifies all four totals rather than
  inventing a proportional currency split.
- A signout's shared-password-protected `/FY##-NNNN` page lets the signee upload
  receipt drafts, submit a package, correct returned items, and resubmit the
  same package. Accepted packages become ledger transactions atomically. Open
  signout state is derived from the ledger and current workflow facts on read.
- **Ledger** is the correctable authoritative history. Transaction IDs remain
  monotonic and stable; material edits and voids require reasons and retain
  before/after evidence.
- **Balances** derives the current SAFE and signout balances from the ledger.
  Its physical-count tool first compares without saving, then records every
  nonzero currency difference, the explanation, and optional count document in
  one atomic command. If the ledger changed after comparison, the count must be
  compared again before it can be saved.
- On first login to a new database, **Set starting cash** records the one
  genuine opening balance. It remains editable from Ledger with a warning and
  full-history replay; it is not combined with recurring donations.
- **Reports** downloads disposable previews or retains numbered finalized CSV
  revisions. Later ledger changes mark an old finalized report as outdated;
  they do not overwrite it.
- **Settings** manages the expense accounts and classes offered during review.
  Deactivating a code never rewrites historical transactions. It also shows
  Google Drive backup health and recent backup activity. Backup failures place
  one concise warning on every administrator page.

## Production assumptions

- A public, systemd-based Linux VM using `apt` or `dnf`
- A normal sudo-capable account (do not run the installer as `root`)
- A domain whose DNS points to the VM
- Inbound TCP ports 80 and 443 allowed by the cloud firewall
- Network access to install host packages, build the application image, and
  upload backups to Google Drive

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
- `documents/` for immutable PDFs with UTC timestamps, document IDs, and safe
  upload-name hints;
- `staging/` for validated database imports;
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
employee password, paths to the account-code and class-code CSV files, and a
Google Drive destination for automatic backups. The
employee password protects every receipt-submission link from random internet
traffic; it does not give employees administrator access. On later runs it offers the installed
values as defaults, so an upgrade does not rotate the password or session
secret or require finding the original CSV files again. The script then:

- installs Podman, Caddy, curl, and rclone when absent;
- opens ports 80 and 443 in an active UFW or firewalld host firewall;
- generates and retains a private session-signing secret;
- validates and stores private copies of the account and class CSV files;
- creates the persistent data and private configuration directories;
- builds a container image tagged with the current Git commit;
- installs a rootless Podman Quadlet so the container returns after reboot;
- binds the application only to host localhost on port 8000;
- configures Caddy as the public reverse proxy with automatic HTTPS;
- installs a persistent nightly backup timer; and
- starts the service, applies migrations, checks SQLite, waits for health, and
  attempts the first Google Drive backup.

Google Drive authorization is handled by `rclone config`, which setup opens
when no remote exists. Google is retiring rclone's shared OAuth client during
2026, so follow [rclone's Drive guide](https://rclone.org/drive/) to create a
Google OAuth client ID and secret when its wizard requests them. The
authorization token remains in rclone's private host
configuration; it is never mounted into the Cash 27 container or committed to
Git. If Drive configuration is postponed, setup still completes and Cash 27
shows administrators that backups need attention.

For unattended reruns after rclone is authorized, the destination can be
provided as `CASH_SETUP_RCLONE_REMOTE` and `CASH_SETUP_RCLONE_PATH`.

The script prints the final `https://.../login` URL. Certificate issuance is
automatic once public DNS and ports 80/443 are correct. Production cookies are
marked `Secure`, so public plain HTTP is intentionally unsupported.

The container has a read-only root filesystem, a private temporary directory,
dropped Linux capabilities, and the persistent bind mount at `/data`. On every
start, pending migrations are applied, standard accounts are seeded, and SQLite
integrity is checked before Gunicorn starts.

Production builds require a clean Git worktree. The image is named from the
checked-out commit, for example `localhost/cash-27:ecad219abc12`, and the
Quadlet records that exact name instead of the mutable `latest` tag. Older
commit-tagged images remain available until explicitly removed, allowing the
application code to be rolled back without restoring or replacing `/data`.

Useful diagnostics, if they are ever needed:

```bash
systemctl --user status cash-27
journalctl --user --unit cash-27 --follow
systemctl --user status cash-27-backup.timer
journalctl --user --unit cash-27-backup --no-pager
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
| `CASH_BACKUP_EXPECTED` | Enables production backup-health warnings; setup fixes this at `1` |

The first start creates an empty ledger with standard accounts. It does not
invent an opening balance, administrator password, or sample transactions.
Every uploaded PDF is stored once with a name such as
`documents/20260904T172305Z--D000123--office-depot.pdf`. `D000123` is a global
monotonic document ID, the timestamp is the UTC capture time, and the final
component is a sanitized hint derived from the upload name. Several independent
files captured together also receive `--01`, `--02`, and so on. Inbox, filing,
document type, and voucher/receipt/transaction relationships are database
metadata and never rename the stored object.

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

Run the upgrade script from the production checkout:

```bash
./upgrade.sh
```

Before pulling any source, the script creates a complete verified backup and
uploads it to Google Drive. A failed backup stops the upgrade before Git or the
running application changes. If backup protection was explicitly turned off in
Settings, the timer records that state and the upgrade is allowed to continue.
The script then pulls from the configured upstream using `git pull --ff-only`,
builds an image tagged with the new commit, updates only the Quadlet's image
reference, restarts the application, and waits for its health check. It does not
rerun setup or change Caddy, passwords, reference CSV copies, the database, or
documents. Normal application startup may apply a database migration when a
release contains one.

If the new application does not become healthy, the script puts the prior image
back and reports the failure. It does not reverse database migrations. Retained
images therefore provide a convenient code rollback, but a release containing a
schema migration still requires the usual compatibility care.

The upgrade can also be started from another computer without opening an
interactive shell:

```bash
ssh cash-production 'cd /path/to/cash-27 && ./upgrade.sh'
```

The image printed by setup is also written to
`~/.config/containers/systemd/cash-27.container`. To see the retained versions:

```bash
podman image list localhost/cash-27
```

For a frontend-only rollback, change the Quadlet's `Image=` line to a retained
commit tag, then reload and restart the user service. This rolls back code only;
do not restore an older database, because doing so would discard newer live
transactions. A code rollback across a database migration requires checking
that the older application supports the migrated schema.

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

## Google Drive backups and restore

The host's `cash-27-backup.timer` fires nightly around 2:00 AM and persists
missed runs across a reboot. It asks the running container to create one
timestamped `.tar.gz` package, then uses rclone to copy that package into the
configured Google Drive folder. Every package contains:

- a consistent SQLite online snapshot, never a copied live WAL database;
- every retained immutable PDF object, deduplicated by hash inside the archive;
- every retained finalized report after its recorded hash is verified; and
- a manifest containing the path, size, and SHA-256 hash of every payload file.

The newest successful package is also retained locally at
`backups/drive/latest-local.tar.gz`. At most one failed package is retained
locally for diagnosis. Google Drive receives uniquely named packages and the
backup job never deletes remote files.

Settings reports whether rclone was configured, whether the timer has checked
in, the latest successful backup, and recent job activity. Turning off automatic
backups requires confirmation and produces a warning on every administrator
page. The systemd timer remains installed and records a cheap disabled heartbeat;
the web container never receives permission to control host systemd or access
the rclone token.

The same backup can be run manually when diagnosing production:

```bash
systemctl --user start cash-27-backup.service
```

For a complete restore, download one package from Drive, extract it into a new
directory, stop the application, and run the existing verified restore command:

```bash
systemctl --user stop cash-27
backup_root=/absolute/path/to/extracted-backup
data_root=~/.local/share/cash-27
image=$(sed -n 's/^Image=//p' \
  "$HOME/.config/containers/systemd/cash-27.container")
podman run --rm --userns keep-id \
  --volume "$data_root:/data:Z" \
  --volume "$backup_root:/backup:Z" \
  --entrypoint python "$image" \
  -m cash.ops restore /data /backup
systemctl --user start cash-27
```

Restore applies migrations, runs SQLite integrity checks, verifies document
state, restores and hash-verifies finalized reports, and replays the ledger.
Test restoration periodically instead of assuming that the existence of backup
files is sufficient.

## Future publishers

Human-readable Google Drive folders and a live Google Sheets ledger are planned
as optional projections over Cash 27's authoritative data, not as core storage.
The design notes and operational boundaries are in
[`FUTURE-PLUGINS.md`](FUTURE-PLUGINS.md).

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
