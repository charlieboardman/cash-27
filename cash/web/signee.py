"""The expense report page at /FY##-NNNN and its API (spec 12, 15).

One page serves the signout's recipient (shared employee password) and the
administrator, who sees the same report with review, paperwork, and
settlement tools. Every mutation is CSRF-protected."""

from __future__ import annotations

import re
from datetime import datetime
import uuid

from flask import (Blueprint, current_app, g, redirect, render_template,
                   request, send_file, session)

from ..commands import ValidationFailure, run_command
from ..money import optional_minor, to_minor, to_text
from .. import (document_codes, document_names, expense_drafts, repo,
                signout_recipients, signouts, voucher_defaults, report_rows)
from .app import client_today, err, json_body, ok, safe_next
from .qr_print import qr_svg
from .documents import document_thumbnail, store_document
from .document_delivery import document_url, serve_document, verified_document

bp = Blueprint("signee", __name__)
VOUCHER_RE = re.compile(r"^FY\d{2}-\d{4}$")
ANY_CASE_PAGE_RE = re.compile(r"/(fy\d{2}-\d{4})", re.IGNORECASE)
RECIPIENT_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@bp.before_app_request
def canonical_voucher_page():
    """Typed links like /fy27-0001 land on the canonical /FY27-0001."""
    match = ANY_CASE_PAGE_RE.fullmatch(request.path)
    if match and request.method in ("GET", "HEAD") and \
            match.group(1) != match.group(1).upper():
        query = request.query_string.decode()
        return redirect(f"/{match.group(1).upper()}" +
                        (f"?{query}" if query else ""), 301)
    return None


def _voucher_or_none(voucher_id):
    if not VOUCHER_RE.match(voucher_id):
        return None
    return g.db.execute(
        "SELECT * FROM vouchers WHERE voucher_id = ? AND"
        " type='signout'", (voucher_id,)).fetchone()


def _signee_guard(voucher_id):
    if not (session.get("user") or session.get("admin")):
        if request.endpoint == "signee.signee_page":
            return None, redirect(f"/user-login?next=/{voucher_id}")
        return None, err("unauthorized", "Shared password required", 401)
    v = _voucher_or_none(voucher_id)
    if not v:
        return None, err("not_found", "Unknown voucher", 404)
    return v, None


def _recipient_registry():
    try:
        return signout_recipients.load(current_app.config["DATA_ROOT"])
    except ValidationFailure as exc:
        current_app.logger.warning("signout recipient config: %s", exc)
        return signout_recipients.Registry([])


@bp.get("/FY<fy>-<num>")
def signee_page(fy, num):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    recipient_slug, _ = _recipient_registry().resolve(v["recipient"])
    return render_template(
        "expense_report.html", voucher_id=vid, admin=bool(session.get("admin")),
        recipient_slug=recipient_slug,
        default_approver_name=voucher_defaults.approver_name(current_app.config["DATA_ROOT"]))


@bp.get("/signouts/<slug>")
@bp.get("/entregas/<slug>")
def signout_index(slug):
    """One person's signouts, derived from the vouchers themselves."""
    if not (session.get("user") or session.get("admin")):
        return redirect(f"/user-login?next={request.path}")
    key = signout_recipients.normalize(slug)
    if not key or not RECIPIENT_SLUG_RE.fullmatch(key):
        return err("not_found", "Unknown recipient", 404)

    registry = _recipient_registry()
    requested = registry.find(key)
    canonical = requested["slug"] if requested else key
    rows = sorted(
        g.db.execute(
            "SELECT v.voucher_id, v.recipient, v.purpose, v.status,"
            " (SELECT MIN(t.date) FROM transactions t"
            "  WHERE t.voucher_id=v.voucher_id AND t.txn_type='signout')"
            " AS signout_date"
            " FROM vouchers v WHERE v.type='signout'").fetchall(),
        key=lambda row: (row["signout_date"] or "", row["voucher_id"]),
        reverse=True)
    states = signouts.summaries(g.db)
    display_name = requested["name"] if requested else ""
    items = []
    for row in rows:
        row_slug, row_name = registry.resolve(row["recipient"])
        if row_slug != canonical:
            continue
        if not display_name:
            display_name = row_name
        state = states.get(row["voucher_id"])
        status = state["status"] if state else (
            "void" if row["status"] == "void" else "open")
        items.append({
            "voucher_id": row["voucher_id"],
            "date": row["signout_date"],
            "purpose": row["purpose"],
            "status": status,
        })
    if not requested and not items:
        return err("not_found", "Unknown recipient", 404)
    if slug != canonical:
        prefix = "entregas" if request.path.startswith("/entregas/") \
            else "signouts"
        query = request.query_string.decode()
        return redirect(f"/{prefix}/{canonical}" +
                        (f"?{query}" if query else ""), 301)
    return render_template("signout_recipients.html",
                           person_name=display_name or canonical,
                           signout_slug=canonical, signouts=items)


@bp.get("/user-login")
def user_login_page():
    if session.get("user") or session.get("admin"):
        return redirect(safe_next(request.args.get("next", "/")))
    return render_template("user_login.html")


def _activity(vid, v):
    """Cash movements into and out of the signout, oldest first."""
    activity_rows = g.db.execute(
        "SELECT t.movement_id, MIN(t.date) AS date,"
        " MIN(t.same_day_seq) AS same_day_seq, MIN(t.txn_type) AS txn_type,"
        " SUM(CASE WHEN t.to_account=? THEN t.usd_cents"
        "          WHEN t.from_account=? THEN -t.usd_cents ELSE 0 END)"
        " AS usd_change,"
        " SUM(CASE WHEN t.to_account=? THEN t.mxn_centavos"
        "          WHEN t.from_account=? THEN -t.mxn_centavos ELSE 0 END)"
        " AS mxn_change, MAX(COALESCE(r.vendor, '')) AS vendor,"
        " MAX(COALESCE(t.description, '')) AS description,"
        " MAX(COALESCE(a.reason, '')) AS reason"
        " FROM transactions t"
        " LEFT JOIN receipts r ON r.receipt_id=t.receipt_id"
        " LEFT JOIN addenda a ON a.addendum_id=t.addendum_id"
        " WHERE t.posting_state='active'"
        " AND (t.from_account=? OR t.to_account=?)"
        " GROUP BY t.movement_id"
        " ORDER BY MIN(t.date), MIN(t.same_day_seq), t.movement_id",
        (vid, vid, vid, vid, vid, vid)).fetchall()
    activity = []
    for row in activity_rows:
        kind = row["txn_type"]
        usd_change = row["usd_change"] or 0
        mxn_change = row["mxn_change"] or 0
        if kind == "signout":
            label, detail = "Originally received", v["purpose"]
        elif kind == "addendum" and (usd_change > 0 or mxn_change > 0):
            label, detail = "Additional cash received", row["reason"]
        elif kind == "addendum":
            label, detail = "Cash returned", row["reason"]
        elif kind == "expense":
            label = row["vendor"] or "Receipt"
            detail = row["description"]
        else:
            label, detail = "Missing cash", row["description"]
        activity.append({
            "date": row["date"], "kind": kind, "label": label,
            "detail": detail, "usd": to_text(usd_change), "mxn": to_text(mxn_change),
        })
    return activity


@bp.post("/FY<fy>-<num>/documents")
def signee_upload(fy, num):
    """Combine ordered camera images/PDFs into one voucher-scoped receipt.
    The page then attaches the returned document to a report row."""
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    # Uploading only stores a draft receipt; attaching it is checked on its
    # own, so approved rows can take their receipt even after settlement.
    if v["status"] == "void" or signouts.summary(g.db, vid)["status"] == "void":
        return err("voucher_void", "This signout is void", 422)
    files = request.files.getlist("files")
    if not files:
        return err("no_files", "No files", 422)
    document_id, display_name, meta = store_document(
        g.db, current_app.config["DATA_ROOT"], files, state="signee_draft", voucher_id=vid)
    return ok({"documents": [{"document_id": document_id,
                               "display_name": display_name,
                               "sha256": meta["sha256"],
                               "size": meta["size"],
                               "pages": meta["pages"]}]})


@bp.get("/FY<fy>-<num>/statements")
def print_statements(fy, num):
    """No-receipt statements to print and sign, one page per row: the row
    named by ?row=, or every unapproved receipt row without a document."""
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    draft, _ = expense_drafts.load(g.db, vid)
    wanted = request.args.get("row")
    rows = [row for row in draft["rows"]
            if row["kind"] in ("expense", "discrepancy") and "purchases" not in row
            and not row["document_id"] and not row["posted"]
            and row.get("receipt_number") and (row["id"] == wanted if wanted else True)]
    if not rows:
        return err("nothing_to_print", "No receipt rows need a statement", 404)
    statements = [{
        "code": document_codes.statement(vid, row["receipt_number"]),
        "qr": qr_svg(document_codes.statement(vid, row["receipt_number"])),
        "receipt": f"{vid}-R{row['receipt_number']}",
        "date": row["date"], "vendor": row["vendor"], "purpose": row["purpose"],
        "reason": row["no_receipt_reason"], "discrepancy": row["kind"] == "discrepancy",
        "usd": to_text(row["usd"]) if row["usd"] else "",
        "mxn": to_text(row["mxn"]) if row["mxn"] else ""} for row in rows]
    return render_template("statement.html", voucher_id=vid,
                           recipient=v["recipient"], statements=statements)


@bp.get("/FY<fy>-<num>/purchase-log")
def print_purchase_log(fy, num):
    """A blank purchase log sheet. Print as many as needed: every sheet
    carries the same code, and all of them are filed as one document."""
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    code = document_codes.log(vid)
    draft, _ = expense_drafts.load(g.db, vid)
    log = report_rows.purchase_log(draft)
    return render_template("purchase_log.html", voucher_id=vid, recipient=v["recipient"],
                           code=code, qr=qr_svg(code), lines=log['purchases'] if log else [],
                           total_usd=to_text(log['usd']) if log else '0.00',
                           total_mxn=to_text(log['mxn']) if log else '0.00')


@bp.post("/FY<fy>-<num>/api/rows/<row_id>/documentation")
def add_row_documentation(fy, num, row_id):
    """The receipt for an approved row that has none, at any time the signout
    is not void. The document was uploaded here or, for an administrator,
    captured into the inbox."""
    def action(data, admin):
        document_id = str(data["document_id"])
        reviewed_date = client_today()
        return lambda conn, _: expense_drafts.add_documentation(
            conn, voucher_id=f"FY{fy}-{num}", row_id=row_id,
            document_id=document_id, admin=admin, reviewed_date=reviewed_date)
    return _report_command(fy, num, "report_add_documentation", action,
                           allowed=("submission_id", "document_id"))


@bp.get("/FY<fy>-<num>/documents/<document_id>/<filename>.pdf")
def signee_document(fy, num, document_id, filename):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    return serve_document(document_id, filename + '.pdf', vid)


@bp.get("/FY<fy>-<num>/documents/<document_id>/thumbnail")
def signee_thumbnail(fy, num, document_id):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    verified, failure = verified_document(document_id, vid)
    if failure is not None:
        return failure
    path, _ = verified
    try:
        out = document_thumbnail(path)
    except Exception:
        return err("thumbnail_unavailable", "Open the PDF to view this receipt", 404)
    response = send_file(out, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


# ---- expense report (draft rows) -----------------------------------------------
# One page serves the recipient and the administrator. Money crosses the wire as
# decimal strings; blank means zero. Every mutation returns the whole view.

def _report_view(vid, v):
    draft, version = expense_drafts.load(g.db, vid)
    replayed = repo.replay_projected(g.db)
    derived = signouts.summary(g.db, vid, replayed)
    admin = bool(session.get("admin"))
    names = document_names.canonical_filenames(g.db)
    for item in draft["rows"]:
        reference = expense_drafts.receipt_reference(vid, item)
        if item.get("document_id") and reference:
            kind = item.get("document_kind")
            name = f"{vid}-log" if kind == "log" or "purchases" in item else reference + ("x" if kind == "statement" else "")
            names.setdefault(item["document_id"], name + ".pdf")
    for paper in draft.get("settlement_papers", {}).values():
        if paper.get("document_id"):
            names.setdefault(paper["document_id"], paper["paper_id"] + ".pdf")
    for match in g.db.execute(
            "SELECT document_id, detected_id FROM document_qr"
            " WHERE voucher_id=? AND status='matched'", (vid,)):
        names.setdefault(match["document_id"], match["detected_id"] + ".pdf")

    def document(document_id):
        if not document_id:
            return None
        stored = g.db.execute("SELECT voucher_id FROM documents WHERE document_id=?", (document_id,)).fetchone()
        if not stored:
            return None
        return {"document_id": document_id,
                "display_name": document_names.filename(document_id, names),
                "stream_url": document_url(document_id, None if admin and stored['voucher_id'] != vid else vid),
                "thumbnail_url": f"/{vid}/documents/{document_id}/thumbnail"}

    def split(item):
        return {**item, "usd": to_text(item["usd"]), "mxn": to_text(item["mxn"]),
                "receipt_total": to_text(item["receipt_total"])}

    # Scanned statements wait on their row for the administrator to attach.
    scans = {r["detected_id"]: {"document_id": r["document_id"], "display_name": r["display_name"]}
             for r in g.db.execute(
                 "SELECT d.document_id, d.display_name, q.detected_id FROM documents d"
                 " JOIN document_qr q USING(document_id) WHERE q.voucher_id=?"
                 " AND q.review_kind='statement' AND q.status='matched' AND d.state='inbox'",
                 (vid,))} if admin else {}

    def row(item):
        reference = expense_drafts.receipt_reference(vid, item)
        return {**item, "usd": to_text(item["usd"]), "mxn": to_text(item["mxn"]),
                "receipt_reference": reference,
                "later_receipts": [{"receipt_id": later["receipt_id"],
                                    "document": document(later["document_id"])}
                                   for later in item.get("later_receipts", [])],
                "statement_scan": None if item["document_id"] or not reference
                else scans.get(f"{reference}x"),
                "receipt_total": to_text(item["receipt_total"]),
                "document": document(item["document_id"]),
                "splits": [split(s) for s in item["splits"]]}

    # Scanned purchase log sheets wait for the administrator to attach.
    log_scans = [dict(r) for r in g.db.execute(
        "SELECT d.document_id, d.display_name, d.page_count FROM documents d"
        " JOIN document_qr q USING(document_id) WHERE q.voucher_id=?"
        " AND q.review_kind='log' AND q.status='matched' AND d.state='inbox'"
        " ORDER BY d.created_at", (vid,))] if admin else []
    balance = expense_drafts.balance(g.db, vid, draft, replayed)
    locked = derived["read_only"] or (draft["status"] == "submitted" and not admin)
    return {
        "cash_rows": report_rows.view(g.db, vid, document=document, admin=admin),
        "voucher": {"voucher_id": vid, "recipient": v["recipient"], "purpose": v["purpose"],
                    "status": derived["status"]},
        "can_edit": not locked, "read_only": derived["read_only"],
        "status": draft["status"], "version": version, "note": draft["note"],
        "review": draft["review"], "rows": [row(item) for item in draft["rows"]],
        "log_scans": log_scans,
        "balance": {"usd": to_text(balance["usd"]), "mxn": to_text(balance["mxn"]),
                    "balanced": balance["balanced"],
                    "implied_exchange": balance["implied_exchange"]},
        "cash_difference": (expense_drafts.cash_difference(g.db, vid, draft, replayed)
                            if admin and not derived["read_only"] else None),
        "activity": _activity(vid, v),
        "options": repo.code_options(g.db, projects=admin),
        "csrf": session.get("csrf"),
        **({"paperwork": _paperwork(vid, derived, draft)} if admin else {}),
    }


def _paperwork(vid, derived, draft):
    """Administrator-only facts: what is still needed, addenda to print and
    file, documents on record, and the proposed settlement."""
    addenda = [{"addendum_id": row["addendum_id"], "kind": row["kind"], "status": row["status"],
                "reason": row["reason"], "txn_id": row["txn_id"], "date": row["date"],
                "usd": to_text(row["usd_cents"]), "mxn": to_text(row["mxn_centavos"]),
                "print_url": f"/voucher/{vid}/addendum/{row['addendum_id']}/print"}
               for row in g.db.execute(
                   "SELECT a.addendum_id, a.kind, a.status, a.reason, t.txn_id, t.date,"
                   " t.usd_cents, t.mxn_centavos FROM addenda a JOIN transactions t"
                   " ON t.addendum_id=a.addendum_id WHERE a.voucher_id=?"
                   " AND t.posting_state='active' ORDER BY a.seq", (vid,))]
    documents = [dict(row) | {"stream_url": document_url(row["document_id"])}
                 for row in g.db.execute(
                     "SELECT DISTINCT d.document_id, d.display_name, d.doc_type, d.page_count"
                     " FROM documents d JOIN transaction_documents td"
                     " ON td.document_id=d.document_id JOIN transactions t ON t.txn_id=td.txn_id"
                     " WHERE t.voucher_id=? ORDER BY d.created_at", (vid,))]
    # Scans whose QR code names this signout wait in Review until filed.
    documents += [dict(row) | {
        "stream_url": document_url(row["document_id"]),
        "review_url": f"/review?item={row['review_kind']}:{row['detected_id']}",
        "doc_type": "Awaiting signed-paper review"} for row in g.db.execute(
        "SELECT d.document_id, d.display_name, d.page_count, q.review_kind, q.detected_id"
        " FROM documents d JOIN document_qr q USING(document_id)"
        " WHERE q.voucher_id=? AND q.status='matched' AND d.state='inbox'"
        " AND q.review_kind NOT IN ('statement','log') ORDER BY d.created_at", (vid,))]
    declared = expense_drafts.declared_settlement(draft)
    return {
        "reasons": derived["reasons"], "has_voucher": derived["has_voucher"],
        "voucher_print_url": f"/voucher/{vid}/print",
        "voucher_review_url": f"/review?item=signout:{vid}",
        "addenda": addenda, "documents": documents,
        "closeout": derived["closeout"],
        "proposed_settlement": dict(zip(
            ("return_usd", "return_mxn", "reimburse_usd", "reimburse_mxn"),
            (to_text(value) for value in declared))),
    }


def _row_fields(data):
    """Wire fields to draft fields: decimal strings become signed minor units."""
    fields = {key: value for key, value in data.items()
              if key not in ("submission_id", "rev")}
    for key in ("usd", "mxn"):
        if key in fields:
            fields[key] = to_minor(fields[key], key.upper())
    if "receipt_total" in fields:
        fields["receipt_total"] = optional_minor(fields["receipt_total"], "receipt total")
    if "splits" in fields:
        fields["splits"] = [{**item, "usd": to_minor(item.get("usd"), "USD"),
                             "mxn": to_minor(item.get("mxn"), "MXN"),
                             "receipt_total": optional_minor(item.get("receipt_total"), "receipt total")}
                            for item in fields["splits"] or []]
    return fields


def _report_command(fy, num, kind, action, *, admin_only=False, allowed=None):
    """Run one draft mutation for /FY##-NNNN and answer with the fresh view.
    Row edits check their own fields; other steps list theirs in allowed."""
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    admin = bool(session.get("admin"))
    if admin_only and not admin:
        return err("forbidden", "Only an administrator can do this", 403)
    data = json_body(*allowed) if allowed else (request.get_json(force=True, silent=True) or {})
    try:
        submission = str(uuid.UUID(data["submission_id"]))
        call = action(data, admin)
    except (KeyError, TypeError, ValueError) as exc:
        return err("bad_request", f"Invalid request: {exc}", 400)
    run_command(g.db, submission, kind, lambda conn: call(conn, submission))
    return ok(_report_view(vid, v))


@bp.get("/FY<fy>-<num>/api/report")
def report_view(fy, num):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    return ok(_report_view(vid, v))


@bp.post("/FY<fy>-<num>/api/rows")
def add_report_row(fy, num):
    def action(data, admin):
        kind = data.get("kind", "expense")
        fields = _row_fields({k: v for k, v in data.items() if k != "kind"})
        return lambda conn, _: expense_drafts.add_row(
            conn, voucher_id=f"FY{fy}-{num}", admin=admin, kind=kind, fields=fields)
    return _report_command(fy, num, "report_add_row", action)


@bp.patch("/FY<fy>-<num>/api/rows/<row_id>")
def update_report_row(fy, num, row_id):
    def action(data, admin):
        rev, fields = int(data["rev"]), _row_fields(data)
        return lambda conn, _: expense_drafts.update_row(
            conn, voucher_id=f"FY{fy}-{num}", row_id=row_id, expected_rev=rev,
            fields=fields, admin=admin)
    return _report_command(fy, num, "report_update_row", action)


@bp.delete("/FY<fy>-<num>/api/rows/<row_id>")
def delete_report_row(fy, num, row_id):
    def action(data, admin):
        rev = int(data["rev"])
        return lambda conn, _: expense_drafts.delete_row(
            conn, voucher_id=f"FY{fy}-{num}", row_id=row_id, expected_rev=rev, admin=admin)
    return _report_command(fy, num, "report_delete_row", action, allowed=("submission_id", "rev"))


@bp.post("/FY<fy>-<num>/api/rows/codes")
def set_report_codes(fy, num):
    def action(data, admin):
        row_ids = list(data["row_ids"])
        codes = {key: data[key] for key in ("class_code", "expense_account") if key in data}
        return lambda conn, _: expense_drafts.set_codes(
            conn, voucher_id=f"FY{fy}-{num}", row_ids=row_ids, admin=admin, **codes)
    return _report_command(fy, num, "report_set_codes", action,
                           allowed=("submission_id", "row_ids", "class_code", "expense_account"))


@bp.patch("/FY<fy>-<num>/api/report")
def set_report_note(fy, num):
    def action(data, admin):
        note = str(data.get("note", ""))
        return lambda conn, _: expense_drafts.set_note(
            conn, voucher_id=f"FY{fy}-{num}", note=note, admin=admin)
    return _report_command(fy, num, "report_set_note", action, allowed=("submission_id", "note"))


def _approval(vid, data):
    """Approving also settles the signout: the cash the administrator
    confirms came back or was paid, on the date and under the approver named."""
    settlement = {"date": data.get("date") or client_today(), "confirmed": data.get("confirmed"),
                  "return_usd_cents": to_minor(data.get("return_usd"), "USD returned"),
                  "return_mxn_centavos": to_minor(data.get("return_mxn"), "MXN returned"),
                  "reimburse_usd_cents": to_minor(data.get("reimburse_usd"), "USD reimbursed"),
                  "reimburse_mxn_centavos": to_minor(data.get("reimburse_mxn"), "MXN reimbursed"),
                  "resolution_note": str(data.get("resolution_note", "")).strip()}
    root = current_app.config["DATA_ROOT"]
    name = voucher_defaults.approver_name(root, data.get("approver_name"))

    def run(conn, submission):
        result = expense_drafts.approve(
            conn, voucher_id=vid, submission_id=submission, settlement=settlement,
            acknowledged=data.get("acknowledged") is True, reviewed_date=client_today())
        voucher_defaults.save_selection(root, submission, name)
        return result
    return run


def _calculated_approval(vid, data):
    """Approving also settles the signout: the cash the administrator
    confirms came back or was paid, on the date and under the approver named."""
    root = current_app.config["DATA_ROOT"]
    name = voucher_defaults.approver_name(root, data.get("approver_name"))

    def run(conn, submission):
        report_rows.require_submission(conn, vid)
        draft, _ = expense_drafts.load(conn, vid)
        for number, row in enumerate(draft['rows'], 1):
            if row['status'] != 'approved':
                expense_drafts._check_row_complete(row, number, for_approval=True)
        settlement = report_rows.calculated_settlement(conn, vid,
            date=data.get("date") or client_today() or datetime.now(current_app.config["TIMEZONE"]).date().isoformat(), resolution_note=str(data.get("resolution_note", "")).strip())
        result = expense_drafts.approve(
            conn, voucher_id=vid, submission_id=submission, settlement=settlement,
            acknowledged=data.get("acknowledged") is True, reviewed_date=client_today(), calculated=True)
        voucher_defaults.save_selection(root, submission, name)
        return result
    return run


@bp.post("/FY<fy>-<num>/api/report/<step>")
def report_step(fy, num, step):
    vid = f"FY{fy}-{num}"
    steps = {
        "submit-calculated": (False, lambda data, admin: lambda conn, _: expense_drafts.submit(
            conn, voucher_id=vid, admin=admin, calculated=True)),
        "approve-calculated": (True, lambda data, admin: _calculated_approval(vid, data)),
        "submit": (False, lambda data, admin: lambda conn, _: expense_drafts.submit(
            conn, voucher_id=vid, admin=admin)),
        "reopen": (False, lambda data, admin: lambda conn, _: expense_drafts.reopen(
            conn, voucher_id=vid)),
        "corrections": (True, lambda data, admin: lambda conn, _: expense_drafts.request_corrections(
            conn, voucher_id=vid, summary=data.get("summary", ""),
            row_notes=dict(data.get("row_notes") or {}))),
        "approve": (True, lambda data, admin: _approval(vid, data)),
        "cash-difference": (True, lambda data, admin: lambda conn, _: expense_drafts.resolve_cash_difference(
            conn, voucher_id=vid,
            actual_mxn=to_minor(data.get("actual_mxn"), "MXN"),
            method=str(data.get("method", "")),
            reason=str(data.get("reason", "")))),
    }
    if step not in steps:
        return err("not_found", "Unknown report step", 404)
    admin_only, action = steps[step]
    allowed = {"approve-calculated": ("submission_id", "date", "approver_name", "resolution_note", "acknowledged"),
               "corrections": ("submission_id", "summary", "row_notes"),
               "cash-difference": ("submission_id", "actual_mxn", "method", "reason"),
               "approve": ("submission_id", "date", "approver_name", "return_usd", "return_mxn",
                           "reimburse_usd", "reimburse_mxn", "resolution_note", "confirmed",
                           "acknowledged")}.get(step, ("submission_id",))
    return _report_command(fy, num, f"report_{step}", action, admin_only=admin_only, allowed=allowed)
