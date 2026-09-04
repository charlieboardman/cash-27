# Future publishers

Cash 27's authoritative state is its SQLite ledger and immutable PDF object
store. Human-readable folders and spreadsheets should be optional, rebuildable
projections rather than storage invariants in the accounting application.

These ideas are deliberately not current Cash 27 features.

## Publication boundary

Prefer a consistent, versioned publication snapshot over coupling a publisher
to the live WAL-mode SQLite files. A snapshot could contain:

```text
publication/
  ledger.csv
  documents.json
  revision.json
```

`documents.json` would map document IDs and hashes to their voucher, receipt,
addendum, transaction, and fiscal-year relationships. A document publisher may
also receive read-only access to the immutable document-object directory. Each
publisher owns its external credentials, logs, cursor, and health state; it can
fail without affecting transaction processing or disaster-recovery backups.

Publishers can run as short-lived Podman containers invoked by host timers.
They do not need to be part of the Flask process or hold write access to Cash
27's database or document store.

## Human-readable Google Drive library

Build a disposable tree such as:

```text
FY27/
  FY27-0001/
    FY27-0001.pdf
    FY27-0001-A1.pdf
    FY27-0001-R1.pdf
  Standalone/
    TXN-000123-exchange.pdf
```

The builder derives paths from all relevant database relationships, verifies
each source object's SHA-256, and emits a manifest from readable path to
document ID and hash. Repeated runs must converge on the same result. A local
temporary tree can use hard links to avoid duplicating bytes before rclone
synchronizes it to a dedicated Google Drive folder.

This library is a convenience filing cabinet, not a backup. Keep it separate
from `Cash 27 Backups`. If a corrected relationship removes or replaces a
projected path, preserve the prior Drive object in dated history rather than
destroying it.

## Google Sheets ledger

Publish the current authoritative ledger into a Google Sheet with `txn_id` as
the stable key and `ledger_revision` as the source revision. A Google Apps
Script implementation can remain idempotent:

```text
transaction ID absent  -> appendRow()
transaction ID present -> update the row when its exported values changed
```

The update case matters because voiding or superseding changes an existing
transaction's status. At Cash 27's expected size, scanning the sheet into a
transaction-ID map on each run is acceptable. An alternative publisher
container could use the Google Sheets API and batch updates.

The Sheet is a familiar live view, not an accounting authority or a finalized
report. Editing it must never write back to Cash 27.

## Operations

Publisher status may eventually appear in Settings, but a publisher failure is
less severe than a failed disaster-recovery backup. Production upgrades should
tag publisher images by Git commit and preserve each publisher's independent
credentials and state.
