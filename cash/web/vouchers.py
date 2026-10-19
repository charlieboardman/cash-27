"""Voucher preparation API + print pages (spec 8, 14)."""

from __future__ import annotations

from datetime import date as calendar_date
import uuid

from flask import Blueprint, current_app, g, redirect, render_template

from .. import prep, repo, voucher_defaults
from ..commands import run_command
from ..money import to_minor
from .app import admin_required, err, json_body, ok
from .qr_print import qr_svg

bp = Blueprint("vouchers", __name__)


def _prepare_with_approver(data, conn, submission_id, command_kind, prepare):
    def save(connection):
        root = current_app.config["DATA_ROOT"]
        name = voucher_defaults.approver_name(root, data.get("approver_name"))
        result = prepare(connection)
        voucher_defaults.save_selection(root, submission_id, name)
        return result
    return run_command(conn, submission_id, command_kind, save)


@bp.post("/api/vouchers/prepare")
@admin_required
def prepare():
    data = json_body("submission_id", "type", "date", "same_day_seq",
                     "recipient", "purpose", "notes", "usd", "mxn", "approver_name")
    usd = to_minor(data.get("usd"), "USD", allow_negative=False)
    mxn = to_minor(data.get("mxn"), "MXN", allow_negative=False)
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
    except (KeyError, ValueError) as e:
        return err("bad_request", f"Invalid field: {e}")
    if vtype not in ("direct_expense", "signout"):
        return err("bad_type", "direct_expense or signout", 400,
                   {"type": "invalid"})

    fy_month = current_app.config["FY_START_MONTH"]
    fn = (prep.prepare_direct_expense if vtype == "direct_expense"
          else prep.prepare_signout)
    result = _prepare_with_approver(
        data, g.db, sub, f"prepare_{vtype}",
        lambda c: fn(c, submission_id=sub, date=date, seq_no=seq_no,
                     recipient=recipient, purpose=purpose, notes=notes,
                     usd_cents=usd, mxn_centavos=mxn,
                     fy_start_month=fy_month))
    return ok(result)


@bp.post("/api/addenda/prepare")
@admin_required
def prepare_addendum():
    data = json_body("submission_id", "voucher_id", "kind", "date",
                     "same_day_seq", "reason", "usd", "mxn", "approver_name")
    usd = to_minor(data.get("usd"), "USD")
    mxn = to_minor(data.get("mxn"), "MXN")
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        date = data["date"]
        calendar_date.fromisoformat(date)
        seq_value = data.get("same_day_seq")
        seq_no = int(seq_value) if seq_value not in (None, "") else None
        kind = data["kind"]
        voucher_id = data["voucher_id"]
    except (KeyError, ValueError) as e:
        return err("bad_request", f"Invalid field: {e}")
    result = _prepare_with_approver(
        data, g.db, sub, f"prepare_addendum_{kind}",
        lambda c: prep.prepare_addendum(
            c, submission_id=sub, voucher_id=voucher_id,
            date=date, seq_no=seq_no,
            kind=kind, reason=data.get("reason", ""),
            usd_cents=usd, mxn_centavos=mxn))
    return ok(result)


@bp.patch("/api/vouchers/<voucher_id>/purpose")
@admin_required
def update_purpose(voucher_id):
    data = json_body("submission_id", "expected_version", "purpose")
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        expected_version = int(data["expected_version"])
    except (KeyError, TypeError, ValueError):
        return err("bad_request",
                   "Valid submission_id and expected_version required", 400)
    result = run_command(
        g.db, sub, "update_signout_purpose",
        lambda c: prep.update_signout_purpose(
            c, voucher_id=voucher_id, expected_version=expected_version,
            purpose=data.get("purpose")))
    return ok(result)


@bp.get("/transactions/new")
@admin_required
def new_transaction_page():
    return render_template("voucher.html", cash_accounts=repo.cash_accounts(g.db),
                           default_approver_name=voucher_defaults.approver_name(current_app.config["DATA_ROOT"]))


@bp.get("/transactions/<int:txn_id>")
@admin_required
def transaction_workspace(txn_id):
    """Stable entry point for the workflow surrounding a ledger row."""
    row = g.db.execute(
        "SELECT txn_type, voucher_id, addendum_id, posting_state FROM"
        " transactions WHERE txn_id=?", (txn_id,)).fetchone()
    if not row:
        return err("not_found", "Transaction not found", 404)
    if row["txn_type"] == "bank_withdrawal" and any(
            withdrawal["txn_id"] == txn_id
            for withdrawal in repo.pending_withdrawals(g.db)):
        return redirect(f"/review?item=withdrawal:{txn_id}")
    if row["txn_type"] == "exchange" and any(
            exchange["txn_id"] == txn_id
            for exchange in repo.pending_exchanges(g.db)):
        return redirect(f"/review?item=exchange:{txn_id}")
    if row["voucher_id"] and row["txn_type"] in ("signout", "addendum"):
        return redirect(f"/{row['voucher_id']}")
    if row["voucher_id"] and row["txn_type"] == "expense" and \
            row["posting_state"] == "active" and g.db.execute(
                "SELECT 1 FROM vouchers WHERE voucher_id=?"
                " AND type='direct_expense' AND status='pending'",
                (row["voucher_id"],)).fetchone():
        return redirect(f"/review?item=direct_expense:{row['voucher_id']}")
    return redirect(f"/ledger?txn={txn_id}")


@bp.get("/voucher/<voucher_id>/print")
@admin_required
def voucher_print(voucher_id):
    """Print page: prefilled from authoritative data; template tokens
    substituted in the browser (spec 8)."""
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
        "SELECT date, submission_id FROM transactions WHERE voucher_id=? AND"
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
        "APPROVER": voucher_defaults.selected_approver(current_app.config["DATA_ROOT"],
                                                       transaction["submission_id"] if transaction else None),
        "NOTES": v["notes"],
        "USD": f"{usd / 100:,.2f}" if usd else "",
        "MXN": f"{mxn / 100:,.2f}" if mxn else "",
        "DATE": transaction["date"] if transaction else v["created_at"][:10],
    }
    return render_template("print.html", voucher=dict(v),
                           tokens=ctx, document_qr=qr_svg(voucher_id),
                           document_code=voucher_id)


@bp.get("/voucher/<voucher_id>/addendum/<addendum_id>/print")
@admin_required
def addendum_print(voucher_id, addendum_id):
    a = g.db.execute(
        "SELECT a.*, v.recipient FROM addenda a JOIN vouchers v ON"
        " v.voucher_id = a.voucher_id WHERE a.addendum_id = ?"
        " AND a.voucher_id = ?", (addendum_id, voucher_id)).fetchone()
    if not a:
        return err("not_found", "Addendum not found", 404)
    t = g.db.execute(
        "SELECT date, usd_cents, mxn_centavos, submission_id FROM transactions WHERE"
        " addendum_id = ?", (addendum_id,)).fetchone()
    # Settlement records which positive addendum repaid personal spending.
    # Only additional cash puts money in the recipient's custody, so only
    # that form asks for the recipient's signature.
    if a["kind"] == "return":
        form, label = "return", "Cash returned"
    elif g.db.execute("SELECT 1 FROM signout_closeouts WHERE"
                      " reimbursement_addendum_id = ?", (addendum_id,)).fetchone():
        form, label = "reimbursement", "Reimbursement"
    else:
        form, label = "add_cash", "Additional cash authorized"
    ctx = {
        "ADDENDUM_ID": addendum_id,
        "VOUCHER_ID": voucher_id,
        "DATE": t["date"] if t else a["created_at"][:10],
        "RECIPIENT": a["recipient"],
        "REASON": a["reason"],
        "APPROVER": voucher_defaults.selected_approver(current_app.config["DATA_ROOT"],
                                                       t["submission_id"] if t else None),
        "KIND_LABEL": label,
        "USD": f"{t['usd_cents'] / 100:,.2f}" if t and t["usd_cents"] else "",
        "MXN": f"{t['mxn_centavos'] / 100:,.2f}" if t and t["mxn_centavos"]
               else "",
    }
    return render_template("addendum.html", tokens=ctx, addendum_form=form,
                           document_qr=qr_svg(addendum_id), document_code=addendum_id)
