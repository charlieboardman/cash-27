"""Review worklist and contextual bodies (spec 11).

Worklist kinds: sparse direct expense, sparse signout, sparse addendum,
inbox document, submitted package, correction tasks. Each opens a typed
body with the fields, documents, and decisions still needed."""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from flask import Blueprint, g, request

from .. import prep
from ..commands import run_command, ValidationFailure
from .. import repo
from .app import ok, err, require_admin

bp = Blueprint("review", __name__)


def _cents(v):
    if v in (None, "", 0):
        return 0
    return int(Decimal(str(v)) * 100)


@bp.get("/api/review/worklist")
def worklist():
    guard = require_admin()
    if guard:
        return guard
    items = []
    # sparse direct expenses
    for r in g.db.execute(
            "SELECT v.voucher_id, v.recipient, v.purpose, v.status,"
            " t.txn_id, t.usd_cents, t.mxn_centavos, t.date"
            " FROM vouchers v JOIN transactions t ON"
            "  t.voucher_id = v.voucher_id"
            " WHERE v.type='direct_expense' AND v.status='pending'"
            "  AND t.posting_state='active'"
            "  AND t.to_account='UNALLOCATED_EXPENSE'"):
        items.append({
            "kind": "direct_expense", "id": r["voucher_id"],
            "label": f"{r['voucher_id']} {r['recipient']}",
            "date": r["date"],
            "usd": r["usd_cents"] / 100,
            "mxn": r["mxn_centavos"] / 100,
            "badges": ["unallocated", "no-voucher"],
            "version": 1,
        })
    # pending signouts (custody voucher not attached)
    for r in g.db.execute(
            "SELECT v.voucher_id, v.recipient, v.status FROM vouchers v"
            " WHERE v.type='signout' AND v.status='pending'"):
        items.append({"kind": "signout", "id": r["voucher_id"],
                      "label": f"{r['voucher_id']} {r['recipient']}",
                      "badges": ["no-voucher"], "version": 1})
    # pending addenda
    for r in g.db.execute(
            "SELECT addendum_id, voucher_id, kind, seq FROM addenda"
            " WHERE status='pending'"):
        items.append({"kind": "addendum", "id": r["addendum_id"],
                      "label": f"{r['addendum_id']} ({r['kind']})",
                      "badges": ["no-document"], "version": 1})
    # inbox documents
    for r in g.db.execute(
            "SELECT document_id, page_count, created_at FROM documents"
            " WHERE state='inbox' ORDER BY created_at"):
        items.append({"kind": "inbox_document", "id": r["document_id"],
                      "label": f"Document {r['document_id'][:8]}",
                      "badges": ["unassigned"], "version": 1})
    # submitted packages
    for r in g.db.execute(
            "SELECT package_id, voucher_id, status FROM"
            " signout_reconciliation_packages"
            " WHERE status IN ('submitted','in_review','needs_changes')"):
        items.append({"kind": "package", "id": r["package_id"],
                      "label": f"Package {r['package_id'][:8]}",
                      "badges": [r["status"]], "version": 1})
    return ok({"items": items})


@bp.get("/api/review/item/<kind>/<item_id>")
def item_detail(kind, item_id):
    guard = require_admin()
    if guard:
        return guard
    if kind == "direct_expense":
        v = g.db.execute(
            "SELECT * FROM vouchers WHERE voucher_id = ?",
            (item_id,)).fetchone()
        if not v:
            return err("not_found", "Not found", 404)
        txns = g.db.execute(
            "SELECT txn_id, usd_cents, mxn_centavos, to_account FROM"
            " transactions WHERE voucher_id = ? AND"
            " posting_state='active'", (item_id,)).fetchall()
        docs = g.db.execute(
            "SELECT d.document_id, d.page_count FROM"
            " transaction_documents td JOIN documents d ON"
            " d.document_id = td.document_id JOIN transactions t ON"
            " t.txn_id = td.txn_id WHERE t.voucher_id = ? AND"
            " t.posting_state='active'", (item_id,)).fetchall()
        return ok({"voucher": dict(v), "allocations": [dict(t) for t in txns],
                   "documents": [dict(d) for d in docs]})
    if kind == "signout":
        v = g.db.execute(
            "SELECT * FROM vouchers WHERE voucher_id = ?",
            (item_id,)).fetchone()
        if not v:
            return err("not_found", "Not found", 404)
        res = repo.replay_projected(g.db)
        st = res.states.get(item_id)
        return ok({"voucher": dict(v),
                   "balance": None if st is None else {
                       "usd": st.usd / 100, "mxn": st.mxn / 100,
                       "basis_usd": str(st.basis)}})
    if kind == "addendum":
        a = g.db.execute(
            "SELECT * FROM addenda WHERE addendum_id = ?",
            (item_id,)).fetchone()
        if not a:
            return err("not_found", "Not found", 404)
        return ok({"addendum": dict(a)})
    if kind == "inbox_document":
        d = g.db.execute(
            "SELECT * FROM documents WHERE document_id = ?",
            (item_id,)).fetchone()
        if not d:
            return err("not_found", "Not found", 404)
        return ok({"document": dict(d)})
    if kind == "package":
        p = g.db.execute(
            "SELECT * FROM signout_reconciliation_packages WHERE"
            " package_id = ?", (item_id,)).fetchone()
        if not p:
            return err("not_found", "Not found", 404)
        claims = g.db.execute(
            "SELECT * FROM signout_expense_claims WHERE package_id = ?",
            (item_id,)).fetchall()
        return ok({"package": dict(p),
                   "claims": [dict(c) for c in claims]})
    return err("bad_kind", "Unknown kind", 400)


@bp.post("/api/review/direct-expense/<voucher_id>/complete")
def complete_direct_expense(voucher_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        expected_v = int(data["expected_version"])
        allocs = data["allocations"]
        for a in allocs:
            a["usd_cents"] = _cents(a.get("usd", 0))
            a["mxn_centavos"] = _cents(a.get("mxn", 0))
    except (KeyError, ValueError, InvalidOperation) as e:
        return err("bad_request", str(e), 400)
    result = run_command(
        g.db, sub, "complete_direct_expense",
        lambda c: prep.complete_direct_expense(
            c, submission_id=sub, voucher_id=voucher_id,
            expected_version=expected_v, allocations=allocs,
            document_id=data.get("document_id"),
            receipt_face_mxn=(int(Decimal(str(
                data["receipt_face_mxn"])) * 100)
                if data.get("receipt_face_mxn") else None)))
    return ok(result)


@bp.post("/api/review/claims/<claim_id>/verify")
def verify_claim(claim_id):
    guard = require_admin()
    if guard:
        return guard
    from .. import packages
    data = request.get_json(force=True)
    result = run_command(
        g.db, str(uuid.UUID(data["submission_id"])), "verify_claim",
        lambda c: packages.verify_claim(
            c, claim_id=claim_id,
            expected_content_version=int(data["expected_content_version"]),
            admin_note=data.get("admin_note", "")))
    return ok(result)


@bp.post("/api/review/packages/<package_id>/return")
def return_package(package_id):
    guard = require_admin()
    if guard:
        return guard
    from .. import packages
    data = request.get_json(force=True)
    result = run_command(
        g.db, str(uuid.UUID(data["submission_id"])), "return_package",
        lambda c: packages.return_package(
            c, package_id=package_id,
            admin_summary=data.get("admin_summary", "")))
    return ok(result)


@bp.post("/api/review/packages/<package_id>/accept")
def accept_package(package_id):
    guard = require_admin()
    if guard:
        return guard
    from .. import packages
    data = request.get_json(force=True)
    result = run_command(
        g.db, str(uuid.UUID(data["submission_id"])), "accept_package",
        lambda c: packages.accept_package(
            c, submission_id=str(uuid.uuid4()), package_id=package_id))
    return ok(result)


@bp.get("/capture")
def capture_page():
    guard = require_admin()
    if guard:
        return guard
    from flask import render_template
    return render_template("capture.html")


@bp.get("/review")
def review_page():
    guard = require_admin()
    if guard:
        return guard
    from flask import render_template
    return render_template("review.html")
