-- Review progress is separate from posted allocations and document filing.
CREATE TABLE direct_expense_review_drafts (
    voucher_id TEXT PRIMARY KEY REFERENCES vouchers(voucher_id),
    draft_json TEXT NOT NULL
);
