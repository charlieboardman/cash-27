# Future plugins

These are design notes, not implemented Cash 27 features, except where marked.
Production uses managed DigitalOcean Droplet backups, verified local
application archives, and optionally the nightly Drive backup. No Google
integration is required for deployment.

Cash 27 owns its SQLite ledger, immutable PDF store, retained finalized
reports, and the code that makes consistent snapshots and validates restores.
Optional plugins own external transport, credentials, scheduling, and status.
They may run as separate services or scheduled jobs in the same Podman Compose
project. They must not write to the authoritative database or document store,
or make transaction processing depend on an external service.

## Drive backup (implemented)

The nightly Google Apps Script copy is no longer a proposal. It pulls from
Cash 27 rather than receiving pushes, so the VM holds no Google credentials:
see "Drive backup" in the README, `gas/Code.gs`, and "Offsite Drive copy" in
spec section 17. It keeps every document in one flat `Documents/` folder and a
rewritten Sheet per fiscal year. The ideas below remain possible additions.

## Human-readable Google Drive library

A per-voucher folder view beside the Drive backup's flat `Documents/` folder.
It is a separate, rebuildable view and is not needed to restore a backup. The
Sheets the backup writes already link each row to its documents.

Cash 27 supplies a consistent, versioned publication snapshot:

```text
publication/
  ledger.csv
  documents.json
  revision.json
```

The JSON maps document IDs and hashes to voucher, receipt, addendum,
transaction, and fiscal-year relationships. Prepare this on the Cash 27 side;
do not assume GAS can interpret a raw SQLite file or run the replay engine.

The publisher builds a friendly tree such as:

```text
FY27/
  FY27-0001/
    FY27-0001.pdf
    FY27-0001-A1.pdf
    FY27-0001-R1.pdf
  Standalone/
    TXN-000123-exchange.pdf
```

Derive paths from database relationships, verify source hashes, and preserve
the mapping from readable path to document ID. Repeated runs should converge.
Use readable copies or Drive shortcuts to uploaded objects where appropriate.
Corrected assignments must update the view without deleting backup objects.
Google-side edits must never change Cash 27's source records.
