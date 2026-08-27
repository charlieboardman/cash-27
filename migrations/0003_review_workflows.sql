-- Prepared direct-expense facts are known before the paper is printed. Keep
-- them on the voucher so review can compare the signed paper to the original
-- authorization without reconstructing facts from allocation rows.

ALTER TABLE vouchers ADD COLUMN face_value_minor INTEGER;
ALTER TABLE vouchers ADD COLUMN face_currency TEXT
    CHECK (face_currency IN ('USD', 'MXN'));
ALTER TABLE vouchers ADD COLUMN usd_paid_cents INTEGER;
ALTER TABLE vouchers ADD COLUMN mxn_paid_centavos INTEGER;
ALTER TABLE vouchers ADD COLUMN mxn_change_centavos INTEGER;
ALTER TABLE vouchers ADD COLUMN usd_change_cents INTEGER;

-- Existing vouchers predate the richer preparation form. Their authorized
-- net movement is still authoritative; treat it as paid with no change while
-- leaving the receipt face unknown for review.
UPDATE vouchers
SET usd_paid_cents = COALESCE(authorized_usd_cents, 0),
    mxn_paid_centavos = COALESCE(authorized_mxn_centavos, 0),
    mxn_change_centavos = 0,
    usd_change_cents = 0
WHERE type = 'direct_expense';

-- Material in-place history edits keep an independently evidenced reason and
-- before/after snapshots without turning the ledger into append-only storage.
CREATE TABLE transaction_edits (
    edit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id INTEGER NOT NULL REFERENCES transactions(txn_id),
    submission_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT
        (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE INDEX idx_transaction_edits_txn
ON transaction_edits(txn_id, edit_id);
