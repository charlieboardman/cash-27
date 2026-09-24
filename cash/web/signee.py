"""Signee page and APIs (spec 12, 15): predictable voucher-scoped URL,
shared employee-password session, and CSRF-protected mutations."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import re
import uuid
from decimal import Decimal, InvalidOperation

from pathlib import Path

from flask import (Blueprint, current_app, g, redirect, render_template,
                   request, send_file, session)

from ..commands import run_command
from .. import expense_drafts, expense_reports, packages, repo, signouts
from .app import ok, err
from .documents import (document_rel_path, normalize_document_parts,
                        reserve_document_ids, upload_display_name, document_thumbnail)

bp = Blueprint("signee", __name__)
VOUCHER_RE = re.compile(r"^FY\d{2}-\d{4}$")
ANY_CASE_PAGE_RE = re.compile(r"/(fy\d{2}-\d{4})", re.IGNORECASE)


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


def _cents(v):
    if v in (None, "", 0):
        return 0
    value = Decimal(str(v))
    if not value.is_finite() or value.as_tuple().exponent < -2:
        raise ValueError("money has more than two decimals")
    return int(value * 100)


def _claim_body(data):
    sub = str(uuid.UUID(data["submission_id"]))
    face_usd = _cents(data.get("face_usd"))
    face_mxn = _cents(data.get("face_mxn"))
    claim_date = data.get("date") or date.today().isoformat()
    date.fromisoformat(claim_date)
    return sub, dict(
        document_id=data["document_id"], claim_date=claim_date,
        vendor=str(data.get("vendor", "")).strip(),
        description=str(data.get("description", "")).strip(),
        face_usd_cents=face_usd, face_mxn_centavos=face_mxn,
        payment_mode=data.get("payment_mode", "cash"),
        usd_paid_cents=_cents(data.get("usd_paid")),
        mxn_paid_centavos=_cents(data.get("mxn_paid")),
        mxn_change_centavos=_cents(data.get("mxn_change")),
        usd_change_cents=_cents(data.get("usd_change")),
        notes=str(data.get("notes", "")).strip(),
        simple_entry=data.get("simple_entry") is True)


@bp.get("/FY<fy>-<num>")
def signee_page(fy, num):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    return render_template("signee.html", voucher_id=vid)


@bp.get("/user-login")
def user_login_page():
    if session.get("user") or session.get("admin"):
        target = request.args.get("next", "/")
        return redirect(target if target.startswith("/FY") else "/")
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
        " AS mxn_change, MAX(COALESCE(r.vendor, c.vendor, '')) AS vendor,"
        " MAX(COALESCE(NULLIF(c.description,''),"
        "              NULLIF(t.description,''), '')) AS description,"
        " MAX(COALESCE(a.reason, '')) AS reason"
        " FROM transactions t"
        " LEFT JOIN receipts r ON r.receipt_id=t.receipt_id"
        " LEFT JOIN signout_expense_claims c ON c.document_id=r.document_id"
        "  AND c.voucher_id=t.voucher_id"
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
            label, detail = "Cash balance corrected", row["description"]
        activity.append({
            "date": row["date"], "kind": kind, "label": label,
            "detail": detail, "usd_change_cents": usd_change,
            "mxn_change_centavos": mxn_change,
        })
    return activity


@bp.get("/FY<fy>-<num>/state")
def signee_state(fy, num):
    """Projected state: authoritative balance + draft effects (spec 12)."""
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    res = repo.replay_projected(g.db)
    derived = signouts.summary(g.db, vid, res)
    st = res.states.get(vid)
    claims = g.db.execute(
        "SELECT c.claim_id, c.package_id, c.document_id, c.claim_date,"
        " c.vendor, c.description,"
        " c.face_usd_cents, c.face_mxn_centavos, c.payment_mode,"
        " c.usd_paid_cents, c.mxn_paid_centavos, c.mxn_change_centavos,"
        " c.usd_change_cents, c.notes, c.state, c.admin_note,"
        " c.content_version, c.verified_version, c.simple_entry, d.display_name"
        " FROM signout_expense_claims c JOIN documents d ON"
        " d.document_id=c.document_id WHERE c.voucher_id = ?"
        " ORDER BY c.created_at", (vid,)).fetchall()
    pkgs = g.db.execute(
        "SELECT package_id, seq, status, admin_summary FROM"
        " signout_reconciliation_packages WHERE voucher_id = ?"
        " ORDER BY seq", (vid,)).fetchall()
    pending_usd = sum(c["usd_paid_cents"] - c["usd_change_cents"]
                      for c in claims if c["state"] != "accepted")
    pending_mxn = sum(c["mxn_paid_centavos"] - c["mxn_change_centavos"]
                      for c in claims if c["state"] != "accepted")
    authoritative = None if st is None else {
        "usd": f"{st.usd / 100:.2f}", "mxn": f"{st.mxn / 100:.2f}"}
    proj_usd = (st.usd - pending_usd) if st else 0
    proj_mxn = (st.mxn - pending_mxn) if st else 0
    activity = _activity(vid, v)
    return ok({
        "voucher": {"voucher_id": vid, "purpose": v["purpose"],
                    "recipient": v["recipient"],
                    "status": derived["status"],
                    "authorized_usd_cents": v["authorized_usd_cents"] or 0,
                    "authorized_mxn_centavos":
                        v["authorized_mxn_centavos"] or 0},
        "report": expense_reports.get(g.db, vid),
        "admin": bool(session.get("admin")),
        "authoritative": authoritative,
        "projected_after_pending": {
            "usd": f"{proj_usd / 100:.2f}", "mxn": f"{proj_mxn / 100:.2f}"},
        "activity": activity,
        "claims": [dict(c) | {
            "stream_url": f"/{vid}/documents/{c['document_id']}/stream"}
            for c in claims],
        "packages": [dict(p) for p in pkgs],
        "read_only": derived["read_only"],
        "settlement_confirmed": bool(derived["closeout"] and derived["closeout"]["valid"]),
        "csrf": session.get("csrf"),
    })


@bp.post("/FY<fy>-<num>/documents")
def signee_upload(fy, num):
    """Combine ordered camera images/PDFs into one voucher-scoped receipt."""
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    if signouts.summary(g.db, vid)["read_only"]:
        return err("voucher_read_only", "This signout is closed", 409)
    token = request.headers.get("X-CSRF")
    if not token or token != session.get("csrf"):
        return err("csrf", "CSRF token missing or invalid", 403)
    expense_reports.assert_editable(g.db, vid)
    files = request.files.getlist("files")
    if not files:
        return err("no_files", "No files", 422)
    document_store = current_app.config["DATA_ROOT"] / "documents"
    document_id = reserve_document_ids(g.db, 1)[0]
    display_name = upload_display_name(files[0].filename, len(files))
    rel_path = document_rel_path(
        document_id, display_name, datetime.now(timezone.utc))
    target = current_app.config["DATA_ROOT"] / rel_path
    published = False
    try:
        meta = normalize_document_parts(
            [f.stream for f in files], document_store, rel_path)
        published = True
        g.db.execute("BEGIN IMMEDIATE")
        g.db.execute(
            "INSERT INTO documents (document_id, voucher_id, rel_path,"
            " sha256, size_bytes, page_count, display_name, state)"
            " VALUES (?,?,?,?,?,?,?,"
            " 'signee_draft')",
            (document_id, vid, rel_path, meta["sha256"],
             meta["size"], meta["pages"], display_name))
        if request.form.get("simple_entry") == "1":
            expense_reports.assert_editable(g.db, vid)
            packages.create_claim(g.db, voucher_id=vid, document_id=document_id,
                                          claim_date=date.today().isoformat(), vendor="", description="", simple_entry=True)
        g.db.execute("COMMIT")
    except Exception:
        if g.db.in_transaction:
            g.db.execute("ROLLBACK")
        if published:
            target.unlink(missing_ok=True)
        raise
    return ok({"documents": [{"document_id": document_id,
                               "display_name": display_name,
                               "sha256": meta["sha256"],
                               "size": meta["size"],
                               "pages": meta["pages"]}]})


@bp.get("/FY<fy>-<num>/documents/<document_id>/stream")
def signee_document(fy, num, document_id):
    """Voucher-scoped evidence preview for the anonymous signee session."""
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    row = g.db.execute(
        "SELECT rel_path, sha256 FROM documents WHERE document_id=?"
        " AND voucher_id=?", (document_id, vid)).fetchone()
    if not row:
        return err("not_found", "Receipt document not found", 404)
    path = current_app.config["DATA_ROOT"] / row["rel_path"]
    if not path.exists():
        return err("missing", "Receipt document is missing", 410)
    if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
        return err("hash_mismatch", "Receipt document is corrupted", 410)
    return send_file(path, mimetype="application/pdf")


@bp.post("/FY<fy>-<num>/claims")
def create_claim_route(fy, num):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    token = request.headers.get("X-CSRF")
    if not token or token != session.get("csrf"):
        return err("csrf", "CSRF token missing or invalid", 403)
    data = request.get_json(force=True)
    try:
        sub, body = _claim_body(data)
    except (KeyError, ValueError, InvalidOperation) as e:
        return err("bad_request", f"Invalid field: {e}", 400)
    owned = g.db.execute(
        "SELECT state FROM documents WHERE document_id=? AND voucher_id=?",
        (body["document_id"], vid)).fetchone()
    if not owned or owned["state"] != "signee_draft":
        return err("bad_document", "Receipt upload is unavailable", 422)
    result = run_command(g.db, sub, "create_claim",
                         lambda c: _edit_report_claim(c, vid, packages.create_claim, **body))
    return ok(result)


@bp.patch("/FY<fy>-<num>/claims/<claim_id>")
def update_claim_route(fy, num, claim_id):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    data = request.get_json(force=True)
    try:
        sub, body = _claim_body(data)
        expected = int(data["expected_content_version"])
    except (KeyError, ValueError, InvalidOperation) as exc:
        return err("bad_request", f"Invalid field: {exc}", 400)
    result = run_command(
        g.db, sub, "update_claim",
        lambda connection: _edit_report_claim(
            connection, vid, packages.update_claim, claim_id=claim_id,
            expected_content_version=expected, **body))
    return ok(result)


@bp.delete("/FY<fy>-<num>/claims/<claim_id>")
def delete_claim_route(fy, num, claim_id):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    data = request.get_json(force=True)
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        expected = int(data["expected_content_version"])
    except (KeyError, ValueError, TypeError) as exc:
        return err("bad_request", f"Invalid field: {exc}", 400)
    result = run_command(
        g.db, sub, "delete_claim",
        lambda connection: _edit_report_claim(
            connection, vid, packages.delete_claim, claim_id=claim_id,
            expected_content_version=expected))
    return ok(result)


@bp.post("/FY<fy>-<num>/packages")
def submit_package_route(fy, num):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    token = request.headers.get("X-CSRF")
    if not token or token != session.get("csrf"):
        return err("csrf", "CSRF token missing or invalid", 403)
    data = request.get_json(force=True)
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        claim_ids = data["claim_ids"]
    except (KeyError, ValueError) as e:
        return err("bad_request", f"Invalid field: {e}", 400)
    result = run_command(
        g.db, sub, "submit_package",
        lambda c: packages.submit_package(c, voucher_id=vid,
                                          claim_ids=claim_ids))
    return ok(result)


@bp.post("/FY<fy>-<num>/packages/<package_id>/resubmit")
def resubmit_route(fy, num, package_id):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    data = request.get_json(force=True)
    try:
        sub = str(uuid.UUID(data["submission_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        return err("bad_request", f"Invalid field: {exc}", 400)
    result = run_command(
        g.db, sub, "resubmit_package",
        lambda connection: packages.resubmit_package(
            connection, voucher_id=vid, package_id=package_id))
    return ok(result)


@bp.post("/FY<fy>-<num>/packages/<package_id>/withdraw")
def withdraw_route(fy, num, package_id):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    data = request.get_json(force=True)
    result = run_command(
        g.db, str(uuid.UUID(data["submission_id"])), "withdraw_package",
        lambda c: packages.withdraw_package(c, voucher_id=vid,
                                            package_id=package_id))
    return ok(result)


def _edit_report_claim(conn, vid, fn, **body):
    expense_reports.assert_editable(conn, vid)
    return fn(conn, voucher_id=vid, **body)


@bp.post("/FY<fy>-<num>/report/<action>")
def report_action(fy, num, action):
    vid = f"FY{fy}-{num}"
    _, failure = _signee_guard(vid)
    if failure:
        return failure
    data = request.get_json(force=True)
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        args = {"voucher_id": vid, "expected_version": int(data["expected_version"])}
        if action == "save":
            # Blank amounts mean zero.
            args.update({key: _cents(data.get(key)) for key in
                         ("cash_usd", "cash_mxn", "personal_usd", "personal_mxn")})
            args["discrepancy"] = str(data.get("discrepancy", ""))
            fn = expense_reports.save
        elif action == "submit":
            args["claim_versions"] = data["claim_versions"]
            fn = expense_reports.submit
        elif action == "reopen":
            fn = expense_reports.reopen
        else:
            return err("not_found", "Unknown report action", 404)
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        return err("bad_request", f"Invalid report: {exc}", 400)
    return ok(run_command(g.db, sub, "expense_report_" + action, lambda conn: fn(conn, **args)))


@bp.get("/FY<fy>-<num>/documents/<document_id>/thumbnail")
def signee_thumbnail(fy, num, document_id):
    # Reuse the evidence route's ownership and integrity checks.
    response = signee_document(fy, num, document_id)
    if not hasattr(response, "status_code") or response.status_code != 200:
        return response
    response.close()
    row = g.db.execute("SELECT rel_path FROM documents WHERE document_id=?", (document_id,)).fetchone()
    try:
        out = document_thumbnail(current_app.config["DATA_ROOT"] / row["rel_path"])
    except Exception:
        return err("thumbnail_unavailable", "Open the PDF to view this receipt", 404)
    response = send_file(out, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


# ---- expense report (draft rows) -----------------------------------------------
# One page serves the recipient and the administrator. Money crosses the wire as
# decimal strings; blank means zero. Every mutation returns the whole view.

def _decimal(minor):
    return None if minor is None else f"{Decimal(minor) / 100:.2f}"


def _report_view(vid, v):
    draft, version = expense_drafts.load(g.db, vid)
    replayed = repo.replay_projected(g.db)
    derived = signouts.summary(g.db, vid, replayed)
    admin = bool(session.get("admin"))
    names = {row["document_id"]: row["display_name"] for row in g.db.execute(
        "SELECT document_id, display_name FROM documents WHERE voucher_id=?", (vid,))}

    def document(document_id):
        return None if not document_id else {
            "document_id": document_id, "display_name": names.get(document_id) or document_id,
            "stream_url": f"/{vid}/documents/{document_id}/stream",
            "thumbnail_url": f"/{vid}/documents/{document_id}/thumbnail"}

    def split(item):
        return {**item, "usd": _decimal(item["usd"]), "mxn": _decimal(item["mxn"]),
                "receipt_total": _decimal(item["receipt_total"])}

    def row(item):
        return {**item, "usd": _decimal(item["usd"]), "mxn": _decimal(item["mxn"]),
                "receipt_total": _decimal(item["receipt_total"]),
                "document": document(item["document_id"]),
                "receipt_total_currency": expense_drafts.change_currency(item["usd"], item["mxn"]),
                "splits": [split(s) for s in item["splits"]]}

    balance = expense_drafts.balance(g.db, vid, draft, replayed)
    locked = derived["read_only"] or (draft["status"] == "submitted" and not admin)
    return {
        "voucher": {"voucher_id": vid, "recipient": v["recipient"], "purpose": v["purpose"],
                    "status": derived["status"]},
        "admin": admin, "can_edit": not locked, "read_only": derived["read_only"],
        "status": draft["status"], "version": version, "note": draft["note"],
        "review": draft["review"], "rows": [row(item) for item in draft["rows"]],
        "balance": {"usd": _decimal(balance["usd"]), "mxn": _decimal(balance["mxn"]),
                    "balanced": balance["balanced"],
                    "implied_exchange": balance["implied_exchange"]},
        "activity": _activity(vid, v),
        "settlement_confirmed": bool(derived["closeout"] and derived["closeout"]["valid"]),
        "options": {
            "classes": [dict(r) for r in g.db.execute(
                "SELECT class_code, name FROM classes WHERE active=1 ORDER BY name, class_code")],
            "expense_accounts": [dict(r) for r in g.db.execute(
                "SELECT account_id, name FROM accounts WHERE kind='expense' AND active=1"
                " AND account_id!='UNALLOCATED_EXPENSE' ORDER BY name, account_id")]},
        "csrf": session.get("csrf"),
    }


def _row_fields(data):
    """Wire fields to draft fields: decimal strings become signed minor units."""
    fields = {key: value for key, value in data.items()
              if key not in ("submission_id", "rev")}
    for key in ("usd", "mxn"):
        if key in fields:
            fields[key] = _cents(fields[key])
    if "receipt_total" in fields:
        fields["receipt_total"] = (None if fields["receipt_total"] in (None, "")
                                   else _cents(fields["receipt_total"]))
    if "splits" in fields:
        fields["splits"] = [{**item, **{key: _cents(item.get(key)) for key in ("usd", "mxn")},
                             "receipt_total": (None if item.get("receipt_total") in (None, "")
                                               else _cents(item["receipt_total"]))}
                            for item in fields["splits"] or []]
    return fields


def _report_command(fy, num, kind, action, *, admin_only=False):
    """Run one draft mutation for /FY##-NNNN and answer with the fresh view."""
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    admin = bool(session.get("admin"))
    if admin_only and not admin:
        return err("forbidden", "Only an administrator can do this", 403)
    data = request.get_json(force=True, silent=True) or {}
    try:
        submission = str(uuid.UUID(data["submission_id"]))
        call = action(data, admin)
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
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
    return _report_command(fy, num, "report_delete_row", action)


@bp.post("/FY<fy>-<num>/api/rows/codes")
def set_report_codes(fy, num):
    def action(data, admin):
        row_ids = list(data["row_ids"])
        codes = {key: data[key] for key in ("class_code", "expense_account") if key in data}
        return lambda conn, _: expense_drafts.set_codes(
            conn, voucher_id=f"FY{fy}-{num}", row_ids=row_ids, admin=admin, **codes)
    return _report_command(fy, num, "report_set_codes", action)


@bp.patch("/FY<fy>-<num>/api/report")
def set_report_note(fy, num):
    def action(data, admin):
        note = str(data.get("note", ""))
        return lambda conn, _: expense_drafts.set_note(
            conn, voucher_id=f"FY{fy}-{num}", note=note, admin=admin)
    return _report_command(fy, num, "report_set_note", action)


@bp.post("/FY<fy>-<num>/api/report/<step>")
def report_step(fy, num, step):
    vid = f"FY{fy}-{num}"
    steps = {
        "submit": (False, lambda data, admin: lambda conn, _: expense_drafts.submit(
            conn, voucher_id=vid, admin=admin)),
        "reopen": (False, lambda data, admin: lambda conn, _: expense_drafts.reopen(
            conn, voucher_id=vid)),
        "corrections": (True, lambda data, admin: lambda conn, _: expense_drafts.request_corrections(
            conn, voucher_id=vid, summary=data.get("summary", ""),
            row_notes=dict(data.get("row_notes") or {}))),
        "approve": (True, lambda data, admin: lambda conn, submission: expense_drafts.approve(
            conn, voucher_id=vid, submission_id=submission)),
    }
    if step not in steps:
        return err("not_found", "Unknown report step", 404)
    admin_only, action = steps[step]
    return _report_command(fy, num, f"report_{step}", action, admin_only=admin_only)
