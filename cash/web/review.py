"""Human-facing review worklist and contextual accounting commands.

The API returns view models for the review desk, never an instruction to dump
database rows into the browser. Mutations still delegate to domain commands and
run through the idempotent command executor.
"""

from __future__ import annotations

import uuid

from flask import Blueprint, g, render_template

from .. import expense_drafts, prep, repo
from ..commands import ValidationFailure, run_command
from ..money import optional_minor, to_minor, to_text
from .app import admin_required, client_today, err, json_body, ok
from .document_delivery import document_url

bp = Blueprint("review", __name__)


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
        "stream_url": document_url(row["document_id"]),
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


def _worklist_items():
    items = []
    for row in repo.pending_withdrawals(g.db):
        items.append({
            "kind": "withdrawal", "id": str(row["txn_id"]),
            "type_label": "Bank withdrawal", "label": "Bank → Safe",
            "secondary": row["notes"], "date": row["date"],
            "usd": f"{row['usd_cents'] / 100:.2f}", "mxn": "0.00",
            "receipt": None, "badges": ["Needs receipt"],
            "version": row["version"], "created_at": row["created_at"],
        })
    for row in repo.pending_counts(g.db):
        differences = [f"{currency} {to_text(amount)}" for currency, amount in
                       (("USD", row["delta_usd"]), ("MXN", row["delta_mxn"])) if amount]
        items.append({
            "kind": "count", "id": str(row["txn_id"]),
            "type_label": "Safe count", "label": f"Txn-{row['txn_id']}",
            "secondary": " · ".join(differences) or "Matched", "date": row["date"],
            "usd": to_text(row["delta_usd"]), "mxn": to_text(row["delta_mxn"]),
            "receipt": None, "badges": ["Needs count sheet"],
            "version": row["version"], "created_at": row["created_at"],
        })
    for row in repo.pending_exchanges(g.db):
        items.append({
            "kind": "exchange", "id": str(row["txn_id"]),
            "type_label": "Currency exchange", "label": row["from_account"],
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
        draft = prep.load_direct_expense_review(row["draft_json"])
        coded = draft and draft["rows"] and all(
            line.get("expense_account") and line.get("class_code")
            for row_draft in draft["rows"] for line in row_draft["splits"] or [row_draft])
        items.append({
            "kind": "direct_expense", "id": row["voucher_id"],
            "type_label": "Direct expense", "label": row["recipient"],
            "secondary": row["purpose"], "date": row["date"],
            "usd": f"{row['usd_cents'] / 100:.2f}",
            "mxn": f"{row['mxn_centavos'] / 100:.2f}",
            "receipt": None,
            "badges": ([] if coded else ["Needs codes"]) +
                      ([] if prep.direct_voucher_document(g.db, row["voucher_id"]) else ["Needs voucher"]),
            "version": row["version"], "created_at": row["created_at"],
        })
    for row in g.db.execute(
            "SELECT v.voucher_id, v.recipient, v.purpose, v.version,"
            " v.created_at, v.authorized_usd_cents,"
            " v.authorized_mxn_centavos, (SELECT MIN(st.date) FROM"
            " transactions st WHERE st.voucher_id=v.voucher_id"
            " AND st.txn_type='signout' AND st.posting_state='active')"
            " AS transaction_date FROM vouchers v WHERE"
            " v.type='signout' AND v.status!='void' AND EXISTS ("
            " SELECT 1 FROM transactions t WHERE t.voucher_id=v.voucher_id"
            " AND t.txn_type='signout' AND t.posting_state='active')"
            " AND NOT EXISTS ("
            " SELECT 1 FROM transactions t JOIN transaction_documents td"
            " ON td.txn_id=t.txn_id WHERE t.voucher_id=v.voucher_id"
            " AND t.txn_type='signout' AND t.posting_state='active'"
            " AND td.role='voucher')"):
        items.append({
            "kind": "signout", "id": row["voucher_id"],
            "type_label": "Signout", "label": row["recipient"],
            "secondary": row["purpose"], "date": row["transaction_date"],
            "usd": f"{(row['authorized_usd_cents'] or 0) / 100:.2f}",
            "mxn": f"{(row['authorized_mxn_centavos'] or 0) / 100:.2f}",
            "receipt": None,
            "badges": ["Needs voucher"],
            "version": row["version"], "created_at": row["created_at"],
        })
    for row in g.db.execute(
            "SELECT a.*, t.date, t.usd_cents, t.mxn_centavos FROM addenda a"
            " JOIN transactions t ON t.addendum_id=a.addendum_id WHERE"
            " a.status='pending' AND t.posting_state='active'"):
        items.append({
            "kind": "addendum", "id": row["addendum_id"],
            "type_label": "Addendum", "label": row["addendum_id"],
            "secondary": ("Additional cash" if row["kind"] == "positive"
                          else "Cash return"), "date": row["date"],
            "usd": f"{row['usd_cents'] / 100:.2f}",
            "mxn": f"{row['mxn_centavos'] / 100:.2f}",
            "receipt": None,
            "badges": ["Needs signed addendum"],
            "version": row["version"], "created_at": row["created_at"],
        })
    for voucher_id, recipient, purpose, created_at in g.db.execute(
            "SELECT voucher_id, recipient, purpose, created_at FROM vouchers"
            " WHERE type='signout' AND status!='void' ORDER BY voucher_id"):
        draft, _ = expense_drafts.load(g.db, voucher_id)
        if draft["status"] != "submitted":
            continue
        pending = [row for row in draft["rows"] if row["status"] != "approved"]
        items.append({
            "kind": "expense_report", "id": voucher_id,
            "type_label": "Expense report", "label": recipient,
            "secondary": purpose, "date": None, "usd": None, "mxn": None,
            "receipt": f"{len(pending)} row{'s' if len(pending) != 1 else ''}",
            "badges": ["Needs approval"], "url": f"/{voucher_id}",
            "version": 0, "created_at": created_at,
        })
    items.sort(key=lambda item: (item["created_at"], item["kind"],
                                 item["id"]))
    return items


@bp.get("/api/review/worklist")
@admin_required
def worklist():
    return ok({"items": _worklist_items()})


@bp.get("/api/nav-counts")
@admin_required
def nav_counts():
    """What is waiting: records to review and documents in the inbox."""
    inbox = g.db.execute(
        "SELECT COUNT(*) FROM documents WHERE state='inbox'").fetchone()[0]
    return ok({"review": len(_worklist_items()), "inbox": inbox})


@bp.get("/api/review/options")
@admin_required
def options():
    return ok({**repo.code_options(g.db),
               "cash_accounts": [dict(row) for row in repo.cash_accounts(g.db)]})


def _voucher_view(voucher):
    return {"voucher_id": voucher["voucher_id"], "version": voucher["version"],
            "status": voucher["status"],
            "recipient": voucher["recipient"], "purpose": voucher["purpose"],
            "authorized_usd": to_text(voucher["authorized_usd_cents"] or 0),
            "authorized_mxn": to_text(voucher["authorized_mxn_centavos"] or 0)}


def _draft_rows_view(draft):
    """A saved direct-expense draft with amounts as decimal strings."""
    def amounts(item):
        return {**item, "usd": to_text(item["usd"]), "mxn": to_text(item["mxn"]),
                "receipt_total": to_text(item["receipt_total"])}
    return {**draft, "rows": [{**amounts(row), "splits": [amounts(split) for split in row["splits"]]}
                              for row in draft["rows"]]}


@bp.get("/api/review/item/<kind>/<item_id>")
@admin_required
def item_detail(kind, item_id):
    if kind in ("exchange", "withdrawal"):
        transaction = g.db.execute(
            "SELECT * FROM transactions WHERE txn_id=? AND txn_type=?",
            (item_id, "bank_withdrawal" if kind == "withdrawal" else "exchange")).fetchone()
        if not transaction:
            return err("not_found", f"{kind.title()} transaction not found", 404)
        return ok({"kind": kind, "transaction": {
            "txn_id": transaction["txn_id"], "version": transaction["version"],
            "date": transaction["date"], "from_account": transaction["from_account"],
            "usd": to_text(transaction["usd_cents"]),
            "mxn": to_text(transaction["mxn_centavos"])}})
    if kind == "count":
        rows = prep.safe_count_rows(g.db, int(item_id)) if item_id.isdigit() else []
        if not rows or str(rows[0]["txn_id"]) != item_id:
            return err("not_found", "Safe count not found", 404)
        sign = lambda row: 1 if row["to_account"] == "SAFE" else -1
        return ok({"kind": kind, "transaction": {
            "txn_id": rows[0]["txn_id"], "version": rows[0]["version"],
            "date": rows[0]["date"], "reason": rows[0]["description"],
            "usd": to_text(sum(sign(row) * row["usd_cents"] for row in rows)),
            "mxn": to_text(sum(sign(row) * row["mxn_centavos"] for row in rows)),
            "print_url": f"/balances/count/{rows[0]['txn_id']}/print"}})
    if kind == "direct_expense":
        voucher = g.db.execute("SELECT * FROM vouchers WHERE voucher_id=?",
                               (item_id,)).fetchone()
        if not voucher:
            return err("not_found", "Voucher not found", 404)
        sparse = g.db.execute(
            "SELECT date, usd_cents, mxn_centavos FROM transactions WHERE voucher_id=?"
            " AND posting_state='active' ORDER BY allocation_line, txn_id",
            (item_id,)).fetchone()
        saved = g.db.execute(
            "SELECT draft_json FROM direct_expense_review_drafts WHERE voucher_id=?",
            (item_id,)).fetchone()
        draft = prep.load_direct_expense_review(saved["draft_json"]) if saved else None
        return ok({"kind": kind, "voucher": _voucher_view(voucher),
                   "cash": sparse and {"date": sparse["date"],
                                       "usd": to_text(sparse["usd_cents"]),
                                       "mxn": to_text(sparse["mxn_centavos"])},
                   "review_draft": draft and _draft_rows_view(draft),
                   "documents": _documents_for_voucher(item_id)})
    if kind == "signout":
        voucher = g.db.execute("SELECT * FROM vouchers WHERE voucher_id=?",
                               (item_id,)).fetchone()
        if not voucher:
            return err("not_found", "Voucher not found", 404)
        return ok({"kind": kind, "voucher": _voucher_view(voucher),
                   "documents": _documents_for_voucher(item_id)})
    if kind == "addendum":
        addendum = g.db.execute(
            "SELECT a.*, t.date, t.usd_cents, t.mxn_centavos FROM addenda a"
            " JOIN transactions t ON t.addendum_id=a.addendum_id WHERE"
            " a.addendum_id=? AND t.posting_state='active'",
            (item_id,)).fetchone()
        if not addendum:
            return err("not_found", "Addendum not found", 404)
        return ok({"kind": kind, "addendum": {
            "addendum_id": addendum["addendum_id"], "voucher_id": addendum["voucher_id"],
            "version": addendum["version"], "kind": addendum["kind"],
            "reason": addendum["reason"], "date": addendum["date"],
            "usd": to_text(addendum["usd_cents"]), "mxn": to_text(addendum["mxn_centavos"])},
            "documents": _documents_for_voucher(addendum["voucher_id"])})
    return err("bad_kind", "Unknown review item kind", 400)


DIRECT_ROW_FIELDS = {"purpose", "project", "usd", "mxn", "receipt_total",
                     "class_code", "expense_account", "splits"}


def _direct_rows(raw_rows):
    """Wire rows (decimal strings) -> minor units. Shape only; completeness is
    checked when the expense is completed."""
    if not isinstance(raw_rows, list):
        raise ValidationFailure("bad_rows", "Send the expense rows as a list")
    rows = []
    for number, raw in enumerate(raw_rows, 1):
        if not isinstance(raw, dict) or set(raw) - DIRECT_ROW_FIELDS:
            raise ValidationFailure("bad_row", f"Row {number} has unknown fields")
        splits = []
        for part in raw.get("splits") or []:
            if not isinstance(part, dict) or set(part) - expense_drafts.SPLIT_FIELDS:
                raise ValidationFailure("bad_row", f"Row {number} has an invalid split")
            splits.append({
                "usd": to_minor(part.get("usd"), f"row {number} split USD"),
                "mxn": to_minor(part.get("mxn"), f"row {number} split MXN"),
                "receipt_total": optional_minor(part.get("receipt_total"),
                                                f"row {number} split receipt total",
                                                allow_negative=False),
                "purpose": str(part.get("purpose") or "").strip(),
                "class_code": part.get("class_code") or None,
                "expense_account": part.get("expense_account") or None})
        rows.append({
            "purpose": str(raw.get("purpose") or "").strip(),
            "project": str(raw.get("project") or "").strip(),
            "usd": to_minor(raw.get("usd"), f"row {number} USD"),
            "mxn": to_minor(raw.get("mxn"), f"row {number} MXN"),
            "receipt_total": optional_minor(raw.get("receipt_total"),
                                            f"row {number} receipt total",
                                            allow_negative=False),
            "class_code": raw.get("class_code") or None,
            "expense_account": raw.get("expense_account") or None,
            "splits": splits})
    return rows


def _direct_lines(row, number):
    """Check a completed row and turn it into ledger allocation lines, with
    the receipt total recorded in the change currency on mixed-sign lines."""
    usd, mxn = row["usd"], row["mxn"]
    where = f"Row {number}"
    if usd <= 0 and mxn <= 0:
        raise ValidationFailure("no_amount", f"{where}: enter the amount spent")
    currency = expense_drafts.change_currency(usd, mxn)
    if currency and not (row["receipt_total"] or 0) > 0:
        raise ValidationFailure("purchase_value_required",
                                f"{where}: enter the receipt total in {currency}")
    expense_drafts.check_split(row, where)
    expense_drafts.check_codes(row, where)
    lines = []
    for part in row["splits"] or [row]:
        total = part["receipt_total"] if row["splits"] else row["receipt_total"]
        lines.append({
            "expense_account": part["expense_account"], "class_code": part["class_code"],
            "usd_cents": part["usd"], "mxn_centavos": part["mxn"],
            "face_usd_cents": total if currency == "USD" else None,
            "face_mxn_centavos": total if currency == "MXN" else None,
            "project": row["project"], "description": part["purpose"] or row["purpose"],
            "notes": ""})
    return lines


def _receipt_document_ids(data):
    ids = data.get("receipt_document_ids") or []
    if not isinstance(ids, list) or not all(isinstance(item, str) and item for item in ids):
        raise ValidationFailure("bad_receipts", "Send the receipt documents as a list")
    return ids


@bp.post("/api/review/direct-expense/<voucher_id>/draft")
@admin_required
def save_direct_expense_review(voucher_id):
    data = json_body("submission_id", "expected_version", "rows",
                     "primary_document_id", "use_voucher_as_receipt",
                     "receipt_document_ids")
    submission = _submission(data)
    rows = _direct_rows(data.get("rows") or [])
    if not rows:
        raise ValidationFailure("no_rows", "Add at least one row")
    draft = {"format": prep.DIRECT_DRAFT_FORMAT, "rows": rows,
             "primary_document_id": data.get("primary_document_id") or None,
             "use_voucher_as_receipt": bool(data.get("use_voucher_as_receipt")),
             "receipt_document_ids": _receipt_document_ids(data)}
    result = run_command(
        g.db, submission, "save_direct_expense_review",
        lambda connection: prep.save_direct_expense_review(
            connection, voucher_id=voucher_id, expected_version=_version(data),
            draft=draft))
    return ok(result)


@bp.post("/api/review/direct-expense/<voucher_id>/complete")
@admin_required
def complete_direct_expense(voucher_id):
    """The rows' lines replace the provisional expense as one movement; the
    voucher and any separate receipts are filed with it."""
    data = json_body("submission_id", "expected_version", "voucher_document_id",
                     "use_voucher_as_receipt", "receipt_document_ids", "rows")
    submission = _submission(data)
    expected_version = _version(data)
    voucher_document_id = data.get("voucher_document_id")
    use_voucher_as_receipt = data.get("use_voucher_as_receipt", False)
    if not isinstance(use_voucher_as_receipt, bool):
        raise ValidationFailure("bad_receipt_choice", "Confirm the vendor acknowledgment with the checkbox")
    if not voucher_document_id:
        raise ValidationFailure("voucher_required", "Attach the signed voucher")
    receipt_document_ids = _receipt_document_ids(data)
    rows = _direct_rows(data.get("rows") or [])
    if not rows:
        raise ValidationFailure("no_rows", "Add at least one row")
    lines = [line for number, row in enumerate(rows, 1)
             for line in _direct_lines(row, number)]
    return ok(run_command(
        g.db, submission, "complete_direct_expense",
        lambda connection: prep.complete_direct_expense(
            connection, submission_id=submission, voucher_id=voucher_id,
            expected_version=expected_version, allocations=lines,
            voucher_document_id=voucher_document_id,
            receipt_document_ids=receipt_document_ids,
            use_voucher_as_receipt=use_voucher_as_receipt,
            reviewed_date=client_today())))


@bp.post("/api/review/exchange/<int:txn_id>/complete")
@admin_required
def complete_exchange(txn_id):
    data = json_body("submission_id", "expected_version", "document_id")
    submission = _submission(data)
    return ok(run_command(
        g.db, submission, "complete_exchange_document",
        lambda connection: prep.complete_cash_receipt(
            connection, txn_id=txn_id, expected_version=_version(data),
            document_id=data.get("document_id"), kind="exchange")))


@bp.post("/api/review/count/<int:txn_id>/complete")
@admin_required
def complete_count(txn_id):
    data = json_body("submission_id", "expected_version", "document_id")
    submission = _submission(data)
    return ok(run_command(
        g.db, submission, "complete_count_sheet",
        lambda connection: prep.complete_count_sheet(
            connection, count_id=txn_id, expected_version=_version(data),
            document_id=data.get("document_id"))))


@bp.post("/api/review/withdrawal/<int:txn_id>/complete")
@admin_required
def complete_withdrawal(txn_id):
    data = json_body("submission_id", "expected_version", "document_id")
    submission = _submission(data)
    return ok(run_command(
        g.db, submission, "complete_withdrawal_document",
        lambda connection: prep.complete_cash_receipt(
            connection, txn_id=txn_id, expected_version=_version(data),
            document_id=data.get("document_id"), kind="withdrawal")))


@bp.post("/api/review/direct-expense/<voucher_id>/attach-voucher")
@admin_required
def attach_direct_expense(voucher_id):
    data = json_body("submission_id", "expected_version", "document_id")
    return ok(run_command(
        g.db, _submission(data), "attach_direct_expense_voucher",
        lambda connection: prep.attach_direct_expense_voucher(
            connection, voucher_id=voucher_id, expected_version=_version(data),
            document_id=data.get("document_id"))))


@bp.post("/api/review/signout/<voucher_id>/attach-voucher")
@admin_required
def attach_signout(voucher_id):
    data = json_body("submission_id", "expected_version", "document_id")
    submission = _submission(data)
    document_id = data.get("document_id")
    if not document_id:
        raise ValidationFailure("document_required",
                                "Select the signed voucher")
    return ok(run_command(
        g.db, submission, "attach_signout_voucher",
        lambda connection: prep.attach_signout_voucher(
            connection, voucher_id=voucher_id,
            expected_version=_version(data),
            document_id=document_id)))


@bp.post("/api/review/addendum/<addendum_id>/complete")
@admin_required
def complete_addendum(addendum_id):
    data = json_body("submission_id", "expected_version", "document_id")
    submission = _submission(data)
    document_id = data.get("document_id")
    if not document_id:
        raise ValidationFailure("document_required",
                                "Select the signed addendum")
    return ok(run_command(
        g.db, submission, "complete_addendum_document",
        lambda connection: prep.complete_addendum_document(
            connection, addendum_id=addendum_id,
            expected_version=_version(data),
            document_id=document_id)))


@bp.get("/capture")
@admin_required
def capture_page():
    return render_template("capture.html")


@bp.get("/review")
@admin_required
def review_page():
    return render_template("review.html")
