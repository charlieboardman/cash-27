"""Voucher preparation API + print pages (spec 8, 14)."""

from __future__ import annotations

from datetime import date as calendar_date
import uuid
from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, g, redirect, render_template, request

from .. import prep, repo, signouts
from ..commands import run_command
from .app import ok, err, require_admin

bp = Blueprint("vouchers", __name__)


def _cents(data, name):
    """Decimal-string dollars -> integer cents."""
    v = data.get(name, "0") or "0"
    try:
        value = Decimal(str(v))
        if value.as_tuple().exponent < -2:
            raise ValueError(name)
        return int(value * 100)
    except (InvalidOperation, ValueError):
        raise ValueError(name)


@bp.post("/api/vouchers/prepare")
def prepare():
    guard = require_admin()
    if guard:
        return guard
    allowed = {"submission_id", "type", "date", "same_day_seq",
               "recipient", "purpose", "notes", "usd", "mxn",
               "face_value", "face_currency", "usd_paid", "mxn_paid",
               "mxn_change", "usd_change", "receipt_usd", "receipt_mxn"}
    data = request.get_json(force=True, silent=False)
    unknown = set(data) - allowed
    if unknown:
        return err("unknown_fields", f"Unknown fields: {sorted(unknown)}",
                   400, {k: "unknown" for k in unknown})
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        vtype = data["type"]
        date = data["date"]
        calendar_date.fromisoformat(date)
        seq_value = data.get("same_day_seq")
        seq_no = int(seq_value) if seq_value not in (None, "") else None
        recipient = data.get("recipient", "")
        purpose = data.get("purpose", "")
        notes = data.get("notes", "")
        if vtype == "direct_expense":
            required = ("usd_paid", "mxn_paid")
            missing = [name for name in required if name not in data]
            if missing:
                raise ValueError(f"required direct-expense facts: {missing}")
            # New clients provide separate components.  Accept the old
            # singular pair too, so a previously printed/offline form does
            # not become unusable on deployment day.
            if "receipt_usd" in data or "receipt_mxn" in data:
                receipt_usd = _cents(data, "receipt_usd")
                receipt_mxn = _cents(data, "receipt_mxn")
            else:
                legacy_face = _cents(data, "face_value")
                legacy_currency = data.get("face_currency")
                receipt_usd = legacy_face if legacy_currency == "USD" else 0
                receipt_mxn = legacy_face if legacy_currency == "MXN" else 0
            if receipt_usd < 0 or receipt_mxn < 0 or \
                    (receipt_usd == 0 and receipt_mxn == 0):
                raise ValueError("positive receipt value required")
            face = None
            face_currency = None
            if receipt_usd and not receipt_mxn:
                face, face_currency = receipt_usd, "USD"
            elif receipt_mxn and not receipt_usd:
                face, face_currency = receipt_mxn, "MXN"
            usd_paid = _cents(data, "usd_paid")
            mxn_paid = _cents(data, "mxn_paid")
            mxn_change = _cents(data, "mxn_change")
            usd_change = _cents(data, "usd_change")
            usd = usd_paid - usd_change
            mxn = mxn_paid - mxn_change
        else:
            face = None
            face_currency = None
            usd_paid = mxn_paid = None
            mxn_change = usd_change = 0
            usd = _cents(data, "usd")
            mxn = _cents(data, "mxn")
    except (KeyError, ValueError) as e:
        return err("bad_request", f"Invalid field: {e}")
    if vtype not in ("direct_expense", "signout"):
        return err("bad_type", "direct_expense or signout", 400,
                   {"type": "invalid"})

    fy_month = current_app.config["FY_START_MONTH"]
    fn = (prep.prepare_direct_expense if vtype == "direct_expense"
          else prep.prepare_signout)
    direct_args = ({
        "face_value_minor": face, "face_currency": face_currency,
        "face_usd_cents": receipt_usd, "face_mxn_centavos": receipt_mxn,
        "usd_paid_cents": usd_paid, "mxn_paid_centavos": mxn_paid,
        "mxn_change_centavos": mxn_change,
        "usd_change_cents": usd_change,
    } if vtype == "direct_expense" else {})
    result = run_command(
        g.db, sub, f"prepare_{vtype}",
        lambda c: fn(c, submission_id=sub, date=date, seq_no=seq_no,
                     recipient=recipient, purpose=purpose, notes=notes,
                     usd_cents=usd, mxn_centavos=mxn,
                     fy_start_month=fy_month, **direct_args))
    return ok(result)


@bp.post("/api/addenda/prepare")
def prepare_addendum():
    guard = require_admin()
    if guard:
        return guard
    allowed = {"submission_id", "voucher_id", "kind", "date",
               "same_day_seq", "reason", "usd", "mxn"}
    data = request.get_json(force=True)
    unknown = set(data) - allowed
    if unknown:
        return err("unknown_fields", f"Unknown fields: {sorted(unknown)}",
                   400, {k: "unknown" for k in unknown})
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        usd = _cents(data, "usd")
        mxn = _cents(data, "mxn")
        date = data["date"]
        calendar_date.fromisoformat(date)
        seq_value = data.get("same_day_seq")
        seq_no = int(seq_value) if seq_value not in (None, "") else None
        kind = data["kind"]
        voucher_id = data["voucher_id"]
    except (KeyError, ValueError) as e:
        return err("bad_request", f"Invalid field: {e}")
    result = run_command(
        g.db, sub, f"prepare_addendum_{kind}",
        lambda c: prep.prepare_addendum(
            c, submission_id=sub, voucher_id=voucher_id,
            date=date, seq_no=seq_no,
            kind=kind, reason=data.get("reason", ""),
            usd_cents=usd, mxn_centavos=mxn))
    return ok(result)


@bp.get("/api/signouts/<voucher_id>")
def signout_detail(voucher_id):
    guard = require_admin()
    if guard:
        return guard
    voucher = g.db.execute(
        "SELECT * FROM vouchers WHERE voucher_id=? AND type='signout'",
        (voucher_id,)).fetchone()
    if not voucher:
        return err("not_found", "Signout not found", 404)
    replayed = repo.replay_projected(g.db)
    derived = signouts.summary(g.db, voucher_id, replayed)
    movements = [dict(row) for row in g.db.execute(
        "SELECT txn_id, movement_id, date, same_day_seq, txn_type,"
        " from_account, to_account, usd_cents, mxn_centavos, receipt_id,"
        " addendum_id, description, posting_state FROM transactions"
        " WHERE voucher_id=? ORDER BY date, same_day_seq, txn_id",
        (voucher_id,))]
    addenda = [dict(row) for row in g.db.execute(
        "SELECT a.*, t.txn_id, t.date, t.usd_cents, t.mxn_centavos"
        " FROM addenda a JOIN transactions t ON"
        " t.addendum_id=a.addendum_id WHERE a.voucher_id=?"
        " ORDER BY a.seq, t.txn_id", (voucher_id,))]
    packages = [dict(row) for row in g.db.execute(
        "SELECT p.*, COUNT(c.claim_id) AS receipt_count FROM"
        " signout_reconciliation_packages p LEFT JOIN"
        " signout_expense_claims c ON c.package_id=p.package_id"
        " WHERE p.voucher_id=? GROUP BY p.package_id ORDER BY p.seq",
        (voucher_id,))]
    documents = [dict(row) | {
        "stream_url": f"/api/documents/{row['document_id']}/stream"}
        for row in g.db.execute(
            "SELECT DISTINCT d.document_id, d.display_name, d.doc_type,"
            " d.page_count, d.created_at FROM documents d JOIN"
            " transaction_documents td ON td.document_id=d.document_id"
            " JOIN transactions t ON t.txn_id=td.txn_id"
            " WHERE t.voucher_id=? ORDER BY d.created_at", (voucher_id,))]
    return ok({"voucher": dict(voucher), "summary": derived,
               "movements": movements, "addenda": addenda,
               "packages": packages, "documents": documents})


@bp.get("/signouts/<voucher_id>")
def signout_workspace(voucher_id):
    guard = require_admin()
    if guard:
        return guard
    if not g.db.execute(
            "SELECT 1 FROM vouchers WHERE voucher_id=? AND type='signout'",
            (voucher_id,)).fetchone():
        return err("not_found", "Signout not found", 404)
    return render_template("signout_admin.html", voucher_id=voucher_id)


@bp.get("/voucher")
def voucher_page():
    guard = require_admin()
    if guard:
        return guard
    return render_template("voucher.html")


@bp.get("/transactions/new")
def new_transaction_page():
    guard = require_admin()
    if guard:
        return guard
    return render_template("voucher.html")


@bp.get("/transactions/<int:txn_id>")
def transaction_workspace(txn_id):
    """Stable entry point for the workflow surrounding a ledger row."""
    guard = require_admin()
    if guard:
        return guard
    row = g.db.execute(
        "SELECT txn_type, voucher_id, addendum_id, posting_state FROM"
        " transactions WHERE txn_id=?", (txn_id,)).fetchone()
    if not row:
        return err("not_found", "Transaction not found", 404)
    if row["voucher_id"] and row["txn_type"] in ("signout", "addendum"):
        return redirect(f"/signouts/{row['voucher_id']}")
    if row["voucher_id"] and row["txn_type"] == "expense" and \
            row["posting_state"] == "active" and g.db.execute(
                "SELECT 1 FROM vouchers WHERE voucher_id=?"
                " AND type='direct_expense' AND status='pending'",
                (row["voucher_id"],)).fetchone():
        return redirect(f"/review?item=direct_expense:{row['voucher_id']}")
    return redirect(f"/ledger?txn={txn_id}")


@bp.get("/voucher/<voucher_id>/print")
def voucher_print(voucher_id):
    """Print page: prefilled from authoritative data; template tokens
    substituted in the browser (spec 8)."""
    guard = require_admin()
    if guard:
        return guard
    v = g.db.execute(
        "SELECT * FROM vouchers WHERE voucher_id = ?",
        (voucher_id,)).fetchone()
    if not v:
        return err("not_found", "Voucher not found", 404)
    # The voucher prints only what it authorized. Addenda and accepted
    # claim expenses carry the same voucher_id but are separate
    # documents; summing all active rows would inflate the paper.
    usd = v["authorized_usd_cents"] or 0
    mxn = v["authorized_mxn_centavos"] or 0
    transaction = g.db.execute(
        "SELECT date FROM transactions WHERE voucher_id=? AND"
        " txn_type IN ('expense','signout') ORDER BY txn_id LIMIT 1",
        (voucher_id,)).fetchone()
    ctx = {
        "VOUCHER_ID": voucher_id,
        "TYPE": v["type"],
        "RECIPIENT": v["recipient"],
        "SIGNOUT_RECIPIENT": (v["recipient"]
                              if v["type"] == "signout" else ""),
        "VENDOR": (v["recipient"]
                   if v["type"] == "direct_expense" else ""),
        "PURPOSE": v["purpose"],
        "NOTES": v["notes"],
        "USD": f"{usd / 100:.2f}" if usd else "",
        "MXN": f"{mxn / 100:.2f}" if mxn else "",
        "FACE_VALUE": (f"{v['face_value_minor'] / 100:.2f}"
                       if v["face_value_minor"] else ""),
        "FACE_CURRENCY": v["face_currency"] or "",
        "FACE_USD": (f"{v['face_usd_cents'] / 100:.2f}"
                     if v["face_usd_cents"] else ""),
        "FACE_MXN": (f"{v['face_mxn_centavos'] / 100:.2f}"
                     if v["face_mxn_centavos"] else ""),
        "USD_PAID": (f"{v['usd_paid_cents'] / 100:.2f}"
                     if v["usd_paid_cents"] else ""),
        "MXN_PAID": (f"{v['mxn_paid_centavos'] / 100:.2f}"
                     if v["mxn_paid_centavos"] else ""),
        "MXN_CHANGE": (f"{v['mxn_change_centavos'] / 100:.2f}"
                       if v["mxn_change_centavos"] else ""),
        "USD_CHANGE": (f"{v['usd_change_cents'] / 100:.2f}"
                       if v["usd_change_cents"] else ""),
        "DATE": transaction["date"] if transaction else v["created_at"][:10],
    }
    return render_template("print.html", voucher=dict(v),
                           tokens=ctx)


@bp.get("/voucher/<voucher_id>/addendum/<addendum_id>/print")
def addendum_print(voucher_id, addendum_id):
    guard = require_admin()
    if guard:
        return guard
    a = g.db.execute(
        "SELECT a.*, v.recipient FROM addenda a JOIN vouchers v ON"
        " v.voucher_id = a.voucher_id WHERE a.addendum_id = ?"
        " AND a.voucher_id = ?", (addendum_id, voucher_id)).fetchone()
    if not a:
        return err("not_found", "Addendum not found", 404)
    t = g.db.execute(
        "SELECT date, usd_cents, mxn_centavos FROM transactions WHERE"
        " addendum_id = ?", (addendum_id,)).fetchone()
    ctx = {
        "ADDENDUM_ID": addendum_id,
        "VOUCHER_ID": voucher_id,
        "DATE": t["date"] if t else a["created_at"][:10],
        "RECIPIENT": a["recipient"],
        "REASON": a["reason"],
        "KIND_LABEL": ("Additional cash authorized"
                       if a["kind"] == "positive" else "Cash returned"),
        "USD": f"{t['usd_cents'] / 100:.2f}" if t and t["usd_cents"] else "",
        "MXN": f"{t['mxn_centavos'] / 100:.2f}" if t and t["mxn_centavos"]
               else "",
    }
    return render_template("addendum.html", tokens=ctx)
