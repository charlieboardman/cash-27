"""Human-facing review worklist and contextual accounting commands.

The API returns view models for the review desk, never an instruction to dump
database rows into the browser. Mutations still delegate to domain commands and
run through the idempotent command executor.
"""

from __future__ import annotations

import json
import uuid
from datetime import date as calendar_date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, g, render_template, request

from .. import packages, prep, repo
from ..commands import ValidationFailure, run_command
from .app import err, ok, require_admin

bp = Blueprint("review", __name__)


def _minor(value, field="amount") -> int:
    if value in (None, ""):
        return 0
    try:
        decimal = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValidationFailure("bad_amount", f"Invalid {field}",
                                {field: "invalid amount"}) from exc
    if not decimal.is_finite():
        raise ValidationFailure("bad_amount", f"Invalid {field}",
                                {field: "invalid amount"})
    if decimal.as_tuple().exponent < -2:
        raise ValidationFailure(
            "bad_amount", f"{field} has more than two decimals",
            {field: "two decimals maximum"})
    return int(decimal * 100)


def _submission(data) -> str:
    try:
        return str(uuid.UUID(data["submission_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationFailure(
            "bad_submission", "Valid submission_id required") from exc


def _version(data, field="expected_version") -> int:
    try:
        value = int(data[field])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationFailure(
            "bad_version", f"Valid {field} required") from exc
    if value < 1:
        raise ValidationFailure("bad_version", f"Valid {field} required")
    return value


def _document(row):
    return {
        "document_id": row["document_id"],
        "display_name": row["display_name"],
        "page_count": row["page_count"],
        "state": row["state"],
        "doc_type": row["doc_type"],
        "created_at": row["created_at"],
        "stream_url": f"/api/documents/{row['document_id']}/stream",
    }


def _documents_for_voucher(voucher_id):
    documents = []
    for row in g.db.execute(
            "SELECT d.document_id, d.display_name, d.page_count,"
            " d.state, d.doc_type,"
            " d.created_at, GROUP_CONCAT(DISTINCT td.role) AS roles"
            " FROM documents d JOIN transaction_documents td ON"
            " td.document_id=d.document_id JOIN transactions t ON"
            " t.txn_id=td.txn_id WHERE t.voucher_id=?"
            " GROUP BY d.document_id ORDER BY d.created_at", (voucher_id,)):
        item = _document(row)
        item["roles"] = (row["roles"].split(",")
                         if row["roles"] else [])
        documents.append(item)
    return documents


@bp.get("/api/review/worklist")
def worklist():
    guard = require_admin()
    if guard:
        return guard
    items = []
    for row in repo.pending_withdrawals(g.db):
        items.append({
            "kind": "withdrawal", "id": str(row["txn_id"]),
            "eyebrow": "Bank withdrawal", "label": "Bank → Safe",
            "secondary": row["notes"], "date": row["date"],
            "usd": f"{row['usd_cents'] / 100:.2f}", "mxn": "0.00",
            "receipt": None, "badges": ["Needs receipt"],
            "version": row["version"], "created_at": row["created_at"],
        })
    for row in repo.pending_exchanges(g.db):
        items.append({
            "kind": "exchange", "id": str(row["txn_id"]),
            "eyebrow": "Currency exchange", "label": row["from_account"],
            "secondary": row["notes"], "date": row["date"],
            "usd": f"{row['usd_cents'] / 100:.2f}",
            "mxn": f"{row['mxn_centavos'] / 100:.2f}",
            "receipt": None, "badges": ["Needs receipt"],
            "version": row["version"], "created_at": row["created_at"],
        })
    for row in g.db.execute(
            "SELECT v.voucher_id, v.recipient, v.purpose, v.version,"
            " v.created_at,"
            " t.usd_cents, t.mxn_centavos, t.date, rd.draft_json"
            " FROM vouchers v JOIN transactions t ON"
            " t.voucher_id=v.voucher_id LEFT JOIN direct_expense_review_drafts rd"
            " ON rd.voucher_id=v.voucher_id WHERE v.type='direct_expense'"
            " AND v.status='pending' AND t.posting_state='active'"
            " AND t.to_account='UNALLOCATED_EXPENSE'"):
        draft = json.loads(row["draft_json"]) if row["draft_json"] else None
        coded = draft and all(
            allocation.get("expense_account") and allocation.get("class_code")
            for editor in draft["editors"] for allocation in editor["allocations"])
        items.append({
            "kind": "direct_expense", "id": row["voucher_id"],
            "eyebrow": "Direct expense", "label": row["recipient"],
            "secondary": row["purpose"], "date": row["date"],
            "usd": f"{row['usd_cents'] / 100:.2f}",
            "mxn": f"{row['mxn_centavos'] / 100:.2f}",
            "receipt": None,
            "badges": ([] if coded else ["Needs codes"]) +
                      ["Needs voucher"],
            "version": row["version"], "created_at": row["created_at"],
        })
    for row in g.db.execute(
            "SELECT v.voucher_id, v.recipient, v.purpose, v.version,"
            " v.created_at, v.authorized_usd_cents,"
            " v.authorized_mxn_centavos, (SELECT MIN(st.date) FROM"
            " transactions st WHERE st.voucher_id=v.voucher_id"
            " AND st.txn_type='signout' AND st.posting_state='active')"
            " AS transaction_date, EXISTS ("
            " SELECT 1 FROM transaction_documents td JOIN transactions t"
            " ON t.txn_id=td.txn_id WHERE t.voucher_id=v.voucher_id"
            " AND td.role='support') AS has_receipt FROM vouchers v WHERE"
            " v.type='signout' AND v.status!='void' AND NOT EXISTS ("
            " SELECT 1 FROM transactions t JOIN transaction_documents td"
            " ON td.txn_id=t.txn_id WHERE t.voucher_id=v.voucher_id"
            " AND t.txn_type='signout' AND t.posting_state='active'"
            " AND td.role='voucher')"):
        badges = ["Needs voucher"]
        if not row["has_receipt"]:
            badges.append("Needs receipt")
        items.append({
            "kind": "signout", "id": row["voucher_id"],
            "eyebrow": "Signout", "label": row["recipient"],
            "secondary": row["purpose"], "date": row["transaction_date"],
            "usd": f"{(row['authorized_usd_cents'] or 0) / 100:.2f}",
            "mxn": f"{(row['authorized_mxn_centavos'] or 0) / 100:.2f}",
            "receipt": None,
            "badges": badges,
            "version": row["version"], "created_at": row["created_at"],
        })
    for row in g.db.execute(
            "SELECT a.*, t.date, t.usd_cents, t.mxn_centavos FROM addenda a"
            " JOIN transactions t ON t.addendum_id=a.addendum_id WHERE"
            " a.status='pending' AND t.posting_state='active'"):
        items.append({
            "kind": "addendum", "id": row["addendum_id"],
            "eyebrow": "Addendum", "label": row["addendum_id"],
            "secondary": ("Additional cash" if row["kind"] == "positive"
                          else "Cash return"), "date": row["date"],
            "usd": f"{row['usd_cents'] / 100:.2f}",
            "mxn": f"{row['mxn_centavos'] / 100:.2f}",
            "receipt": None,
            "badges": ["Needs signed addendum"],
            "version": row["version"], "created_at": row["created_at"],
        })
    for row in g.db.execute(
            "SELECT p.*, v.recipient, COUNT(c.claim_id) AS claim_count,"
            " SUM(CASE WHEN c.state='verified' AND c.verified_version="
            " c.content_version THEN 1 ELSE 0 END) AS verified_count"
            " FROM signout_reconciliation_packages p JOIN vouchers v ON"
            " v.voucher_id=p.voucher_id LEFT JOIN signout_expense_claims c"
            " ON c.package_id=p.package_id WHERE p.status IN"
            " ('submitted','in_review','needs_changes') GROUP BY p.package_id"):
        verified = row["verified_count"] or 0
        total = row["claim_count"] or 0
        badges = ([f"{verified} of {total} checked"]
                  if row["status"] != "needs_changes"
                  else ["Returned to recipient"])
        items.append({
            "kind": "package", "id": row["package_id"],
            "eyebrow": "Receipt submission", "label": row["recipient"],
            "secondary": f"{row['voucher_id']} · Submission {row['seq']}",
            "date": (row["submitted_at"] or row["created_at"])[:10],
            "usd": None, "mxn": None,
            "receipt": f"{total} receipt{'s' if total != 1 else ''}",
            "badges": badges,
            "version": row["version"], "created_at": row["created_at"],
        })
    items.sort(key=lambda item: (item["created_at"], item["kind"],
                                 item["id"]))
    return ok({"items": items})


@bp.get("/api/review/options")
def options():
    guard = require_admin()
    if guard:
        return guard
    accounts = [dict(row) for row in g.db.execute(
        "SELECT account_id, name FROM accounts WHERE kind='expense'"
        " AND active=1 AND account_id!='UNALLOCATED_EXPENSE'"
        " ORDER BY name, account_id")]
    classes = [dict(row) for row in g.db.execute(
        "SELECT class_code, name FROM classes WHERE active=1"
        " ORDER BY name, class_code")]
    cash_accounts = [dict(row) for row in g.db.execute(
        "SELECT account_id, name FROM accounts WHERE kind IN ('safe','signout')"
        " AND active=1 ORDER BY CASE WHEN kind='safe' THEN 0 ELSE 1 END,"
        " name, account_id")]
    projects = [row["project"] for row in g.db.execute(
        "SELECT project FROM ("
        " SELECT DISTINCT project FROM transactions"
        " WHERE posting_state='active' AND TRIM(project)!=''"
        " UNION SELECT DISTINCT project FROM signout_claim_allocations"
        " WHERE TRIM(project)!=''"
        ") ORDER BY project COLLATE NOCASE")]
    return ok({"expense_accounts": accounts, "classes": classes,
               "cash_accounts": cash_accounts, "projects": projects})


@bp.get("/api/review/item/<kind>/<item_id>")
def item_detail(kind, item_id):
    guard = require_admin()
    if guard:
        return guard
    if kind in ("exchange", "withdrawal"):
        transaction = g.db.execute(
            "SELECT * FROM transactions WHERE txn_id=? AND txn_type=?",
            (item_id, "bank_withdrawal" if kind == "withdrawal" else "exchange")).fetchone()
        if not transaction:
            return err("not_found", f"{kind.title()} transaction not found", 404)
        return ok({"kind": kind, "transaction": dict(transaction)})
    if kind == "direct_expense":
        voucher = g.db.execute("SELECT * FROM vouchers WHERE voucher_id=?",
                               (item_id,)).fetchone()
        if not voucher:
            return err("not_found", "Voucher not found", 404)
        rows = [dict(row) for row in g.db.execute(
            "SELECT txn_id, date, usd_cents, mxn_centavos, to_account,"
            " class_code, receipt_face_usd_cents,"
            " receipt_face_mxn_centavos, project, description, notes"
            " FROM transactions WHERE voucher_id=? AND posting_state='active'"
            " ORDER BY allocation_line, txn_id", (item_id,))]
        saved = g.db.execute(
            "SELECT draft_json FROM direct_expense_review_drafts WHERE voucher_id=?",
            (item_id,)).fetchone()
        return ok({"kind": kind, "voucher": dict(voucher),
                   "review_draft": json.loads(saved["draft_json"]) if saved else None,
                   "allocations": rows,
                   "documents": _documents_for_voucher(item_id)})
    if kind == "signout":
        voucher = g.db.execute("SELECT * FROM vouchers WHERE voucher_id=?",
                               (item_id,)).fetchone()
        if not voucher:
            return err("not_found", "Voucher not found", 404)
        replayed = repo.replay_projected(g.db)
        state = replayed.states.get(item_id)
        return ok({
            "kind": kind, "voucher": dict(voucher),
            "balance": None if state is None else {
                "usd": f"{state.usd / 100:.2f}",
                "mxn": f"{state.mxn / 100:.2f}",
                "basis_usd": str(state.basis)},
            "documents": _documents_for_voucher(item_id),
        })
    if kind == "addendum":
        addendum = g.db.execute(
            "SELECT a.*, t.date, t.usd_cents, t.mxn_centavos FROM addenda a"
            " JOIN transactions t ON t.addendum_id=a.addendum_id WHERE"
            " a.addendum_id=? AND t.posting_state='active'",
            (item_id,)).fetchone()
        if not addendum:
            return err("not_found", "Addendum not found", 404)
        return ok({"kind": kind, "addendum": dict(addendum),
                   "documents": _documents_for_voucher(
                       addendum["voucher_id"])})
    if kind == "package":
        package = g.db.execute(
            "SELECT p.*, v.recipient, v.purpose FROM"
            " signout_reconciliation_packages p JOIN vouchers v ON"
            " v.voucher_id=p.voucher_id WHERE p.package_id=?",
            (item_id,)).fetchone()
        if not package:
            return err("not_found", "Package not found", 404)
        claims = []
        for claim in g.db.execute(
                "SELECT * FROM signout_expense_claims WHERE package_id=?"
                " ORDER BY claim_date, created_at, claim_id", (item_id,)):
            allocations = [dict(row) for row in g.db.execute(
                "SELECT * FROM signout_claim_allocations WHERE claim_id=?"
                " ORDER BY line_no", (claim["claim_id"],))]
            document = g.db.execute(
                "SELECT document_id, display_name, page_count, state,"
                " doc_type, created_at"
                " FROM documents WHERE document_id=?",
                (claim["document_id"],)).fetchone()
            claim_item = dict(claim)
            claim_item["allocations"] = allocations
            claim_item["document"] = (_document(document)
                                      if document else None)
            claims.append(claim_item)
        return ok({"kind": kind, "package": dict(package),
                   "claims": claims})
    return err("bad_kind", "Unknown review item kind", 400)


def _allocations(data):
    parsed = []
    for index, allocation in enumerate(data.get("allocations") or []):
        try:
            parsed.append({
                "expense_account": allocation["expense_account"],
                "class_code": allocation.get("class_code") or None,
                "usd_cents": _minor(
                    allocation.get("usd"), f"allocations.{index}.usd"),
                "mxn_centavos": _minor(
                    allocation.get("mxn"), f"allocations.{index}.mxn"),
                "face_usd_cents": (
                    _minor(allocation.get("face_usd"),
                           f"allocations.{index}.face_usd")
                    if allocation.get("face_usd") not in (None, "")
                    else None),
                "face_mxn_centavos": (
                    _minor(allocation.get("face_mxn"),
                           f"allocations.{index}.face_mxn")
                    if allocation.get("face_mxn") not in (None, "")
                    else None),
                "project": allocation.get("project", ""),
                "description": allocation.get("description", ""),
                "notes": allocation.get("notes", ""),
            })
        except KeyError as exc:
            raise ValidationFailure(
                "bad_allocation", f"Missing allocation field: {exc}") from exc
    if not parsed:
        raise ValidationFailure("no_allocations",
                                "Add at least one allocation")
    return parsed


@bp.post("/api/review/direct-expense/<voucher_id>/draft")
def save_direct_expense_review(voucher_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    editors = []
    for editor in data.get("editors") or []:
        if not isinstance(editor, dict):
            raise ValidationFailure("bad_draft", "Invalid expense review progress")
        editors.append({
            "document_id": editor.get("document_id") or None,
            "receipt_date": str(editor.get("receipt_date") or ""),
            "receipt_vendor": str(editor.get("receipt_vendor") or ""),
            "receipt_description": str(editor.get("receipt_description") or ""),
            "allocations": _allocations(editor),
        })
    if not editors:
        raise ValidationFailure("no_allocations", "Add at least one allocation")
    draft = {"editors": editors,
             "primary_document_id": data.get("primary_document_id") or None,
             "use_voucher_as_receipt": bool(data.get("use_voucher_as_receipt"))}
    result = run_command(
        g.db, submission, "save_direct_expense_review",
        lambda connection: prep.save_direct_expense_review(
            connection, voucher_id=voucher_id, expected_version=_version(data),
            draft=draft))
    return ok(result)


@bp.post("/api/review/direct-expense/<voucher_id>/complete")
def complete_direct_expense(voucher_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    expected_version = _version(data)
    voucher_document_id = (data.get("voucher_document_id") or
                           data.get("document_id"))
    use_voucher_as_receipt = data.get("use_voucher_as_receipt", False)
    if not isinstance(use_voucher_as_receipt, bool):
        raise ValidationFailure("bad_receipt_choice", "Confirm the vendor acknowledgment with the checkbox")
    if use_voucher_as_receipt and (data.get("receipts") or data.get("support_document_ids")):
        raise ValidationFailure("duplicate_receipt_choice", "Choose the acknowledged voucher or separate receipts")
    if not voucher_document_id:
        raise ValidationFailure("voucher_required",
                                "Attach the signed voucher")
    receipt_items = []
    for index, receipt in enumerate(data.get("receipts") or []):
        if not isinstance(receipt, dict):
            raise ValidationFailure(
                "bad_receipt", f"Receipt {index + 1} is invalid")
        receipt_date = receipt.get("date")
        try:
            calendar_date.fromisoformat(receipt_date)
        except (TypeError, ValueError) as exc:
            raise ValidationFailure(
                "bad_date", f"Receipt {index + 1} needs a valid date") from exc
        receipt_items.append({
            "document_id": receipt.get("document_id"),
            "date": receipt_date,
            "vendor": str(receipt.get("vendor", "")).strip(),
            "description": str(receipt.get("description", "")).strip(),
            "usd_cents": _minor(receipt.get("usd"),
                                f"receipts.{index}.usd"),
            "mxn_centavos": _minor(receipt.get("mxn"),
                                    f"receipts.{index}.mxn"),
            "face_usd_cents": _minor(receipt.get("face_usd"),
                                      f"receipts.{index}.face_usd"),
            "face_mxn_centavos": _minor(receipt.get("face_mxn"),
                                         f"receipts.{index}.face_mxn"),
            "allocations": _allocations(receipt),
        })
    allocations = _allocations(data) if not receipt_items else None
    result = run_command(
        g.db, submission, "complete_direct_expense",
        lambda connection: (
            prep.complete_direct_expense_receipts(
                connection, submission_id=submission,
                voucher_id=voucher_id, expected_version=expected_version,
                receipts=receipt_items,
                voucher_document_id=voucher_document_id)
            if receipt_items else prep.complete_direct_expense(
                connection, submission_id=submission,
                voucher_id=voucher_id,
                expected_version=expected_version,
                allocations=allocations,
                voucher_document_id=voucher_document_id,
                use_voucher_as_receipt=use_voucher_as_receipt,
                support_document_ids=data.get("support_document_ids") or [])))
    return ok(result)


@bp.post("/api/review/exchange/<int:txn_id>/complete")
def complete_exchange(txn_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    result = run_command(
        g.db, submission, "complete_exchange_document",
        lambda connection: prep.complete_exchange_document(
            connection, txn_id=txn_id, expected_version=_version(data),
            document_id=data.get("document_id")))
    return ok(result)


@bp.post("/api/review/withdrawal/<int:txn_id>/complete")
def complete_withdrawal(txn_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    result = run_command(
        g.db, submission, "complete_withdrawal_document",
        lambda connection: prep.complete_withdrawal_document(
            connection, txn_id=txn_id, expected_version=_version(data),
            document_id=data.get("document_id")))
    return ok(result)


@bp.post("/api/review/signout/<voucher_id>/attach-voucher")
def attach_signout(voucher_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    document_id = data.get("document_id")
    if not document_id:
        raise ValidationFailure("document_required",
                                "Select the signed voucher")
    use_as_receipt = bool(data.get("use_as_receipt", False))
    receipt_document_id = data.get("receipt_document_id") or None
    result = run_command(
        g.db, submission, "attach_signout_voucher",
        lambda connection: prep.attach_signout_voucher(
            connection, voucher_id=voucher_id,
            expected_version=_version(data),
            document_id=document_id,
            use_as_receipt=use_as_receipt,
            receipt_document_id=receipt_document_id))
    return ok(result)


@bp.post("/api/review/signout/<voucher_id>/reopen")
def reopen_signout(voucher_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    result = run_command(
        g.db, submission, "reopen_signout_voucher",
        lambda connection: prep.reopen_signout_voucher(
            connection, voucher_id=voucher_id,
            expected_version=_version(data)))
    return ok(result)


@bp.post("/api/review/addendum/<addendum_id>/reopen")
def reopen_addendum(addendum_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    result = run_command(
        g.db, submission, "reopen_addendum",
        lambda connection: prep.reopen_addendum(
            connection, addendum_id=addendum_id,
            expected_version=_version(data)))
    return ok(result)


@bp.post("/api/review/addendum/<addendum_id>/complete")
def complete_addendum(addendum_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    document_id = data.get("document_id")
    if not document_id:
        raise ValidationFailure("document_required",
                                "Select the signed addendum")
    result = run_command(
        g.db, submission, "complete_addendum_document",
        lambda connection: prep.complete_addendum_document(
            connection, addendum_id=addendum_id,
            expected_version=_version(data),
            document_id=document_id))
    return ok(result)


@bp.post("/api/review/claims/<claim_id>/allocations")
def set_allocations(claim_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    allocations = _allocations(data)
    result = run_command(
        g.db, submission, "set_claim_allocations",
        lambda connection: packages.set_claim_allocations(
            connection, claim_id=claim_id,
            expected_content_version=_version(
                data, "expected_content_version"),
            allocations=allocations, receipt_date=data.get("receipt_date"), vendor=data.get("vendor")))
    return ok(result)


@bp.post("/api/review/claims/<claim_id>/verify")
def verify_claim(claim_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    result = run_command(
        g.db, submission, "verify_claim",
        lambda connection: packages.verify_claim(
            connection, claim_id=claim_id,
            expected_content_version=_version(
                data, "expected_content_version"),
            admin_note=data.get("admin_note", "")))
    return ok(result)


@bp.post("/api/review/packages/<package_id>/return")
def return_package(package_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    item_notes = data.get("item_notes") or {}
    if not isinstance(item_notes, dict):
        raise ValidationFailure("bad_item_notes",
                                "Correction notes must be keyed by receipt")
    result = run_command(
        g.db, submission, "return_package",
        lambda connection: packages.return_package(
            connection, package_id=package_id,
            admin_summary=data.get("admin_summary", ""),
            item_notes=item_notes))
    return ok(result)


@bp.post("/api/review/packages/<package_id>/accept")
def accept_package(package_id):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    result = run_command(
        g.db, submission, "accept_package",
        lambda connection: packages.accept_package(
            connection, submission_id=submission, package_id=package_id))
    return ok(result)


@bp.get("/capture")
def capture_page():
    guard = require_admin()
    if guard:
        return guard
    return render_template("capture.html")


@bp.get("/review")
def review_page():
    guard = require_admin()
    if guard:
        return guard
    return render_template("review.html")
