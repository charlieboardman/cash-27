"""The expense report's HTML interface: forms in, fragments out.

Every route is a thin adapter over the commands the JSON API already uses
(`expense_drafts`, `run_command`): parse the form, run the same command, and
render the fresh report view. No rule lives here. Draft saves answer with
small out-of-band fragments (`expense/refresh.html`) so the server stays the
authority without replacing what the person is typing; rarer actions answer
with a page refresh. Labels in fragments are English keys the browser
translates (`data-i18n`), the same as the rest of the page."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import uuid

from flask import Blueprint, current_app, g, make_response, render_template, request, session

from .. import document_jobs, expense_drafts, prep, voucher_defaults
from ..commands import ValidationFailure, run_command
from ..money import to_minor
from .app import admin_required, client_today, err
from .signee import _approval, _report_view, _row_fields, _signee_guard
from .vouchers import _prepare_with_approver

bp = Blueprint("expense_ui", __name__)
BASE = "/FY<fy>-<num>/ui"

# Names shown for voucher statuses, signout reasons, and document types. The
# JSON pages keep their own copy in common.js until they are converted.
STATUS_LABELS = {"draft": "Draft", "pending": "Open", "open": "Open", "submitted": "Submitted",
                 "approved": "Approved", "complete": "Complete", "closed": "Closed",
                 "void": "Void", "superseded": "Replaced"}
REASON_LABELS = {"cash remaining": "Cash remaining", "currency value needs reconciliation": "USD value unresolved",
                 "voucher needed": "Needs signed voucher", "addendum needed": "Needs signed addendum",
                 "receipts in progress": "Receipts in progress",
                 "receipt submission in progress": "Receipts awaiting review",
                 "closeout needed": "Needs settlement"}
DOCUMENT_KINDS = {"voucher": "Voucher", "receipt": "Receipt", "addendum": "Addendum",
                  "statement": "No-receipt statement", "purchase_log": "Purchase log",
                  "physical_count": "Count sheet",
                  "Awaiting signed-paper review": "Awaiting signed-paper review"}
CASH_GROUPS = (("cash_return", "Cash remaining to return"),
               ("reimbursement", "Personal funds used (reimbursement requested)"),
               ("discrepancy", "Missing cash (only if your totals don't add up)"))
FLAG_LABELS = ("Approved", "Needs correction", "No receipt", "Needs statement", "Needs codes")
ROW_FIELDS = ("date", "vendor", "purpose", "usd", "mxn", "receipt_total", "class_code",
              "expense_account", "project", "no_receipt_reason")


@bp.app_template_filter("money")
def money(value):
    return f"{Decimal(value or 0):,.2f}"


@bp.app_template_filter("input_value")
def input_value(value):
    return "" if value in (None, "") or Decimal(value) == 0 else value


def _today():
    """The device's date when it sent one, else today in UTC."""
    return client_today() or datetime.now(timezone.utc).date().isoformat()


def _present(view):
    """Add to the report view what the templates show about each row."""
    options = view["options"]
    name = lambda items, key, code: next((item["name"] for item in items if item[key] == code), code)
    log_row = next((row for row in view["rows"] if row.get("document_kind") == "log"), None)
    for row in view["rows"]:
        row["editable"] = view["can_edit"] and not row["posted"]
        row["selectable"] = row["editable"] and row["kind"] == "expense"
        row["documentable"] = bool(row["posted"]) and row["kind"] == "expense" \
            and view["voucher"]["status"] != "void"
        flags = []
        if row["posted"]:
            flags.append(("Approved", "success"))
        else:
            if row["admin_note"]:
                flags.append(("Needs correction", "warning"))
            if row["kind"] == "expense":
                if not row["document"] and not row["no_receipt_reason"]:
                    flags.append(("No receipt", "flag"))
                elif not row["document"]:
                    flags.append(("Needs statement", "flag"))
                lines = row["splits"] or [row]
                if any(not line["class_code"] or not line["expense_account"] for line in lines):
                    flags.append(("Needs codes", "flag"))
        row["flags"] = flags
        row["split_count"] = len(row["splits"])
        row["codes_text"] = " · ".join(filter(None, [
            row["class_code"] and name(options["classes"], "class_code", row["class_code"]),
            row["expense_account"] and name(options["expense_accounts"], "account_id", row["expense_account"])]))
        row["title"] = " · ".join(filter(None, [row["vendor"], row["purpose"]]))
        row["receipt_number"] = (row["receipt_reference"] or "").split("-")[-1]
        row["split_json"] = json.dumps(row["splits"])
    view["receipt_rows"] = [row for row in view["rows"] if row["kind"] == "expense"]
    view["cash"] = {kind: next((row for row in view["rows"] if row["kind"] == kind and not row["posted"]), None)
                    for kind in ("cash_return", "reimbursement", "discrepancy")}
    view["log_row"] = log_row
    view["status_label"] = (STATUS_LABELS.get(view["voucher"]["status"], view["voucher"]["status"].title())
                            if view["read_only"] else "Submitted" if view["status"] == "submitted"
                            else "Approved" if view["status"] == "approved"
                            and not [r for r in view["rows"] if r["status"] != "approved"] else "Draft")
    view["pending"] = len([r for r in view["rows"] if r["status"] != "approved"])
    view["needs_statements"] = view["can_edit"] and any(
        row["kind"] == "expense" and not row["posted"] and not row["document"]
        and row["no_receipt_reason"] and row["receipt_reference"] for row in view["rows"])
    view["show_cash"] = view["can_edit"] or any(view["cash"].values())
    income = [item for item in view["activity"] if item["kind"] in ("signout", "addendum")
              and (Decimal(item["usd"]) > 0 or Decimal(item["mxn"]) > 0)]
    view["received"] = {"usd": sum(Decimal(item["usd"]) for item in income),
                        "mxn": sum(Decimal(item["mxn"]) for item in income)}
    usd, mxn = Decimal(view["balance"]["usd"]), Decimal(view["balance"]["mxn"])
    view["help"] = ("Everything is accounted for." if view["balance"]["balanced"]
                    else "You spent more pesos than you received and have dollars left. "
                         "Your administrator will settle the exchange." if view["balance"]["implied_exchange"]
                    else "You spent more than you received." if usd <= 0 and mxn <= 0
                    else "Explain the difference below." if usd < 0 or mxn < 0 else "")
    return view


def _context(vid, v, **extra):
    view = _present(_report_view(vid, v))
    return {"view": view, "vid": vid, "admin": bool(session.get("admin")),
            "csrf": session.get("csrf"),
            "default_approver_name": voucher_defaults.approver_name(current_app.config["DATA_ROOT"]),
            "reason_labels": REASON_LABELS, "document_kinds": DOCUMENT_KINDS,
            "cash_groups": CASH_GROUPS, **extra}


def page_context(vid, v):
    return _context(vid, v)


def _form_fields():
    """The row fields the form carries, in the API's decimal-string shape."""
    form = request.form
    data = {name: form[name] for name in ROW_FIELDS if name in form}
    if "splits" in form:
        data["splits"] = json.loads(form["splits"] or "[]")
    for name in ("class_code", "expense_account"):
        if name in data and not data[name]:
            data[name] = None
    return _row_fields(data)


def _command(fy, num, kind, call, template, *, admin_only=False, refresh=False, **extra):
    """Run one report command and answer with a fragment (or a page refresh)."""
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    if admin_only and not session.get("admin"):
        return err("forbidden", "Only an administrator can do this", 403)
    admin = bool(session.get("admin"))
    try:
        submission = str(uuid.UUID(request.form["submission_id"]))
    except (KeyError, ValueError):
        submission = str(uuid.uuid4())          # a draft save needs no retry safety
    g.ui_submission = submission
    result = run_command(g.db, submission, kind, lambda conn: call(conn, vid, admin))
    if refresh:
        response = make_response("")
        response.headers["HX-Refresh"] = "true"
        return response
    return render_template(template, **_context(vid, v, result=result, **extra))


def _row_id(result):
    return (result.get("row") or {}).get("id")


# ---- rows ------------------------------------------------------------------------

@bp.post(f"{BASE}/rows")
def add_row(fy, num):
    return _command(fy, num, "ui_add_row", lambda conn, vid, admin: expense_drafts.add_row(
        conn, voucher_id=vid, admin=admin, kind="expense",
        fields=_row_fields({"date": _today()})),
        "expense/row_added.html")


@bp.post(f"{BASE}/rows/<row_id>")
def save_row(fy, num, row_id):
    return _command(fy, num, "ui_save_row", lambda conn, vid, admin: expense_drafts.update_row(
        conn, voucher_id=vid, row_id=row_id, expected_rev=int(request.form["rev"]),
        fields=_form_fields(), admin=admin), "expense/saved.html")


@bp.post(f"{BASE}/rows/<row_id>/structure")
def restructure_row(fy, num, row_id):
    return _command(fy, num, "ui_restructure_row", lambda conn, vid, admin: expense_drafts.update_row(
        conn, voucher_id=vid, row_id=row_id, expected_rev=int(request.form["rev"]),
        fields=_form_fields(), admin=admin), "expense/row_replaced.html", row_id=row_id)


@bp.post(f"{BASE}/rows/<row_id>/receipt")
def attach_receipt(fy, num, row_id):
    mode, document_id = request.form["mode"], request.form["document_id"]

    def call(conn, vid, admin):
        if mode == "document":
            return expense_drafts.add_documentation(conn, voucher_id=vid, row_id=row_id,
                                                    document_id=document_id, admin=admin,
                                                    reviewed_date=client_today())
        fields = {"document_id": document_id}
        if mode in ("statement", "log"):
            fields["document_kind"] = mode
        elif mode == "replace":
            fields["document_kind"] = request.form.get("document_kind") or None
        return expense_drafts.update_row(conn, voucher_id=vid, row_id=row_id,
                                         expected_rev=int(request.form["rev"]), fields=fields, admin=admin)
    return _command(fy, num, "ui_attach_receipt", call, "expense/row_replaced.html", row_id=row_id)


@bp.post(f"{BASE}/rows/<row_id>/receipt/remove")
def remove_receipt(fy, num, row_id):
    return _command(fy, num, "ui_remove_receipt", lambda conn, vid, admin: expense_drafts.update_row(
        conn, voucher_id=vid, row_id=row_id, expected_rev=int(request.form["rev"]),
        fields={"document_id": None}, admin=admin), "expense/row_replaced.html", row_id=row_id)


@bp.post(f"{BASE}/rows/<row_id>/delete")
def delete_row(fy, num, row_id):
    return _command(fy, num, "ui_delete_row", lambda conn, vid, admin: expense_drafts.delete_row(
        conn, voucher_id=vid, row_id=row_id, expected_rev=int(request.form["rev"]), admin=admin),
        "expense/saved.html")


@bp.post(f"{BASE}/bulk")
def apply_codes(fy, num):
    codes = {key: request.form[key] for key in ("class_code", "expense_account") if request.form.get(key)}
    if not codes:
        raise ValidationFailure("no_codes", "Choose a class code or an account code to apply.")
    return _command(fy, num, "ui_set_codes", lambda conn, vid, admin: expense_drafts.set_codes(
        conn, voucher_id=vid, row_ids=request.form.getlist("row_ids"), admin=admin, **codes),
        "expense/saved.html", refresh=True)


@bp.get(f"{BASE}/bulk-count")
def bulk_count(fy, num):
    count = len(request.args.getlist("row_ids"))
    return render_template("expense/bulk_count.html", count=count)


# ---- cash return, reimbursement, missing cash, note -------------------------------

@bp.post(f"{BASE}/cash/<kind>")
def save_cash(fy, num, kind):
    if kind not in ("cash_return", "reimbursement", "discrepancy"):
        return err("not_found", "Unknown cash group", 404)
    fields = {name: request.form.get(name, "") for name in ("usd", "mxn")}
    if kind == "discrepancy":
        fields["purpose"] = request.form.get("purpose", "")
        fields["date"] = _today()
    return _command(fy, num, "ui_save_cash", lambda conn, vid, admin: expense_drafts.set_declaration(
        conn, voucher_id=vid, kind=kind, fields=_row_fields(fields), admin=admin), "expense/saved.html")


@bp.post(f"{BASE}/note")
def save_note(fy, num):
    return _command(fy, num, "ui_save_note", lambda conn, vid, admin: expense_drafts.set_note(
        conn, voucher_id=vid, note=request.form.get("note", ""), admin=admin), "expense/saved.html")


# ---- report steps (they reload the page: the whole report changes) ------------------

@bp.post(f"{BASE}/report/<step>")
def report_step(fy, num, step):
    vid = f"FY{fy}-{num}"
    form = request.form

    def approve(conn, vid, admin):
        data = {"date": form.get("date"), "approver_name": form.get("approver_name"),
                "return_usd": form.get("return_usd") or "0", "return_mxn": form.get("return_mxn") or "0",
                "reimburse_usd": form.get("reimburse_usd") or "0", "reimburse_mxn": form.get("reimburse_mxn") or "0",
                "resolution_note": form.get("resolution_note", ""),
                "confirmed": form.get("confirmed") == "on", "acknowledged": form.get("acknowledged") == "on"}
        return _approval(vid, data)(conn, g.ui_submission)

    steps = {
        "submit": (False, lambda conn, vid, admin: expense_drafts.submit(conn, voucher_id=vid, admin=admin)),
        "reopen": (False, lambda conn, vid, admin: expense_drafts.reopen(conn, voucher_id=vid)),
        "corrections": (True, lambda conn, vid, admin: expense_drafts.request_corrections(
            conn, voucher_id=vid, summary=form.get("summary", ""),
            row_notes={key[len("note_"):]: value for key, value in form.items() if key.startswith("note_")})),
        "approve": (True, approve),
    }
    if step not in steps:
        return err("not_found", "Unknown report step", 404)
    admin_only, call = steps[step]
    return _command(fy, num, f"ui_report_{step}", call, "", admin_only=admin_only, refresh=True)


# ---- administrator ---------------------------------------------------------------

@bp.get(f"{BASE}/approval")
@admin_required
def approval(fy, num):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    return render_template("expense/approval.html", **_context(
        vid, v, submission_id=str(uuid.uuid4()), today=_today()))


@bp.post(f"{BASE}/addendum")
@admin_required
def add_cash(fy, num):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    form = request.form
    try:
        submission = str(uuid.UUID(form["submission_id"]))
    except (KeyError, ValueError):
        submission = str(uuid.uuid4())
    result = _prepare_with_approver(
        form, g.db, submission, "prepare_addendum_positive",
        lambda conn: prep.prepare_addendum(
            conn, submission_id=submission, voucher_id=vid, date=form["date"], seq_no=None,
            kind="positive", reason=form.get("reason", ""),
            usd_cents=to_minor(form.get("usd") or "0", "USD"), mxn_centavos=to_minor(form.get("mxn") or "0", "MXN")))
    response = make_response("")
    response.headers["HX-Refresh"] = "true"
    response.headers["HX-Trigger"] = json.dumps({"erAddendum": {"url": result["print_url"]}})
    return response


@bp.get(f"{BASE}/inbox")
@admin_required
def attach_inbox(fy, num):
    rows = [dict(row) for row in g.db.execute(
        "SELECT document_id, display_name, page_count FROM documents WHERE state='inbox'"
        " ORDER BY created_at")]
    ready = [row for row in rows
             if (document_jobs.metadata(g.db, row["document_id"]) or {}).get("status") not in ("queued", "running")]
    return render_template("expense/inbox.html", documents=ready)


@bp.post(f"{BASE}/log-scan")
def attach_log_scan(fy, num):
    document_id = request.form["document_id"]

    def call(conn, vid, admin):
        draft, _ = expense_drafts.load(conn, vid)
        log = next((row for row in draft["rows"] if row.get("document_kind") == "log"), None)
        if not log:
            return expense_drafts.add_row(conn, voucher_id=vid, admin=admin, kind="expense", fields={
                "document_id": document_id, "document_kind": "log", "purpose": "Purchase log"})
        if log["posted"]:
            return expense_drafts.add_documentation(conn, voucher_id=vid, row_id=log["id"],
                                                    document_id=document_id, admin=admin,
                                                    reviewed_date=client_today())
        return expense_drafts.update_row(conn, voucher_id=vid, row_id=log["id"], expected_rev=log["rev"],
                                         fields={"document_id": document_id, "document_kind": "log"}, admin=admin)
    return _command(fy, num, "ui_attach_log_scan", call, "", refresh=True)
