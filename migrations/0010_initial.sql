-- Fresh-install schema. Replaces the pre-reset schema history.

CREATE TABLE accounts (
    account_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN
        ('safe','signout','expense','bank','opening_equity','donation',
         'reconciliation','fx_counterparty')),
    active INTEGER NOT NULL DEFAULT 1,
    parent_voucher_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE classes (
    class_code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE vouchers (
    voucher_id TEXT PRIMARY KEY,           -- FY26-0001
    type TEXT NOT NULL CHECK (type IN ('direct_expense','signout')),
    fiscal_year INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    recipient TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    authorized_usd_cents INTEGER,
    authorized_mxn_centavos INTEGER,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','complete','void')),
    version INTEGER NOT NULL DEFAULT 1,
    void_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (fiscal_year, seq)
) STRICT;

CREATE TABLE addenda (
    addendum_id TEXT PRIMARY KEY,          -- FY26-0001-A1
    voucher_id TEXT NOT NULL REFERENCES vouchers(voucher_id),
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('positive','return')),
    movement_id TEXT,
    document_id TEXT,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','complete','void')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (voucher_id, seq)
) STRICT;

CREATE TABLE receipts (
    receipt_id TEXT PRIMARY KEY,           -- FY26-0001-R1
    voucher_id TEXT NOT NULL REFERENCES vouchers(voucher_id),
    seq INTEGER NOT NULL,
    document_id TEXT,
    vendor TEXT NOT NULL DEFAULT '',
    reviewed_date TEXT,
    status TEXT NOT NULL DEFAULT 'accepted'
        CHECK (status IN ('accepted','void')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    face_usd_cents INTEGER,
    face_mxn_centavos INTEGER,
    usd_paid_cents INTEGER NOT NULL DEFAULT 0,
    mxn_paid_centavos INTEGER NOT NULL DEFAULT 0,
    usd_change_cents INTEGER NOT NULL DEFAULT 0,
    mxn_change_centavos INTEGER NOT NULL DEFAULT 0,
    payment_mode TEXT NOT NULL
        DEFAULT 'cash' CHECK (payment_mode IN ('cash', 'receipt_currency')),
    UNIQUE (voucher_id, seq)
) STRICT;

CREATE TABLE documents (
    document_id TEXT PRIMARY KEY,
    doc_type TEXT,                         -- NULL while unassigned
    voucher_id TEXT REFERENCES vouchers(voucher_id),
    rel_path TEXT NOT NULL UNIQUE CHECK (
        substr(rel_path, 1, 10) = 'documents/'
        AND length(rel_path) > 14
        AND substr(rel_path, -4) = '.pdf'
        AND instr(substr(rel_path, 11), '/') = 0
        AND instr(rel_path, char(92)) = 0
        AND substr(rel_path, 11, 1) != '.'
    ),
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    page_count INTEGER,
    state TEXT NOT NULL DEFAULT 'inbox'
        CHECK (state IN ('inbox','signee_draft','package_submitted',
                         'filed','discarded','missing')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    display_name TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE transaction_documents (
    txn_id INTEGER NOT NULL REFERENCES transactions(txn_id),
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    role TEXT NOT NULL CHECK (role IN ('voucher','support')),
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (txn_id, document_id, role)
) STRICT;

CREATE TABLE signout_expense_claims (
    claim_id TEXT PRIMARY KEY,
    voucher_id TEXT NOT NULL REFERENCES vouchers(voucher_id),
    package_id TEXT,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    claim_date TEXT NOT NULL,
    vendor TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    usd_paid_cents INTEGER NOT NULL DEFAULT 0,
    mxn_paid_centavos INTEGER NOT NULL DEFAULT 0,
    mxn_change_centavos INTEGER NOT NULL DEFAULT 0,
    usd_change_cents INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'draft'
        CHECK (state IN ('draft','submitted','verified','flagged',
                         'accepted')),
    admin_note TEXT NOT NULL DEFAULT '',
    content_version INTEGER NOT NULL DEFAULT 1,
    verified_version INTEGER,
    final_receipt_id TEXT,
    final_movement_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    face_usd_cents INTEGER,
    face_mxn_centavos INTEGER,
    payment_mode TEXT NOT NULL
        DEFAULT 'cash' CHECK (payment_mode IN ('cash', 'receipt_currency'))
) STRICT;

CREATE TABLE signout_claim_allocations (
    claim_id TEXT NOT NULL REFERENCES signout_expense_claims(claim_id),
    line_no INTEGER NOT NULL,
    usd_cents INTEGER NOT NULL,
    mxn_centavos INTEGER NOT NULL,
    expense_account TEXT NOT NULL REFERENCES accounts(account_id),
    class_code TEXT REFERENCES classes(class_code),
    project TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    face_usd_cents INTEGER,
    face_mxn_centavos INTEGER,
    PRIMARY KEY (claim_id, line_no)
) STRICT;

CREATE TABLE signout_reconciliation_packages (
    package_id TEXT PRIMARY KEY,
    voucher_id TEXT NOT NULL REFERENCES vouchers(voucher_id),
    seq INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted','in_review','accepted',
                          'needs_changes','withdrawn')),
    version INTEGER NOT NULL DEFAULT 1,
    submitted_at TEXT,
    reviewed_at TEXT,
    admin_summary TEXT NOT NULL DEFAULT '',
    accepting_submission_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (voucher_id, seq)
) STRICT;

CREATE TABLE submissions (
    submission_id TEXT PRIMARY KEY,
    command_kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (state IN ('in_progress','committed')),
    response_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    committed_at TEXT
) STRICT;

CREATE TABLE ledger_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    ledger_revision INTEGER NOT NULL,
    dirty INTEGER NOT NULL DEFAULT 0 CHECK (dirty IN (0, 1))
) STRICT;

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

CREATE TABLE document_id_sequence (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    last_value INTEGER NOT NULL CHECK (last_value >= 0)
) STRICT;

CREATE TABLE direct_expense_review_drafts (
    voucher_id TEXT PRIMARY KEY REFERENCES vouchers(voucher_id),
    draft_json TEXT NOT NULL CHECK (json_valid(draft_json))
) STRICT;

CREATE TABLE signout_closeouts (
    closeout_id TEXT PRIMARY KEY,
    voucher_id TEXT NOT NULL REFERENCES vouchers(voucher_id),
    submission_id TEXT NOT NULL UNIQUE REFERENCES submissions(submission_id),
    settlement_txn_id INTEGER REFERENCES transactions(txn_id),
    return_addendum_id TEXT REFERENCES addenda(addendum_id),
    facts_json TEXT NOT NULL CHECK (json_valid(facts_json)),
    facts_sha256 TEXT NOT NULL,
    confirmed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE UNIQUE INDEX idx_one_unresolved_package
ON signout_reconciliation_packages(voucher_id)
WHERE status IN ('submitted','in_review','needs_changes');

CREATE INDEX idx_transaction_edits_txn
ON transaction_edits(txn_id, edit_id);

CREATE INDEX idx_txn_movement ON transactions(movement_id);

CREATE INDEX idx_txn_date ON transactions(date, same_day_seq);

CREATE INDEX idx_txn_voucher ON transactions(voucher_id);

CREATE INDEX idx_closeouts_voucher ON signout_closeouts(voucher_id, confirmed_at);

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

INSERT INTO ledger_state (singleton_id, ledger_revision, dirty) VALUES (1, 0, 0);

INSERT INTO document_id_sequence (singleton_id, last_value) VALUES (1, 0);
