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
                    RECONCILIATION, SAFE, UNALLOCATED_EXPENSE, Row,
                    allocate_minor_proportionally)
from . import repo


def fiscal_year_for(date: str, fy_start_month: int = 9) -> int:
    """Ending-year fiscal label for an ISO date.

    With the configured September start, 2026-09-01 through 2027-08-31
    is FY27.
    """
    y, m = int(date[:4]), int(date[5:7])
    return y + 1 if m >= fy_start_month else y


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


def next_same_day_seq(conn, date, *cash_accounts):
    """Return the next replay position touching these accounts that day."""
    accounts = tuple(dict.fromkeys(a for a in cash_accounts if a))
    if not accounts:
        row = conn.execute(
            "SELECT COALESCE(MAX(same_day_seq), -1) + 1 FROM transactions"
            " WHERE date=?", (date,)).fetchone()
        return row[0]
    placeholders = ",".join("?" for _ in accounts)
    row = conn.execute(
        f"SELECT COALESCE(MAX(same_day_seq), -1) + 1 FROM transactions"
        f" WHERE date=? AND"
        f" (from_account IN ({placeholders}) OR"
        f" to_account IN ({placeholders}))",
        (date, *accounts, *accounts)).fetchone()
    return row[0]


def _same_day_seq(conn, date, requested, *cash_accounts):
    return (next_same_day_seq(conn, date, *cash_accounts)
            if requested is None else requested)


def _require_inbox_document(conn, document_id):
    row = conn.execute(
        "SELECT state FROM documents WHERE document_id=?",
        (document_id,)).fetchone()
    if not row or row["state"] != "inbox":
        raise ValidationFailure(
            "document_unavailable",
            "The selected document is no longer available in the inbox",
            {"document_id": "not available"})


def attach_signout_voucher(conn, *, voucher_id, expected_version,
                           document_id) -> dict:
    """File the signed custody voucher without closing the signout.

    A signout becomes complete only after its overall reconciliation; attaching
    its custody paper resolves the review blocker but does not change cash or
    prematurely close the voucher.
    """
    voucher = conn.execute(
        "SELECT type, status FROM vouchers WHERE voucher_id=?",
        (voucher_id,)).fetchone()
    if not voucher or voucher["type"] != "signout":
        raise ValidationFailure("not_signout", "Signout voucher not found")
    if voucher["status"] == "void":
        raise ValidationFailure("voucher_void", "Voucher is void")
    repo.assert_versions(conn, "vouchers", "voucher_id",
                         [(voucher_id, expected_version)])
    _require_inbox_document(conn, document_id)
    txns = conn.execute(
        "SELECT txn_id FROM transactions WHERE voucher_id=? AND"
        " txn_type='signout' AND posting_state='active' ORDER BY txn_id",
        (voucher_id,)).fetchall()
    if not txns:
        raise ValidationFailure("missing_movement",
                                "Signout movement not found")
    for txn in txns:
        conn.execute(
            "INSERT INTO transaction_documents (txn_id, document_id, role)"
            " VALUES (?, ?, 'voucher')",
            (txn["txn_id"], document_id))
    conn.execute(
        "UPDATE documents SET state='filed', voucher_id=?,"
        " doc_type='voucher' WHERE document_id=?",
        (voucher_id, document_id))
    conn.execute(
        "UPDATE vouchers SET version=version+1, updated_at="
        " strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE voucher_id=?",
        (voucher_id,))
    return {"voucher_id": voucher_id, "document_id": document_id,
            "paperwork": "complete"}


def complete_addendum_document(conn, *, addendum_id, expected_version,
                               document_id) -> dict:
    """Attach the signed paper to exactly one prepared addendum."""
    addendum = conn.execute(
        "SELECT voucher_id, status FROM addenda WHERE addendum_id=?",
        (addendum_id,)).fetchone()
    if not addendum:
        raise ValidationFailure("not_found", "Addendum not found")
    if addendum["status"] != "pending":
        raise ValidationFailure("not_pending", "Addendum is not pending")
    repo.assert_versions(conn, "addenda", "addendum_id",
                         [(addendum_id, expected_version)])
    _require_inbox_document(conn, document_id)
    txns = conn.execute(
        "SELECT txn_id FROM transactions WHERE addendum_id=? AND"
        " posting_state='active' ORDER BY txn_id",
        (addendum_id,)).fetchall()
    if not txns:
        raise ValidationFailure("missing_movement",
                                "Addendum movement not found")
    for txn in txns:
        conn.execute(
            "INSERT INTO transaction_documents (txn_id, document_id, role)"
            " VALUES (?, ?, 'voucher')",
            (txn["txn_id"], document_id))
    conn.execute(
        "UPDATE documents SET state='filed', voucher_id=?,"
        " doc_type='addendum' WHERE document_id=?",
        (addendum["voucher_id"], document_id))
    conn.execute(
        "UPDATE addenda SET document_id=?, status='complete',"
        " version=version+1, updated_at="
        " strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE addendum_id=?",
        (document_id, addendum_id))
    return {"addendum_id": addendum_id, "document_id": document_id,
            "status": "complete"}


def complete_direct_expense(conn, *, submission_id, voucher_id,
                            expected_version, allocations,
                            actual_usd_cents=None,
                            actual_mxn_centavos=None,
                            receipt_face_mxn=None, document_id=None,
                            voucher_document_id=None,
                            support_document_ids=None,
                            class_default=None):
    """Replace the sparse UNALLOCATED_EXPENSE allocation with final rows
    under the same movement_id (spec 7). The provisional row is voided
    and superseded; the cash movement does not change.

    allocations: list of dicts {expense_account, class_code, usd_cents,
    mxn_centavos, project, description}. Their signed totals must equal
    the provisional movement's amounts."""
    v = conn.execute(
        "SELECT status, version, face_value_minor, face_currency,"
        " face_usd_cents, face_mxn_centavos FROM"
        " vouchers WHERE voucher_id = ?",
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
    if not allocations:
        raise ValidationFailure("no_allocations",
                                "At least one allocation is required")
    allocations = [dict(allocation) for allocation in allocations]

    expected_usd = (v["face_usd_cents"] if v["face_usd_cents"] is not None
                    else (v["face_value_minor"] if
                          v["face_currency"] == "USD" else None))
    expected_mxn = (v["face_mxn_centavos"] if
                    v["face_mxn_centavos"] is not None else
                    (v["face_value_minor"] if
                     v["face_currency"] == "MXN" else None))
    if expected_usd is None and expected_mxn is None and receipt_face_mxn is not None:
        expected_mxn = receipt_face_mxn
    mixed_face = expected_usd is not None and expected_mxn is not None
    if expected_usd is not None or expected_mxn is not None:
        if mixed_face:
            for allocation in allocations:
                if allocation.get("face_usd_cents") is None or \
                        allocation.get("face_mxn_centavos") is None:
                    raise ValidationFailure(
                        "face_allocation_required",
                        "Every allocation needs USD and MXN receipt shares")
            if sum(int(a["face_usd_cents"]) for a in allocations) != expected_usd or \
                    sum(int(a["face_mxn_centavos"]) for a in allocations) != expected_mxn:
                raise ValidationFailure(
                    "face_allocation_mismatch",
                    "Receipt shares must equal both acknowledged amounts")
        else:
            expected_face = expected_usd if expected_usd is not None else expected_mxn
            expected_currency = "USD" if expected_usd is not None else "MXN"
            shares = [a.get("face_value_minor") if a.get("face_value_minor") is not None
                      else (a.get("face_usd_cents") if expected_currency == "USD"
                            else a.get("face_mxn_centavos")) for a in allocations]
            if any(s is None or int(s) <= 0 for s in shares):
                raise ValidationFailure(
                    "face_allocation_required",
                    "Every allocation needs a positive receipt share")
            if sum(int(s) for s in shares) != expected_face:
                raise ValidationFailure(
                    "face_allocation_mismatch",
                    f"Receipt shares must sum to {expected_face}")
            face_shares = [int(share) for share in shares]
        if not mixed_face:
            derived_usd = allocate_minor_proportionally(usd, face_shares)
            derived_mxn = allocate_minor_proportionally(mxn, face_shares)
            for allocation, usd_share, mxn_share in zip(
                    allocations, derived_usd, derived_mxn):
                allocation["usd_cents"] = usd_share
                allocation["mxn_centavos"] = mxn_share
        else:
            alloc_usd = sum(a["usd_cents"] for a in allocations)
            alloc_mxn = sum(a["mxn_centavos"] for a in allocations)
            if alloc_usd != usd or alloc_mxn != mxn:
                raise ValidationFailure(
                    "allocation_mismatch",
                    "Mixed receipt allocations must equal cash used")
    else:
        alloc_usd = sum(a["usd_cents"] for a in allocations)
        alloc_mxn = sum(a["mxn_centavos"] for a in allocations)
        if alloc_usd != usd or alloc_mxn != mxn:
            raise ValidationFailure(
                "allocation_mismatch",
                f"Allocations ({alloc_usd},{alloc_mxn}) must equal actual "
                f"({usd},{mxn})")

    for allocation in allocations:
        account = conn.execute(
            "SELECT kind, active FROM accounts WHERE account_id=?",
            (allocation.get("expense_account"),)).fetchone()
        class_code = allocation.get("class_code") or class_default
        class_row = conn.execute(
            "SELECT active FROM classes WHERE class_code=?",
            (class_code,)).fetchone()
        if not account or account["kind"] != "expense" or \
                not account["active"]:
            raise ValidationFailure(
                "bad_expense_account", "Select an active expense account")
        if not class_row or not class_row["active"]:
            raise ValidationFailure("bad_class", "Select an active class")
        allocation["class_code"] = class_code

    conn.execute(
        "UPDATE transactions SET posting_state='void',"
        " void_reason='superseded by final allocations',"
        " voided_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
        " version=version+1 WHERE txn_id = ?", (sparse["txn_id"],))

    new_rows = []
    for line, a in enumerate(allocations):
        face_usd = a.get("face_usd_cents")
        face_mxn = a.get("face_mxn_centavos")
        if not mixed_face:
            face_usd = (a.get("face_value_minor") if expected_usd is not None
                        else None)
            face_mxn = (a.get("face_value_minor") if expected_mxn is not None
                        else None)
        new_rows.append(_row(
            sparse["movement_id"], sparse["date"], sparse["same_day_seq"],
            line + 1, "expense", SAFE, a["expense_account"],
            a["usd_cents"], a["mxn_centavos"], voucher_id=voucher_id,
            receipt_value_mxn=(int(face_mxn) if face_mxn is not None else None),
            receipt_value_usd=(int(face_usd) if face_usd is not None else None),
            class_code=a["class_code"],
            related_row_id=str(sparse["txn_id"])))
    ids = repo.insert_rows(conn, submission_id, new_rows)

    for txn_id, allocation in zip(ids, allocations):
        conn.execute(
            "UPDATE transactions SET project=?, description=?, notes=?"
            " WHERE txn_id=?",
            (allocation.get("project", ""),
             allocation.get("description", ""),
             allocation.get("notes", ""), txn_id))

    voucher_document_id = voucher_document_id or document_id
    support_document_ids = list(dict.fromkeys(support_document_ids or []))
    if voucher_document_id:
        _require_inbox_document(conn, voucher_document_id)
        # vendor-signed voucher serves both roles (spec 7); the
        # document attaches to every final row as voucher+support
        for txn_id in ids:
            for role in ("voucher", "support"):
                conn.execute(
                    "INSERT INTO transaction_documents (txn_id,"
                    " document_id, role) VALUES (?,?,?)",
                    (txn_id, voucher_document_id, role))
        conn.execute(
            "UPDATE documents SET state='filed', voucher_id=?,"
            " doc_type='voucher' WHERE document_id=?",
            (voucher_id, voucher_document_id))
    for support_id in support_document_ids:
        if support_id == voucher_document_id:
            continue
        _require_inbox_document(conn, support_id)
        for txn_id in ids:
            conn.execute(
                "INSERT INTO transaction_documents (txn_id, document_id,"
                " role) VALUES (?, ?, 'support')",
                (txn_id, support_id))
        conn.execute(
            "UPDATE documents SET state='filed', voucher_id=?,"
            " doc_type='receipt' WHERE document_id=?",
            (voucher_id, support_id))

    conn.execute(
        "UPDATE vouchers SET status='complete', version=version+1,"
        " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        " WHERE voucher_id=?", (voucher_id,))
    return {"voucher_id": voucher_id, "txn_ids": ids,
            "superseded_txn_id": sparse["txn_id"]}


def prepare_direct_expense(conn, *, submission_id, date, seq_no, recipient,
                           purpose, notes, usd_cents=0, mxn_centavos=0,
                           face_value_minor=None, face_currency=None,
                           face_usd_cents=None, face_mxn_centavos=None,
                           usd_paid_cents=None, mxn_paid_centavos=None,
                           mxn_change_centavos=0, usd_change_cents=0,
                           fy_start_month=9) -> dict:
    """SAFE -> UNALLOCATED_EXPENSE sparse row, active immediately."""
    if usd_paid_cents is not None or mxn_paid_centavos is not None:
        paid_usd = usd_paid_cents or 0
        paid_mxn = mxn_paid_centavos or 0
        if any(value < 0 for value in (
                paid_usd, paid_mxn, usd_change_cents or 0,
                mxn_change_centavos or 0)):
            raise ValidationFailure(
                "negative_tender", "Paid and change cannot be negative")
        net_usd = paid_usd - (usd_change_cents or 0)
        net_mxn = paid_mxn - (mxn_change_centavos or 0)
        if net_usd != usd_cents or net_mxn != mxn_centavos:
            raise ValidationFailure(
                "tender_mismatch",
                "Cash used must equal cash handed over minus change")
    else:
        paid_usd = usd_cents
        paid_mxn = mxn_centavos
    if face_usd_cents is None and face_mxn_centavos is None:
        face_usd_cents = (face_value_minor if face_currency == "USD" else 0)
        face_mxn_centavos = (face_value_minor if face_currency == "MXN" else 0)
    face_usd_cents = face_usd_cents or 0
    face_mxn_centavos = face_mxn_centavos or 0
    if face_usd_cents < 0 or face_mxn_centavos < 0:
        raise ValidationFailure("bad_face_value",
                                "Receipt values cannot be negative")
    if face_usd_cents and face_mxn_centavos:
        face_value_minor = None
        face_currency = None
    elif face_usd_cents:
        face_value_minor, face_currency = face_usd_cents, "USD"
    elif face_mxn_centavos:
        face_value_minor, face_currency = face_mxn_centavos, "MXN"
    if face_value_minor is not None:
        if face_value_minor <= 0 or face_currency not in ("USD", "MXN"):
            raise ValidationFailure("bad_face_value",
                                    "Enter a positive receipt total and currency")
    if usd_cents <= 0 and mxn_centavos <= 0:
        raise ValidationFailure("no_amount", "Amount required",
                                {"usd_cents": "positive",
                                 "mxn_centavos": "positive"})
    vid, fy, seq = _next_voucher_id(conn, date, fy_start_month)
    mv = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO vouchers (voucher_id, type, fiscal_year, seq,"
        " recipient, purpose, notes, authorized_usd_cents,"
        " authorized_mxn_centavos, face_value_minor, face_currency,"
        " face_usd_cents, face_mxn_centavos,"
        " usd_paid_cents, mxn_paid_centavos, mxn_change_centavos,"
        " usd_change_cents) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (vid, "direct_expense", fy, seq, recipient, purpose, notes,
         usd_cents or None, mxn_centavos or None, face_value_minor,
         face_currency, face_usd_cents or None, face_mxn_centavos or None,
         paid_usd, paid_mxn, mxn_change_centavos or 0,
         usd_change_cents or 0))
    seq_no = _same_day_seq(conn, date, seq_no, SAFE)
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
                    usd_cents=0, mxn_centavos=0, fy_start_month=9) -> dict:
    """Create voucher + signout account + SAFE -> FY##-NNNN movement."""
    if usd_cents < 0 or mxn_centavos < 0:
        raise ValidationFailure("negative_amount",
                                "Signout amounts cannot be negative")
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
    seq_no = _same_day_seq(conn, date, seq_no, SAFE, acct)
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
    if usd_cents < 0 or mxn_centavos < 0 or not (
            usd_cents or mxn_centavos):
        raise ValidationFailure(
            "bad_amount", "Enter a positive USD or MXN amount")
    v = conn.execute(
        "SELECT type, status FROM vouchers WHERE voucher_id = ?",
        (voucher_id,)).fetchone()
    if not v or v["type"] != "signout":
        raise ValidationFailure("not_signout",
                                "Choose an existing signout")
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
    seq_no = _same_day_seq(conn, date, seq_no, SAFE, acct)
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
    seq_no = _same_day_seq(conn, date, seq_no, source_account)
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
    seq_no = _same_day_seq(conn, date, seq_no, BANK, SAFE)
    row = _row(mv, date, seq_no, 0, "bank_withdrawal", BANK, SAFE,
               usd_cents, 0)
    ids = repo.insert_rows(conn, submission_id, [row])
    return {"movement_id": mv, "txn_ids": ids}


def create_opening(conn, *, submission_id, date, usd_cents=0, mxn_centavos=0,
                   assigned_rate_milli=None, notes="") -> dict:
    """Genuine initial balance (spec 3: administrator supplies the
    historical MXN-per-USD rate for MXN)."""
    if conn.execute(
            "SELECT 1 FROM transactions WHERE txn_type='opening' AND"
            " posting_state='active' LIMIT 1").fetchone():
        raise ValidationFailure(
            "opening_exists",
            "The ledger already has an active opening balance")
    if usd_cents < 0 or mxn_centavos < 0 or not (
            usd_cents or mxn_centavos):
        raise ValidationFailure("bad_opening",
                                "Enter a positive opening cash amount")
    if mxn_centavos and not assigned_rate_milli:
        raise ValidationFailure(
            "rate_required", "Opening MXN needs the historical rate")
    mv = str(uuid.uuid4())
    seq_no = next_same_day_seq(conn, date, SAFE)
    row = _row(mv, date, seq_no, 0, "opening", OPENING_EQUITY, SAFE,
               usd_cents, mxn_centavos,
               assigned_rate_milli=assigned_rate_milli)
    ids = repo.insert_rows(conn, submission_id, [row])
    return {"movement_id": mv, "txn_ids": ids}


def create_donation(conn, *, submission_id, date, usd_cents=0, mxn_centavos=0,
                    assigned_rate_milli=None, target=SAFE, notes="") -> dict:
    """Donated cash. Into a positive pool: pool proportion (no rate);
    into an empty pool: assigned rate required (spec 3)."""
    if usd_cents < 0 or mxn_centavos < 0 or not (
            usd_cents or mxn_centavos):
        raise ValidationFailure("bad_donation",
                                "Enter a positive donated cash amount")
    if mxn_centavos and not assigned_rate_milli:
        accounts = repo.load_accounts(conn)
        rows = repo.load_rows(conn)
        from .replay import replay
        res = replay(rows, accounts)
        st = res.states.get(target)
        if st is None or st.mxn <= 0:
            raise ValidationFailure(
                "rate_required",
                f"Donation into {target}: the pool has no positive MXN "
                f"to inherit basis from; supply the approved rate")
    mv = str(uuid.uuid4())
    seq_no = next_same_day_seq(conn, date, target)
    row = _row(mv, date, seq_no, 0, "donation", DONATION, target,
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
    seq_no = _same_day_seq(conn, date, seq_no, SAFE)
    row = _row(mv, date, seq_no, 0, "adjustment", f, t, usd, mxn,
               assigned_rate_milli=assigned_rate_milli,
               related_row_id=(str(related_txn_id)
                               if related_txn_id else None))
    ids = repo.insert_rows(conn, submission_id, [row])
    return {"movement_id": mv, "txn_ids": ids}
