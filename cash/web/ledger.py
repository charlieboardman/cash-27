"""Ledger view APIs (spec 10): read-only replay-order transaction
browsing and corrections drill. Current balances live on /balances."""

from __future__ import annotations

import uuid
from decimal import Decimal

from flask import Blueprint, g, render_template, request

from .. import history, repo, signouts
from ..commands import ValidationFailure, run_command
from ..prep import fiscal_year_for
from .app import ok, err, require_admin

bp = Blueprint("ledger", __name__)


def _is_incomplete(transaction, voucher_statuses, addendum_statuses,
                   signout_summaries, pending_exchange_ids):
    if transaction["posting_state"] != "active":
        return False
    if transaction["txn_type"] == "exchange":
        return transaction["txn_id"] in pending_exchange_ids
    if transaction["addendum_id"]:
        return addendum_statuses.get(transaction["addendum_id"]) == "pending"
    if transaction["txn_type"] == "signout" and transaction["voucher_id"]:
        summary = signout_summaries.get(transaction["voucher_id"], {})
        return not summary.get("has_voucher", False)
    return (
        voucher_statuses.get(transaction["voucher_id"]) == "pending"
        or transaction["to_account"] == "UNALLOCATED_EXPENSE"
    )


@bp.get("/ledger")
def ledger_page():
    guard = require_admin()
    if guard:
        return guard
    return render_template("ledger.html")


@bp.get("/api/ledger")
def ledger():
    guard = require_admin()
    if guard:
        return guard
    res = repo.replay_projected(g.db)

    q = request.args
    fy = q.get("fy")
    account = q.get("account")
    voucher = q.get("voucher")
    ttype = q.get("type")
    class_code = q.get("class")

    voucher_statuses = {
        row["voucher_id"]: row["status"]
        for row in g.db.execute("SELECT voucher_id, status FROM vouchers")
    }
    addendum_statuses = {
        row["addendum_id"]: row["status"]
        for row in g.db.execute("SELECT addendum_id, status FROM addenda")
    }
    signout_states = signouts.summaries(g.db, res)
    pending_exchange_ids = {row["txn_id"] for row in repo.pending_exchanges(g.db)}
    rows = []
    for t in g.db.execute(
            "SELECT * FROM transactions ORDER BY date, same_day_seq,"
            " txn_id"):
        if fy:
            try:
                if fiscal_year_for(t["date"]) != int(fy):
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
        d = dict(t)
        d["usd"] = t["usd_cents"] / 100
        d["mxn"] = t["mxn_centavos"] / 100
        mid = t["movement_id"]
        mres = res.movements.get(mid)
        calculated = (mres.calc_usd_by_row.get(str(t["txn_id"]))
                      if mres else None)
        if mres and str(t["txn_id"]) in mres.unresolved_rows:
            calculated = None
        d["calc_usd"] = str(calculated) if calculated is not None else None
        d["incomplete"] = _is_incomplete(
            t, voucher_statuses, addendum_statuses,
            signout_states, pending_exchange_ids)
        rows.append(d)

    has_opening = bool(g.db.execute(
        "SELECT 1 FROM transactions WHERE txn_type='opening' AND"
        " posting_state='active' LIMIT 1").fetchone())

    return ok({"transactions": rows, "has_opening": has_opening})


@bp.get("/api/ledger/<int:txn_id>")
def transaction_detail(txn_id):
    guard = require_admin()
    if guard:
        return guard
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
    transaction_data = dict(transaction)
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
    transaction_data["incomplete"] = _is_incomplete(
        transaction, voucher_statuses, addendum_statuses,
        signout_states, {row["txn_id"] for row in repo.pending_exchanges(g.db)})
    return ok({"transaction": transaction_data, "documents": documents,
               "edits": edits, "accounts": accounts, "classes": classes})


def _minor(value, field):
    try:
        decimal = Decimal(str(value or "0"))
    except InvalidOperation as exc:
        raise ValidationFailure("bad_amount", f"Invalid {field}") from exc
    if decimal.as_tuple().exponent < -2:
        raise ValidationFailure("bad_amount", f"{field} has too many decimals")
    return int(decimal * 100)


@bp.patch("/api/ledger/<int:txn_id>")
def edit_transaction(txn_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
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
        changes["usd_cents"] = _minor(data["usd"], "USD")
    if "mxn" in data:
        changes["mxn_centavos"] = _minor(data["mxn"], "MXN")
    if "class_code" in data:
        changes["class_code"] = data["class_code"] or None
    if "receipt_face_usd" in data or "receipt_face_mxn" in data:
        face_usd = (_minor(data.get("receipt_face_usd"), "USD receipt value")
                    if data.get("receipt_face_usd") not in (None, "") else None)
        face_mxn = (_minor(data.get("receipt_face_mxn"), "MXN receipt value")
                    if data.get("receipt_face_mxn") not in (None, "") else None)
        if (face_usd is not None and face_usd < 0) or \
                (face_mxn is not None and face_mxn < 0):
            raise ValidationFailure("bad_amount",
                                    "Receipt values cannot be negative")
        changes["receipt_face_usd_cents"] = face_usd
        changes["receipt_face_mxn_centavos"] = face_mxn
        # Retain the old singular representation when there is only one
        # currency, so existing reports and integrations remain readable.
        if face_usd is not None and face_mxn is None:
            changes["receipt_face_minor"] = face_usd
            changes["receipt_face_currency"] = "USD"
        elif face_mxn is not None and face_usd is None:
            changes["receipt_face_minor"] = face_mxn
            changes["receipt_face_currency"] = "MXN"
        else:
            changes["receipt_face_minor"] = None
            changes["receipt_face_currency"] = None
    elif "receipt_face_value" in data:
        changes["receipt_face_minor"] = (
            _minor(data["receipt_face_value"], "receipt face")
            if data["receipt_face_value"] not in (None, "") else None)
        changes["receipt_face_currency"] = (
            data.get("receipt_face_currency") or None)
    if "assigned_rate" in data:
        try:
            assigned_rate = (
                int(Decimal(str(data["assigned_rate"])) * 1000)
                if data["assigned_rate"] not in (None, "") else None)
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
def void_transaction(txn_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
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
