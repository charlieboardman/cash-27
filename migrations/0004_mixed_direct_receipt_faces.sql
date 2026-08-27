-- A direct voucher can itself be a receipt whose acknowledged value has both
-- USD and MXN components. Keep the legacy singular columns for old rows, but
-- preserve the two components independently on new vouchers and allocations.

ALTER TABLE vouchers ADD COLUMN face_usd_cents INTEGER;
ALTER TABLE vouchers ADD COLUMN face_mxn_centavos INTEGER;

UPDATE vouchers
SET face_usd_cents = face_value_minor
WHERE face_currency = 'USD';

UPDATE vouchers
SET face_mxn_centavos = face_value_minor
WHERE face_currency = 'MXN';

ALTER TABLE transactions ADD COLUMN receipt_face_usd_cents INTEGER;
ALTER TABLE transactions ADD COLUMN receipt_face_mxn_centavos INTEGER;

UPDATE transactions
SET receipt_face_usd_cents = receipt_face_minor
WHERE receipt_face_currency = 'USD';

UPDATE transactions
SET receipt_face_mxn_centavos = receipt_face_minor
WHERE receipt_face_currency = 'MXN';
