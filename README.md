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
- Initial root access; setup creates a locked, unprivileged `cash27` runtime
  account and delegates all container work to it
- A domain whose DNS points to the VM
- Inbound TCP ports 80 and 443 allowed by the cloud firewall
- Network access to install host packages, build the application image, and
  upload backups to Google Drive

The Compose project contains the Flask/Gunicorn application, Caddy, a private
archive-builder service, and a separate Google Drive adapter. The host does not
need a Python virtual environment. The application runs one Gunicorn worker
because the deployment is built around one SQLite database.

## Persistent data is required

The database and uploaded evidence are runtime data, not source code. They must
live in a persistent directory that survives application deployments and
container rebuilds. Prefer a bind mount or a directory outside the checkout,
owned by the service account and inaccessible to other users.

When setup is launched by root, the installer uses
`/home/cash27/.local/share/cash-27` by default and bind-mounts it into the
container at `/data`. It prints that location at the beginning and end of every
run. The root-owned checkout and container images are disposable; this
directory is not.

The persistent directory contains:

- `cash.db` and its SQLite WAL sidecars while the application is running;
- `documents/` for immutable PDFs with UTC timestamps, document IDs, and safe
  upload-name hints;
- `staging/` for validated database imports;
- `reports/` for retained finalized reports; and
- `backups/` for local backup working state and recovery archives; and
- `caddy/` for certificate and proxy state that survives container rebuilds.

These paths, Python bytecode, and environment files are ignored by Git. Do not
force-add them.

## First production deployment

First point the chosen domain at the VM and allow inbound TCP ports 80 and 443
in the provider's firewall. Give the VM's root account read-only access to this
private GitHub repository, then run:

```bash
git clone git@github.com:charlieboardman/cash-27.git /opt/cash-27
cd /opt/cash-27
./setup.sh
```

The easiest place for the two private reference files is
`/opt/cash-27/accounts.csv` and `/opt/cash-27/classes.csv` before setup starts;
those names are ignored by Git. Files elsewhere must be readable by the
runtime account when the installer prompts for them.

The root bootstrap creates a locked `cash27` account, makes the checkout
root-owned and readable—but not writable—by that account, installs host
packages and firewall settings, enables its systemd user manager, and then
relaunches setup as `cash27`. Root retains ownership of Git updates; Podman,
application configuration, persistent data, and every long-running service
remain unprivileged. Set `CASH_SERVICE_USER` before the first run only if a
different runtime username is required.

`setup.sh` then asks for the public domain, an administrator password, one shared
employee password, paths to the account-code and class-code CSV files, and a
Google Drive destination for automatic backups. The
employee password protects every receipt-submission link from random internet
traffic; it does not give employees administrator access. On later runs it offers the installed
values as defaults, so an upgrade does not rotate the password or session
secret or require finding the original CSV files again. The script then:

- installs Podman, a Compose provider, curl, and rclone when absent;
- opens ports 80 and 443 in an active UFW or firewalld host firewall;
- permits rootless containers to bind ports 80 and 443 when necessary;
- generates and retains a private session-signing secret;
- validates and stores private copies of the account and class CSV files;
- creates the persistent data and private configuration directories;
- builds application and Drive-backup images tagged with the current commit;
- installs one user service that brings the Compose project back after reboot;
- binds the application only to host localhost on port 8000;
- runs Caddy in Compose as the public reverse proxy with automatic HTTPS;
- runs the isolated Drive adapter at startup and every 24 hours; and
- starts the stack, applies migrations, checks SQLite, and waits for health.

Google Drive authorization is handled by `rclone config`, which setup opens
when no remote exists. Follow the wizard's current Google Drive instructions;
the authorization token remains in rclone's private host configuration. Compose
mounts it read-only into the Drive service only; it is never mounted into the
application container or committed to Git. If Drive configuration is postponed,
setup still completes and Cash 27 shows administrators that backups need
attention.

For unattended reruns after rclone is authorized, the destination can be
provided as `CASH_SETUP_RCLONE_REMOTE` and `CASH_SETUP_RCLONE_PATH`.

The script prints the final `https://.../login` URL. Certificate issuance is
automatic once public DNS and ports 80/443 are correct. Production cookies are
marked `Secure`, so public plain HTTP is intentionally unsupported.

The application and backup containers have read-only root filesystems, private
temporary directories, dropped Linux capabilities, and strict bind mounts.
The private archive builder can access authoritative data but receives no
Google configuration. The Drive adapter has no database or document mount; it
can access only `backups/drive/` and alone sees the rclone token. On application
start, pending migrations are applied, standard accounts are seeded, and
SQLite integrity is checked.

Production builds require a clean Git worktree. Both locally built images are
named from the checked-out commit, for example
`localhost/cash-27:ecad219abc12`; private `compose.env` records the exact
deployed names rather than a mutable `latest` tag. Older commit-tagged images
remain available until explicitly removed.

Useful diagnostics from the root shell, if they are ever needed:

```bash
./deploy/compose.sh ps
./deploy/compose.sh logs --follow app
./deploy/compose.sh logs --follow backup-core
./deploy/compose.sh logs --follow drive-backup
./deploy/compose.sh logs --follow caddy
./deploy/as-runtime-user.sh systemctl --user status cash-27-compose
```

The Compose helper, image builder, restore command, and upgrade command
automatically delegate to the installed runtime account when root invokes
them. `/etc/cash-27/operator.conf` records the non-secret runtime identity and
installed checkout path.

The installer stores private application settings at
`/home/cash27/.config/cash-27/cash-27.env`. The image accepts these variables:

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
files to `/home/cash27/.config/cash-27/reference/` with private permissions and
mounts those copies read-only in the container. On a later run, press Enter to
keep the installed copies or answer `n` to select replacement files. For
unattended setup, provide `CASH_SETUP_ACCOUNTS_CSV` and
`CASH_SETUP_CLASSES_CSV`.

Container startup adds new rows, refreshes names, and reactivates matching
codes. It never deletes database rows omitted from a CSV, so rerunning setup is
safe for historical records. Codes may also be managed later in Settings.

Project remains an optional free-text field rather than a separate reference
table. While entering a project, Review offers existing project values from
the ledger as selectable suggestions.

## Upgrades

Run the upgrade script as root from the production checkout:

```bash
cd /opt/cash-27
./upgrade.sh
```

Before pulling any source, the script asks the Compose Drive service to create
a complete verified backup and upload it to Google Drive. A failed backup stops
the upgrade before Git or the running application changes. If backup protection
was explicitly turned off in Settings, the service records that state and the
upgrade is allowed to continue. Root then runs `git pull --ff-only`; the script
delegates build and runtime operations to `cash27`, builds commit-tagged
application and backup images,
atomically updates their private Compose image references, recreates the
services, and waits for application health. It does not rerun setup or change
passwords, reference CSV copies, the database, documents, or Caddy certificate
state. Normal application startup may apply a database migration.

If the new application does not become healthy, the script puts the prior image
back and reports the failure. It does not reverse database migrations. Retained
images therefore provide a convenient code rollback, but a release containing a
schema migration still requires the usual compatibility care.

The upgrade can also be started from another computer without opening an
interactive shell:

```bash
ssh root@cash-production 'cd /opt/cash-27 && ./upgrade.sh'
```

The deployed image names are recorded in
`/home/cash27/.config/cash-27/compose.env`. To see retained versions:

```bash
./deploy/as-runtime-user.sh podman image list localhost/cash-27
```

The upgrade script automatically restores the prior image references when the
new application fails its health check. A manual code rollback can similarly
select retained tags in `compose.env` and run
`./deploy/compose.sh up --detach`. Do not restore an older database merely to
roll back frontend code, because that would discard newer live transactions.

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

The `drive-backup` Compose service runs once when it starts and every 24 hours
thereafter. It asks the private `backup-core` service to create a timestamped
`.tar.gz` package, verifies that package, then uploads it with rclone. The
adapter never mounts the database or document store. Every package contains:

- a consistent SQLite online snapshot, never a copied live WAL database;
- every retained immutable PDF object, deduplicated by hash inside the archive;
- every retained finalized report after its recorded hash is verified; and
- a manifest containing the application revision, database schema version, and
  path, size, and SHA-256 hash of every payload file.

The newest successful package is also retained locally at
`backups/drive/latest-local.tar.gz`. At most one failed package is retained
locally for diagnosis. Google Drive receives uniquely named packages and the
backup job never deletes remote files.

Settings reports whether rclone was configured, whether the service has checked
in, the latest successful backup, and recent job activity. Turning off automatic
backups requires confirmation and produces a warning on every administrator
page. The adapter remains installed and records a disabled heartbeat; the web
container never receives the rclone token or permission to control Podman.

The same backup can be run manually when diagnosing production:

```bash
./deploy/compose.sh run --rm --no-deps drive-backup once
```

For a complete restore, run:

```bash
./restore-from-drive.sh
```

The command lists Drive archives, downloads the selected file through the
isolated adapter, rejects unsafe archive entries, verifies every manifest hash,
and exercises a staged restore before stopping the application. It creates a
local safety archive of current live state, stops the application and backup
services, stages and validates the selected restore, installs evidence before
atomically replacing the database, restarts the stack, and checks health. A
failed restore or health check automatically reinstates the safety archive.
If the current database is already too damaged to make a verified safety
archive, the command stops writers and preserves the raw database, WAL,
documents, and reports in a clearly marked forensic archive before proceeding.
This recovery route deliberately does not depend on Flask being healthy.

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
