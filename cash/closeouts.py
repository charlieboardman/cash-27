"""Signout settlement: the cash returned or reimbursed when an expense report is approved."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date as calendar_date

from . import repo
from .commands import ValidationFailure
from .model import Row, SAFE, FX_COUNTERPARTY


def facts(conn, voucher_id):
    """Paper filing may finish after confirmation; cash/receipt changes invalidate it."""
    def rows(sql):
        return [dict(row) for row in conn.execute(sql, (voucher_id,))]

    payload = {
        "voucher": rows("SELECT voucher_id, type, recipient, purpose,"
                        " authorized_usd_cents, authorized_mxn_centavos"
                        " FROM vouchers WHERE voucher_id=?"),
        "transactions": [dict(row) for row in conn.execute(
            "SELECT txn_id, movement_id, date, same_day_seq, allocation_line,"
            " txn_type, from_account, to_account, usd_cents, mxn_centavos,"
            " receipt_id, addendum_id, receipt_face_usd_cents,"
            " receipt_face_mxn_centavos, assigned_rate_milli, posting_state, version"
            " FROM transactions WHERE voucher_id=? OR from_account=? OR to_account=?"
            " ORDER BY txn_id", (voucher_id, voucher_id, voucher_id))],
        "receipts": rows("SELECT * FROM receipts WHERE voucher_id=? ORDER BY receipt_id"),
        "addenda": rows("SELECT addendum_id, kind, movement_id, reason, status='void' AS void"
                        " FROM addenda WHERE voucher_id=? ORDER BY seq"),
    }
    from . import expense_drafts
    draft = expense_drafts.stored(conn, voucher_id)
    if draft:
        payload["expense_report_draft"] = draft[0]
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return serialized, hashlib.sha256(serialized.encode()).hexdigest()


def confirmation(conn, voucher_id):
    row = conn.execute(
        "SELECT * FROM signout_closeouts WHERE voucher_id=? ORDER BY rowid DESC LIMIT 1",
        (voucher_id,)).fetchone()
    if not row:
        return None
    return {"closeout_id": row["closeout_id"], "confirmed_at": row["confirmed_at"],
            "settlement_txn_id": row["settlement_txn_id"],
            "return_addendum_id": row["return_addendum_id"],
            "reimbursement_addendum_id": row["reimbursement_addendum_id"],
            "resolution_note": row["resolution_note"]}


def settle(conn, *, submission_id, voucher_id, date, confirmed,
           return_usd_cents=0, return_mxn_centavos=0,
           reimburse_usd_cents=0, reimburse_mxn_centavos=0, resolution_note="", settlement_papers=None):
    """Return and reimburse cash and close out the signout. Runs inside the
    approval of the signout's expense report, after its receipts are posted,
    so any failure undoes the whole approval."""
    from . import expense_drafts, prep, signouts

    if confirmed is not True:
        raise ValidationFailure("confirmation_required", "Confirm that all spending and returned cash are accounted for")
    try:
        calendar_date.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise ValidationFailure("bad_date", "Enter a valid settlement date") from exc
    if min(return_usd_cents, return_mxn_centavos, reimburse_usd_cents, reimburse_mxn_centavos) < 0:
        raise ValidationFailure("bad_return", "Returned amounts cannot be negative")
    state = signouts.summary(conn, voucher_id)
    if not state or state["status"] == "void":
        raise ValidationFailure("not_signout", "Choose an open signout")
    if not state["has_voucher"] or state["pending_addenda"]:
        raise ValidationFailure("paperwork_needed", "File the signed voucher and existing addenda before approving")
    if any(row["from_account"] == voucher_id for row in repo.pending_exchanges(conn)):
        raise ValidationFailure("exchange_needed", "Review the signout's existing currency exchanges first")

    draft = expense_drafts.stored(conn, voucher_id)[0]
    declared = expense_drafts.declared_settlement(draft)
    actual = (return_usd_cents, return_mxn_centavos, reimburse_usd_cents, reimburse_mxn_centavos)
    # A report that declares no return or reimbursement leaves the amounts
    # to the administrator.
    differs = any(row["kind"] in ("cash_return", "reimbursement") for row in draft["rows"]) and declared != actual
    if (draft["note"] or differs) and not str(resolution_note).strip():
        raise ValidationFailure("resolution_required", "Explain how the report's note or the changed amounts were resolved")
    rows = repo.load_rows(conn)
    related = [row for row in rows if not row.void and
               (row.voucher_id == voucher_id or voucher_id in (row.from_account, row.to_account))]
    if related and date < max(row.date for row in related):
        raise ValidationFailure("closeout_date", "Settlement cannot precede the signout's last cash movement")
    usd = state["usd_cents"] + reimburse_usd_cents - return_usd_cents
    mxn = state["mxn_centavos"] + reimburse_mxn_centavos - return_mxn_centavos
    if (usd or mxn) and not (usd > 0 and mxn < 0):
        raise ValidationFailure(
            "unreconciled_cash",
            "Check the receipts and cash returned. The remaining difference does not describe a USD-to-MXN settlement")

    papers = settlement_papers or {}
    reimbursed = None
    if reimburse_usd_cents or reimburse_mxn_centavos:
        reimbursed = prep.prepare_addendum(
            conn, submission_id=submission_id, voucher_id=voucher_id, date=date,
            seq_no=None, kind="positive", reason="Personal spending reimbursed at expense report settlement",
            usd_cents=reimburse_usd_cents, mxn_centavos=reimburse_mxn_centavos,
            addendum_id=papers.get("positive", {}).get("paper_id"))
        if "positive" in papers:
            prep.complete_addendum_document(conn, addendum_id=reimbursed["addendum_id"],
                expected_version=1, document_id=papers["positive"]["document_id"])
    txn_id = None
    if usd:
        row = Row("pending", str(uuid.uuid4()), date,
                  prep.next_same_day_seq(conn, date, voucher_id, SAFE), 0,
                  "exchange", voucher_id, FX_COUNTERPARTY, usd, mxn,
                  voucher_id=voucher_id)
        txn_id = repo.insert_rows(conn, submission_id, [row])[0]
        conn.execute("UPDATE transactions SET description='Currency settlement at signout closeout'"
                     " WHERE txn_id=?", (txn_id,))
    returned = None
    if return_usd_cents or return_mxn_centavos or "return" in papers:
        returned = prep.prepare_addendum(
            conn, submission_id=submission_id, voucher_id=voucher_id, date=date,
            seq_no=None, kind="return", reason="Cash returned at signout closeout",
            usd_cents=return_usd_cents, mxn_centavos=return_mxn_centavos,
            addendum_id=papers.get("return", {}).get("paper_id"), rounding_zero=True)
        if "return" in papers:
            prep.complete_addendum_document(conn, addendum_id=returned["addendum_id"],
                expected_version=1, document_id=papers["return"]["document_id"])
    replayed = repo.replay_projected(conn)
    final = replayed.states[voucher_id]
    if final.usd or final.mxn or final.basis or any(
            claim.account == voucher_id for claim in replayed.open_claims):
        raise ValidationFailure("unreconciled_cash", "The settlement leaves unresolved cash or currency value")
    serialized, fingerprint = facts(conn, voucher_id)
    conn.execute(
        "INSERT INTO signout_closeouts (closeout_id, voucher_id, submission_id,"
        " settlement_txn_id, return_addendum_id, facts_json, facts_sha256, reimbursement_addendum_id, resolution_note)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), voucher_id, submission_id, txn_id,
         returned["addendum_id"] if returned else None, serialized, fingerprint,
         reimbursed["addendum_id"] if reimbursed else None, str(resolution_note).strip()))
