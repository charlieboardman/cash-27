# Future plugins

These are design notes, not implemented Cash 27 features. Production currently
uses managed DigitalOcean Droplet backups and verified local application
archives. No Google integration is required for deployment.

Cash 27 owns its SQLite ledger, immutable PDF store, retained finalized
reports, and the code that makes consistent snapshots and validates restores.
Optional plugins own external transport, credentials, scheduling, and status.
They may run as separate services or scheduled jobs in the same Podman Compose
project. They must not write to the authoritative database or document store,
or make transaction processing depend on an external service.

## Incremental backup through a Google Apps Script web app

The proposed transport uses a GAS web app running under its owner's Google
authorization. The VM sends authenticated requests to that endpoint. This
avoids managing Google OAuth refresh tokens on the VM; GAS still needs owner
authorization and remains subject to Google's permissions, policies, quotas,
and deployment restrictions. There is no rclone dependency in this proposal.

### Snapshot and upload protocol

The schedule is user configurable: hourly, nightly, or another interval. Each
run fixes one logical snapshot before uploading, so new transactions do not
cause an initial backfill to chase an ever-changing document set:

1. Ask Cash 27's snapshot code for a complete, consistent SQLite online backup
   and a manifest of every required document and retained report artifact.
   Never copy the live database without its WAL or derive the file inventory
   from a different live database revision. The database has no embedded PDFs;
   sending it in full each run is expected to be inexpensive.
2. Ask GAS, in paginated inventory requests, which required objects it has.
   Compare stable object identity, SHA-256, and size. Treat local upload history
   only as a cache; destination inventory controls what is missing.
3. Send the new database snapshot and only missing immutable files. Use small
   batches, optionally one PDF per request. Base64 is the proposed GAS request
   encoding; its roughly one-third size overhead must be accounted for.
4. GAS verifies decoded content and durably stores the object before
   acknowledging it. Each database snapshot has a unique backup ID and its own
   checksum; it does not overwrite the previous database snapshot.
5. Publish a committed manifest only when that database and every required
   object have been verified at the destination. Incomplete uploads are not
   listed as restorable backups.

No tarballs, binary database diffs, or incremental patch chains are required.
Each restore uses one complete database snapshot and the immutable objects
referenced by its manifest. Reports are included too: the current application
retains finalized CSV artifacts outside the database.

Google storage could be arranged as:

```text
Cash 27 Backups/
  snapshots/<backup-id>/cash.db
  snapshots/<backup-id>/manifest.json
  objects/<stable-object-name>
  reports/<stable-report-name>
```

Objects are shared among snapshots. The manifest records original relative
paths so restore can reconstruct the exact Cash 27 directory layout. Keep the
application revision, schema version, checksums, and sizes with each snapshot.

### Inventory, interruptions, and manual first uploads

GAS may keep an index in a private Sheet containing object identity, checksum,
size, and Drive file ID. An index entry alone does not prove a file still exists
or still has the right bytes. Reconciliation must detect deleted, trashed,
inaccessible, and changed files. The index must be rebuildable from stable
object names, metadata, and committed manifests.

Retries need explicit handling for a lost response, concurrent duplicate
requests, and a crash after Drive saves a file but before the index is updated.
Drive filenames are not unique. Use serialized index updates and a recoverable
object identity to find already-written files; never assume an index lookup
alone makes upload idempotent. A retry of the same backup/batch/object should
converge without duplicating authoritative objects or committing partial data.

If a large file store exists before the first run, the operator may upload the
ordinary PDFs through Drive's normal browser interface. GAS inventories and
checks them in resumable batches. Once verified, these objects satisfy the same
inventory checks as API uploads and need not be retransmitted. Do not make GAS
unpack a giant archive as part of manual seeding. Capture the consistent
database snapshot and manifest using the core snapshot procedure.

The VM owns pacing and retries and can pause when quotas are reached. Each GAS
request should finish well below the per-execution limit. The published daily
trigger-runtime allowance must not be assumed to apply to inbound web requests;
similarly, URL Fetch's outgoing payload limit is not a documented inbound
web-app upload limit. Measure real payload, memory, runtime, and throughput
limits before selecting batch sizes. Unsupported large files must be reported;
any chunking scheme needs a tested reassembly and restore path.

### Authentication, retention, and restore

Authenticate the endpoint with a strong shared secret (preferably HMAC over a
well-defined request body, timestamp, and request ID). Validate before decoding
large payloads or touching Drive. A public endpoint URL is not a credential.
Store credentials outside Git and outside Flask; keep the Drive files private.

Old snapshots remain selectable after ledger history is rewritten. Retention
must never delete an object required by any retained complete snapshot or
active upload. Initially, retaining all remote objects is simpler than remote
garbage collection. Preserve pending local snapshot material while its initial
upload is incomplete, or explicitly abandon it and start a new snapshot.

Restore downloads a selected committed snapshot and its referenced objects,
checks hashes and the required set, then uses Cash 27's staged migration,
integrity, document/report, and full-replay validation before activation.
Test the download path as well as upload; browser-based manual recovery may be
an escape hatch, but it needs an exact documented procedure.

Before relying on this plugin, test a full restore, a large seeded backlog,
quota pauses, lost acknowledgements, duplicate requests, interrupted index
writes, missing remote objects, and historical edits. Until then, managed
Droplet backups remain the operational recovery mechanism. GAS feasibility is
to be measured, not assumed either guaranteed or impossible.

## Human-readable Google Drive library

This is a separate, rebuildable view and is not needed to restore a backup.
It may share a Google deployment with the backup plugin, but the two jobs have
independent completion and failure states. The same PDF upload limits apply.

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

## Google Sheets ledger

Publish the current ledger using `txn_id` as its stable key and
`ledger_revision` as the source revision. An absent ID can use `appendRow()`;
an existing ID must update when exported values change. IDs alone cannot detect
historical edits, voids, superseded rows, or recalculated values after replay.
Use content hashes or compare the complete exported rows. Reconcile rows absent
from a later publication, including after a database restore, and prevent an
older retried batch from overwriting a newer revision. Batch Sheet operations
where possible.

The Sheet is a live view, not an accounting authority or finalized report.
Editing it never writes back to Cash 27. Publisher credentials, checkpoints,
and logs are independently owned and preserved across application upgrades.
