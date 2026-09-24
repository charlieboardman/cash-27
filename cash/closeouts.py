"""Explicit signout settlement, previewed without writes and committed atomically."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date as calendar_date
from decimal import Decimal

from . import repo
from .commands import ValidationFailure
from .model import Row, SAFE, FX_COUNTERPARTY
from .replay import replay, ValidationError


def _legacy_report(conn, voucher_id):
    """The cash-left declaration from the report format before migration 0015."""
    row = conn.execute("SELECT * FROM signout_expense_reports WHERE voucher_id=?",
                       (voucher_id,)).fetchone()
    return dict(row) if row else None


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
        "claims": rows("SELECT claim_id, document_id, content_version, state,"
                       " final_receipt_id, final_movement_id FROM signout_expense_claims"
                       " WHERE voucher_id=? ORDER BY claim_id"),
        "packages": rows("SELECT package_id, status, version FROM signout_reconciliation_packages"
                         " WHERE voucher_id=? ORDER BY package_id"),
        "addenda": rows("SELECT addendum_id, kind, movement_id, reason, status='void' AS void"
                        " FROM addenda WHERE voucher_id=? ORDER BY seq"),
    }
    report = _legacy_report(conn, voucher_id)
    if report:
        payload["expense_report"] = report
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
            "resolution_note": row["resolution_note"],
            "valid": row["facts_sha256"] == facts(conn, voucher_id)[1]}


def preview(conn, *, voucher_id, date, return_usd_cents=0, return_mxn_centavos=0,
            reimburse_usd_cents=0, reimburse_mxn_centavos=0, resolution_note=""):
    from . import expense_drafts, signouts
    from .prep import next_same_day_seq

    try:
        calendar_date.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise ValidationFailure("bad_date", "Enter a valid closeout date") from exc
    if min(return_usd_cents, return_mxn_centavos, reimburse_usd_cents, reimburse_mxn_centavos) < 0:
        raise ValidationFailure("bad_return", "Returned amounts cannot be negative")
    state = signouts.summary(conn, voucher_id)
    if not state or state["status"] == "void":
        raise ValidationFailure("not_signout", "Choose an open signout")
    if state["closeout"] and state["closeout"]["valid"]:
        raise ValidationFailure("already_confirmed", "This closeout is already confirmed; file any remaining signed addendum")
    if not state["has_voucher"] or state["pending_addenda"]:
        raise ValidationFailure("paperwork_needed", "File the signed voucher and existing addenda before closeout")
    if state["open_claims"] or state["unresolved_packages"]:
        raise ValidationFailure("receipts_in_progress", "Finish reviewing all receipts before closeout")
    if any(row["from_account"] == voucher_id for row in repo.pending_exchanges(conn)):
        raise ValidationFailure("exchange_needed", "Review the signout's existing currency exchanges first")

    draft = expense_drafts.stored(conn, voucher_id)
    report = None if draft else _legacy_report(conn, voucher_id)
    if draft:
        draft = draft[0]
        if draft["status"] != "approved":
            raise ValidationFailure("report_not_approved", "Approve the expense report before settling it")
        declared = expense_drafts.declared_settlement(draft)
        actual = (return_usd_cents, return_mxn_centavos, reimburse_usd_cents, reimburse_mxn_centavos)
        # A report that declares no return or reimbursement leaves the amounts
        # to the administrator, as a signout without a report always has.
        differs = any(row["kind"] != "expense" for row in draft["rows"]) and declared != actual
        if (draft["note"] or (not state["closeout"] and differs)) and not str(resolution_note).strip():
            raise ValidationFailure("resolution_required", "Explain how the discrepancy or changed settlement was resolved")
    if report:
        if report["status"] != "submitted":
            raise ValidationFailure("report_not_submitted", "Submit the expense report before settling it")
        declared = (report["cash_usd"], report["cash_mxn"], report["personal_usd"], report["personal_mxn"])
        actual = (return_usd_cents, return_mxn_centavos, reimburse_usd_cents, reimburse_mxn_centavos)
        # After a confirmed settlement, corrections must not repay the old declaration.
        if (report["discrepancy"] or (not state["closeout"] and declared != actual)) and not str(resolution_note).strip():
            raise ValidationFailure("resolution_required", "Explain how the discrepancy or changed settlement was resolved")
    rows = repo.load_rows(conn)
    related = [row for row in rows if not row.void and
               (row.voucher_id == voucher_id or voucher_id in (row.from_account, row.to_account))]
    if related and date < max(row.date for row in related):
        raise ValidationFailure("closeout_date", "Closeout cannot precede the signout's last cash movement")
    usd = state["usd_cents"] + reimburse_usd_cents - return_usd_cents
    mxn = state["mxn_centavos"] + reimburse_mxn_centavos - return_mxn_centavos
    if (usd or mxn) and not (usd > 0 and mxn < 0):
        raise ValidationFailure(
            "unreconciled_cash",
            "Check the receipts and cash returned. The remaining difference does not describe a USD-to-MXN settlement")
    seq = next_same_day_seq(conn, date, voucher_id, SAFE)
    proposed = []
    if reimburse_usd_cents or reimburse_mxn_centavos:
        proposed.append(Row("closeout-reimbursement", "closeout-reimbursement", date, seq, 0,
                            "addendum", SAFE, voucher_id, reimburse_usd_cents,
                            reimburse_mxn_centavos, voucher_id=voucher_id))
        seq += 1
    if usd or mxn:
        proposed.append(Row("closeout-exchange", "closeout-exchange", date, seq, 0,
                            "exchange", voucher_id, FX_COUNTERPARTY, usd, mxn,
                            voucher_id=voucher_id))
        seq += 1
    if return_usd_cents or return_mxn_centavos:
        proposed.append(Row("closeout-return", "closeout-return", date, seq, 0,
                            "addendum", voucher_id, SAFE, return_usd_cents,
                            return_mxn_centavos, voucher_id=voucher_id))
    try:
        settled = replay(rows + proposed, repo.load_accounts(conn))
    except ValidationError as exc:
        raise ValidationFailure("invalid_closeout", str(exc)) from exc
    final = settled.states[voucher_id]
    if final.usd or final.mxn or final.basis or any(
            claim.account == voucher_id for claim in settled.open_claims):
        raise ValidationFailure("unreconciled_cash", "The proposed settlement leaves unresolved cash or currency value")
    basis = (settled.movements["closeout-return"].basis_out_by_row["closeout-return"]
             if "closeout-return" in settled.movements else Decimal(0))
    expense = sum((movement.calc_usd_by_row.get(row.row_id, Decimal(0))
                   for row in related if row.txn_type == "expense"
                   for movement in [settled.movements[row.movement_id]]), Decimal(0))
    fingerprint = facts(conn, voucher_id)[1]
    revision = conn.execute("SELECT ledger_revision FROM ledger_state WHERE singleton_id=1").fetchone()[0]
    token = hashlib.sha256(json.dumps([fingerprint, revision, date,
                                      return_usd_cents, return_mxn_centavos, reimburse_usd_cents,
                                      reimburse_mxn_centavos, resolution_note]).encode()).hexdigest()
    return {"voucher_id": voucher_id, "date": date,
            "return_usd_cents": return_usd_cents, "return_mxn_centavos": return_mxn_centavos,
            "reimburse_usd_cents": reimburse_usd_cents, "reimburse_mxn_centavos": reimburse_mxn_centavos,
            "resolution_note": resolution_note,
            "requires_reimbursement_addendum": bool(reimburse_usd_cents or reimburse_mxn_centavos),
            "exchange_usd_cents": usd, "exchange_mxn_centavos": -mxn,
            "rate_mxn_per_usd": str(Decimal(-mxn) / Decimal(usd)) if usd else None,
            "returned_mxn_basis_usd": str(basis), "expense_value_usd": str(expense),
            "facts_sha256": fingerprint, "preview_token": token,
            "requires_return_addendum": bool(return_usd_cents or return_mxn_centavos)}


def confirm(conn, *, submission_id, voucher_id, date, preview_token, confirmed,
            return_usd_cents=0, return_mxn_centavos=0,
            reimburse_usd_cents=0, reimburse_mxn_centavos=0, resolution_note=""):
    from . import prep, signouts

    if confirmed is not True:
        raise ValidationFailure("confirmation_required", "Confirm that all spending and returned cash are accounted for")
    result = preview(conn, voucher_id=voucher_id, date=date,
                     return_usd_cents=return_usd_cents, return_mxn_centavos=return_mxn_centavos,
                     reimburse_usd_cents=reimburse_usd_cents, reimburse_mxn_centavos=reimburse_mxn_centavos,
                     resolution_note=resolution_note)
    if preview_token != result["preview_token"]:
        raise ValidationFailure("closeout_changed", "The ledger or return amounts changed; review a new closeout preview")
    reimbursed = None
    if result["requires_reimbursement_addendum"]:
        reimbursed = prep.prepare_addendum(
            conn, submission_id=submission_id, voucher_id=voucher_id, date=date,
            seq_no=None, kind="positive", reason="Personal spending reimbursed at expense report settlement",
            usd_cents=reimburse_usd_cents, mxn_centavos=reimburse_mxn_centavos)
    txn_id = None
    if result["exchange_usd_cents"]:
        row = Row("pending", str(uuid.uuid4()), date,
                  prep.next_same_day_seq(conn, date, voucher_id, SAFE), 0,
                  "exchange", voucher_id, FX_COUNTERPARTY,
                  result["exchange_usd_cents"], -result["exchange_mxn_centavos"],
                  voucher_id=voucher_id)
        txn_id = repo.insert_rows(conn, submission_id, [row])[0]
        conn.execute("UPDATE transactions SET description='Currency settlement at signout closeout'"
                     " WHERE txn_id=?", (txn_id,))
    returned = None
    if result["requires_return_addendum"]:
        returned = prep.prepare_addendum(
            conn, submission_id=submission_id, voucher_id=voucher_id, date=date,
            seq_no=None, kind="return", reason="Cash returned at signout closeout",
            usd_cents=return_usd_cents, mxn_centavos=return_mxn_centavos)
    serialized, fingerprint = facts(conn, voucher_id)
    closeout_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO signout_closeouts (closeout_id, voucher_id, submission_id,"
        " settlement_txn_id, return_addendum_id, facts_json, facts_sha256, reimbursement_addendum_id, resolution_note)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (closeout_id, voucher_id, submission_id, txn_id,
         returned["addendum_id"] if returned else None, serialized, fingerprint,
         reimbursed["addendum_id"] if reimbursed else None, resolution_note))
    summary = signouts.summary(conn, voucher_id)
    return {**result, "closeout_id": closeout_id, "summary": summary,
            "print_url": (returned or reimbursed or {}).get("print_url"),
            "print_urls": [item["print_url"] for item in (returned, reimbursed) if item]}
