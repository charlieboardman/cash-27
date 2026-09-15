# cash-27

`cash-27` is a small USD/MXN cash ledger for vouchers, sign-outs, receipts,
reconciliation, and revisioned fiscal-year reports. It is intended to replace a
spreadsheet while keeping the workflow understandable and correctable. The
project specification in [`spec-new.md`](spec-new.md) is the behavioral source
of truth.

## Workflow at a glance

- **New transaction** records a direct expense, signout, currency exchange, or
  bank withdrawal immediately. Direct expenses and signouts produce printable
  vouchers showing the USD/MXN authorized for the purpose, with one large Notes
  area. Purchase values and change are entered during review when needed for
  currency conversion. **Save** opens the transaction without
  printing; **Save and print voucher** opens the printable form. Add cash or
  record a return from the original signout's
  administrator workspace, choosing **Save** or **Save and print addendum**.
  Saved addenda can be printed later from the signout's cash activity or the
  ledger row's details. The page's **Other** tab holds the infrequent cash
  donation workflow.
- **Capture** turns PDF/JPEG/PNG evidence into immutable normalized PDFs and
  places their database records in the inbox. Voucher and addendum QR codes name
  scans and suggest the exact review record. QR orientation corrects sideways or
  upside-down pages; ordinary receipts rotate only when four model views agree
  with scores of at least 0.90. Uncertain scans stay unchanged for manual review.
- **Review** separates unfinished ledger transactions from employee receipt
  submissions. On desktop it presents the document queue, a stable transaction
  table, and the selected transaction's workspace from left to right. On a
  phone, the table, workspace, and document picker become separate full-screen
  layers. Direct expenses need a signed authorization voucher; separate receipts
  are optional. Select **Also use voucher as the receipt** only when it includes
  the vendor's acknowledgment signature. Ordinary expenses need one set of
  USD/MXN amounts; expand **Change or currency conversion** when needed. Signout
  receipt splits conserve their recorded cash and receipt-value components.
  Print controls sit above the drawer's
  document attachments; save/file controls sit below its editing fields.
  New account and class selections start blank. **Save progress** remembers
  expense coding and splits before paperwork is available, leaving the expense
  open; **Save & review next** still requires the codes and documents to complete it.
  Exchanges remain incomplete until reviewed with a receipt selected from the
  document queue or uploaded in their review workspace.
- A signout's shared-password-protected `/FY##-NNNN` page lets the signee upload
  receipt drafts, submit a package, correct returned items, and resubmit the
  same package. The **English / Español** toggle on this page and its login
  screen remembers the language in the browser and preserves unsaved entries
  and selected files when switched. Receipt files and purchase details share
  one editor; saved receipts appear as cards with totals, status, and document
  links. Enter net cash spent in USD and MXN, using negative amounts for change
  received. Opposite-sign amounts reveal the receipt total in the change
  currency; ordinary payments do not require a separate receipt total.
  Accepted packages become ledger
  transactions atomically.
- The administrator's `/signouts/FY##-NNNN` workspace groups receipt submissions,
  cash activity, documents, and closeout under the voucher number. Open a
  submission to inspect its receipts, assign account and class codes, mark
  receipts checked, and approve them or request corrections. Submissions awaiting
  review open automatically; approved submissions remain available as history.
  **Open signout** in Review opens the same submission in this workspace.
- **Close out this signout** in the administrator workspace previews the final
  cash return and any remaining USD-to-MXN settlement. For example, $20 issued,
  a MXN 400 receipt, and MXN 100 returned implies 25 MXN/USD: $16 of expense and
  $4 of returned peso value. Confirmation records the settlement and return
  together and prints the mandatory return addendum. The signout closes only
  after signed paperwork is filed, all receipts are reviewed, both cash balances
  reconcile to zero, and its closeout confirmation still matches the records.
  Later edits can require a new confirmation. Fresh-install schema `migrations/0010_initial.sql` stores closeout confirmations
  and distinguishes receipt-currency entries from known tender. This release
  requires the planned reset; it does not upgrade the pre-reset database.
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
  they do not overwrite it. CSV reports and **Export expenses XLSX** on Ledger
  are for the bookkeeper: one row per active expense allocation, including
  negative expense adjustments. Cash movements, superseded rows, and void rows
  are excluded. Columns are `date`, `Account`, `mxn`, `ER`, `usd`, `calc_usd`,
  `voucher_id`, `receipt_id`, `addendum_id`, `class_code`, `project`, `recipient`,
  `vendor`, `description`, and `notes`. ER is the engine-derived MXN per USD
  for the peso portion, displayed to three decimals; monetary values use two
  decimals. Rounding occurs only on export. USD-only expenses have no ER;
  unresolved expenses have neither ER nor calculated USD. Preview exports can
  include unallocated expenses, which continue to block report finalization.
  Excel amounts are numeric and summable, with an `expenses` sheet covering
  all fiscal years and a separate sheet per fiscal year. Existing retained CSV
  revisions keep the format and bytes with which they were originally saved.
- **Settings** manages the expense accounts and classes offered during review.
  Deactivating a code never rewrites historical transactions. It also provides
  browser-based database export, validation, and import.

## Production assumptions

- A public, systemd-based Linux VM using `apt` or `dnf`
- Initial root access; setup creates a locked, unprivileged `cash27` runtime
  account and delegates all container work to it
- A domain whose DNS points to the VM
- Inbound TCP ports 80 and 443 allowed by the cloud firewall
- Network access to install host packages and build the application image
- Managed Droplet backups enabled separately in DigitalOcean

The Compose project contains the Flask/Gunicorn application, Caddy, and a local
archive service using the same application image. The host does not
need a Python virtual environment. The application runs one Gunicorn worker
because the deployment is built around one SQLite database.

### Capture recognition

The application image includes CPU QReader (nano) and the approximately 7 MB
Paddle document-orientation ONNX model. Checkpoints are downloaded and verified
when building the image; uploads need no model downloads or network access.
Each model runs in a short-lived subprocess, sequentially, so there is no
permanent ML worker or idle model RAM. The shared lock allows only one model
process at a time. Recognition does occupy the single application worker during
an upload. QReader gets at most 45 seconds, receipt orientation 15 seconds, and
a capture request has an 80-second recognition deadline across its files.
After that, remaining files are saved for manual review.

Both features are enabled in the container image. Set these in the application's
environment file and recreate the app container to change them:

| Variable | Container default | Effect |
|---|---|---|
| `CASH_QR_ENABLED` | `1` | `0` disables capture recognition, naming, matching, and rotation |
| `CASH_RECEIPT_ORIENTATION_ENABLED` | `1` | `0` disables only the ordinary-receipt fallback |

Local Python installs default both flags to `0`. To enable locally, install
CPU-only Torch/torchvision and `requirements-capture.txt`, cache the checkpoints
as in `Containerfile`, and set the flags and model paths. The container is the
tested deployment path. A valid voucher QR always takes precedence over the
receipt classifier, including pages with conflicting voucher IDs. Generic
orientation requires all four 90-degree views to predict the same correction
and each score to reach 0.90; that is an abstention heuristic, not a calibrated
accuracy guarantee. PDF rotation changes page metadata without recompressing
scan images, before calculating the final stored hash.

Unique voucher IDs name scans such as `FY27-0002-A1.pdf`; absent or conflicting
IDs use UTC timestamp names. Matching only suggests an eligible active record.
Review still checks the document and signatures and files it explicitly;
recognition never approves transactions. Failures retain a usable inbox PDF.
Employee receipt submissions keep their existing workflow. Scanner uploading
is separate and is not implemented here.

See [the local benchmark report](experiments/qr/RESULTS.md) for Podman resource
measurements and accuracy limits. The larger image and transient decoder RAM
must be considered when deploying to the 1 GB VM. Compose caps the app at
512 MiB RAM with no swap, matching the local test budget. The Compose environment
can override `CASH_APP_MEMORY_LIMIT` and `CASH_APP_MEMORY_SWAP_LIMIT` (the latter
is combined RAM plus swap). These limits do not cover Caddy or the backup
service. No VM swap changes are part of this feature.

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
- `reports/` for retained finalized reports;
- `backups/` for local recovery archives and import safety copies; and
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
employee password, and paths to the account-code and class-code CSV files. The
employee password protects every receipt-submission link from random internet
traffic; it does not give employees administrator access. On later runs it offers the installed
values as defaults, so an upgrade does not rotate the password or session
secret or require finding the original CSV files again. The script then:

- installs Podman, a Compose provider, and curl when absent;
- opens ports 80 and 443 in an active UFW or firewalld host firewall;
- permits rootless containers to bind ports 80 and 443 when necessary;
- generates and retains a private session-signing secret;
- validates and stores private copies of the account and class CSV files;
- creates the persistent data and private configuration directories;
- builds one application image tagged with the current commit;
- installs one user service that brings the Compose project back after reboot;
- binds the application only to host localhost on port 8000;
- runs Caddy in Compose as the public reverse proxy with automatic HTTPS;
- creates verified local archives at startup and every 24 hours; and
- starts the stack, applies migrations, checks SQLite, and waits for health.

Enable managed backups in the Droplet's **Backups** section in DigitalOcean.
Choose the frequency based on how much recent work you could afford to re-enter
after a failure. Setup reminds you, but does not enable or verify the provider's
backup service. No Google account, OAuth application, or offsite-storage
configuration is required by cash-27.

Keep the persistent data directory on the Droplet's own disk. A Podman bind
mount of a directory on that disk is covered; a separately attached
DigitalOcean Volume is **not** included in Droplet backups. This applies to
custom data locations as well as the default under `/home/cash27`.

The script prints the final `https://.../login` URL. Certificate issuance is
automatic once public DNS and ports 80/443 are correct. Production cookies are
marked `Secure`, so public plain HTTP is intentionally unsupported.

The application and backup containers have read-only root filesystems, private
temporary directories, dropped Linux capabilities, and strict bind mounts.
The local archive service has no network and receives no application passwords
or external credentials. It reads the data root and writes recovery archives
under `backups/local/`. On application start, pending migrations are applied,
standard accounts are seeded, and
SQLite integrity is checked.

Production builds require a clean Git worktree. The locally built image is
named from the checked-out commit, for example
`localhost/cash-27:ecad219abc12`; private `compose.env` records the exact
deployed names rather than a mutable `latest` tag. Older commit-tagged images
remain available until explicitly removed.

Useful diagnostics from the root shell, if they are ever needed:

```bash
./deploy/compose.sh ps
./deploy/compose.sh logs --follow app
./deploy/compose.sh logs --follow local-backup
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
| `CASH_ADMIN_PASSWORD` | The shared administrator password chosen during setup |
| `CASH_USER_PASSWORD` | The shared password for employee receipt pages |
| `CASH_SECRET_KEY` | A strong, persistent Flask session-signing secret |
| `CASH_FY_START_MONTH` | Optional fiscal-year start month; defaults to `9` |

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

Account and class codes can be numeric, such as `1000`, `0010`, or `1`, or use
letters, periods, and underscores, such as `7.5` or `EXP_TRAVEL`. They are stored
as text, so leading zeros are preserved and `4.10` stays distinct from `4.1`.
Codes accept 1-32 characters, starting with a letter or digit; letters are
normalized to uppercase and spaces to underscores.
After building the image, setup validates every CSV row before starting the
services. A validation error identifies the file and row to correct.

Container startup adds new rows, refreshes names, and reactivates matching
codes. It never deletes database rows omitted from a CSV, so rerunning setup is
safe for historical records. Codes may also be managed later in Settings.

Project remains an optional free-text field rather than a separate reference
table. While entering a project, Review offers existing project values from
the ledger as selectable suggestions.

## Powerwash and reinstall

To deliberately discard the installation and reenter the ledger, run these
commands as root after the new release has been pushed:

```bash
cd /opt/cash-27
git pull --ff-only
./powerwash.sh
./setup.sh
```

`powerwash.sh` shows the installed runtime account, its entire home, and the
configured data directory. It requires typing exactly `yes` at two separate
prompts in an interactive terminal before stopping anything. Either refusal
or end of input cancels the reset. Only run setup after powerwash succeeds.

The reset stops and disables the Compose service, then backs up the database
and all uploaded documents to a root-only directory:

```text
/cash27-backups/<UTC timestamp>/
  data.db
  documents/
```

The backup directory is created if needed. SQLite's backup API includes any
committed WAL data, and the script checks database integrity and document copies
before deleting installation data. A backup failure stops the reset and leaves
the account and application data in place, with services stopped. This backup
preserves the old schema for reference; it is not an import into the new schema.
It does not include finalized reports, configuration, or installed CSV copies.

After the backup succeeds, the reset removes the runtime account's
Podman containers and storage, disables lingering, terminates its sessions,
stops the systemd user manager, and waits for all runtime processes to exit. It then
deletes the data directory and the Linux account with its home, and removes
`/etc/cash-27/operator.conf`. This erases the database, documents, reports,
**all backups inside the installation**, passwords, session secret, installed CSV copies, and
HTTPS certificate state. A configured data directory outside the home is also
erased. Shutdown failures or processes still running after the wait stop the
reset before ledger/account deletion.

The checkout and files inside it, including the original `accounts.csv` and
`classes.csv`, are preserved. Keep those original CSVs outside the runtime home
and data directory. Private configuration must be inside the runtime home;
the helper rejects unsafe or overlapping paths. The new backup under
`/cash27-backups/` is preserved. Powerwash does not pull or build code and leaves
the application stopped.

Run `setup.sh` afterward to recreate the account and initialize an empty
database. Supply the domain, administrator and employee passwords, and original
CSV paths again. Caddy obtains fresh certificates. Then enter the opening
balances (including the opening MXN exchange rate) and the remaining transactions.

## Upgrades

Run the upgrade script as root from the production checkout:

```bash
cd /opt/cash-27
./upgrade.sh
```

Before pulling any source, the script creates and verifies a local full archive
under `backups/local/pre-upgrade/`. A failed archive stops the upgrade before Git
or the running application changes. This step needs no external storage service.
Root then runs `git pull --ff-only`; the script delegates build and runtime
operations to `cash27`, builds a commit-tagged application image,
atomically updates its private Compose image reference, recreates the
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

## Backups and recovery

DigitalOcean managed backups provide recovery of the Droplet. Cash 27 also
keeps consistent application archives on that Droplet so a restored disk has
portable recovery points. Local archives share the server's fate until a
provider backup captures them or you download a copy. Cash 27 does not claim to
monitor or verify DigitalOcean backups.

DigitalOcean describes its backups as crash-consistent disk images and
excludes attached Volumes. Keep the database, documents, reports, and local
archives on the backed-up Droplet disk. See
[DigitalOcean backup limits](https://docs.digitalocean.com/products/backups/details/limits/).

### Local application archives

The `local-backup` Compose service creates an archive on startup and every
24 hours after a successful run. If the database is not ready yet or an archive
fails, it logs the error and retries after 15 minutes. It reuses the application
image, runs without network access, and has no Google credentials.

Each local `.tar.gz` archive contains:

- a consistent SQLite online snapshot, including committed WAL data;
- every document not recorded as missing, including discarded documents,
  deduplicated by hash within the archive;
- all retained finalized report files with their recorded hashes verified; and
- a manifest with the application revision, schema version, payload paths,
  sizes, and SHA-256 hashes.

An archive becomes visible in its final location only after a staged restore
passes integrity, document/report checks, and ledger replay. Scheduled, manual,
and pre-upgrade jobs share a filesystem lock. A failed creation or verification
does not rotate away previous archives.

Default retention keeps the latest seven scheduled archives, three
pre-upgrade archives, and three manual archives in separate directories:

```text
backups/local/
  scheduled/
  pre-upgrade/
  manual/
```

Run an extra local archive or inspect activity:

```bash
./deploy/compose.sh run --rm --no-deps local-backup once
./deploy/compose.sh logs --tail 50 local-backup
./deploy/compose.sh ps
```

The container health check checks the age of the latest verified scheduled
archive only. It says nothing about DigitalOcean or any offsite copy.

Optional tuning goes in
`/home/cash27/.config/cash-27/compose.env`; setup also accepts these environment
variables and preserves installed values on reruns:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CASH_LOCAL_BACKUP_INTERVAL_SECONDS` | `86400` | Delay after a successful scheduled archive |
| `CASH_LOCAL_BACKUP_RETRY_SECONDS` | `900` | Delay after a failed scheduled archive |
| `CASH_LOCAL_BACKUP_KEEP` | `7` | Scheduled archives retained |
| `CASH_LOCAL_BACKUP_UPGRADE_KEEP` | `3` | Pre-upgrade archives retained |
| `CASH_LOCAL_BACKUP_MANUAL_KEEP` | `3` | Manual archives retained |

All values must be positive integers. Apply changed values with
`./deploy/compose.sh up --detach`. Retention is count-based: allow disk space
for retained full archives plus temporary creation and restore-verification
copies. Pre-import database copies and pre-restore safety archives are excluded
from automatic rotation; review those separately before removing them.

### Restore a local application archive

From the production checkout:

```bash
./restore-local.sh
```

The helper lists local archives and asks which to restore. You can also pass
the path relative to the persistent data root, for example:

```bash
./restore-local.sh backups/local/scheduled/cash-27-full-20260904T180000123456Z.tar.gz
```

For a previously downloaded archive, place it under
`<persistent-data-root>/backups/restore/`, preserving its
`cash-27-full-<UTC timestamp>.tar.gz` name, and make it readable by `cash27`.
It then appears in the same menu. A non-interactive restore also requires
`CASH_RESTORE_CONFIRM='RESTORE CASH 27'`.

Arrange a pause in user activity and do not run upgrades or imports during a
restore. The helper pauses scheduled archives, verifies the selected archive
before stopping Flask, then stops Flask and creates a current-state safety
archive. It stages and validates the restore, installs referenced files before
replacing the database, restarts the stack, and checks health. Restore or health
failure reinstates the safety archive. If the current database is too damaged
for a verified safety archive, it preserves the stopped database, WAL,
documents, and reports in a forensic archive before attempting recovery.

Full recovery does not depend on Flask being healthy. The browser's SQL
export/import remains available for database maintenance; it does **not**
upload or restore the PDFs and report files contained in a full archive.

### Rehearse a DigitalOcean restore

After the first managed backup completes:

1. In DigitalOcean, create a temporary Droplet from that backup, leaving the
   production Droplet and DNS unchanged.
2. Keep the temporary Droplet private using its cloud firewall. Open a tunnel
   from your own computer:
   `ssh -L 8001:127.0.0.1:8000 root@<temporary-droplet-ip>`.
3. Check `http://localhost:8001/api/health`. For authenticated browser checks,
   point a temporary hostname at the test Droplet and rerun `./setup.sh` there
   using that hostname, retaining the copied passwords and CSVs. Allow the
   access needed for Caddy's certificate issuance and use the temporary HTTPS
   URL. Keep production DNS and the production configuration unchanged.
4. Confirm expected ledger rows, signout balances, attached PDFs, and retained
   reports. On the temporary Droplet, exercise `./restore-local.sh` against a
   local archive as well, and check the same records again.
5. Once satisfied, remove the temporary Droplet through DigitalOcean.

A successful rehearsal tests the stored data and the recovery procedure.
Setup does not perform this provider-side exercise for you.

## Future Google integrations

No Google transport is bundled or required. The proposed Apps Script backup
plugin sends a new complete database snapshot and only missing immutable
documents and report files, using destination inventory, checksums, resumable
batches, and committed manifests. Large initial PDF collections can be uploaded
manually through Drive. This future transport does not require tarballs or
binary diffs; local portable archives above remain a separate feature.

Human-readable Drive folders and a Google Sheet are separately rebuildable
views. The proposed backup protocol, authentication, retention, manual seeding,
and restore acceptance tests are recorded in
[`FUTURE-PLUGINS.md`](FUTURE-PLUGINS.md). These are design notes, not implemented
features.

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

Optional browser tests exercise saving/printing and the review drawer at
desktop and phone widths. They use a temporary database and loopback server,
never the development or production data. Install the test-only browser tools
to include them (otherwise the browser module is skipped):

```bash
uv pip install --python .venv/bin/python playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q tests/test_browser_workflows.py
```

For ledger rules, correction semantics, report finalization, security
boundaries, and acceptance criteria, read [`spec-new.md`](spec-new.md).
