"""Preparation workflows (spec 7-8): direct expense, signout, positive
addendum, return addendum. Each allocates IDs transactionally, commits
the sparse active movement, and returns the print context.

Also: opening, donation, correction (spec 7) and exchange/withdrawal
creation (spec 12). Every function runs inside run_command.
"""

from __future__ import annotations

import json
import uuid

from .commands import ValidationFailure
from .model import (BANK, DONATION, FX_COUNTERPARTY, OPENING_EQUITY,
                    RECONCILIATION, SAFE, UNALLOCATED_EXPENSE, Row)
from . import paper, repo


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
    from .document_jobs import require_ready
    require_ready(conn, document_id)
    row = conn.execute(
        "SELECT state FROM documents WHERE document_id=?",
        (document_id,)).fetchone()
    if not row or row["state"] != "inbox":
        raise ValidationFailure(
            "document_unavailable",
            "The selected document is no longer available in the inbox",
            {"document_id": "not available"})


def file_document(conn, document_id, txn_ids, doc_type, *, roles=("support",),
                  voucher_id=None):
    """Attach a document to transactions in each role and mark it filed."""
    for txn_id in txn_ids:
        for role in roles:
            conn.execute("INSERT INTO transaction_documents (txn_id, document_id, role)"
                         " VALUES (?, ?, ?)", (txn_id, document_id, role))
    conn.execute(
        "UPDATE documents SET state='filed', doc_type=?, voucher_id=COALESCE(?, voucher_id),"
        " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE document_id=?",
        (doc_type, voucher_id, document_id))


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
    if conn.execute(
            "SELECT 1 FROM transaction_documents td JOIN transactions t"
            " ON t.txn_id=td.txn_id WHERE t.voucher_id=? AND"
            " t.txn_type='signout' AND t.posting_state='active' AND"
            " td.role='voucher' LIMIT 1", (voucher_id,)).fetchone():
        raise ValidationFailure(
            "voucher_already_filed", "This signout voucher is already filed")
    file_document(conn, document_id, [txn["txn_id"] for txn in txns], "voucher",
                  roles=("voucher",), voucher_id=voucher_id)
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
    file_document(conn, document_id, [txn["txn_id"] for txn in txns], "addendum",
                  roles=("voucher",), voucher_id=addendum["voucher_id"])
    conn.execute(
        "UPDATE addenda SET document_id=?, status='complete',"
        " version=version+1, updated_at="
        " strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE addendum_id=?",
        (document_id, addendum_id))
    return {"addendum_id": addendum_id, "document_id": document_id,
            "status": "complete"}


DIRECT_DRAFT_FORMAT = 2


def load_direct_expense_review(draft_json) -> dict | None:
    """A saved direct-expense draft in the current format (see
    save_direct_expense_review). Older drafts are converted as they are read:
    before format 2 they held "editors" with allocation lines, and format 2
    drafts without "receipt_document_ids" gave each receipt its own row."""
    if not draft_json:
        return None
    draft = json.loads(draft_json)
    if draft.get("format") != DIRECT_DRAFT_FORMAT:
        draft = _direct_draft_from_editors(draft)
    if "receipt_document_ids" not in draft:
        draft = _direct_draft_as_one_payment(draft)
    return draft


def _direct_draft_as_one_payment(draft) -> dict:
    """Receipts leave the rows; several receipt rows become one split row."""
    rows = draft["rows"]
    receipts = [row["document_id"] for row in rows if row.get("document_id")]
    rows = [{key: value for key, value in row.items()
             if key not in ("document_id", "date", "vendor")} for row in rows]
    if len(rows) > 1:
        parts = [{"usd": part["usd"], "mxn": part["mxn"],
                  "receipt_total": part.get("receipt_total"),
                  "purpose": part.get("purpose") or row.get("purpose", ""),
                  "class_code": part.get("class_code"),
                  "expense_account": part.get("expense_account")}
                 for row in rows for part in row["splits"] or [row]]
        totals = [part["receipt_total"] for part in parts
                  if part["receipt_total"] is not None]
        rows = [{"purpose": rows[0].get("purpose", ""),
                 "project": rows[0].get("project", ""),
                 "usd": sum(part["usd"] for part in parts),
                 "mxn": sum(part["mxn"] for part in parts),
                 "receipt_total": sum(totals) if totals else None,
                 "class_code": None, "expense_account": None,
                 "splits": parts}]
    return {**draft, "rows": rows, "receipt_document_ids": receipts}


def _direct_draft_from_editors(draft) -> dict:
    rows = []
    for editor in draft.get("editors") or []:
        lines = editor.get("allocations") or []
        usd = sum(int(line.get("usd_cents") or 0) for line in lines)
        mxn = sum(int(line.get("mxn_centavos") or 0) for line in lines)
        face = "face_mxn_centavos" if usd > 0 > mxn else \
            "face_usd_cents" if mxn > 0 > usd else None

        def total(items):
            return sum(int(item.get(face) or 0) for item in items) or None if face else None

        row = {"document_id": editor.get("document_id"),
               "date": editor.get("receipt_date") or "",
               "vendor": editor.get("receipt_vendor") or "",
               "purpose": editor.get("receipt_description") or
               (lines[0].get("description", "") if lines else ""),
               "project": lines[0].get("project", "") if lines else "",
               "usd": usd, "mxn": mxn, "receipt_total": total(lines),
               "class_code": None, "expense_account": None, "splits": []}
        if len(lines) == 1:
            row.update(class_code=lines[0].get("class_code"),
                       expense_account=lines[0].get("expense_account") or None)
        else:
            row["splits"] = [{
                "usd": int(line.get("usd_cents") or 0), "mxn": int(line.get("mxn_centavos") or 0),
                "receipt_total": total([line]), "purpose": line.get("description", ""),
                "class_code": line.get("class_code"),
                "expense_account": line.get("expense_account") or None} for line in lines]
        rows.append(row)
    return {"format": DIRECT_DRAFT_FORMAT, "rows": rows,
            "primary_document_id": draft.get("primary_document_id"),
            "use_voucher_as_receipt": bool(draft.get("use_voucher_as_receipt"))}


def save_direct_expense_review(conn, *, voucher_id, expected_version, draft):
    """Persist unfinished coding without posting allocations or filing paper.

    draft is format 2: {"format": 2, "primary_document_id", "use_voucher_as_receipt",
    "receipt_document_ids": [...], "rows": [{"purpose", "project", "usd", "mxn",
    "receipt_total", "class_code", "expense_account", "splits": [...]}]} with
    amounts in minor units. Codes may be missing until the expense is completed.
    Receipts were added to format 2 without a new number so the previous
    release still reads the coding."""
    voucher = conn.execute(
        "SELECT type, status FROM vouchers WHERE voucher_id=?",
        (voucher_id,)).fetchone()
    if not voucher or voucher["type"] != "direct_expense":
        raise ValidationFailure("not_found", "Direct expense not found")
    repo.assert_versions(conn, "vouchers", "voucher_id",
                         [(voucher_id, expected_version)])
    if voucher["status"] != "pending" or not conn.execute(
            "SELECT 1 FROM transactions WHERE voucher_id=?"
            " AND posting_state='active' AND to_account='UNALLOCATED_EXPENSE'",
            (voucher_id,)).fetchone():
        raise ValidationFailure("not_pending", "Expense is not pending review")
    document_ids = [draft["primary_document_id"]] if draft["primary_document_id"] else []
    document_ids += draft["receipt_document_ids"]
    for row in draft["rows"]:
        for allocation in row["splits"] or [row]:
            account_code = allocation.get("expense_account")
            if account_code:
                account = conn.execute(
                    "SELECT kind, active FROM accounts WHERE account_id=?",
                    (account_code,)).fetchone()
                if not account or account["kind"] != "expense" or \
                        not account["active"] or account_code == UNALLOCATED_EXPENSE:
                    raise ValidationFailure("bad_expense_account",
                                            "Select an active account code")
            class_code = allocation.get("class_code")
            if class_code:
                class_row = conn.execute(
                    "SELECT active FROM classes WHERE class_code=?",
                    (class_code,)).fetchone()
                if not class_row or not class_row["active"]:
                    raise ValidationFailure("bad_class", "Select an active class code")
    if len(document_ids) != len(set(document_ids)):
        raise ValidationFailure("duplicate_document", "Select each document only once")
    for document_id in document_ids:
        _require_inbox_document(conn, document_id)
    conn.execute(
        "INSERT INTO direct_expense_review_drafts (voucher_id, draft_json)"
        " VALUES (?, ?) ON CONFLICT(voucher_id) DO UPDATE"
        " SET draft_json=excluded.draft_json", (voucher_id, json.dumps(draft)))
    conn.execute(
        "UPDATE vouchers SET version=version+1,"
        " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE voucher_id=?",
        (voucher_id,))
    return {"voucher_id": voucher_id, "version": expected_version + 1,
            "status": "pending"}


def _validate_direct_payment(paid, usd, mxn, allocations):
    """A direct expense's amount is what the voucher paid. Coding cannot change
    it; the only addition is change received in a currency it did not pay."""
    for currency, amount, total in (("USD", paid["usd_cents"], usd),
                                    ("MXN", paid["mxn_centavos"], mxn)):
        if amount and total != amount:
            raise ValidationFailure(
                "amount_changed",
                f"The voucher paid {currency} {amount / 100:,.2f}. The lines must add up to it.")
        if not amount and total > 0:
            raise ValidationFailure(
                "amount_changed",
                f"The voucher paid no {currency}. Enter {currency} only as change received,"
                " with a minus sign.")
    for allocation in allocations:
        face_usd = allocation.get("face_usd_cents") or 0
        face_mxn = allocation.get("face_mxn_centavos") or 0
        if face_usd < 0 or face_mxn < 0:
            raise ValidationFailure("bad_face_value", "Purchase values cannot be negative")
        if allocation["usd_cents"] > 0 and allocation["mxn_centavos"] < 0 \
                and not face_mxn:
            raise ValidationFailure(
                "purchase_value_required",
                "Enter the MXN purchase value to value pesos received as change")


def complete_direct_expense(conn, *, submission_id, voucher_id,
                            expected_version, allocations,
                            voucher_document_id=None,
                            receipt_document_ids=None,
                            use_voucher_as_receipt=False, reviewed_date=None):
    """Code the payment the voucher recorded. Its row is filled in place and
    keeps its transaction number; further split lines join the same movement.

    allocations: list of dicts {expense_account, class_code, usd_cents,
    mxn_centavos, project, description}. They add up to the voucher's payment,
    plus any change received in a currency it did not pay, which needs a
    purchase value.

    The payment stays one movement whatever documents support it. Each
    separate receipt is numbered (FY27-0042-R1, ...) and attached to every
    line; receipt numbers carry no amounts and no line points at them."""
    v = conn.execute(
        "SELECT status FROM vouchers WHERE voucher_id = ?",
        (voucher_id,)).fetchone()
    if not v:
        raise ValidationFailure("not_found", "Voucher not found")
    if v["status"] != "pending":
        raise ValidationFailure("not_pending", "Voucher not pending")
    repo.assert_versions(conn, "vouchers", "voucher_id",
                         [(voucher_id, expected_version)])

    payment = conn.execute(
        "SELECT txn_id, movement_id, date, same_day_seq, usd_cents,"
        " mxn_centavos, recipient FROM transactions WHERE voucher_id = ? AND"
        " to_account = 'UNALLOCATED_EXPENSE' AND posting_state='active'",
        (voucher_id,)).fetchone()
    if not payment:
        raise ValidationFailure("no_sparse", "No payment awaiting coding")

    if not allocations:
        raise ValidationFailure("no_allocations",
                                "At least one allocation is required")
    allocations = [dict(allocation) for allocation in allocations]
    alloc_usd = sum(int(a["usd_cents"]) for a in allocations)
    alloc_mxn = sum(int(a["mxn_centavos"]) for a in allocations)
    _validate_direct_payment(payment, alloc_usd, alloc_mxn, allocations)

    for allocation in allocations:
        account = conn.execute(
            "SELECT kind, active FROM accounts WHERE account_id=?",
            (allocation.get("expense_account"),)).fetchone()
        class_code = allocation.get("class_code")
        class_row = conn.execute(
            "SELECT active FROM classes WHERE class_code=?",
            (class_code,)).fetchone()
        if not account or account["kind"] != "expense" or \
                not account["active"]:
            raise ValidationFailure(
                "bad_expense_account", "Select an active expense account")
        if not class_row or not class_row["active"]:
            raise ValidationFailure("bad_class", "Select an active class")

    def faces(allocation):
        return (int(allocation.get("face_usd_cents") or 0) or None,
                int(allocation.get("face_mxn_centavos") or 0) or None)

    first, rest = allocations[0], allocations[1:]
    face_usd, face_mxn = faces(first)
    conn.execute(
        "UPDATE transactions SET to_account=?, class_code=?, usd_cents=?,"
        " mxn_centavos=?, receipt_face_usd_cents=?, receipt_face_mxn_centavos=?,"
        " version=version+1, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        " WHERE txn_id=?",
        (first["expense_account"], first["class_code"], first["usd_cents"],
         first["mxn_centavos"], face_usd, face_mxn, payment["txn_id"]))
    new_rows = []
    for line, a in enumerate(rest, 1):
        face_usd, face_mxn = faces(a)
        new_rows.append(_row(
            payment["movement_id"], payment["date"], payment["same_day_seq"],
            line, "expense", SAFE, a["expense_account"],
            a["usd_cents"], a["mxn_centavos"], voucher_id=voucher_id,
            receipt_value_mxn=face_mxn, receipt_value_usd=face_usd,
            class_code=a["class_code"]))
    ids = [payment["txn_id"], *repo.insert_rows(conn, submission_id, new_rows)]

    for txn_id, allocation in zip(ids, allocations):
        conn.execute(
            "UPDATE transactions SET recipient=?, project=?, description=?,"
            " notes=? WHERE txn_id=?",
            (payment["recipient"], allocation.get("project", ""),
             allocation.get("description", ""),
             allocation.get("notes", ""), txn_id))

    receipt_document_ids = list(receipt_document_ids or [])
    if len(set(receipt_document_ids)) != len(receipt_document_ids):
        raise ValidationFailure("duplicate_document", "Attach each receipt only once")
    if voucher_document_id in receipt_document_ids:
        raise ValidationFailure(
            "duplicate_document",
            "The voucher and a receipt must be different documents")
    if voucher_document_id:
        _require_inbox_document(conn, voucher_document_id)
        # Only the explicit vendor-acknowledgment choice adds the receipt role.
        file_document(conn, voucher_document_id, ids, "voucher",
                      roles=("voucher", "support") if use_voucher_as_receipt else ("voucher",),
                      voucher_id=voucher_id)
    receipt_ids = _file_direct_receipts(conn, voucher_id, ids, receipt_document_ids,
                                        reviewed_date)

    conn.execute(
        "UPDATE vouchers SET status='complete', version=version+1,"
        " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        " WHERE voucher_id=?", (voucher_id,))
    conn.execute("DELETE FROM direct_expense_review_drafts WHERE voucher_id=?",
                 (voucher_id,))
    return {"voucher_id": voucher_id, "txn_ids": ids,
            "receipt_ids": receipt_ids}


def _file_direct_receipts(conn, voucher_id, txn_ids, document_ids,
                          reviewed_date=None) -> list[str]:
    """Number each receipt document and attach it to every line of the payment."""
    next_receipt = conn.execute(
        "SELECT COALESCE(MAX(seq),0)+1 FROM receipts WHERE voucher_id=?",
        (voucher_id,)).fetchone()[0]
    receipt_ids = []
    for offset, document_id in enumerate(document_ids):
        _require_inbox_document(conn, document_id)
        receipt_id = f"{voucher_id}-R{next_receipt + offset}"
        conn.execute(
            "INSERT INTO receipts (receipt_id, voucher_id, seq, document_id,"
            " reviewed_date) VALUES (?,?,?,?, COALESCE(?, strftime('%Y-%m-%d','now')))",
            (receipt_id, voucher_id, next_receipt + offset, document_id, reviewed_date))
        file_document(conn, document_id, txn_ids, "receipt", voucher_id=voucher_id)
        receipt_ids.append(receipt_id)
    return receipt_ids


def add_direct_expense_receipt(conn, *, txn_id, document_id, reviewed_date=None) -> dict:
    """File a receipt that arrived after a direct expense was completed. It is
    numbered like the others and supports every line of the payment."""
    row = conn.execute(
        "SELECT t.voucher_id, t.posting_state, v.type, v.status FROM transactions t"
        " JOIN vouchers v USING(voucher_id) WHERE t.txn_id=?", (txn_id,)).fetchone()
    if not row or row["type"] != "direct_expense":
        raise ValidationFailure("not_direct_expense", "Direct expense not found")
    if row["posting_state"] != "active" or row["status"] != "complete":
        raise ValidationFailure(
            "not_complete", "Add receipts to a pending expense in Review")
    voucher_id = row["voucher_id"]
    ids = [line[0] for line in conn.execute(
        "SELECT txn_id FROM transactions WHERE voucher_id=? AND posting_state='active'"
        " ORDER BY allocation_line, txn_id", (voucher_id,))]
    [receipt_id] = _file_direct_receipts(conn, voucher_id, ids, [document_id], reviewed_date)
    conn.execute(
        "UPDATE vouchers SET version=version+1, updated_at="
        " strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE voucher_id=?", (voucher_id,))
    return {"voucher_id": voucher_id, "receipt_id": receipt_id, "txn_ids": ids}


def _voucher_text(recipient, purpose, notes):
    """Voucher text that prints, checked so the voucher stays one page."""
    return (paper.check(recipient, "recipient", "The name", paper.NAME),
            paper.check(purpose, "purpose", "The purpose", paper.VOUCHER_PURPOSE),
            paper.check(notes, "notes", "Notes", paper.VOUCHER_NOTES, paper.VOUCHER_NOTES_LINES))


def prepare_direct_expense(conn, *, submission_id, date, seq_no, recipient,
                           purpose, notes, usd_cents=0, mxn_centavos=0,
                           fy_start_month=9) -> dict:
    """SAFE -> UNALLOCATED_EXPENSE sparse row, active immediately."""
    recipient, purpose, notes = _voucher_text(recipient, purpose, notes)
    if usd_cents < 0 or mxn_centavos < 0:
        raise ValidationFailure("negative_amount", "Authorized amounts cannot be negative")
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
    recipient, purpose, notes = _voucher_text(recipient, purpose, notes)
    if usd_cents < 0 or mxn_centavos < 0:
        raise ValidationFailure("negative_amount",
                                "Signout amounts cannot be negative")
    if usd_cents <= 0 and mxn_centavos <= 0:
        raise ValidationFailure("no_amount", "Amount required")
    vid, fy, seq = _next_voucher_id(conn, date, fy_start_month)
    acct = vid  # signout accounts are named FY##-NNNN (spec 2)
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
    reason = paper.check(reason, "reason", "The reason", paper.REASON)
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
    acct = voucher_id  # signout accounts are named FY##-NNNN (spec 2)
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
    st = repo.replay_projected(conn).states[voucher_id]
    if usd_cents > st.usd or mxn_centavos > st.mxn:
        raise ValidationFailure(
            "return_exceeds_balance",
            f"Return exceeds signout balance "
            f"({st.usd} cents USD, {st.mxn} centavos MXN held)")


def create_exchange(conn, *, submission_id, date, seq_no, source_account,
                    usd_cents, mxn_centavos, notes="") -> dict:
    """Record active cash movement; receipt review is required for completion.

    All outgoing USD becomes incoming MXN basis (calc_usd = 0).
    """
    if usd_cents <= 0 or mxn_centavos >= 0:
        raise ValidationFailure(
            "bad_exchange",
            "Exchange needs positive USD out and negative MXN (returned)")
    mv = str(uuid.uuid4())
    seq_no = _same_day_seq(conn, date, seq_no, source_account)
    row = _row(mv, date, seq_no, 0, "exchange", source_account,
               FX_COUNTERPARTY, usd_cents, mxn_centavos)
    ids = repo.insert_rows(conn, submission_id, [row])
    conn.execute("UPDATE transactions SET notes=? WHERE txn_id=?",
                 (notes, ids[0]))
    return {"movement_id": mv, "txn_ids": ids}


def complete_cash_receipt(conn, *, txn_id, expected_version, document_id, kind):
    """File the required receipt of an exchange or bank withdrawal already
    recorded; never create another movement."""
    txn_type = "bank_withdrawal" if kind == "withdrawal" else "exchange"
    transaction = conn.execute(
        "SELECT txn_type, posting_state FROM transactions WHERE txn_id=?",
        (txn_id,)).fetchone()
    if not transaction or transaction["txn_type"] != txn_type:
        raise ValidationFailure(f"not_{kind}", f"{kind.title()} transaction not found")
    repo.assert_versions(conn, "transactions", "txn_id",
                         [(txn_id, expected_version)])
    if transaction["posting_state"] != "active" or conn.execute(
            "SELECT 1 FROM transaction_documents WHERE txn_id=?"
            " AND role='support'", (txn_id,)).fetchone():
        raise ValidationFailure("not_pending", f"{kind.title()} is not pending review")
    if not document_id:
        raise ValidationFailure("document_required", f"Select the {kind} receipt")
    _require_inbox_document(conn, document_id)
    file_document(conn, document_id, [txn_id], f"{kind}_receipt")
    conn.execute(
        "UPDATE transactions SET version=version+1,"
        " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE txn_id=?",
        (txn_id,))
    return {"txn_id": txn_id, "document_id": document_id,
            "status": "complete"}


def create_withdrawal(conn, *, submission_id, date, seq_no, usd_cents,
                      notes="") -> dict:
    """Record BANK -> SAFE cash immediately; its receipt may be filed later."""
    if usd_cents <= 0:
        raise ValidationFailure("bad_withdrawal", "Positive USD required")
    mv = str(uuid.uuid4())
    seq_no = _same_day_seq(conn, date, seq_no, BANK, SAFE)
    row = _row(mv, date, seq_no, 0, "bank_withdrawal", BANK, SAFE,
               usd_cents, 0)
    ids = repo.insert_rows(conn, submission_id, [row])
    conn.execute("UPDATE transactions SET notes=? WHERE txn_id=?",
                 (notes, ids[0]))
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
    if mxn_centavos:
        st = repo.replay_projected(conn).states.get(target)
        if st is not None and st.mxn > 0:
            # The existing pool, not a user-supplied rate, determines the
            # basis of this donation.
            assigned_rate_milli = None
        elif not assigned_rate_milli:
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
    conn.execute(
        "UPDATE transactions SET description=?, notes=? WHERE txn_id=?",
        (str(reason).strip(), str(notes).strip(), ids[0]))
    return {"movement_id": mv, "txn_ids": ids}


SAFE_COUNT_NOTE = "Safe count"


def create_safe_count_corrections(
        conn, *, submission_id, date, expected_ledger_revision,
        counted_usd_cents, counted_mxn_centavos, reason="",
        assigned_rate_milli=None) -> dict:
    """Record a physical SAFE count against one ledger revision.

    Every count is recorded, so each leaves a signed count sheet on file. A
    count that matches posts one 0/0 adjustment; otherwise each nonzero
    currency difference is its own adjustment movement. The count is known by
    its first transaction number (Txn-123, the code on its sheet) and waits
    in Review until its signed sheet is filed.
    """
    if counted_usd_cents < 0 or counted_mxn_centavos < 0:
        raise ValidationFailure(
            "bad_count", "Physical cash counts cannot be negative")
    reason = paper.check(reason, "reason", "The explanation", paper.REASON)
    current_revision = repo.ledger_revision(conn)
    if current_revision != expected_ledger_revision:
        raise repo.ConflictError(
            "The ledger changed after this cash count; compare it again")

    state = repo.replay_projected(conn).states[SAFE]
    delta_usd = counted_usd_cents - state.usd
    delta_mxn = counted_mxn_centavos - state.mxn
    if (delta_usd or delta_mxn) and not reason:
        raise ValidationFailure(
            "reason_required", "Explain why the records need correction")

    created = []
    for currency, delta in (("USD", delta_usd), ("MXN", delta_mxn)):
        if not delta:
            continue
        result = create_correction(
            conn, submission_id=submission_id, date=date, seq_no=None,
            direction="in" if delta > 0 else "out",
            usd_cents=abs(delta) if currency == "USD" else 0,
            mxn_centavos=abs(delta) if currency == "MXN" else 0,
            assigned_rate_milli=(assigned_rate_milli
                                 if currency == "MXN" and delta > 0
                                 else None),
            reason=reason, notes=f"{SAFE_COUNT_NOTE} · {currency}")
        created.append({"currency": currency, **result})
    if not created:
        result = create_correction(
            conn, submission_id=submission_id, date=date, seq_no=None,
            direction="out", reason=reason or "The count matched the records",
            notes=SAFE_COUNT_NOTE)
        created.append({"currency": None, **result})

    txn_ids = [txn_id for item in created for txn_id in item["txn_ids"]]
    return {
        "book": {"usd": state.usd / 100, "mxn": state.mxn / 100},
        "count": {"usd": counted_usd_cents / 100,
                  "mxn": counted_mxn_centavos / 100},
        "delta": {"usd": delta_usd / 100, "mxn": delta_mxn / 100},
        "txn_ids": txn_ids, "movements": created,
        "count_id": txn_ids[0],
        "print_url": f"/balances/count/{txn_ids[0]}/print",
    }


def safe_count_rows(conn, txn_id) -> list:
    """The adjustments recorded by the safe count that includes txn_id,
    first one first; the first one's number identifies the count."""
    found = conn.execute(
        "SELECT submission_id FROM transactions WHERE txn_id=? AND"
        " txn_type='adjustment' AND notes LIKE ?", (txn_id, SAFE_COUNT_NOTE + "%")).fetchone()
    if not found:
        return []
    return conn.execute(
        "SELECT * FROM transactions WHERE submission_id=? AND txn_type='adjustment'"
        " AND notes LIKE ? ORDER BY txn_id", (found["submission_id"], SAFE_COUNT_NOTE + "%")
    ).fetchall()


def complete_count_sheet(conn, *, count_id, expected_version, document_id) -> dict:
    """File the signed count sheet on every adjustment of that count."""
    rows = safe_count_rows(conn, count_id)
    if not rows or rows[0]["txn_id"] != count_id:
        raise ValidationFailure("not_count", "Safe count not found")
    repo.assert_versions(conn, "transactions", "txn_id", [(count_id, expected_version)])
    if rows[0]["posting_state"] != "active" or conn.execute(
            "SELECT 1 FROM transaction_documents WHERE role='support' AND txn_id IN"
            f" ({','.join('?' for _ in rows)})", [row["txn_id"] for row in rows]).fetchone():
        raise ValidationFailure("not_pending", "This count's sheet is already filed")
    if not document_id:
        raise ValidationFailure("document_required", "Select the signed count sheet")
    _require_inbox_document(conn, document_id)
    file_document(conn, document_id, [row["txn_id"] for row in rows], "physical_count")
    conn.execute("UPDATE transactions SET version=version+1 WHERE txn_id=?", (count_id,))
    return {"count_id": count_id, "document_id": document_id,
            "txn_ids": [row["txn_id"] for row in rows]}
