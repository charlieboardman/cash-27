-- Existing snapshots are expense reports and retain their original bytes/headers.
ALTER TABLE report_runs ADD COLUMN export_type TEXT NOT NULL DEFAULT 'expenses'
    CHECK (export_type IN ('expenses', 'cash_ledger'));
