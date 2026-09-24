-- Working copy of each signout's expense report. The whole report is one
-- JSON document whose "format" number lets its shape change without further
-- migrations. Nothing here is financial state: approval posts rows to the
-- ledger in the same command that marks them approved.
--
-- Additive only, so the previous release keeps running against this schema.
CREATE TABLE expense_report_drafts (
    voucher_id TEXT PRIMARY KEY REFERENCES vouchers(voucher_id),
    version INTEGER NOT NULL DEFAULT 1,
    draft_json TEXT NOT NULL CHECK (json_valid(draft_json)),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
