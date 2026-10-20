"""Tray commands for cash paperwork and composite purchase log rows."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from flask import Blueprint, current_app, g, render_template, request, session

from .. import expense_drafts, paper, prep, report_rows, voucher_defaults
from ..commands import ValidationFailure, run_command
from ..money import to_minor
from .app import admin_required, client_today, err
from .documents import store_document
from .qr_print import qr_svg

bp = Blueprint("report_rows", __name__)


def _access(vid):
    from .signee import _signee_guard

    return _signee_guard(vid)[1]


def _response(vid):
    from .app import ok
    from .signee import _report_view, _voucher_or_none

    return ok(_report_view(vid, _voucher_or_none(vid)))


def _command(vid, name, action):
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        raise ValidationFailure("bad_request", "Expected an object")
    supplied = data.get("submission_id") or request.form.get("submission_id")
    try:
        submission = str(uuid.UUID(supplied)) if supplied else str(uuid.uuid4())
    except (ValueError, TypeError, AttributeError):
        raise ValidationFailure("bad_request", "Invalid submission ID")
    run_command(g.db, submission, name, lambda conn: action(conn, data))
    return _response(vid)


def _upload(vid):
    files = request.files.getlist("files")
    if not files:
        raise ValidationFailure("document_required", "Choose a signed document")
    state = "inbox" if session.get("admin") else "signee_draft"
    return store_document(
        g.db, current_app.config["DATA_ROOT"], files, state=state, voucher_id=vid
    )[0]


@bp.post("/<vid>/api/settlement/prepare/<kind>")
@admin_required
def prepare_settlement(vid, kind):
    failure = _access(vid)
    if failure is not None:
        return failure
    return _command(
        vid,
        "prepare_settlement_paper",
        lambda conn, data: report_rows.prepare_settlement(
            conn,
            vid,
            kind,
            date=data.get("date")
            or client_today()
            or datetime.now(current_app.config["TIMEZONE"]).date().isoformat(),
            approver_name=voucher_defaults.approver_name(
                current_app.config["DATA_ROOT"]
            ),
        ),
    )


@bp.post("/<vid>/api/settlement/file/<kind>")
@admin_required
def file_settlement(vid, kind):
    failure = _access(vid)
    if failure is not None:
        return failure
    document_id = (request.get_json(silent=True) or {}).get("document_id") or _upload(
        vid
    )
    return _command(
        vid,
        "file_settlement_paper",
        lambda conn, data: (
            report_rows.file_settlement(conn, vid, kind, document_id) or {}
        ),
    )


@bp.get("/<vid>/settlement/print/<kind>")
@admin_required
def print_settlement(vid, kind):
    failure = _access(vid)
    if failure is not None:
        return failure
    stage = next(
        (s for s in report_rows.settlement_stages(g.db, vid) if s["kind"] == kind), None
    )
    if not stage or stage["status"] in ("calculated", "outdated"):
        return err(
            "paperwork_needed", "Generate current settlement paperwork first", 422
        )
    recipient = g.db.execute(
        "SELECT recipient FROM vouchers WHERE voucher_id=?", (vid,)
    ).fetchone()[0]
    draft, _ = expense_drafts.load(g.db, vid)
    rounding = [r for r in draft["rows"] if r.get("rounding") == kind and not r["posted"]]
    breakdown = []
    for row in rounding:
        currency = "usd" if row["usd"] else "mxn"
        amount = row[currency]
        settled = to_minor(stage[currency], currency.upper())
        base = settled - amount if kind == "positive" else settled + amount
        label = "Reimbursement" if kind == "positive" else "Return"
        sign = "+" if kind == "positive" else "−"
        breakdown.append(f"{label} {currency.upper()} {Decimal(base) / 100:,.2f} {sign} rounding {currency.upper()} {Decimal(amount) / 100:,.2f} ({expense_drafts.receipt_reference(vid, row)})")
    reason = paper.check("; ".join(breakdown), "reason", "The reason", paper.REASON)
    tokens = {
        "ADDENDUM_ID": stage["paper_id"],
        "VOUCHER_ID": vid,
        "DATE": stage["date"],
        "RECIPIENT": recipient,
        "APPROVER": stage["approver_name"],
        "REASON": reason,
        "USD": stage["usd"],
        "MXN": stage["mxn"],
        "KIND_LABEL": "Reimbursement / Reembolso"
        if kind == "positive"
        else "Cash returned / Efectivo devuelto",
    }
    return render_template(
        "addendum.html",
        tokens=tokens, rounding=bool(rounding),
        addendum_form="reimbursement" if kind == "positive" else "return",
        document_qr=qr_svg(stage["paper_id"]),
        document_code=stage["paper_id"],
    )


@bp.post("/<vid>/api/cash-rows/<key>")
@admin_required
def attach_cash_row(vid, key):
    failure = _access(vid)
    if failure is not None:
        return failure
    document_id = (request.get_json(silent=True) or {}).get("document_id") or _upload(
        vid
    )

    def attach(conn, data):
        draft, _ = expense_drafts.load(conn, vid)
        expense_drafts._editable(conn, vid, draft, admin=True)
        if key == "voucher":
            row = conn.execute(
                "SELECT version FROM vouchers WHERE voucher_id=?", (vid,)
            ).fetchone()
            return prep.attach_signout_voucher(
                conn,
                voucher_id=vid,
                expected_version=row["version"],
                document_id=document_id,
            )
        row = conn.execute(
            "SELECT version FROM addenda WHERE voucher_id=? AND addendum_id=?",
            (vid, key),
        ).fetchone()
        if not row:
            raise ValidationFailure("not_found", "Addendum not found")
        return prep.complete_addendum_document(
            conn,
            addendum_id=key,
            expected_version=row["version"],
            document_id=document_id,
        )

    return _command(vid, "attach_cash_row", attach)


@bp.route("/<vid>/api/purchase-log", methods=["POST", "PATCH", "DELETE"])
def purchase_log(vid):
    failure = _access(vid)
    if failure is not None:
        return failure
    document_id = _upload(vid) if request.files.getlist("files") else None

    def save(conn, data):
        doc = document_id
        if data.get("attach_matched"):
            if not session.get("admin"):
                raise ValidationFailure(
                    "forbidden", "Only an administrator can file signed scans"
                )
            matched = conn.execute(
                "SELECT q.document_id FROM document_qr q JOIN documents d USING(document_id)"
                " WHERE q.voucher_id=? AND q.review_kind='log' AND q.status='matched' AND d.state='inbox'"
                " ORDER BY d.created_at DESC LIMIT 1",
                (vid,),
            ).fetchone()
            if not matched:
                raise ValidationFailure("not_found", "No matched purchase log scan")
            doc = matched["document_id"]
        if set(data) - {
            "submission_id",
            "items",
            "rev",
            "class_code",
            "expense_account",
            "attach_matched",
        }:
            raise ValidationFailure("unknown_fields", "Unknown purchase log fields")
        return report_rows.save_log(
            conn,
            vid,
            admin=bool(session.get("admin")),
            data=data,
            document_id=doc,
            deleting=request.method == "DELETE",
        )

    return _command(vid, "save_purchase_log", save)
