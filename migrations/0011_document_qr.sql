-- QR association does not satisfy signed-paper or accounting requirements.
CREATE TABLE document_qr (
    document_id TEXT PRIMARY KEY REFERENCES documents(document_id),
    status TEXT NOT NULL CHECK (status IN (
        'matched','no_code','ambiguous','unknown','already_filed',
        'duplicate','inactive','failed','busy','timeout')),
    detected_id TEXT,
    decoded_ids TEXT NOT NULL,
    voucher_id TEXT REFERENCES vouchers(voucher_id),
    addendum_id TEXT REFERENCES addenda(addendum_id),
    review_kind TEXT
) STRICT;
CREATE INDEX document_qr_voucher ON document_qr(voucher_id);
CREATE INDEX document_qr_detected ON document_qr(detected_id);
