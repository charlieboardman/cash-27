"""A signout's expense report while it is being written and reviewed.

The report is one JSON document in expense_report_drafts (migration 0015).
Its rows are working data only. Approval posts every unapproved expense row
to the ledger through post_receipt and settles the signout's cash, all in the
same command that marks the rows approved, so the financial tables never hold
a half-finished report.

Draft format 1 (amounts are signed integer minor units):

    {"format": 1,
     "status": "draft" | "submitted" | "approved",
     "note": str,                     # recipient's explanation, if any
     "review": {"summary": str},      # administrator's correction request
     "rows": [{
        "id": str, "rev": int,        # rev guards concurrent edits per row
        "kind": "expense" | "cash_return" | "reimbursement" | "discrepancy",
        "status": "draft" | "approved",
        "document_id": str | None, "no_receipt_reason": str,
        "rounding": "positive" | "return", # optional: settlement paper is receipt
        "date": "YYYY-MM-DD" | "", "vendor": str, "purpose": str,
        "project": str,               # optional, set by administrators; may be absent
        "usd": int, "mxn": int,       # negative means change received
        "receipt_total": int | None,  # mixed-sign rows only, in the change currency
        "class_code": str | None, "expense_account": str | None,
        "splits": [{"id", "usd", "mxn", "receipt_total", "purpose",
                    "class_code", "expense_account"}],
        "admin_note": str,
        "receipt_number": int,        # unapproved expense rows; may be absent
        "purchases": [{"date", "vendor", "purpose", "usd", "mxn"}],
                                      # optional composite log, decimal strings
        "document_kind": "statement" | "log",  # a signed no-receipt statement
                                      # or the signout's purchase log; absent
                                      # for a vendor receipt
        "later_receipts": [{"receipt_id", "document_id"}],  # receipts found after
                                      # approval with a statement; may be absent
        "posted": {"receipt_id": str, "txn_ids": [int]} | None}],  # a posted
                                      # discrepancy has txn_ids only
     "last_receipt": int,            # highest receipt number given; optional
     "last_settlement_seq": int,      # optional reserved addendum sequence
     "cash_resolution": {currency: {"method", "settlement", "exact", "actual",
                                    "difference"}}, # optional, usd/mxn minor units
     "settlement_papers": {kind: {"paper_id", "fingerprint", "document_id",
                                  "date", "approver_name"}}}

An expense row gets its receipt number (FY27-0042-R3) when it is first
saved, so paper can name it before approval; approval posts it under that
number. A deleted row leaves a gap: numbers are never reused. Both keys were
added to format 1 without a new number so the previous release, which
refuses unknown formats, can still read the report.

Cash return and reimbursement rows are declarations with nonnegative
amounts. Approval settles them: the administrator confirms the cash that
actually came back or was paid, and the return and reimbursement addenda are
posted by closeouts.settle. A discrepancy row is missing cash: its
purpose is the explanation, its signed statement is required to approve, and
approval posts it as an adjustment from the signout into RECONCILIATION.
"""

from __future__ import annotations

from datetime import date as calendar_date
import json
import uuid

from . import paper, prep, repo, signouts
from .commands import ValidationFailure
from .model import RECONCILIATION, Row
from .money import to_text
from .repo import ConflictError, replay_projected

FORMAT = 1
KINDS = ("expense", "cash_return", "reimbursement", "discrepancy")
ROW_FIELDS = {"kind", "document_id", "document_kind", "no_receipt_reason", "date", "vendor",
              "purpose", "project", "usd", "mxn", "receipt_total", "class_code",
              "expense_account", "splits"}
SPLIT_FIELDS = {"id", "usd", "mxn", "receipt_total", "purpose",
                "class_code", "expense_account"}
USABLE_DOCUMENT_STATES = ("signee_draft", "package_submitted")


# ---- storage -----------------------------------------------------------------

def _empty() -> dict:
    return {"format": FORMAT, "status": "draft", "note": "",
            "review": {"summary": ""}, "rows": []}


def _checked(draft: dict) -> dict:
    """Only format 1 has ever been written; refuse anything else rather than
    misread it."""
    if draft.get("format") != FORMAT:
        raise ValueError(f"Unknown expense report format {draft.get('format')}")
    return draft


def stored(conn, voucher_id):
    row = conn.execute(
        "SELECT version, draft_json FROM expense_report_drafts"
        " WHERE voucher_id=?", (voucher_id,)).fetchone()
    return (_checked(json.loads(row["draft_json"])), row["version"]) if row else None


def load(conn, voucher_id) -> tuple[dict, int]:
    """Return (draft, version). Version 0 means nothing is stored yet."""
    found = stored(conn, voucher_id)
    return found if found else (_empty(), 0)


def _number_rows(conn, voucher_id, draft):
    """Give each unapproved expense or discrepancy row the next unused number."""
    last = max(draft.get("last_receipt", 0), conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM receipts WHERE voucher_id=?",
        (voucher_id,)).fetchone()[0])
    for row in draft["rows"]:
        if row["kind"] not in ("expense", "discrepancy") or row["posted"]:
            row.pop("receipt_number", None)
        elif "receipt_number" not in row:
            last += 1
            row["receipt_number"] = last
            draft["last_receipt"] = last


def receipt_reference(voucher_id, row) -> str | None:
    """The receipt number a person sees for a row, approved or not."""
    if row["posted"]:
        return row["posted"].get("receipt_id")
    return f"{voucher_id}-R{row['receipt_number']}" if row.get("receipt_number") else None


def _store(conn, voucher_id, draft, version) -> int:
    _number_rows(conn, voucher_id, draft)
    payload = json.dumps(draft, sort_keys=True, separators=(",", ":"))
    if version == 0:
        conn.execute("INSERT INTO expense_report_drafts (voucher_id, draft_json)"
                     " VALUES (?, ?)", (voucher_id, payload))
        return 1
    conn.execute("UPDATE expense_report_drafts SET draft_json=?, version=version+1,"
                 " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE voucher_id=?",
                 (payload, voucher_id))
    return version + 1


def _new_row(kind, row_id=None) -> dict:
    return {"id": row_id or str(uuid.uuid4()), "rev": 1, "kind": kind,
            "status": "draft", "document_id": None, "no_receipt_reason": "",
            "date": "", "vendor": "", "purpose": "", "usd": 0, "mxn": 0,
            "receipt_total": None, "class_code": None, "expense_account": None,
            "splits": [], "admin_note": "", "posted": None}


# ---- derived facts ----------------------------------------------------------

def is_mixed(usd, mxn) -> bool:
    """Opposite signs: the change currency's value needs the receipt total."""
    return (usd > 0 > mxn) or (mxn > 0 > usd)


def change_currency(usd, mxn):
    return "MXN" if usd > 0 > mxn else "USD" if mxn > 0 > usd else None


def open_work(draft) -> dict:
    """What a stored draft contributes to the signout's open reasons."""
    pending = [r for r in draft["rows"] if r["kind"] == "expense" and r["status"] != "approved"]
    return {"open_rows": len(pending), "submitted": draft["status"] == "submitted"}


def balance(conn, voucher_id, draft, replayed=None) -> dict:
    """Cash the report says is left: the ledger balance, minus expenses not
    yet approved, minus declared returns and lost cash, plus declared
    reimbursements, until approval settles them into the ledger."""
    replayed = replayed or replay_projected(conn)
    state = replayed.states.get(voucher_id)
    usd, mxn = (state.usd, state.mxn) if state else (0, 0)
    for row in draft["rows"]:
        if row["kind"] == "expense" and row["status"] != "approved":
            usd, mxn = usd - row["usd"], mxn - row["mxn"]
        elif row["kind"] != "expense" and row["status"] != "approved":
            sign = 1 if row["kind"] == "reimbursement" else -1
            usd, mxn = usd + sign * row["usd"], mxn + sign * row["mxn"]
    return {"usd": usd, "mxn": mxn, "balanced": usd == 0 and mxn == 0,
            # Spent pesos bought with dollars: settlement infers the exchange.
            "implied_exchange": usd > 0 > mxn}


def declared_settlement(draft) -> tuple[int, int, int, int]:
    """(return USD, return MXN, reimburse USD, reimburse MXN) from the rows."""
    def total(kind, currency):
        return sum(r[currency] for r in draft["rows"] if r["kind"] == kind)
    return (total("cash_return", "usd"), total("cash_return", "mxn"),
            total("reimbursement", "usd"), total("reimbursement", "mxn"))


def settlement_cash(conn, voucher_id, draft, replayed=None):
    """Signed cash the pending report leaves before declarations.

    Positive MXN is cash the recipient returns; negative MXN is cash the
    recipient is owed. Declarations are not part of this number: the
    administrator settles the actual handoff, not the recipient's estimate.
    """
    replayed = replayed or replay_projected(conn)
    state = replayed.states.get(voucher_id)
    if not state:
        return 0, 0
    pending = [r for r in draft["rows"]
               if r["status"] != "approved" and r["kind"] in ("expense", "discrepancy")]
    return (state.usd - sum(r["usd"] for r in pending),
            state.mxn - sum(r["mxn"] for r in pending))


def cash_difference(conn, voucher_id, draft, replayed=None):
    """Unresolved cash handoffs, independently for each currency."""
    if draft["status"] != "submitted":
        return None
    usd, mxn = settlement_cash(conn, voucher_id, draft, replayed)
    if usd > 0 > mxn:
        return None
    result = {}
    for currency, amount in (("usd", usd), ("mxn", mxn)):
        resolved = (draft.get("cash_resolution") or {}).get(currency, {})
        if not amount or resolved.get("settlement") == amount:
            continue
        exact = abs(amount)
        reimbursement = amount < 0
        result[currency] = {
            "currency": currency.upper(),
            "direction": "reimbursement" if reimbursement else "return",
            "exact": to_text(exact),
            "suggested": to_text(((exact + 99) // 100 if reimbursement else exact // 100) * 100),
            "can_missing_cash": not reimbursement,
        }
    return result or None


# ---- validation -------------------------------------------------------------

def _valid_code(conn, table, value, *, what):
    if value is None:
        return None
    if table == "classes":
        found = conn.execute("SELECT 1 FROM classes WHERE class_code=? AND active=1",
                             (value,)).fetchone()
    else:
        found = conn.execute(
            "SELECT 1 FROM accounts WHERE account_id=? AND kind='expense' AND active=1"
            " AND account_id!='UNALLOCATED_EXPENSE'", (value,)).fetchone()
    if not found:
        raise ValidationFailure(f"bad_{what}", f"Choose an available {what.replace('_', ' ')}")
    return value


def _clean_split(conn, split) -> dict:
    unknown = set(split) - SPLIT_FIELDS
    if unknown:
        raise ValidationFailure("unknown_fields", f"Unknown split fields: {sorted(unknown)}")
    return {"id": str(split.get("id") or uuid.uuid4()),
            "usd": int(split.get("usd") or 0), "mxn": int(split.get("mxn") or 0),
            "receipt_total": (int(split["receipt_total"])
                              if split.get("receipt_total") is not None else None),
            "purpose": str(split.get("purpose") or "").strip(),
            "class_code": _valid_code(conn, "classes", split.get("class_code") or None,
                                      what="class_code"),
            "expense_account": _valid_code(conn, "accounts", split.get("expense_account") or None,
                                           what="account_code")}


def _attach_document(conn, voucher_id, document_id, *, admin):
    from .document_jobs import require_ready
    row = conn.execute("SELECT state, voucher_id FROM documents WHERE document_id=?",
                       (document_id,)).fetchone()
    usable = row and (
        (row["voucher_id"] == voucher_id and row["state"] in USABLE_DOCUMENT_STATES)
        or (admin and row["state"] == "inbox"))
    if not usable:
        raise ValidationFailure("bad_document", "That receipt document is unavailable")
    require_ready(conn, document_id)
    conn.execute("UPDATE documents SET state='signee_draft', voucher_id=?"
                 " WHERE document_id=?", (voucher_id, document_id))


def _release_document(conn, voucher_id, document_id):
    """A receipt removed from the report is discarded, as before."""
    conn.execute("UPDATE documents SET state='discarded' WHERE document_id=?"
                 " AND voucher_id=? AND state IN ('signee_draft','package_submitted')",
                 (document_id, voucher_id))


def _check_row_complete(row, number, *, for_approval, draft=None):
    from .report_rows import validate_purchases
    validate_purchases(row)
    where = f"Row {number}"
    if row["kind"] != "expense":
        if row["usd"] < 0 or row["mxn"] < 0 or not (row["usd"] or row["mxn"]):
            raise ValidationFailure("bad_amount", f"{where}: enter the cash amount")
        if row["kind"] == "discrepancy":
            if not row["purpose"].strip():
                raise ValidationFailure("purpose_required", f"{where}: explain what happened to the cash")
            if for_approval and not row["document_id"]:
                raise ValidationFailure("statement_required", f"{where}: attach the signed statement")
        return
    try:
        calendar_date.fromisoformat(row["date"])
    except (TypeError, ValueError) as exc:
        raise ValidationFailure("bad_date", f"{where}: enter the receipt date") from exc
    usd, mxn = row["usd"], row["mxn"]
    if usd <= 0 and mxn <= 0:
        raise ValidationFailure("bad_amount", f"{where}: enter the amount spent")
    if not row["purpose"].strip():
        raise ValidationFailure("purpose_required", f"{where}: describe what it was for")
    if row.get("rounding"):
        if for_approval:
            if not (draft or {}).get("settlement_papers", {}).get(row["rounding"], {}).get("document_id"):
                raise ValidationFailure("paperwork_needed", f"{where}: file the signed settlement addendum")
            check_codes(row, where)
        return
    if not row["document_id"] and not row["no_receipt_reason"].strip():
        raise ValidationFailure("receipt_required",
                                f"{where}: attach the receipt or explain why there is none")
    if is_mixed(usd, mxn) and not (row["receipt_total"] or 0) > 0:
        raise ValidationFailure(
            "receipt_total_required",
            f"{where}: enter the receipt total in {change_currency(usd, mxn)}")
    check_split(row, where)
    if for_approval and not row["document_id"]:
        raise ValidationFailure(
            "statement_required",
            f"{where}: attach the receipt or the signed no-receipt statement")
    if for_approval:
        check_codes(row, where)


def check_split(row, where):
    """A split has two or more parts that add up to the row, receipt totals
    included. Shared with direct expenses in Review."""
    parts = row["splits"]
    if not parts:
        return
    if len(parts) < 2:
        raise ValidationFailure("bad_split", f"{where}: a split needs two or more parts")
    if sum(p["usd"] for p in parts) != row["usd"] or sum(p["mxn"] for p in parts) != row["mxn"]:
        raise ValidationFailure("split_mismatch",
                                f"{where}: the split amounts must add up to the row")
    if is_mixed(row["usd"], row["mxn"]) and \
            sum(p["receipt_total"] or 0 for p in parts) != row["receipt_total"]:
        raise ValidationFailure("split_mismatch",
                                f"{where}: the split receipt totals must add up to the receipt total")


def check_codes(row, where):
    if any(not line["class_code"] or not line["expense_account"]
           for line in row["splits"] or [row]):
        raise ValidationFailure("codes_required",
                                f"{where}: choose the class and account codes")


# ---- commands ---------------------------------------------------------------
# Each runs inside run_command, which serializes writers, so read-modify-write
# of the draft cannot lose another request's edit. Row revs catch edits made
# from a page that had not seen the latest version of that row.

def _editable(conn, voucher_id, draft, *, admin):
    summary = signouts.summary(conn, voucher_id)
    if not summary or summary["read_only"]:
        raise ValidationFailure("voucher_read_only", "This signout is closed")
    if draft["status"] == "submitted" and not admin:
        raise ValidationFailure("report_locked",
                                "Your report was submitted. Reopen it to make changes.")


def _find(draft, row_id, expected_rev=None):
    for row in draft["rows"]:
        if row["id"] == row_id:
            if expected_rev is not None and row["rev"] != expected_rev:
                raise ConflictError("This row changed on another screen; reload to see it")
            return row
    raise ValidationFailure("not_found", "Row not found")


def _touch(draft):
    """New work after approval reopens the report."""
    if draft["status"] == "approved":
        draft["status"] = "draft"


def add_row(conn, *, voucher_id, admin, kind="expense", fields=None) -> dict:
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=admin)
    if not admin and kind != "expense":
        raise ValidationFailure("forbidden", "Only an administrator can change cash rows")
    if kind not in KINDS:
        raise ValidationFailure("bad_kind", "Unknown row type")
    row = _new_row(kind)
    draft["rows"].append(row)
    _apply(conn, voucher_id, row, fields or {}, admin=admin)
    _one_log(draft)
    _touch(draft)
    return {"row": row, "version": _store(conn, voucher_id, draft, version)}


def _apply(conn, voucher_id, row, fields, *, admin):
    unknown = set(fields) - ROW_FIELDS
    if unknown:
        raise ValidationFailure("unknown_fields", f"Unknown row fields: {sorted(unknown)}")
    if not admin and fields.get("kind", "expense") != "expense":
        raise ValidationFailure("forbidden", "Only an administrator can change cash rows")
    if "kind" in fields:
        if fields["kind"] not in KINDS:
            raise ValidationFailure("bad_kind", "Unknown row type")
        row["kind"] = fields["kind"]
    if "document_id" in fields and fields["document_id"] != row["document_id"]:
        if fields["document_id"]:
            _attach_document(conn, voucher_id, fields["document_id"], admin=admin)
        if row["document_id"]:
            _release_document(conn, voucher_id, row["document_id"])
        row["document_id"] = fields["document_id"] or None
        kind = fields.get("document_kind")
        if row["document_id"] and kind in ("statement", "log"):
            row["document_kind"] = kind
        else:
            row.pop("document_kind", None)
    if "date" in fields:
        value = str(fields["date"] or "")
        if value:
            try:
                calendar_date.fromisoformat(value)
            except ValueError as exc:
                raise ValidationFailure("bad_date", "Enter a valid date") from exc
        row["date"] = value
    # Vendor, purpose, and explanation print on the no-receipt statement.
    printed = {"vendor": ("The vendor", paper.ROW_VENDOR),
               "purpose": ("The purpose", paper.ROW_PURPOSE),
               "no_receipt_reason": ("The explanation", paper.REASON)}
    for text in ("vendor", "purpose", "project", "no_receipt_reason"):
        if text in fields:
            row[text] = (paper.check(fields[text], text, *printed[text]) if text in printed
                         else str(fields[text] or "").strip())
    for amount in ("usd", "mxn"):
        if amount in fields:
            row[amount] = int(fields[amount] or 0)
    if "receipt_total" in fields:
        total = fields["receipt_total"]
        row["receipt_total"] = int(total) if total not in (None, "") else None
        if row["receipt_total"] is not None and row["receipt_total"] < 0:
            raise ValidationFailure("bad_amount", "The receipt total cannot be negative")
    if "class_code" in fields:
        row["class_code"] = _valid_code(conn, "classes", fields["class_code"] or None,
                                        what="class_code")
    if "expense_account" in fields:
        row["expense_account"] = _valid_code(conn, "accounts", fields["expense_account"] or None,
                                             what="account_code")
    if "splits" in fields:
        row["splits"] = [_clean_split(conn, split) for split in fields["splits"] or []]
    if row["kind"] != "expense":
        # Declarations carry nothing but an amount and a note; only a lost-cash
        # statement keeps its signed document.
        if row["document_id"] and row["kind"] != "discrepancy":
            _release_document(conn, voucher_id, row["document_id"])
            row["document_id"] = None
        row.update(receipt_total=None, class_code=None, expense_account=None, splits=[])


def _one_log(draft):
    if sum(row.get("document_kind") == "log" or "purchases" in row for row in draft["rows"]) > 1:
        raise ValidationFailure("log_exists", "This report already has its purchase log")


def update_row(conn, *, voucher_id, row_id, expected_rev, fields, admin) -> dict:
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=admin)
    row = _find(draft, row_id, expected_rev)
    if not admin and (row["kind"] != "expense" or row.get("rounding")):
        raise ValidationFailure("forbidden", "Only an administrator can change cash rows")
    if row["posted"]:
        raise ValidationFailure("row_approved",
                                "This row is approved. Correct it from the ledger.")
    if "purchases" in row:
        raise ValidationFailure("composite_row", "Edit purchases through the purchase log")
    if row.get("rounding") and any(k not in ("class_code", "expense_account") and v != row.get(k) for k, v in fields.items()):
        raise ValidationFailure("rounding_panel", "Change cash rounding through the cash settlement panel")
    _apply(conn, voucher_id, row, fields, admin=admin)
    _one_log(draft)
    # An approved declaration that changes needs approving again.
    row["status"] = "draft"
    row["rev"] += 1
    _touch(draft)
    return {"row": row, "version": _store(conn, voucher_id, draft, version)}


def delete_row(conn, *, voucher_id, row_id, expected_rev, admin) -> dict:
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=admin)
    row = _find(draft, row_id, expected_rev)
    if not admin and (row["kind"] != "expense" or row.get("rounding")):
        raise ValidationFailure("forbidden", "Only an administrator can change cash rows")
    if row["posted"]:
        raise ValidationFailure("row_approved",
                                "This row is approved. Void it from the ledger.")
    if row["document_id"]:
        _release_document(conn, voucher_id, row["document_id"])
    draft["rows"].remove(row)
    if row.get("rounding"):
        draft.get("cash_resolution", {}).pop("usd" if row["usd"] else "mxn", None)
    _touch(draft)
    return {"row_id": row_id, "version": _store(conn, voucher_id, draft, version)}


def add_documentation(conn, *, voucher_id, row_id, document_id, admin,
                      reviewed_date=None) -> dict:
    """File a receipt for an approved row after the fact. A row approved
    without a document gets its receipt. A row approved with a no-receipt
    statement or the purchase log keeps it, and the receipt or missing log
    sheet that turned up takes the next receipt number with no amounts of its
    own. A row with a vendor receipt is never added to. Amounts and codes stay as approved, the
    report's status is unchanged, and a confirmed settlement stays valid."""
    from .document_jobs import require_ready
    voucher = conn.execute("SELECT status FROM vouchers WHERE voucher_id=?",
                           (voucher_id,)).fetchone()
    if not voucher or voucher["status"] == "void":
        raise ValidationFailure("voucher_void", "This signout is void")
    draft, version = load(conn, voucher_id)
    row = _find(draft, row_id)
    if row.get("rounding"):
        raise ValidationFailure("receipt_already_filed", "This receipt already has its document")
    if not row["posted"]:
        raise ValidationFailure("row_not_approved", "Attach receipts to this row as usual")
    if row["document_id"] and row.get("document_kind") not in ("statement", "log"):
        raise ValidationFailure("receipt_already_filed", "This receipt already has its document")
    document = conn.execute("SELECT state, voucher_id FROM documents WHERE document_id=?",
                            (document_id,)).fetchone()
    usable = document and (
        (document["voucher_id"] == voucher_id and document["state"] == "signee_draft")
        or (admin and document["state"] == "inbox"))
    if not usable:
        raise ValidationFailure("bad_document", "That receipt document is unavailable")
    require_ready(conn, document_id)
    receipt_id = row["posted"]["receipt_id"]
    if row["document_id"]:
        seq = max(draft.get("last_receipt", 0), conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM receipts WHERE voucher_id=?",
            (voucher_id,)).fetchone()[0]) + 1
        receipt_id = f"{voucher_id}-R{seq}"
        conn.execute(
            "INSERT INTO receipts (receipt_id, voucher_id, seq, document_id, reviewed_date)"
            " VALUES (?,?,?,?, COALESCE(?, strftime('%Y-%m-%d','now')))",
            (receipt_id, voucher_id, seq, document_id, reviewed_date))
        draft["last_receipt"] = seq
        row.setdefault("later_receipts", []).append(
            {"receipt_id": receipt_id, "document_id": document_id})
    else:
        conn.execute("UPDATE receipts SET document_id=? WHERE receipt_id=?",
                     (document_id, receipt_id))
        row["document_id"] = document_id
    prep.file_document(conn, document_id, row["posted"]["txn_ids"], "receipt",
                       voucher_id=voucher_id)
    row["rev"] += 1
    return {"row": row, "receipt_id": receipt_id,
            "version": _store(conn, voucher_id, draft, version)}


def set_codes(conn, *, voucher_id, row_ids, admin, **codes) -> dict:
    """Set class and/or account on several rows at once, including every
    part of a split row. Only the codes passed are changed."""
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=admin)
    if not row_ids:
        raise ValidationFailure("no_rows", "Select at least one row")
    values = {}
    if "class_code" in codes:
        values["class_code"] = _valid_code(conn, "classes", codes["class_code"] or None,
                                           what="class_code")
    if "expense_account" in codes:
        values["expense_account"] = _valid_code(conn, "accounts", codes["expense_account"] or None,
                                                what="account_code")
    changed = []
    for row_id in dict.fromkeys(row_ids):
        row = _find(draft, row_id)
        if row["posted"] or row["kind"] != "expense":
            continue
        if row.get("rounding") and not admin:
            raise ValidationFailure("forbidden", "Only an administrator can change cash rows")
        row.update(values)
        for split in row["splits"]:
            split.update(values)
        row["rev"] += 1
        changed.append(row)
    return {"rows": changed, "version": _store(conn, voucher_id, draft, version)}


def set_note(conn, *, voucher_id, note, admin) -> dict:
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=admin)
    draft["note"] = str(note or "").strip()
    return {"note": draft["note"], "version": _store(conn, voucher_id, draft, version)}


def resolve_cash_difference(conn, *, voucher_id, method, actual_mxn=None,
                            actual=None, currency="mxn", reason="", date=None) -> dict:
    """Add a receipt for recipient-favorable rounding; never alter receipts."""
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=True)
    if draft["status"] != "submitted":
        raise ValidationFailure("not_submitted", "Submit the expense report before settling it")
    if currency not in ("usd", "mxn") or method not in ("rounding", "missing_cash"):
        raise ValidationFailure("bad_method", "Choose cash rounding or missing cash")
    actual = actual if actual is not None else actual_mxn
    if actual is None or str(actual).strip() == "":
        raise ValidationFailure("bad_amount", "Enter the actual cash amount")
    actual = int(actual)
    if actual < 0 or actual % 100:
        raise ValidationFailure("bad_amount", "Enter nonnegative whole units")
    existing = next((r for r in draft["rows"] if r.get("rounding") and r[currency] and not r["posted"]), None)
    base = {**draft, "rows": [r for r in draft["rows"] if r is not existing]}
    usd, mxn = settlement_cash(conn, voucher_id, base)
    if usd > 0 > mxn:
        raise ValidationFailure("implied_exchange", "Cash rounding does not apply to a currency exchange")
    exact = usd if currency == "usd" else mxn
    if not exact:
        raise ValidationFailure("no_cash_difference", "There is no cash to settle")
    direction = "reimbursement" if exact < 0 else "return"
    difference = actual - abs(exact) if exact < 0 else exact - actual
    if method == "missing_cash" and direction != "return":
        raise ValidationFailure("missing_cash_not_applicable", "Missing cash applies only when the cash returned is short")
    if difference < 0:
        raise ValidationFailure("recipient_unfavorable", "Cash rounding must be in the recipient's favor")
    if method == "missing_cash":
        if direction != "return" or difference <= 0 or existing:
            raise ValidationFailure("missing_cash_not_applicable", "Missing cash applies only when the cash returned is short")
        reason = paper.check(reason, "purpose", "The explanation", paper.ROW_PURPOSE)
        if not reason:
            raise ValidationFailure("purpose_required", "Explain what happened to the cash")
        row = _new_row("discrepancy")
        row.update({currency: difference, "purpose": reason})
        draft["rows"].append(row)
    elif difference:
        row = existing or _new_row("expense")
        if existing:
            row["rev"] += 1
        else:
            lines = [part for r in base["rows"] if r["kind"] == "expense" and not r["posted"]
                     for part in (r["splits"] or [r]) if part[currency] > 0]
            largest = max(lines, key=lambda line: line[currency], default={})
            row.update({key: largest.get(key) for key in ("class_code", "expense_account")})
            draft["rows"].append(row)
        row.update({currency: difference, "date": date or calendar_date.today().isoformat(),
                    "purpose": "Cash rounding", "rounding": "positive" if exact < 0 else "return"})
    elif existing:
        draft["rows"].remove(existing)
    draft.setdefault("cash_resolution", {})[currency] = {
        "method": method, "settlement": -actual if exact < 0 else actual,
        "exact": abs(exact), "actual": actual, "difference": difference}
    return {"version": _store(conn, voucher_id, draft, version)}


def submit(conn, *, voucher_id, admin, calculated=False) -> dict:
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=admin)
    pending = [(number, row) for number, row in enumerate(draft["rows"], 1)
               if row["status"] != "approved"]
    if not pending and not calculated:
        raise ValidationFailure("empty_report", "Add at least one row before submitting")
    for number, row in pending:
        _check_row_complete(row, number, for_approval=False)
    if not calculated:
        left = balance(conn, voucher_id, draft)
        if not (left["balanced"] or left["implied_exchange"]):
            raise ValidationFailure("totals_do_not_add_up",
                                    "The totals do not add up. Enter the missing cash and explain it.")
    draft["status"] = "submitted"
    draft["review"] = {"summary": ""}
    for _, row in pending:
        row["admin_note"] = ""
    return {"status": draft["status"], "version": _store(conn, voucher_id, draft, version)}


def reopen(conn, *, voucher_id) -> dict:
    """The recipient takes back a submitted report to keep editing."""
    draft, version = load(conn, voucher_id)
    if draft["status"] != "submitted":
        raise ValidationFailure("not_submitted", "This report is not waiting for review")
    draft["rows"] = [r for r in draft["rows"] if not r.get("rounding") or r["posted"]]
    draft.pop("cash_resolution", None)
    draft["status"] = "draft"
    return {"status": draft["status"], "version": _store(conn, voucher_id, draft, version)}


def request_corrections(conn, *, voucher_id, summary, row_notes=None) -> dict:
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=True)
    if not str(summary or "").strip():
        raise ValidationFailure("summary_required", "Explain what needs to be corrected")
    for row_id, note in (row_notes or {}).items():
        row = _find(draft, row_id)
        row["admin_note"] = str(note or "").strip()
        row["rev"] += 1
    draft["status"] = "draft"
    draft["rows"] = [r for r in draft["rows"] if not r.get("rounding") or r["posted"]]
    draft.pop("cash_resolution", None)
    draft["review"] = {"summary": str(summary).strip()}
    return {"status": draft["status"], "version": _store(conn, voucher_id, draft, version)}


def approve(conn, *, voucher_id, submission_id, settlement, acknowledged=False,
            reviewed_date=None, calculated=False) -> dict:
    """Post every unapproved receipt and lost-cash row, then settle the
    signout's cash, all in the caller's command. Nothing is posted unless
    every row is complete and the cash reconciles."""
    from . import closeouts
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=True)
    pending = [(number, row) for number, row in enumerate(draft["rows"], 1)
               if row["status"] != "approved"]
    if not pending and not calculated:
        raise ValidationFailure("nothing_to_approve", "There are no new rows to approve")
    if any(r.get("rounding") for _, r in pending) and not calculated:
        raise ValidationFailure("calculated_required", "Use calculated approval to link the signed rounding addendum")
    for number, row in pending:
        _check_row_complete(row, number, for_approval=True, draft=draft)
        for line in row["splits"] or [row]:
            _valid_code(conn, "classes", line["class_code"], what="class_code")
            _valid_code(conn, "accounts", line["expense_account"], what="account_code")
    if any(row["kind"] == "discrepancy" for _, row in pending) and not acknowledged:
        raise ValidationFailure("discrepancy_unacknowledged",
                                "Acknowledge the missing cash to approve")
    for _, row in pending:
        if row["kind"] == "expense":
            row["posted"] = _post(conn, submission_id, voucher_id, row, reviewed_date, draft=draft)
        elif row["kind"] == "discrepancy":
            row["posted"] = _post_discrepancy(conn, submission_id, voucher_id, row, settlement["date"])
        row["status"] = "approved"
        row["admin_note"] = ""
        row["rev"] += 1
    draft["status"] = "approved"
    draft["review"] = {"summary": ""}
    version = _store(conn, voucher_id, draft, version)
    closeouts.settle(conn, submission_id=submission_id, voucher_id=voucher_id, **settlement)
    for _, row in pending:
        if row.get("rounding"):
            document_id = draft["settlement_papers"][row["rounding"]]["document_id"]
            doc = conn.execute("SELECT state, doc_type FROM documents WHERE document_id=?", (document_id,)).fetchone()
            if not doc or doc["doc_type"] != "addendum" or doc["state"] == "inbox":
                raise ValidationFailure("paperwork_needed", "Complete the signed rounding addendum")
            row["document_id"] = document_id
            conn.execute("UPDATE receipts SET document_id=? WHERE receipt_id=?", (document_id, row["posted"]["receipt_id"]))
            for txn_id in row["posted"]["txn_ids"]:
                conn.execute("INSERT INTO transaction_documents (txn_id, document_id, role) VALUES (?, ?, 'support')", (txn_id, document_id))
    if any(row.get("rounding") for _, row in pending):
        version = _store(conn, voucher_id, draft, version)
        # The confirmation snapshot includes the completed receipt links.
        serialized, fingerprint = closeouts.facts(conn, voucher_id)
        conn.execute("UPDATE signout_closeouts SET facts_json=?, facts_sha256=?"
                     " WHERE voucher_id=? AND submission_id=?",
                     (serialized, fingerprint, voucher_id, submission_id))
    return {"status": draft["status"], "version": version,
            "posted": [row["posted"] for _, row in pending if row["posted"]]}


def _post_discrepancy(conn, submission_id, voucher_id, row, date) -> dict:
    """Lost cash leaves the signout for RECONCILIATION, with its signed statement."""
    try:
        calendar_date.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise ValidationFailure("bad_date", "Enter a valid settlement date") from exc
    ids = repo.insert_rows(conn, submission_id, [Row(
        row_id="pending", movement_id=str(uuid.uuid4()), date=date,
        same_day_seq=prep.next_same_day_seq(conn, date, voucher_id),
        allocation_line=0, txn_type="adjustment", from_account=voucher_id,
        to_account=RECONCILIATION, usd=row["usd"], mxn=row["mxn"], voucher_id=voucher_id)])
    conn.execute("UPDATE transactions SET description=? WHERE txn_id=?", (row["purpose"], ids[0]))
    prep.file_document(conn, row["document_id"], ids, "statement")
    return {"txn_ids": ids}


def _post(conn, submission_id, voucher_id, row, reviewed_date=None, *, draft=None) -> dict:
    usd, mxn = row["usd"], row["mxn"]
    face_currency = change_currency(usd, mxn)

    def faces(total):
        return ((total, None) if face_currency == "USD" else
                (None, total) if face_currency == "MXN" else (None, None))

    note = f"No receipt: {row['no_receipt_reason']}" \
        if row.get("document_kind") == "statement" or not row["document_id"] else ""
    if row.get("rounding"):
        note = f"Settlement rounding; receipt: {draft['settlement_papers'][row['rounding']]['paper_id']}"
    lines = []
    for line in row["splits"] or [row]:
        face_usd, face_mxn = faces(line["receipt_total"] if row["splits"]
                                   else row["receipt_total"])
        lines.append({"usd_cents": line["usd"], "mxn_centavos": line["mxn"],
                      "expense_account": line["expense_account"],
                      "class_code": line["class_code"],
                      "face_usd_cents": face_usd, "face_mxn_centavos": face_mxn,
                      "project": row.get("project", ""),
                      "description": line["purpose"] or row["purpose"],
                      "notes": note})
    face_usd, face_mxn = faces(row["receipt_total"])
    posted = post_receipt(
        conn, submission_id, voucher_id, seq=row.get("receipt_number"),
        document_id=row["document_id"],
        date=row["date"], vendor=row["vendor"],
        face_usd_cents=face_usd, face_mxn_centavos=face_mxn,
        usd_paid_cents=max(usd, 0), mxn_paid_centavos=max(mxn, 0),
        usd_change_cents=max(-usd, 0), mxn_change_centavos=max(-mxn, 0),
        payment_mode="cash", lines=lines, reviewed_date=reviewed_date,
        doc_type={"statement": "statement", "log": "purchase_log"}.get(
            row.get("document_kind"), "receipt"))
    return {"receipt_id": posted["receipt_id"], "txn_ids": posted["txn_ids"]}


def post_receipt(conn, submission_id, voucher_id, *, document_id, date,
                 vendor, face_usd_cents, face_mxn_centavos, usd_paid_cents,
                 mxn_paid_centavos, usd_change_cents, mxn_change_centavos,
                 payment_mode, lines, seq=None, doc_type="receipt",
                 reviewed_date=None) -> dict:
    """Post one approved signout receipt: a receipts row, one expense
    movement with a transaction per allocation line, and the filed document.

    Each line is a dict with usd_cents, mxn_centavos, expense_account,
    class_code, face_usd_cents, face_mxn_centavos, project, description, and
    notes. The caller has validated the lines; run_command replays the result.
    seq is the row's receipt number. It is used unless already taken, which
    happens only if a previous release approved receipts in the meantime.
    """
    taken = seq and conn.execute(
        "SELECT 1 FROM receipts WHERE voucher_id=? AND seq=?", (voucher_id, seq)).fetchone()
    if not seq or taken:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM receipts WHERE"
            " voucher_id=?", (voucher_id,)).fetchone()[0]
    rid = f"{voucher_id}-R{seq}"
    conn.execute(
        "INSERT INTO receipts (receipt_id, voucher_id, seq,"
        " document_id, vendor,"
        " reviewed_date, face_usd_cents, face_mxn_centavos,"
        " usd_paid_cents, mxn_paid_centavos, usd_change_cents,"
        " mxn_change_centavos, payment_mode) VALUES (?,?,?,?,?,"
        " COALESCE(?, strftime('%Y-%m-%d','now')),?,?,?,?,?,?,?)",
        (rid, voucher_id, seq, document_id, vendor, reviewed_date,
         face_usd_cents, face_mxn_centavos,
         usd_paid_cents, mxn_paid_centavos,
         usd_change_cents, mxn_change_centavos, payment_mode))
    mv = str(uuid.uuid4())
    # Each receipt is its own movement; receipts on the same date need
    # distinct same-day sequences so replay order is truthful
    # (spec 13). New events go after existing ones on that date.
    seq = prep.next_same_day_seq(conn, date, voucher_id)
    rows = [Row(
        row_id="pending", movement_id=mv, date=date, same_day_seq=seq,
        allocation_line=line, txn_type="expense",
        from_account=voucher_id, to_account=a["expense_account"],
        usd=a["usd_cents"], mxn=a["mxn_centavos"],
        class_code=a["class_code"],
        receipt_value_mxn=a["face_mxn_centavos"],
        receipt_value_usd=a["face_usd_cents"],
        voucher_id=voucher_id, receipt_id=rid)
        for line, a in enumerate(lines)]
    ids = repo.insert_rows(conn, submission_id, rows)
    for txn_id, line in zip(ids, lines):
        conn.execute(
            "UPDATE transactions SET project=?, description=?, notes=?"
            " WHERE txn_id=?",
            (line["project"], line["description"], line["notes"], txn_id))
    if document_id:
        prep.file_document(conn, document_id, ids, doc_type)
    return {"receipt_id": rid, "movement_id": mv, "txn_ids": ids}
