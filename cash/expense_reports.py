"""A signee's declaration of facts, separate from approved cash movements."""
from . import packages
from .commands import ValidationFailure
from .repo import ConflictError


def get(conn, voucher_id):
    row = conn.execute("SELECT * FROM signout_expense_reports WHERE voucher_id=?", (voucher_id,)).fetchone()
    return dict(row) if row else None


def assert_editable(conn, voucher_id):
    packages._voucher(conn, voucher_id)
    report = get(conn, voucher_id)
    pending = packages._unresolved_package(conn, voucher_id)
    if report and report["status"] == "submitted" and not (pending and pending["status"] == "needs_changes"):
        raise ValidationFailure("report_locked", "Reopen the report before changing it")


def save(conn, *, voucher_id, expected_version, cash_usd=None, cash_mxn=None,
         personal_usd=0, personal_mxn=0, discrepancy=""):
    assert_editable(conn, voucher_id)
    previous = get(conn, voucher_id)
    if (previous["version"] if previous else 0) != expected_version:
        raise ConflictError("The report changed; reload before editing")
    for value in (cash_usd, cash_mxn, personal_usd, personal_mxn):
        if value is not None and (not isinstance(value, int) or value < 0):
            raise ValidationFailure("bad_amount", "Cash left and personal money must be zero or positive")
    conn.execute("INSERT INTO signout_expense_reports (voucher_id, cash_usd, cash_mxn, personal_usd, personal_mxn, discrepancy)"
                 " VALUES (?,?,?,?,?,?) ON CONFLICT(voucher_id) DO UPDATE SET cash_usd=excluded.cash_usd, cash_mxn=excluded.cash_mxn,"
                 " personal_usd=excluded.personal_usd, personal_mxn=excluded.personal_mxn, discrepancy=excluded.discrepancy,"
                 " status='draft', version=version+1, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                 (voucher_id, cash_usd, cash_mxn, personal_usd, personal_mxn, str(discrepancy).strip()))
    return get(conn, voucher_id)


def submit(conn, *, voucher_id, expected_version, claim_versions):
    assert_editable(conn, voucher_id)
    report = get(conn, voucher_id)
    if not report or report["version"] != expected_version:
        raise ConflictError("The report changed; reload before submitting")
    if report["cash_usd"] is None or report["cash_mxn"] is None:
        raise ValidationFailure("cash_required", "Count your cash left and enter both currencies, including zero")
    claims = conn.execute("SELECT * FROM signout_expense_claims WHERE voucher_id=?", (voucher_id,)).fetchall()
    if {c["claim_id"]: c["content_version"] for c in claims} != claim_versions:
        raise ConflictError("Receipts changed; reload before submitting")
    for claim in claims:
        if claim["state"] != "accepted":
            packages.validate_report_claim(claim)
    pending = packages._unresolved_package(conn, voucher_id)
    drafts = [c["claim_id"] for c in claims if c["state"] == "draft" and not c["package_id"]]
    if pending:
        if pending["status"] != "needs_changes":
            raise ValidationFailure("package_open", "The report is already being reviewed")
        # Include new evidence with the returned report, keeping one review task.
        for cid in drafts:
            conn.execute("UPDATE signout_expense_claims SET package_id=?, state='submitted' WHERE claim_id=?", (pending["package_id"], cid))
        packages.resubmit_package(conn, voucher_id=voucher_id, package_id=pending["package_id"])
    elif drafts:
        packages.submit_package(conn, voucher_id=voucher_id, claim_ids=drafts)
    conn.execute("UPDATE signout_expense_reports SET status='submitted', version=version+1, submitted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE voucher_id=?", (voucher_id,))
    return get(conn, voucher_id)


def reopen(conn, *, voucher_id, expected_version):
    packages._voucher(conn, voucher_id)
    report = get(conn, voucher_id)
    if not report or report["version"] != expected_version:
        raise ConflictError("The report changed; reload before reopening")
    pending = packages._unresolved_package(conn, voucher_id)
    if pending:
        packages.withdraw_package(conn, voucher_id=voucher_id, package_id=pending["package_id"])
    conn.execute("UPDATE signout_expense_reports SET status='draft', version=version+1 WHERE voucher_id=?", (voucher_id,))
    return get(conn, voucher_id)
