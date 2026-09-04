-- Preserve both currencies on every receipt and on every accounting split.
-- The legacy singular columns remain readable for older exports and backups.

ALTER TABLE receipts ADD COLUMN face_usd_cents INTEGER;
ALTER TABLE receipts ADD COLUMN face_mxn_centavos INTEGER;
ALTER TABLE receipts ADD COLUMN usd_paid_cents INTEGER NOT NULL DEFAULT 0;
ALTER TABLE receipts ADD COLUMN mxn_paid_centavos INTEGER NOT NULL DEFAULT 0;
ALTER TABLE receipts ADD COLUMN usd_change_cents INTEGER NOT NULL DEFAULT 0;
ALTER TABLE receipts ADD COLUMN mxn_change_centavos INTEGER NOT NULL DEFAULT 0;

UPDATE receipts SET face_usd_cents = face_value_minor
WHERE face_currency = 'USD';
UPDATE receipts SET face_mxn_centavos = face_value_minor
WHERE face_currency = 'MXN';

ALTER TABLE signout_expense_claims ADD COLUMN face_usd_cents INTEGER;
ALTER TABLE signout_expense_claims ADD COLUMN face_mxn_centavos INTEGER;

UPDATE signout_expense_claims SET face_usd_cents = face_value_minor
WHERE face_currency = 'USD';
UPDATE signout_expense_claims SET face_mxn_centavos = face_value_minor
WHERE face_currency = 'MXN';

ALTER TABLE signout_claim_allocations ADD COLUMN face_usd_cents INTEGER;
ALTER TABLE signout_claim_allocations ADD COLUMN face_mxn_centavos INTEGER;

UPDATE signout_claim_allocations SET face_usd_cents = face_value_minor
WHERE claim_id IN (
    SELECT claim_id FROM signout_expense_claims WHERE face_currency = 'USD'
);
UPDATE signout_claim_allocations SET face_mxn_centavos = face_value_minor
WHERE claim_id IN (
    SELECT claim_id FROM signout_expense_claims WHERE face_currency = 'MXN'
);
