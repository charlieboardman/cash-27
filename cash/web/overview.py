"""Operational overview and read-only global search."""

from __future__ import annotations

from decimal import Decimal

from flask import Blueprint, g, render_template, request

from .. import prep, repo, signouts
from ..model import SAFE
from ..money import to_text
from . import review
from .app import admin_required, ok

bp = Blueprint("overview", __name__)

ATTENTION_LIMIT = 6
INBOX_LIMIT = 5
SEARCH_LIMIT = 12


def _money_text(value) -> str:
    return f"{Decimal(str(value)):,.2f}"


def _minor_text(value) -> str:
    return _money_text(to_text(value))


def _cash_position():
    replayed = repo.replay_projected(g.db)
    summaries = signouts.summaries(g.db, replayed)
    safe = replayed.states.get(SAFE)
    open_states = [replayed.states.get(voucher_id)
                   for voucher_id, summary in summaries.items()
                   if summary["open"]]
    open_states = [state for state in open_states if state is not None]
    zero = Decimal(0)
    return {
        "safe": {
            "usd": _minor_text(safe.usd if safe else 0),
            "mxn": _minor_text(safe.mxn if safe else 0),
            "basis_usd": _money_text(safe.basis if safe else zero),
        },
        "open_signouts": {
            "count": sum(1 for summary in summaries.values() if summary["open"]),
            "usd": _minor_text(sum((state.usd for state in open_states), 0)),
            "mxn": _minor_text(sum((state.mxn for state in open_states), 0)),
            "value_usd": _money_text(sum(
                (state.value_usd for state in open_states), zero)),
        },
    }


def _attention_url(item) -> str:
    kind = item["kind"]
    if kind == "expense_report":
        return item.get("url") or "/review"
    if kind in ("withdrawal", "exchange", "count"):
        row = g.db.execute(
            "SELECT txn_id FROM transactions WHERE txn_id=?",
            (item["id"],)).fetchone()
    else:
        clauses = {
            "direct_expense": ("voucher_id", "expense"),
            "signout": ("voucher_id", "signout"),
            "addendum": ("addendum_id", "addendum"),
        }
        if kind not in clauses:
            return "/review"
        column, txn_type = clauses[kind]
        row = g.db.execute(
            f"SELECT txn_id FROM transactions WHERE {column}=?"
            " AND txn_type=? AND posting_state='active'"
            " ORDER BY allocation_line, txn_id LIMIT 1",
            (item["id"], txn_type)).fetchone()
    return f"/transactions/{row['txn_id']}" if row else "/review"


def _attention_items():
    items = review._worklist_items()
    return [{**item, "url": _attention_url(item)} for item in items]


def _inbox_documents():
    hidden = prep.staged_document_ids(g.db)
    rows = [row for row in g.db.execute(
        "SELECT document_id, display_name, page_count, created_at"
        " FROM documents WHERE state='inbox'"
        " ORDER BY created_at DESC, document_id DESC")
        if row["document_id"] not in hidden]
    return rows


@bp.get("/overview")
@admin_required
def overview_page():
    attention = _attention_items()
    inbox = _inbox_documents()
    return render_template(
        "overview.html", cash=_cash_position(),
        attention_count=len(attention), attention_items=attention[:ATTENTION_LIMIT],
        attention_remaining=max(0, len(attention) - ATTENTION_LIMIT),
        inbox_count=len(inbox), inbox_documents=inbox[:INBOX_LIMIT],
        inbox_remaining=max(0, len(inbox) - INBOX_LIMIT))


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _transaction_result(row) -> dict:
    title = next((str(row[field]).strip() for field in
                  ("description", "recipient", "vendor", "voucher_id")
                  if row[field] and str(row[field]).strip()),
                 f"Txn-{row['txn_id']}")
    type_label = row["txn_type"].replace("_", " ").capitalize()
    subtitle = [f"Txn-{row['txn_id']}", type_label, row["date"]]
    if row["voucher_id"]:
        subtitle.append(row["voucher_id"])
    subtitle.append(f"{row['from_account']} → {row['to_account']}")
    if row["posting_state"] != "active":
        subtitle.append(row["posting_state"].capitalize())
    return {
        "kind": "transaction",
        "id": row["txn_id"],
        "title": title,
        "subtitle": " · ".join(subtitle),
        "url": f"/transactions/{row['txn_id']}",
    }


def _document_result(row) -> dict:
    display_name = (row["display_name"] or "").strip()
    subtitle = [row["document_id"], row["state"].replace("_", " ").capitalize()]
    if row["page_count"] is not None:
        pages = row["page_count"]
        subtitle.append(f"{pages} page{'s' if pages != 1 else ''}")
    subtitle.append(row["created_at"][:10])
    return {
        "kind": "document",
        "id": row["document_id"],
        "title": display_name or row["document_id"],
        "subtitle": " · ".join(subtitle),
        "url": f"/documents/{row['document_id']}/preview",
    }


@bp.get("/api/search")
@admin_required
def search():
    query = (request.args.get("q") or "").strip()
    if len("".join(query.split())) < 2:
        return ok({"results": []})

    pattern = _like_pattern(query)
    transaction_fields = (
        "CAST(t.txn_id AS TEXT)", "t.voucher_id", "t.recipient", "t.vendor",
        "t.description", "t.project", "t.notes", "t.from_account", "t.to_account",
    )
    transaction_match = " OR ".join(
        f"{field} COLLATE NOCASE LIKE ? ESCAPE '\\'"
        for field in transaction_fields)
    transaction_rows = g.db.execute(
        "SELECT t.* FROM transactions t WHERE " + transaction_match +
        " ORDER BY t.date DESC, t.same_day_seq DESC, t.txn_id DESC LIMIT ?",
        (*([pattern] * len(transaction_fields)), SEARCH_LIMIT)).fetchall()
    results = [_transaction_result(row) for row in transaction_rows]

    remaining = SEARCH_LIMIT - len(results)
    if remaining:
        document_rows = g.db.execute(
            "SELECT document_id, display_name, page_count, state, created_at"
            " FROM documents WHERE document_id COLLATE NOCASE LIKE ? ESCAPE '\\'"
            " OR display_name COLLATE NOCASE LIKE ? ESCAPE '\\'"
            " ORDER BY created_at DESC, document_id DESC LIMIT ?",
            (pattern, pattern, remaining)).fetchall()
        results.extend(_document_result(row) for row in document_rows)
    return ok({"results": results})
