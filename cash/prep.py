"""Preparation workflows (spec 7-8): direct expense, signout, positive
addendum, return addendum. Each allocates IDs transactionally, commits
the sparse active movement, and returns the print context.

Also: opening, donation, correction (spec 7) and exchange/withdrawal
creation (spec 12). Every function runs inside run_command.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from .commands import ValidationFailure
from .model import (BANK, DONATION, FX_COUNTERPARTY, OPENING_EQUITY,
                    RECONCILIATION, SAFE, UNALLOCATED_EXPENSE, Row)
from . import repo


def fiscal_year_for(date: str, fy_start_month: int) -> int:
    """Fiscal year of an ISO date (spec 12): FY starts fy_start_month."""
    y, m = int(date[:4]), int(date[5:7])
    return y if m >= fy_start_month else y - 1


def _next_voucher_id(conn, date: str, fy_start_month: int) -> str:
    fy = fiscal_year_for(date, fy_start_month)
    cur = conn.execute(
        "SELECT MAX(seq) FROM vouchers WHERE fiscal_year = ?",
        (fy,)).fetchone()
    seq = (cur[0] or 0) + 1
    return f"FY{fy % 100:02d}-{seq:04d}", fy, seq


def _signout_account_id(voucher_id: str) -> str:
    return voucher_id  # signout accounts are named FY##-NNNN (spec 2)


def _row(movement_id, date, seq, line, txn_type, f, t, usd, mxn,
         **kw) -> Row:
    return Row(row_id="pending", movement_id=movement_id, date=date,
               same_day_seq=seq, allocation_line=line, txn_type=txn_type,
               from_account=f, to_account=t, usd=usd, mxn=mxn, **kw)


def complete_direct_expense(conn, *, submission_id, voucher_id,
                            expected_version, allocations,
                            actual_usd_cents=None,
                            actual_mxn_centavos=None,
                            receipt_face_mxn=None,
                            document_id=None,
                            class_default=None):
    """Replace the sparse UNALLOCATED_EXPENSE allocation with final rows
    under the same movement_id (spec 7). The provisional row is voided
    and superseded; the cash movement does not change.

    allocations: list of dicts {expense_account, class_code, usd_cents,
    mxn_centavos, project, description}. Their signed totals must equal
    the provisional movement's amounts."""
    v = conn.execute(
        "SELECT status, version FROM vouchers WHERE voucher_id = ?",
        (voucher_id,)).fetchone()
    if not v:
        raise ValidationFailure("not_found", "Voucher not found")
    if v["status"] != "pending":
        raise ValidationFailure("not_pending", "Voucher not pending")
    repo.assert_versions(conn, "vouchers", "voucher_id",
                         [(voucher_id, expected_version)])

    sparse = conn.execute(
        "SELECT txn_id, movement_id, date, same_day_seq, usd_cents,"
        " mxn_centavos FROM transactions WHERE voucher_id = ? AND"
        " to_account = 'UNALLOCATED_EXPENSE' AND posting_state='active'",
        (voucher_id,)).fetchone()
    if not sparse:
        raise ValidationFailure("no_sparse", "No sparse row to replace")

    usd = (actual_usd_cents if actual_usd_cents is not None
           else sparse["usd_cents"])
    mxn = (actual_mxn_centavos if actual_mxn_centavos is not None
           else sparse["mxn_centavos"])
    alloc_usd = sum(a["usd_cents"] for a in allocations)
    alloc_mxn = sum(a["mxn_centavos"] for a in allocations)
    if alloc_usd != usd or alloc_mxn != mxn:
        raise ValidationFailure(
            "allocation_mismatch",
            f"Allocations ({alloc_usd},{alloc_mxn}) must equal actual "
            f"({usd},{mxn})")

    conn.execute(
        "UPDATE transactions SET posting_state='void',"
        " void_reason='superseded by final allocations',"
        " voided_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
        " version=version+1 WHERE txn_id = ?", (sparse["txn_id"],))

    new_rows = []
    for line, a in enumerate(allocations):
        new_rows.append(_row(
            sparse["movement_id"], sparse["date"], sparse["same_day_seq"],
            line + 1, "expense", SAFE, a["expense_account"],
            a["usd_cents"], a["mxn_centavos"], voucher_id=voucher_id,
            receipt_value_mxn=receipt_face_mxn,
            related_row_id=str(sparse["txn_id"])))
    ids = repo.insert_rows(conn, submission_id, new_rows)

    if document_id:
        # vendor-signed voucher serves both roles (spec 7); the
        # document attaches to every final row as voucher+support
        for txn_id in ids:
            for role in ("voucher", "support"):
                conn.execute(
                    "INSERT INTO transaction_documents (txn_id,"
                    " document_id, role) VALUES (?,?,?)",
                    (txn_id, document_id, role))
        conn.execute(
            "UPDATE documents SET state='filed', voucher_id=?,"
            " doc_type='voucher' WHERE document_id=?",
            (voucher_id, document_id))

    conn.execute(
        "UPDATE vouchers SET status='complete', version=version+1,"
        " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        " WHERE voucher_id=?", (voucher_id,))
    return {"voucher_id": voucher_id, "txn_ids": ids,
            "superseded_txn_id": sparse["txn_id"]}


def _check_cash_available(conn, date, seq):
    pass


def prepare_direct_expense(conn, *, submission_id, date, seq_no, recipient, purpose,
                           notes, usd_cents=0, mxn_centavos=0,
                           fy_start_month=8) -> dict:
    """SAFE -> UNALLOCATED_EXPENSE sparse row, active immediately."""
    if usd_cents <= 0 and mxn_centavos <= 0:
        raise ValidationFailure("no_amount", "Amount required",
                                {"usd_cents": "positive",
                                 "mxn_centavos": "positive"})
    vid, fy, seq = _next_voucher_id(conn, date, fy_start_month)
    mv = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO vouchers (voucher_id, type, fiscal_year, seq,"
        " recipient, purpose, notes, authorized_usd_cents,"
        " authorized_mxn_centavos) VALUES (?,?,?,?,?,?,?,?,?)",
        (vid, "direct_expense", fy, seq, recipient, purpose, notes,
         usd_cents or None, mxn_centavos or None))
    row = _row(mv, date, seq_no, 0, "expense", SAFE, UNALLOCATED_EXPENSE,
               usd_cents, mxn_centavos, voucher_id=vid)
    ids = repo.insert_rows(conn, submission_id, [row])
    conn.execute(
        "UPDATE transactions SET recipient=?, description=?, notes=?"
        " WHERE txn_id=?", (recipient, purpose, notes, ids[0]))
    return {"voucher_id": vid, "type": "direct_expense",
            "movement_id": mv, "txn_ids": ids,
            "print_url": f"/voucher/{vid}/print"}


def prepare_signout(conn, *, submission_id, date, seq_no, recipient, purpose, notes,
                    usd_cents=0, mxn_centavos=0, fy_start_month=8) -> dict:
    """Create voucher + signout account + SAFE -> FY##-NNNN movement."""
    if usd_cents <= 0 and mxn_centavos <= 0:
        raise ValidationFailure("no_amount", "Amount required")
    vid, fy, seq = _next_voucher_id(conn, date, fy_start_month)
    acct = _signout_account_id(voucher_id=vid)
    conn.execute(
        "INSERT INTO vouchers (voucher_id, type, fiscal_year, seq,"
        " recipient, purpose, notes, authorized_usd_cents,"
        " authorized_mxn_centavos) VALUES (?,?,?,?,?,?,?,?,?)",
        (vid, "signout", fy, seq, recipient, purpose, notes,
         usd_cents or None, mxn_centavos or None))
    conn.execute(
        "INSERT INTO accounts (account_id, name, kind,"
        " parent_voucher_id) VALUES (?,?, 'signout', ?)",
        (acct, f"Signout {vid} ({recipient})", vid))
    mv = str(uuid.uuid4())
    row = _row(mv, date, seq_no, 0, "signout", SAFE, acct,
               usd_cents, mxn_centavos, voucher_id=vid)
    ids = repo.insert_rows(conn, submission_id, [row])
    return {"voucher_id": vid, "type": "signout", "account_id": acct,
            "movement_id": mv, "txn_ids": ids,
            "print_url": f"/voucher/{vid}/print"}


def _next_addendum(conn, voucher_id: str, kind: str) -> tuple[str, int]:
    cur = conn.execute(
        "SELECT MAX(seq) FROM addenda WHERE voucher_id = ?",
        (voucher_id,)).fetchone()
    seq = (cur[0] or 0) + 1
    return f"{voucher_id}-A{seq}", seq


def prepare_addendum(conn, *, submission_id, voucher_id, date, seq_no, kind, reason,
                     usd_cents=0, mxn_centavos=0) -> dict:
    """Positive: SAFE -> signout (more cash out). Return: signout ->
    SAFE (cash physically returned). Active immediately (spec 7)."""
    if kind not in ("positive", "return"):
        raise ValidationFailure("bad_kind", "positive or return")
    v = conn.execute(
        "SELECT type, status FROM vouchers WHERE voucher_id = ?",
        (voucher_id,)).fetchone()
    if not v or v["type"] != "signout":
        raise ValidationFailure("not_signout",
                                "Addenda attach to signout vouchers")
    if v["status"] == "void":
        raise ValidationFailure("voucher_void", "Voucher is void")
    aid, seq = _next_addendum(conn, voucher_id, kind)
    acct = _signout_account_id(voucher_id)
    mv = str(uuid.uuid4())
    if kind == "positive":
        f, t = SAFE, acct
    else:
        f, t = acct, SAFE
        _check_return_limit(conn, voucher_id, usd_cents, mxn_centavos)
    row = _row(mv, date, seq_no, 0, "addendum", f, t, usd_cents,
               mxn_centavos, voucher_id=voucher_id, addendum_id=aid)
    conn.execute(
        "INSERT INTO addenda (addendum_id, voucher_id, seq, kind,"
        " movement_id, reason) VALUES (?,?,?,?,?,?)",
        (aid, voucher_id, seq, kind, mv, reason))
    ids = repo.insert_rows(conn, submission_id, [row])
    return {"addendum_id": aid, "movement_id": mv, "txn_ids": ids,
            "print_url": f"/voucher/{voucher_id}/addendum/{aid}/print"}


def _check_return_limit(conn, voucher_id, usd_cents, mxn_centavos):
    """Return addendum cannot exceed the signout's positive physical
    balance (spec 7)."""
    accounts = repo.load_accounts(conn)
    rows = repo.load_rows(conn)
    from .replay import replay
    res = replay(rows, accounts)
    st = res.states[voucher_id]
    if usd_cents > st.usd or mxn_centavos > st.mxn:
        raise ValidationFailure(
            "return_exceeds_balance",
            f"Return exceeds signout balance "
            f"({st.usd} cents USD, {st.mxn} centavos MXN held)")


def create_exchange(conn, *, submission_id, date, seq_no, source_account,
                    usd_cents, mxn_centavos, document_id=None,
                    notes="") -> dict:
    """Complete active exchange from an inbox receipt (spec 7). All
    outgoing USD becomes incoming MXN basis (calc_usd = 0)."""
    if usd_cents <= 0 or mxn_centavos >= 0:
        raise ValidationFailure(
            "bad_exchange",
            "Exchange needs positive USD out and negative MXN (returned)")
    mv = str(uuid.uuid4())
    row = _row(mv, date, seq_no, 0, "exchange", source_account,
               FX_COUNTERPARTY, usd_cents, mxn_centavos)
    ids = repo.insert_rows(conn, submission_id, [row])
    return {"movement_id": mv, "txn_ids": ids}


def create_withdrawal(conn, *, submission_id, date, seq_no, usd_cents,
                      document_id=None, notes="") -> dict:
    """Complete active BANK -> SAFE withdrawal from inbox evidence."""
    if usd_cents <= 0:
        raise ValidationFailure("bad_withdrawal", "Positive USD required")
    mv = str(uuid.uuid4())
    row = _row(mv, date, seq_no, 0, "bank_withdrawal", BANK, SAFE,
               usd_cents, 0)
    ids = repo.insert_rows(conn, submission_id, [row])
    return {"movement_id": mv, "txn_ids": ids}


def create_opening(conn, *, submission_id, date, usd_cents=0, mxn_centavos=0,
                   assigned_rate_milli=None, notes="") -> dict:
    """Genuine initial balance (spec 3: administrator supplies the
    historical MXN-per-USD rate for MXN)."""
    if mxn_centavos and not assigned_rate_milli:
        raise ValidationFailure(
            "rate_required", "Opening MXN needs the historical rate")
    mv = str(uuid.uuid4())
    row = _row(mv, date, 0, 0, "opening", OPENING_EQUITY, SAFE,
               usd_cents, mxn_centavos,
               assigned_rate_milli=assigned_rate_milli)
    ids = repo.insert_rows(conn, submission_id, [row])
    return {"movement_id": mv, "txn_ids": ids}


def create_donation(conn, *, submission_id, date, usd_cents=0, mxn_centavos=0,
                    assigned_rate_milli=None, target=SAFE, notes="") -> dict:
    """Donated cash. Into a positive pool: pool proportion (no rate);
    into an empty pool: assigned rate required (spec 3)."""
    mv = str(uuid.uuid4())
    row = _row(mv, date, 0, 0, "donation", DONATION, target,
               usd_cents, mxn_centavos,
               assigned_rate_milli=assigned_rate_milli)
    ids = repo.insert_rows(conn, submission_id, [row])
    return {"movement_id": mv, "txn_ids": ids}


def create_correction(conn, *, submission_id, date, seq_no, direction, usd_cents=0,
                      mxn_centavos=0, assigned_rate_milli=None,
                      related_txn_id=None, reason="", notes="") -> dict:
    """Adjustment against RECONCILIATION. direction='out' means tracked
    cash was short (cash -> RECONCILIATION); 'in' means over."""
    if direction == "out":
        f, t = SAFE, RECONCILIATION
        usd, mxn = usd_cents, mxn_centavos
    elif direction == "in":
        f, t = RECONCILIATION, SAFE
        usd, mxn = usd_cents, mxn_centavos
        if mxn and not assigned_rate_milli:
            raise ValidationFailure(
                "rate_required", "Positive cash correction needs an "
                "approved rate (spec 3)")
    else:
        raise ValidationFailure("bad_direction", "'in' or 'out'")
    mv = str(uuid.uuid4())
    row = _row(mv, date, seq_no, 0, "adjustment", f, t, usd, mxn,
               assigned_rate_milli=assigned_rate_milli,
               related_row_id=(str(related_txn_id)
                               if related_txn_id else None))
    ids = repo.insert_rows(conn, submission_id, [row])
    return {"movement_id": mv, "txn_ids": ids}
