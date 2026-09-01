-- cash27: foreign_keys_off
-- Add the distinct superseded transaction state promised by the specification.
-- Rebuilding is required because SQLite cannot alter a CHECK constraint.

DROP TRIGGER transactions_ledger_dirty_insert;
DROP TRIGGER transactions_ledger_dirty_update;
DROP TRIGGER transactions_ledger_dirty_delete;

ALTER TABLE transactions RENAME TO transactions_legacy;

CREATE TABLE transactions (
    txn_id INTEGER PRIMARY KEY AUTOINCREMENT,
    movement_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    date TEXT NOT NULL,
    same_day_seq INTEGER NOT NULL DEFAULT 0,
    allocation_line INTEGER NOT NULL DEFAULT 0,
    txn_type TEXT NOT NULL CHECK (txn_type IN
        ('bank_withdrawal','exchange','expense','signout','addendum',
         'opening','donation','adjustment')),
    from_account TEXT NOT NULL REFERENCES accounts(account_id),
    to_account TEXT NOT NULL REFERENCES accounts(account_id),
    usd_cents INTEGER NOT NULL,
    mxn_centavos INTEGER NOT NULL,
    voucher_id TEXT REFERENCES vouchers(voucher_id),
    receipt_id TEXT REFERENCES receipts(receipt_id),
    addendum_id TEXT REFERENCES addenda(addendum_id),
    class_code TEXT REFERENCES classes(class_code),
    receipt_face_minor INTEGER,
    receipt_face_currency TEXT CHECK (receipt_face_currency IN ('USD','MXN')),
    assigned_rate_milli INTEGER,
    posting_state TEXT NOT NULL DEFAULT 'active'
        CHECK (posting_state IN ('active','superseded','void')),
    related_txn_id INTEGER REFERENCES transactions(txn_id),
    recipient TEXT NOT NULL DEFAULT '',
    vendor TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    void_reason TEXT,
    voided_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    receipt_face_usd_cents INTEGER,
    receipt_face_mxn_centavos INTEGER
) STRICT;

INSERT INTO transactions (
    txn_id, movement_id, submission_id, date, same_day_seq, allocation_line,
    txn_type, from_account, to_account, usd_cents, mxn_centavos, voucher_id,
    receipt_id, addendum_id, class_code, receipt_face_minor,
    receipt_face_currency, assigned_rate_milli, posting_state, related_txn_id,
    recipient, vendor, description, project, notes, version, void_reason,
    voided_at, created_at, updated_at, receipt_face_usd_cents,
    receipt_face_mxn_centavos
)
SELECT
    txn_id, movement_id, submission_id, date, same_day_seq, allocation_line,
    txn_type, from_account, to_account, usd_cents, mxn_centavos, voucher_id,
    receipt_id, addendum_id, class_code, receipt_face_minor,
    receipt_face_currency, assigned_rate_milli,
    CASE WHEN posting_state = 'void'
              AND void_reason = 'superseded by final allocations'
         THEN 'superseded' ELSE posting_state END,
    related_txn_id, recipient, vendor, description, project, notes, version,
    CASE WHEN posting_state = 'void'
              AND void_reason = 'superseded by final allocations'
         THEN NULL ELSE void_reason END,
    CASE WHEN posting_state = 'void'
              AND void_reason = 'superseded by final allocations'
         THEN NULL ELSE voided_at END,
    created_at, updated_at, receipt_face_usd_cents,
    receipt_face_mxn_centavos
FROM transactions_legacy;

DROP TABLE transactions_legacy;

CREATE INDEX idx_txn_movement ON transactions(movement_id);
CREATE INDEX idx_txn_date ON transactions(date, same_day_seq);
CREATE INDEX idx_txn_voucher ON transactions(voucher_id);

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
