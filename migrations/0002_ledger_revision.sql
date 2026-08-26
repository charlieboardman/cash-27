-- Ledger-wide revision used to identify the live state that produced a report.
-- Transaction triggers only mark the ledger dirty. run_command increments the
-- revision once per successful command, even when the command changes several
-- allocation rows.

CREATE TABLE ledger_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    ledger_revision INTEGER NOT NULL,
    dirty INTEGER NOT NULL DEFAULT 0 CHECK (dirty IN (0, 1))
) STRICT;

INSERT INTO ledger_state (singleton_id, ledger_revision, dirty)
VALUES (1, (SELECT COALESCE(MAX(txn_id), 0) FROM transactions), 0);

CREATE TRIGGER transactions_ledger_dirty_insert
AFTER INSERT ON transactions
BEGIN
    UPDATE ledger_state SET dirty = 1 WHERE singleton_id = 1;
END;

CREATE TRIGGER transactions_ledger_dirty_update
AFTER UPDATE ON transactions
BEGIN
    UPDATE ledger_state SET dirty = 1 WHERE singleton_id = 1;
END;

CREATE TRIGGER transactions_ledger_dirty_delete
AFTER DELETE ON transactions
BEGIN
    UPDATE ledger_state SET dirty = 1 WHERE singleton_id = 1;
END;

-- report_runs now contains finalized artifacts only. Legacy previews were
-- disposable working output and intentionally do not migrate.
ALTER TABLE report_runs RENAME TO report_runs_legacy;

CREATE TABLE report_runs (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,
    revision INTEGER NOT NULL,
    ledger_revision INTEGER NOT NULL,
    generated_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (period, revision)
) STRICT;

INSERT INTO report_runs (
    report_id, period, revision, ledger_revision, generated_path,
    sha256, notes, created_at
)
SELECT report_id, period, revision, 0, generated_path,
       sha256, notes, created_at
FROM report_runs_legacy
WHERE status = 'final';

DROP TABLE report_runs_legacy;
