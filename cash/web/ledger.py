"""Ledger view APIs (spec 10): read-only replay-order transaction
browsing, header balances, SAFE count comparison, corrections drill."""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from flask import Blueprint, g, render_template, request

from .. import history, repo
from ..commands import ValidationFailure, run_command
from ..model import SAFE
from ..prep import fiscal_year_for
from .app import ok, err, require_admin

bp = Blueprint("ledger", __name__)


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
        d["calc_usd"] = str(calculated) if calculated is not None else None
        rows.append(d)

    balances = {}
    for acct, st in res.states.items():
        balances[acct] = {"usd": st.usd / 100, "mxn": st.mxn / 100,
                          "basis_usd": str(st.basis)}
    open_signouts = [r["voucher_id"] for r in g.db.execute(
        "SELECT voucher_id FROM vouchers WHERE type='signout'"
        " AND status='pending'")]
    unallocated = g.db.execute(
        "SELECT COUNT(*) FROM transactions WHERE to_account="
        "'UNALLOCATED_EXPENSE' AND posting_state='active'").fetchone()[0]
    has_opening = bool(g.db.execute(
        "SELECT 1 FROM transactions WHERE txn_type='opening' AND"
        " posting_state='active' LIMIT 1").fetchone())
    return ok({"transactions": rows, "balances": balances,
               "has_opening": has_opening,
               "open_signouts": open_signouts,
               "outstanding_unallocated": unallocated,
               "open_claims": [{"account": c.account,
                                "mxn_unfunded": c.mxn_unfunded / 100}
                               for c in res.open_claims]})


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
            "SELECT d.document_id, d.page_count, d.doc_type, td.role"
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
    return ok({"transaction": dict(transaction), "documents": documents,
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
    if "receipt_face_value" in data:
        changes["receipt_face_minor"] = (
            _minor(data["receipt_face_value"], "receipt face")
            if data["receipt_face_value"] not in (None, "") else None)
        changes["receipt_face_currency"] = (
            data.get("receipt_face_currency") or None)
    if "assigned_rate" in data:
        try:
            changes["assigned_rate_milli"] = (
                int(Decimal(str(data["assigned_rate"])) * 1000)
                if data["assigned_rate"] not in (None, "") else None)
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


@bp.post("/api/ledger/safe-count")
def safe_count():
    """Compare a physical count to the books; nothing persisted
    (spec 13)."""
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    try:
        usd_value = Decimal(str(data.get("usd", "0")))
        mxn_value = Decimal(str(data.get("mxn", "0")))
        if usd_value.as_tuple().exponent < -2 or \
                mxn_value.as_tuple().exponent < -2:
            raise InvalidOperation
        usd = int(usd_value * 100)
        mxn = int(mxn_value * 100)
    except (InvalidOperation, ValueError):
        return err("bad_request", "Invalid amounts", 400)
    res = repo.replay_projected(g.db)
    st = res.states[SAFE]
    return ok({
        "book": {"usd": st.usd / 100, "mxn": st.mxn / 100,
                 "basis_usd": str(st.basis)},
        "count": {"usd": usd / 100, "mxn": mxn / 100},
        "delta": {"usd": (usd - st.usd) / 100,
                  "mxn": (mxn - st.mxn) / 100},
    })
