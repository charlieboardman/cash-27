"""Ledger view APIs (spec 10): read-only replay-order transaction
browsing and corrections drill. Current balances live on /balances."""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, g, render_template, request

from .. import history, repo, signouts
from ..commands import ValidationFailure, run_command
from ..prep import fiscal_year_for
from ..money import optional_minor, rate_text, to_minor, to_text
from .app import admin_required, err, json_body, ok

bp = Blueprint("ledger", __name__)


def _is_incomplete(transaction, voucher_statuses, addendum_statuses,
                   signout_summaries, pending_receipt_ids):
    if transaction["posting_state"] != "active":
        return False
    if transaction["txn_type"] in ("exchange", "bank_withdrawal"):
        return transaction["txn_id"] in pending_receipt_ids
    if transaction["addendum_id"]:
        return addendum_statuses.get(transaction["addendum_id"]) == "pending"
    if transaction["txn_type"] == "signout" and transaction["voucher_id"]:
        summary = signout_summaries.get(transaction["voucher_id"], {})
        return not summary.get("has_voucher", False)
    if transaction["voucher_id"] in signout_summaries:
        return transaction["to_account"] == "UNALLOCATED_EXPENSE"
    return (
        voucher_statuses.get(transaction["voucher_id"]) == "pending"
        or transaction["to_account"] == "UNALLOCATED_EXPENSE"
    )


@bp.get("/ledger")
@admin_required
def ledger_page():
    return render_template("ledger.html")


def _transaction_view(t, **extra):
    """One ledger row for the page: explicit fields, money as decimal strings."""
    return {
        "txn_id": t["txn_id"], "version": t["version"], "date": t["date"],
        "same_day_seq": t["same_day_seq"], "txn_type": t["txn_type"],
        "from_account": t["from_account"], "to_account": t["to_account"],
        "usd": to_text(t["usd_cents"]), "mxn": to_text(t["mxn_centavos"]),
        "class_code": t["class_code"],
        "receipt_face_usd": to_text(t["receipt_face_usd_cents"]),
        "receipt_face_mxn": to_text(t["receipt_face_mxn_centavos"]),
        "assigned_rate": rate_text(t["assigned_rate_milli"]),
        "voucher_id": t["voucher_id"], "addendum_id": t["addendum_id"],
        "receipt_id": t["receipt_id"], "recipient": t["recipient"],
        "vendor": t["vendor"], "description": t["description"],
        "project": t["project"], "notes": t["notes"],
        "posting_state": t["posting_state"], **extra,
    }


@bp.get("/api/ledger")
@admin_required
def ledger():
    res = repo.replay_projected(g.db)

    q = request.args
    fy = q.get("fy")
    account = q.get("account")
    voucher = q.get("voucher")
    ttype = q.get("type")
    class_code = q.get("class")
    fy_month = current_app.config["FY_START_MONTH"]

    voucher_statuses = {
        row["voucher_id"]: row["status"]
        for row in g.db.execute("SELECT voucher_id, status FROM vouchers")
    }
    addendum_statuses = {
        row["addendum_id"]: row["status"]
        for row in g.db.execute("SELECT addendum_id, status FROM addenda")
    }
    signout_states = signouts.summaries(g.db, res)
    pending_receipt_ids = {row["txn_id"] for row in
                            repo.pending_exchanges(g.db) + repo.pending_withdrawals(g.db)}
    rows = []
    for t in g.db.execute(
            "SELECT * FROM transactions ORDER BY date, same_day_seq,"
            " txn_id"):
        if fy:
            try:
                if fiscal_year_for(t["date"], fy_month) != int(fy):
                    continue
            except ValueError:
                return err("bad_fiscal_year", "Fiscal year must be a number", 400)
        if account and account not in (t["from_account"], t["to_account"]):
            continue
        if voucher and t["voucher_id"] != voucher:
            continue
        if ttype and t["txn_type"] != ttype:
            continue
        if class_code and t["class_code"] != class_code:
            continue
        mid = t["movement_id"]
        mres = res.movements.get(mid)
        calculated = (mres.calc_usd_by_row.get(str(t["txn_id"]))
                      if mres else None)
        if mres and str(t["txn_id"]) in mres.unresolved_rows:
            calculated = None
        rows.append(_transaction_view(
            t, calc_usd=str(calculated) if calculated is not None else None,
            incomplete=_is_incomplete(t, voucher_statuses, addendum_statuses,
                                      signout_states, pending_receipt_ids)))

    has_opening = bool(g.db.execute(
        "SELECT 1 FROM transactions WHERE txn_type='opening' AND"
        " posting_state='active' LIMIT 1").fetchone())

    return ok({"transactions": rows, "has_opening": has_opening})


@bp.get("/api/ledger/<int:txn_id>")
@admin_required
def transaction_detail(txn_id):
    transaction = g.db.execute(
        "SELECT * FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()
    if not transaction:
        return err("not_found", "Transaction not found", 404)
    documents = [dict(row) | {
        "stream_url": f"/api/documents/{row['document_id']}/stream"}
        for row in g.db.execute(
            "SELECT d.document_id, d.display_name, d.page_count,"
            " d.doc_type, td.role"
            " FROM transaction_documents td JOIN documents d ON"
            " d.document_id=td.document_id WHERE td.txn_id=?"
            " ORDER BY td.position, d.created_at", (txn_id,))]
    edits = [dict(row) for row in g.db.execute(
        "SELECT edit_id, reason, created_at FROM transaction_edits"
        " WHERE txn_id=? ORDER BY edit_id DESC", (txn_id,))]
    accounts = [dict(row) for row in g.db.execute(
        "SELECT account_id, name, kind FROM accounts WHERE active=1"
        " ORDER BY kind, name")]
    classes = [dict(row) for row in g.db.execute(
        "SELECT class_code, name FROM classes WHERE active=1 ORDER BY name")]
    voucher_statuses = {}
    if transaction["voucher_id"]:
        voucher = g.db.execute(
            "SELECT status FROM vouchers WHERE voucher_id=?",
            (transaction["voucher_id"],)).fetchone()
        if voucher:
            voucher_statuses[transaction["voucher_id"]] = voucher["status"]
    addendum_statuses = {}
    if transaction["addendum_id"]:
        addendum = g.db.execute(
            "SELECT status FROM addenda WHERE addendum_id=?",
            (transaction["addendum_id"],)).fetchone()
        if addendum:
            addendum_statuses[transaction["addendum_id"]] = addendum["status"]
    signout_states = signouts.summaries(g.db)
    transaction_data = _transaction_view(transaction, incomplete=_is_incomplete(
        transaction, voucher_statuses, addendum_statuses,
        signout_states, {row["txn_id"] for row in
                        repo.pending_exchanges(g.db) + repo.pending_withdrawals(g.db)}))
    return ok({"transaction": transaction_data, "documents": documents,
               "edits": edits, "accounts": accounts, "classes": classes})


@bp.patch("/api/ledger/<int:txn_id>")
@admin_required
def edit_transaction(txn_id):
    data = json_body("submission_id", "expected_version", "reason", "date",
                     "same_day_seq", "from_account", "to_account", "usd", "mxn",
                     "class_code", "receipt_face_usd", "receipt_face_mxn",
                     "assigned_rate", "recipient", "vendor", "description",
                     "project", "notes")
    try:
        submission = str(uuid.UUID(data["submission_id"]))
        expected_version = int(data["expected_version"])
    except (KeyError, ValueError, TypeError) as exc:
        return err("bad_request", f"Invalid command metadata: {exc}", 400)
    changes = {}
    direct = ("date", "from_account", "to_account", "recipient", "vendor",
              "description", "project", "notes")
    for field in direct:
        if field in data:
            changes[field] = data[field]
    if "same_day_seq" in data:
        try:
            changes["same_day_seq"] = int(data["same_day_seq"])
        except (ValueError, TypeError) as exc:
            raise ValidationFailure(
                "bad_sequence", "Same-day sequence must be an integer") from exc
    if "usd" in data:
        changes["usd_cents"] = to_minor(data["usd"], "USD")
    if "mxn" in data:
        changes["mxn_centavos"] = to_minor(data["mxn"], "MXN")
    if "class_code" in data:
        changes["class_code"] = data["class_code"] or None
    if "receipt_face_usd" in data or "receipt_face_mxn" in data:
        face_usd = optional_minor(data.get("receipt_face_usd"), "USD receipt value",
                                  allow_negative=False)
        face_mxn = optional_minor(data.get("receipt_face_mxn"), "MXN receipt value",
                                  allow_negative=False)
        changes["receipt_face_usd_cents"] = face_usd
        changes["receipt_face_mxn_centavos"] = face_mxn
    if "assigned_rate" in data:
        try:
            rate = Decimal(str(data["assigned_rate"])) \
                if data["assigned_rate"] not in (None, "") else None
            if rate is not None and not rate.is_finite():
                raise ValueError
            assigned_rate = int(rate * 1000) if rate is not None else None
            if assigned_rate is not None and assigned_rate <= 0:
                raise ValueError
            changes["assigned_rate_milli"] = assigned_rate
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValidationFailure(
                "bad_rate", "Assigned rate must be a number") from exc
    result = run_command(
        g.db, submission, "edit_transaction",
        lambda connection: history.edit_transaction(
            connection, submission_id=submission, txn_id=txn_id,
            expected_version=expected_version, changes=changes,
            reason=data.get("reason", "")))
    return ok(result)


@bp.post("/api/ledger/<int:txn_id>/void")
@admin_required
def void_transaction(txn_id):
    data = json_body("submission_id", "expected_version", "reason")
    try:
        submission = str(uuid.UUID(data["submission_id"]))
        expected_version = int(data["expected_version"])
    except (KeyError, ValueError, TypeError) as exc:
        return err("bad_request", f"Invalid command metadata: {exc}", 400)
    result = run_command(
        g.db, submission, "void_transaction",
        lambda connection: history.void_transaction(
            connection, submission_id=submission, txn_id=txn_id,
            expected_version=expected_version,
            reason=data.get("reason", "")))
    return ok(result)
