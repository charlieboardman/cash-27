ALTER TABLE signout_expense_claims ADD COLUMN simple_entry INTEGER NOT NULL DEFAULT 0 CHECK (simple_entry IN (0,1));
CREATE TABLE signout_expense_reports (
    voucher_id TEXT PRIMARY KEY REFERENCES vouchers(voucher_id),
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted')),
    cash_usd INTEGER CHECK (cash_usd >= 0),
    cash_mxn INTEGER CHECK (cash_mxn >= 0),
    personal_usd INTEGER NOT NULL DEFAULT 0 CHECK (personal_usd >= 0),
    personal_mxn INTEGER NOT NULL DEFAULT 0 CHECK (personal_mxn >= 0),
    discrepancy TEXT NOT NULL DEFAULT '',
    submitted_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
ALTER TABLE signout_closeouts ADD COLUMN reimbursement_addendum_id TEXT REFERENCES addenda(addendum_id);
ALTER TABLE signout_closeouts ADD COLUMN resolution_note TEXT NOT NULL DEFAULT '';
