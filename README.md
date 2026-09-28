# cash-27

`cash-27` is a small USD/MXN cash ledger for vouchers, sign-outs, receipts,
reconciliation, and revisioned fiscal-year reports. It is intended to replace a
spreadsheet while keeping the workflow understandable and correctable. The
project specification in [`spec-new.md`](spec-new.md) is the behavioral source
of truth.

## Using Cash on your phone

Cash is an online-only installable web app. Open the site in your phone's browser
and choose **Install app** or **Add to Home Screen** from its browser/share menu.
It opens in its own window with a plain Cash text icon. Installation controls
vary by browser; you can also keep using the site in a normal browser tab.

At login, **Keep me signed in on this device** retains the session for up to
90 days between visits. It is checked by default for administrators and optional
on employee login. Uncheck it for a temporary browser session. Remembered logins
survive browser restarts and normal deployments using the existing session key;
logging out clears the device's session. You may need to log in once in the
installed app if it has separate browser storage. Existing temporary sessions
become remembered only after logging in again with the option checked.

Cash follows the device's light or dark setting. Printed forms and the
bookkeeper report stay light.

The app uses secure, HttpOnly session cookies, with no password stored in browser
storage. There is no service worker or offline transaction cache; an internet
connection is required for all application work.

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
  ledger row's details. **Record a donation** in the page header opens the
  infrequent cash donation form.
- **Capture** turns PDF/JPEG/PNG evidence into immutable normalized PDFs and
  places their database records in the inbox. The QR codes on the app's paper
  (vouchers, addenda, no-receipt statements, purchase logs, and count sheets)
  name scans and suggest the exact record. Every coded form is one page (a
  purchase log is one coded page per sheet), so a stack scanned together is
  split wherever the code changes; pages without a code, like a vendor
  receipt, become their own document. Printed text fields are limited so each
  form stays one page (see `cash/paper.py`). QR orientation corrects sideways or
  upside-down pages; ordinary receipts rotate only when four model views agree
  with scores of at least 0.90. Uncertain scans stay unchanged for manual review.
- **Review** separates unfinished ledger transactions from submitted expense
  reports, which open on their own page. On desktop it presents the document queue, a stable transaction
  table, and the selected transaction's workspace from left to right. On a
  phone, the table, workspace, and document picker become separate full-screen
  layers. A direct expense is one payment: the voucher's amount, coded in place
  on the row the voucher recorded. It needs the signed voucher; select **Also
  file as a receipt** when the vendor signed its acknowledgment, and attach any
  number of separate receipts, numbered R1, R2, and so on. The amount cannot
  change; the only addition is change received in a currency the voucher did
  not pay (a negative amount with its **Receipt total**). **Split receipt**
  divides the payment into parts with their own codes, and an optional
  **Project** suggests past entries.
  Print controls sit above the drawer's
  document attachments; save/file controls sit below its editing fields.
  New account and class selections start blank. **Save progress** remembers
  the rows before paperwork is available, leaving the expense
  open; **Save & review next** still requires the codes and documents to complete it.
  Exchanges and bank withdrawals remain incomplete until a receipt is attached.
  Use **Attach receipt** in the ledger or open the transaction in Review to choose
  an inbox document or upload one. Filing the receipt never records cash again.
  An open record lists its documents first, one line each: click a document to
  preview it in the inbox panel. A missing document offers **Attach**, then
  **From inbox…** (click a document to attach it) or **Upload…**; a file or
  inbox card can also be dropped on its line. Removing one can be undone.
  Attaching stages the document; nothing is filed until the record is saved,
  including signed addenda, which are filed with **File addendum**. The inbox
  shows thumbnails and opens a preview with Back, previous/next, and Open full
  size. From a preview, **Attach to transaction…** finds a transaction awaiting
  paperwork by date, amount, person, or record and attaches the document there
  (a direct expense takes it as its voucher or as a receipt). **Create new
  transaction…** offers a new withdrawal or exchange. Delete is in the
  preview's **•••** menu. Capture links each inbox document to this preview.
  The open record and tab stay in the page address, so reloading or sharing
  the link returns to them.
- A signout's expense report lives at `/FY##-NNNN`, one page for the recipient
  (shared employee password) and the administrator, who sees the same report
  plus approval, paperwork, and settlement. The link works in
  any letter case; `/fy27-0001` opens `/FY27-0001`. Blank amounts anywhere in
  the app mean zero.
- The report is a list of rows. **Add row** starts one; each row opens in place
  and saves itself as you type. A receipt row has an optional receipt (photos
  or PDFs, with pages that can be added or replaced), date, vendor, purpose,
  signed USD and MXN (negative means change received), and class and account
  codes. **Receipt total** appears only when USD and MXN have opposite signs,
  for example $20 paid with MXN 100 change, because the engine needs it to
  value the change. **Split receipt** divides one receipt into two or more
  parts, each with its own amount, purpose, and codes; the parts must add up.
  A row without a receipt needs an explanation, and before approval a signed
  **no-receipt statement** (`FY27-0042-R3x`): **Print statement** fills one in
  from the row for the recipient and/or the administrator to sign, and
  **Attach signed statement** files it in the receipt's place. **Print missing
  statements** prints one page per row still needing one. A scanned statement's
  QR code suggests it on its row. Purchases without receipts can instead go
  on one **purchase log** per signout (**Print purchase log**, code
  `FY27-0042-log`): all its sheets are filed as one PDF on one row for the
  total, before approval. Tick rows, or **Select all**,
  to set class and account codes on many rows at once. Recipients may code
  their rows; the administrator can change the codes.
- A **Cash returned** or **Reimbursement requested** row declares the cash
  left over or personal money spent. **Left according to this report** counts
  every row and offers the matching row when something is left over or
  overspent. **Submit report** checks every row and locks the report for the
  recipient until they reopen it or the administrator requests corrections,
  optionally with notes on individual rows.
- **Approve** (administrator) posts every new receipt row to the ledger in one
  command and accepts the declarations. Until then a report is a working draft
  in `expense_report_drafts` (migration `0015`), one JSON document per signout
  whose `format` number lets its shape change without further migrations.
  Each receipt row gets its receipt number (FY27-0042-R3) when first saved, and
  approval posts it under that number. A receipt is staged whole (every page,
  in order, as on Capture) and then attached; a filed receipt is never added
  to. Approved rows keep their amounts and codes (later corrections use the
  ledger), and a row approved without a receipt can take one at any time, even
  after settlement; the administrator may also choose it from the inbox. A
  receipt that turns up after a row was approved with a no-receipt statement
  takes the next receipt number, and the statement stays on record.
  Reports started
  before `0015` open from their earlier records, which are left unchanged.
- **Settlement** (administrator, after approval) proposes the declared return
  and reimbursement. The admin reviews any inferred USD-to-MXN exchange and
  confirms cash actually received or paid. A report note, or amounts that
  differ from the declared ones, need a resolution note; unexplained
  arithmetic differences still block settlement. For $20 issued, MXN 400 spent
  and MXN 100 returned, the implied rate is 25 MXN/USD: $16 of expense and $4
  returned. Confirmation atomically records reimbursement, exchange, and return
  as needed, and provides the resulting addenda for printing and signing. Only
  addenda that give the recipient additional cash ask for the recipient's
  signature; reimbursement and cash-return addenda are signed by the approver.
  Completion is automatic after all signed paperwork is filed and cash and
  basis reconcile. Later changes to the report or its cash invalidate the
  confirmation.
- **Ledger** is the correctable authoritative history. Transaction IDs remain
  monotonic and stable; material edits and voids require reasons and retain
  before/after evidence. Voiding any line of a direct expense voids the whole
  payment and its voucher. A receipt that arrives after a direct expense is
  complete is added from its ledger detail with the next receipt number.
  Documents are listed by reference (FY27-0042, FY27-0042-R1), the same names
  the bookkeeper package uses.
- **Balances** derives the current SAFE and signout balances from the ledger.
  Its physical-count tool first compares without saving, then **Save count**
  records the count in one atomic command: every nonzero currency difference
  with its required explanation, or a 0/0 adjustment when the count matches.
  **Print count sheet** prints it for "counted by" and "witnessed by"
  signatures with the QR code `Txn-123`, and the count waits in Review until
  the signed sheet is filed. If the ledger changed after comparison, the count
  must be compared again before it can be saved.
- On first login to a new database, **Set starting cash** records the one
  genuine opening balance. It remains editable from Ledger with a warning and
  full-history replay; it is not combined with recurring donations.
- **Reports** offers an **Export type** selector, **Format** (XLSX or CSV), and a
  **Fiscal year** list: every year from the first transaction to the current
  one, plus **All years**. The current year, taken from the device's date, is
  selected first. Ledger links there through **Exports and reports**.
  Expense reports contain only active expense allocations, including negative
  adjustments, with accounting codes and calculated USD. Cash ledgers include
  every active transaction: openings, withdrawals, donations, exchanges,
  signouts, returns, adjustments, and expenses. They add readable **Activity**,
  **From**, **To**, **Safe Balance USD**, and **Safe Balance MXN** columns;
  calculated USD remains blank for cash movements. Both views omit superseded
  and void rows. Safe balances show physical cash in SAFE immediately after
  each movement, including earlier history outside the selected period. Cash
  held by signees is excluded; splits of one receipt share its ending balance.
  Monetary values use two decimals and ER uses three. Expense ER comes from
  the engine's peso valuation; exchange ER is the actual MXN received per USD
  paid. Excel amounts are numeric; account codes remain text. Ledger workbooks
  include an all-years sheet and a separate sheet per fiscal year.
  **Download** includes transactions still awaiting review and saves nothing on
  the server. File names carry the period and a UTC timestamp, such as
  `FY2027-expenses-20260927T183000Z.xlsx`. Workbooks end with an **Export** sheet
  recording the generation time, ledger revision, and highest transaction ID;
  `since_txn_id` on `/api/export/transactions.*` exports only later
  transactions. Unallocated expenses and unresolved basis are reported after
  the download as warnings.
- **Bookkeeper report** is available from Reports via **View bookkeeper
  report**. Choose Expense report or Cash ledger, a fiscal year and/or date range.
  The table uses Category and Class names, keeps split purchase lines together,
  and shows MXN, ER, USD, and subtly highlighted **Calc USD**. Search, category/class
  filters and sortable headings help review; document references open their PDFs.
  Signout purchases include linked parent vouchers and addenda. Older upload names
  work through existing document associations; unlinked or missing paperwork is
  marked rather than guessed from filenames.
  **Download with documents** creates a ZIP with `index.html`, `report.xlsx`,
  `report.csv`, supporting PDFs and a manifest. Extract everything together: HTML
  and the workbook's Documents sheet link to local PDFs without login or internet.
  Each document is included once, with a readable reference-based export filename;
  originals are unchanged. Search/category/class filters apply to the browser view;
  downloads include the whole selected period. The package is a working snapshot,
  not a QBO import file. Both new endpoints are admin-only, use a consistent
  read snapshot, and make no database changes. ZIP creation streams PDFs through
  a temporary file on the existing data disk and verifies their stored hashes.
- **Settings** manages user access and the expense accounts and classes offered
  during review. **Employee password** updates `CASH_USER_PASSWORD` in the existing
  `/home/cash27/.config/cash-27/cash-27.env`, preserving all other lines. The app
  reads this literal, single-line value at each user login; no restart is needed.
  Passwords cannot be empty, multiline, or have surrounding whitespace. Existing
  user sessions remain signed in. The current password is never returned to the UI.
  This creates no separate password config and writes nothing to the database.
  The normal upgrade mounts the existing config directory writable in the app so
  atomic file replacement works; `setup.sh` reads the updated password on reruns.
  Admin password and other environment settings retain their startup behavior.
  **Voucher defaults** sets the default approver name without SSH or a restart.
  It writes `config/voucher-defaults.json` under the mounted data directory
  (`/data` in the container), not SQLite. On the production host this is
  `/home/cash27/.local/share/cash-27/config/voucher-defaults.json`.
  The JSON is `{"approver_name": "Name"}` and is read live, so editing the file
  also takes effect without a restart.
  New vouchers and addenda prefill that name in an editable field. Each document
  retains its selected name for reprints; changing the default affects future
  documents only. These selections are separate JSON files in
  `config/voucher-approvers/`, keyed by the existing creation submission ID.
  Full backups include both; database-only exports do not. No database schema
  changes are needed. Leave the field blank for a handwritten name. Printed
  recipient, vendor, and approver names, plus purpose/reason, use larger type.
  **Database** downloads either the SQL export, which **Restore from an export**
  accepts, or the SQLite database file itself. The file is an online-backup
  snapshot that includes committed WAL data and opens without any `-wal` file.
  **Drive backup** holds the access token for the Google Apps Script in `gas/`:
  generate one or type your own. It stays visible here, is kept as plain text in
  `config/sync/token.json`, and travels with full backups and the Drive copy, so
  a restored server keeps working with the script. Replacing or revoking it
  stops the old token at once. The token opens only `/api/sync/*`, which reads
  and never changes the ledger. When a token exists and no script run has
  finished for 36 hours, administrators see a warning on every page.
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

The Compose project contains the Flask/Gunicorn application, Caddy, a document
worker, and a local archive service. The worker and archive service share the
application image. The host does not
need a Python virtual environment. The application runs one Gunicorn worker
because the deployment is built around one SQLite database.

### Capture recognition

The application image includes CPU QReader (nano) and the approximately 7 MB
Paddle document-orientation ONNX model. Checkpoints are downloaded and verified
when building the image; uploads need no model downloads or network access.
Uploads validate and normalize the original, then atomically save an inbox
record and a durable SQLite job. The response confirms storage immediately;
QR recognition and rotation run in the separate `document-worker` container.
It processes one document at a time, at half of one CPU core by default. The
idle worker is a small Python queue loop; each model still runs in a short-lived
subprocess and releases its RAM when it exits. QReader gets up to 120 seconds
and receipt orientation 60 seconds, allowing for the CPU cap.

Capture and Review poll the inbox every two seconds while visible. A
**Document processing…** spinner changes into the final name, preview, and QR
suggestion without reloading. Unsaved review coding remains in place. Processing
documents can be viewed or deleted, but cannot be filed or used to create a
transaction until ready. Failure keeps the original available for manual review.
Jobs survive application or worker restarts; interrupted jobs resume, with a
three-attempt limit before falling back to manual review. A shared OS lock
prevents overlapping worker/model processes. Model execution holds no database
write lock. Scanner `X-Upload-ID` retries return the original acceptance response
and never enqueue a second job; current status comes from the inbox.

Both features are enabled in the container image. Set these in the application's
environment file and recreate both the app and document-worker containers to change them:

| Variable | Container default | Effect |
|---|---|---|
| `CASH_QR_ENABLED` | `1` | `0` stops queuing recognition for new uploads; accepted jobs still finish |
| `CASH_RECEIPT_ORIENTATION_ENABLED` | `1` | `0` disables only the ordinary-receipt fallback |

Local Python installs default both flags to `0`. To enable locally, install
CPU-only Torch/torchvision and `requirements-capture.txt`, cache the checkpoints
as in `Containerfile`, set the flags and model paths, and run
`python -m cash.document_worker loop` with the same `CASH_DATA_ROOT`. The container is the
tested deployment path. A valid voucher QR always takes precedence over the
receipt classifier, including pages with conflicting voucher IDs. Generic
orientation requires all four 90-degree views to predict the same correction
and each score to reach 0.90; that is an abstention heuristic, not a calibrated
accuracy guarantee. PDF rotation changes page metadata without recompressing
scan images. A rotated result is a new immutable object with a new hash; the
original remains available to backups that started before processing completed.
If no rotation is needed, no second PDF object is created. Display/download names
can change after recognition without renaming the original stored object.

Unique voucher IDs name scans such as `FY27-0002-A1.pdf`; absent or conflicting
IDs use UTC timestamp names. Matching only suggests an eligible active record.
Review still checks the document and signatures and files it explicitly;
recognition never approves transactions. Failures retain a usable inbox PDF.
Expense reports keep their own upload flow. The separate scanner
uploader works with the same acceptance response and needs no change.

See [the local benchmark report](experiments/qr/RESULTS.md) for Podman resource
measurements and accuracy limits, including the asynchronous responsiveness
check in `experiments/qr/benchmark_async.py`.

| Compose setting | Default | Meaning |
|---|---|---|
| `CASH_DOCUMENT_WORKER_CPUS` | `0.5` | Worker CPU quota in cores |
| `CASH_DOCUMENT_WORKER_MEMORY_LIMIT` | `512m` | Worker RAM cap |
| `CASH_APP_MEMORY_LIMIT` | `512m` | Web app RAM cap |

These are limits, not reserved allocations. They do not include Caddy or the
archive service. Podman defaults the combined RAM + swap limit to twice the RAM
cap, so each 512 MiB container can use up to another 512 MiB of swap. The installed
`podman-compose` 1.5 ignores `memswap_limit`; we use and verify Podman's default
instead of offering a setting that would silently do nothing.
Host swap must exist for container swap allowances to help:
`setup.sh` provisions 2 GiB idempotently (see below). On an existing installation
without swap, run the updated setup once during deployment; `upgrade.sh` does
not change host swap. Subsequent upgrades build the shared image, recreate the
worker through Compose, and verify both web and worker health. The worker needs
no network access or Podman socket. Check it with
`./deploy/compose.sh logs document-worker` or
`./deploy/compose.sh exec -T document-worker python -m cash.document_worker health`.

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
- provisions a 2 GiB `/swapfile.cash-27` with root-only permissions and an
  `/etc/fstab` entry when the host has no active or fstab-configured swap;
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

Swap provisioning preserves existing host swap. Re-running setup reuses its
own initialized swap file, enables it if necessary, and avoids duplicate fstab
entries. Creation requires at least 3 GiB free disk space, leaving 1 GiB after
allocation; an existing unrelated file at the chosen path is never overwritten.
Render-only setup does not change host swap. `upgrade.sh` does not provision
swap, and container swap allowances remain controlled separately in Compose.

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

Podman caches dependency and model installation layers across code-only upgrades.
The commit revision is deliberately applied after those layers in `Containerfile`;
putting it before the installs would invalidate them on every new commit.
`pip --no-cache-dir` keeps wheel downloads out of the image and does not disable
Podman's layer cache. Dependency/base-image changes or missing cached layers
still require installation. Moving the revision metadata to its new location
can cause a one-time dependency rebuild before subsequent upgrades reuse it.

Useful diagnostics from the root shell, if they are ever needed:

```bash
./deploy/compose.sh ps
./deploy/compose.sh logs --follow app
./deploy/compose.sh logs --follow local-backup
./deploy/compose.sh logs --follow caddy
./deploy/as-runtime-user.sh systemctl --user status cash-27-compose
```

At startup, the app logs `Ledger amount-sign errors` with every active
transaction ID that violates the cash-movement or exchange sign rules. The
check leaves accounting records unchanged and does not stop startup. Affected
history still fails replay until corrected against its supporting records;
this message is a diagnostic, not an automatic repair. Voided and superseded
rows are excluded.

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
| `CASH_USER_PASSWORD` | Shared employee password; read live from the installed env file when mounted |
| `CASH_AUTH_ENV_FILE` | Path to the existing env file for live user-password reads and Settings updates; configured automatically by Compose |
| `CASH_SECRET_KEY` | A strong, persistent Flask session-signing secret |
| `CASH_FY_START_MONTH` | Optional fiscal-year start month; defaults to `9` |
| `CASH_TIMEZONE` | Optional IANA time zone for dates the server picks without a browser, such as the Drive backup's; defaults to `America/Los_Angeles`. Stored times stay UTC |

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
contain uploaded PDFs, so it complements rather than replaces the full backup
below.

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
- the print-setting files under `config/`; and
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
upload or restore the PDFs contained in a full archive.

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

## Drive backup

A Google Apps Script in `gas/` copies Cash 27 into Google Drive every night:
the database as `.db` and `.sql`, every document, and a Sheet per fiscal year
with the bookkeeper Expense report and Cash ledger. It runs as your Google
account and only reads from Cash 27; the server holds no Google credentials.

```text
Cash 27/
  Sync log              one row per run; database snapshots kept
  Documents/            every document, all years; never delete from here
  Config/               latest print settings and the access token
  FY27/
    FY27 Ledger         Expense report and Cash ledger tabs, links to Documents/
    Database/           snapshots taken during FY27
```

Set it up by following [`gas/README.md`](gas/README.md), which starts from a
fresh Google account and covers permissions, checking on it, changing it
later, and troubleshooting.

Each night the script downloads the database, then the list of files a full
restore needs, updates the Sheets of any fiscal year whose content changed
(late entries reach past years too), copies the files Drive lacks, and tells
Cash 27 it finished. It checks every file's SHA-256 before storing it and
stores a new database pair only when the contents changed. It keeps every pair
for 30 days, then the first of each month. A document deleted from Drive is
copied again the next night; a missing database snapshot, or any other
problem, is emailed to you and recorded in the Sync log. If no run finishes
for 36 hours, Cash 27 shows administrators a warning.

The Sheets are rewritten each night and marked as generated; copy a tab to
work with it. Replacing or revoking the token in Settings stops the script
until `CASH_TOKEN` is updated.

### Restore from the Drive backup

If the server is lost, Drive holds everything needed to rebuild it.

1. In Google Drive, right-click the `Cash 27` folder and choose **Download**.
   Large folders arrive as several zip files; keep all of them.
2. Install a new server with `./setup.sh` as in
   [First production deployment](#first-production-deployment).
3. Copy the zip files to the server somewhere the runtime user can read, such
   as `/tmp` (`chmod a+r` them if needed), then run:

   ```bash
   sudo ./restore-drive.sh /tmp/drive-download-*.zip
   ```

   It uses the newest database snapshot. To choose another, add
   `--database cash-27-20260927T093000Z.db` (names are in each year's
   `Database/` folder).

The script finds every file by its SHA-256, so Drive's renaming and zip
splitting do not matter. It builds a standard archive in `backups/restore/`
from the snapshot and the documents and settings it needs,
refuses to continue if any is missing, and proves the archive restores before
handing it to `restore-local.sh`, which then replaces the live data as usual.
The Sheets are not used. The restored server has the same access token, so the
script carries on without changes.

Rehearse this now and then: download the folder, run
`python -m cash.ops drive-archive /tmp/test.tar.gz drive-download-*.zip` on any
checkout, and confirm it prints `'integrity': 'ok'`.

## Local development

For local password-settings development, set `CASH_AUTH_ENV_FILE` to an existing
private env file containing a single `CASH_USER_PASSWORD=...` line. Without that
path, development keeps its supplied user password and password saving is disabled.

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

Tests run in parallel, one worker per CPU core (`pytest.ini`). Add `-n 0` to
run them one at a time, for `pdb` or print debugging, or `-n 4` to cap the
workers on a machine short of memory. During development, run just the files
for the code you are changing, such as `pytest -q tests/test_sync.py`.

An optional Podman regression checks installation-layer reuse across two build
revision IDs, including the final image's revision environment and label:

```bash
CASH_TEST_CONTAINER_CACHE=1 .venv/bin/python -m pytest tests/test_container_build_cache.py -q
```

It uses the real Containerfile instruction order with tiny fixtures and marker
commands replacing expensive installs, requires `python:3.12-slim` already cached
locally, and runs without downloading packages or contacting production.

Optional browser tests exercise saving/printing and the review drawer at
desktop and phone widths. They use a temporary database and loopback server,
never the development or production data. Install the test-only browser tools
to include them (otherwise the browser module is skipped):

```bash
uv pip install --python .venv/bin/python playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q tests/test_browser_workflows.py
```

For ledger rules, correction semantics, report exports, security
boundaries, and acceptance criteria, read [`spec-new.md`](spec-new.md).

Scanner upload clients may send a stable UUID in `X-Upload-ID` to
`POST /api/documents/capture`, using the normal admin session and CSRF header.
A retry with the same filenames and original bytes returns the first result
without repeating recognition or creating another document. Reusing that ID
with different contents is rejected. Requests without the header retain the
normal capture behavior. Retry receipts use the existing submissions table;
no additional database migration is required.
