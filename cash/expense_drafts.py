"""A signout's expense report while it is being written and reviewed.

The report is one JSON document in expense_report_drafts (migration 0015).
Its rows are working data only. Approval posts every unapproved expense row
to the ledger through post_receipt, in the same command that marks
the rows approved, so the financial tables never hold a half-finished report.

Draft format 1 (amounts are signed integer minor units):

    {"format": 1,
     "status": "draft" | "submitted" | "approved",
     "note": str,                     # recipient's explanation, if any
     "review": {"summary": str},      # administrator's correction request
     "rows": [{
        "id": str, "rev": int,        # rev guards concurrent edits per row
        "kind": "expense" | "cash_return" | "reimbursement",
        "status": "draft" | "approved",
        "document_id": str | None, "no_receipt_reason": str,
        "date": "YYYY-MM-DD" | "", "vendor": str, "purpose": str,
        "project": str,               # optional, set by administrators; may be absent
        "usd": int, "mxn": int,       # negative means change received
        "receipt_total": int | None,  # mixed-sign rows only, in the change currency
        "class_code": str | None, "expense_account": str | None,
        "splits": [{"id", "usd", "mxn", "receipt_total", "purpose",
                    "class_code", "expense_account"}],
        "admin_note": str,
        "receipt_number": int,        # unapproved expense rows; may be absent
        "document_kind": "statement" | "log",  # a signed no-receipt statement
                                      # or the signout's purchase log; absent
                                      # for a vendor receipt
        "later_receipts": [{"receipt_id", "document_id"}],  # receipts found after
                                      # approval with a statement; may be absent
        "posted": {"receipt_id": str, "txn_ids": [int]} | None}],
     "last_receipt": int}             # highest receipt number given; may be absent

An expense row gets its receipt number (FY27-0042-R3) when it is first
saved, so paper can name it before approval; approval posts it under that
number. A deleted row leaves a gap: numbers are never reused. Both keys were
added to format 1 without a new number so the previous release, which
refuses unknown formats, can still read the report.

Cash return and reimbursement rows are declarations with nonnegative
amounts. They feed the settlement proposal and are never posted here.

A signout without a stored draft is read from the older claim tables, so
reports in progress at upgrade time keep their receipts. Those rows are not
changed; the first edit stores the draft, which then supersedes them.
"""

from __future__ import annotations

from datetime import date as calendar_date
import json
import uuid

from . import paper, repo, signouts
from .commands import ValidationFailure
from .model import Row
from .repo import ConflictError, replay_projected

FORMAT = 1
KINDS = ("expense", "cash_return", "reimbursement")
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


def _upgrade(draft: dict) -> dict:
    """Bring an older stored format up to FORMAT. Readers accept every
    format ever written; writers only write the current one."""
    if draft.get("format") != FORMAT:
        raise ValueError(f"Unknown expense report format {draft.get('format')}")
    return draft


def stored(conn, voucher_id):
    row = conn.execute(
        "SELECT version, draft_json FROM expense_report_drafts"
        " WHERE voucher_id=?", (voucher_id,)).fetchone()
    return (_upgrade(json.loads(row["draft_json"])), row["version"]) if row else None


def load(conn, voucher_id) -> tuple[dict, int]:
    """Return (draft, version). Version 0 means nothing is stored yet."""
    found = stored(conn, voucher_id)
    return found if found else (_from_legacy(conn, voucher_id), 0)


def _number_rows(conn, voucher_id, draft):
    """Give each unapproved expense row the next unused receipt number."""
    last = max(draft.get("last_receipt", 0), conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM receipts WHERE voucher_id=?",
        (voucher_id,)).fetchone()[0])
    for row in draft["rows"]:
        if row["kind"] != "expense" or row["posted"]:
            row.pop("receipt_number", None)
        elif "receipt_number" not in row:
            last += 1
            row["receipt_number"] = last
            draft["last_receipt"] = last


def receipt_reference(voucher_id, row) -> str | None:
    """The receipt number a person sees for a row, approved or not."""
    if row["posted"]:
        return row["posted"]["receipt_id"]
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


def _from_legacy(conn, voucher_id) -> dict:
    """Present unaccepted legacy claims and the old declaration as a draft.

    Row IDs derive from the legacy records so a page can edit a row before
    the draft is first stored."""
    draft = _empty()
    for claim in conn.execute(
            "SELECT * FROM signout_expense_claims WHERE voucher_id=?"
            " AND state!='accepted' ORDER BY created_at, claim_id", (voucher_id,)):
        usd = claim["usd_paid_cents"] - claim["usd_change_cents"]
        mxn = claim["mxn_paid_centavos"] - claim["mxn_change_centavos"]
        face = claim["face_mxn_centavos"] if usd > 0 > mxn else \
            claim["face_usd_cents"] if mxn > 0 > usd else None
        allocations = conn.execute(
            "SELECT * FROM signout_claim_allocations WHERE claim_id=?"
            " ORDER BY line_no", (claim["claim_id"],)).fetchall()
        row = _new_row("expense", row_id=claim["claim_id"])
        row.update(document_id=claim["document_id"], date=claim["claim_date"],
                   vendor=claim["vendor"], usd=usd, mxn=mxn, receipt_total=face,
                   purpose=claim["notes"] or claim["description"],
                   admin_note=claim["admin_note"] if claim["state"] == "flagged" else "")
        if allocations:
            row["project"] = allocations[0]["project"]
        if len(allocations) == 1:
            row.update(class_code=allocations[0]["class_code"],
                       expense_account=_code(allocations[0]["expense_account"]))
        elif allocations:
            row["splits"] = [{
                "id": f"{claim['claim_id']}-{a['line_no']}",
                "usd": a["usd_cents"], "mxn": a["mxn_centavos"],
                "receipt_total": a["face_mxn_centavos"] if usd > 0 > mxn else
                a["face_usd_cents"] if mxn > 0 > usd else None,
                "purpose": a["description"] or a["notes"],
                "class_code": a["class_code"],
                "expense_account": _code(a["expense_account"])} for a in allocations]
        draft["rows"].append(row)
    report = conn.execute("SELECT * FROM signout_expense_reports WHERE voucher_id=?",
                          (voucher_id,)).fetchone()
    if report:
        for kind, usd, mxn in (("cash_return", report["cash_usd"], report["cash_mxn"]),
                               ("reimbursement", report["personal_usd"], report["personal_mxn"])):
            if usd or mxn:
                row = _new_row(kind, row_id=f"legacy-{kind}")
                row.update(usd=usd or 0, mxn=mxn or 0)
                draft["rows"].append(row)
        draft["note"] = report["discrepancy"]
    package = conn.execute(
        "SELECT status, admin_summary FROM signout_reconciliation_packages WHERE"
        " voucher_id=? AND status IN ('submitted','in_review','needs_changes')",
        (voucher_id,)).fetchone()
    if package and package["status"] == "needs_changes":
        draft["review"]["summary"] = package["admin_summary"]
    elif package or (report and report["status"] == "submitted"):
        draft["status"] = "submitted"
    return draft


def _code(account):
    return None if account == "UNALLOCATED_EXPENSE" else account


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
    yet approved, minus declared returns plus declared reimbursements until
    a confirmed settlement has moved that cash in the ledger."""
    replayed = replayed or replay_projected(conn)
    state = replayed.states.get(voucher_id)
    usd, mxn = (state.usd, state.mxn) if state else (0, 0)
    closeout = signouts.summary(conn, voucher_id, replayed)["closeout"]
    settled = bool(closeout and closeout["valid"])
    for row in draft["rows"]:
        if row["kind"] == "expense" and row["status"] != "approved":
            usd, mxn = usd - row["usd"], mxn - row["mxn"]
        elif row["kind"] != "expense" and not settled:
            sign = -1 if row["kind"] == "cash_return" else 1
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


def _clean_split(conn, split, existing_id=None) -> dict:
    unknown = set(split) - SPLIT_FIELDS
    if unknown:
        raise ValidationFailure("unknown_fields", f"Unknown split fields: {sorted(unknown)}")
    return {"id": str(split.get("id") or existing_id or uuid.uuid4()),
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


def _check_row_complete(row, number, *, for_approval):
    where = f"Row {number}"
    if row["kind"] != "expense":
        if row["usd"] < 0 or row["mxn"] < 0 or not (row["usd"] or row["mxn"]):
            raise ValidationFailure("bad_amount", f"{where}: enter the cash amount")
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
    if not row["document_id"] and not row["no_receipt_reason"].strip():
        raise ValidationFailure("receipt_required",
                                f"{where}: attach the receipt or explain why there is none")
    mixed = is_mixed(usd, mxn)
    if mixed and not (row["receipt_total"] or 0) > 0:
        raise ValidationFailure(
            "receipt_total_required",
            f"{where}: enter the receipt total in {change_currency(usd, mxn)}")
    lines = row["splits"] or [row]
    if row["splits"]:
        if len(row["splits"]) < 2:
            raise ValidationFailure("bad_split", f"{where}: a split needs two or more parts")
        if sum(s["usd"] for s in lines) != usd or sum(s["mxn"] for s in lines) != mxn:
            raise ValidationFailure("split_mismatch",
                                    f"{where}: the split amounts must add up to the row")
        if mixed and sum(s["receipt_total"] or 0 for s in lines) != row["receipt_total"]:
            raise ValidationFailure("split_mismatch",
                                    f"{where}: the split receipt totals must add up to the receipt total")
    if for_approval and not row["document_id"]:
        raise ValidationFailure(
            "statement_required",
            f"{where}: attach the receipt or the signed no-receipt statement")
    if for_approval and any(not line["class_code"] or not line["expense_account"]
                            for line in lines):
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


def assert_can_edit(conn, voucher_id, *, admin):
    _editable(conn, voucher_id, load(conn, voucher_id)[0], admin=admin)


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
        # Declarations carry nothing but an amount and an optional note.
        if row["document_id"]:
            _release_document(conn, voucher_id, row["document_id"])
        row.update(document_id=None, receipt_total=None, class_code=None,
                   expense_account=None, splits=[])


def _one_log(draft):
    if sum(row.get("document_kind") == "log" for row in draft["rows"]) > 1:
        raise ValidationFailure("log_exists", "This report already has its purchase log")


def update_row(conn, *, voucher_id, row_id, expected_rev, fields, admin) -> dict:
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=admin)
    row = _find(draft, row_id, expected_rev)
    if row["posted"]:
        raise ValidationFailure("row_approved",
                                "This row is approved. Correct it from the ledger.")
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
    if row["posted"]:
        raise ValidationFailure("row_approved",
                                "This row is approved. Void it from the ledger.")
    if row["document_id"]:
        _release_document(conn, voucher_id, row["document_id"])
    draft["rows"].remove(row)
    _touch(draft)
    return {"row_id": row_id, "version": _store(conn, voucher_id, draft, version)}


def add_documentation(conn, *, voucher_id, row_id, document_id, admin) -> dict:
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
            " VALUES (?,?,?,?, strftime('%Y-%m-%d','now'))",
            (receipt_id, voucher_id, seq, document_id))
        draft["last_receipt"] = seq
        row.setdefault("later_receipts", []).append(
            {"receipt_id": receipt_id, "document_id": document_id})
    else:
        conn.execute("UPDATE receipts SET document_id=? WHERE receipt_id=?",
                     (document_id, receipt_id))
        row["document_id"] = document_id
    for txn_id in row["posted"]["txn_ids"]:
        conn.execute("INSERT INTO transaction_documents (txn_id, document_id, role)"
                     " VALUES (?, ?, 'support')", (txn_id, document_id))
    conn.execute("UPDATE documents SET state='filed', doc_type='receipt', voucher_id=?"
                 " WHERE document_id=?", (voucher_id, document_id))
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


def submit(conn, *, voucher_id, admin) -> dict:
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=admin)
    pending = [(number, row) for number, row in enumerate(draft["rows"], 1)
               if row["status"] != "approved"]
    if not pending:
        raise ValidationFailure("empty_report", "Add at least one row before submitting")
    for number, row in pending:
        _check_row_complete(row, number, for_approval=False)
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
    draft["review"] = {"summary": str(summary).strip()}
    return {"status": draft["status"], "version": _store(conn, voucher_id, draft, version)}


def approve(conn, *, voucher_id, submission_id) -> dict:
    """Post every unapproved expense row and accept the declarations, all in
    the caller's command. Nothing is posted unless every row is complete."""
    draft, version = load(conn, voucher_id)
    _editable(conn, voucher_id, draft, admin=True)
    pending = [(number, row) for number, row in enumerate(draft["rows"], 1)
               if row["status"] != "approved"]
    if not pending:
        raise ValidationFailure("nothing_to_approve", "There are no new rows to approve")
    for number, row in pending:
        _check_row_complete(row, number, for_approval=True)
        for line in row["splits"] or [row]:
            _valid_code(conn, "classes", line["class_code"], what="class_code")
            _valid_code(conn, "accounts", line["expense_account"], what="account_code")
    for _, row in pending:
        if row["kind"] == "expense":
            row["posted"] = _post(conn, submission_id, voucher_id, row)
        row["status"] = "approved"
        row["admin_note"] = ""
        row["rev"] += 1
    draft["status"] = "approved"
    draft["review"] = {"summary": ""}
    return {"status": draft["status"], "version": _store(conn, voucher_id, draft, version),
            "posted": [row["posted"] for _, row in pending if row["posted"]]}


def _post(conn, submission_id, voucher_id, row) -> dict:
    usd, mxn = row["usd"], row["mxn"]
    face_currency = change_currency(usd, mxn)

    def faces(total):
        return ((total, None) if face_currency == "USD" else
                (None, total) if face_currency == "MXN" else (None, None))

    note = f"No receipt: {row['no_receipt_reason']}" \
        if row.get("document_kind") == "statement" or not row["document_id"] else ""
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
        payment_mode="cash", lines=lines,
        doc_type={"statement": "statement", "log": "purchase_log"}.get(
            row.get("document_kind"), "receipt"))
    return {"receipt_id": posted["receipt_id"], "txn_ids": posted["txn_ids"]}


def post_receipt(conn, submission_id, voucher_id, *, document_id, date,
                 vendor, face_usd_cents, face_mxn_centavos, usd_paid_cents,
                 mxn_paid_centavos, usd_change_cents, mxn_change_centavos,
                 payment_mode, lines, seq=None, doc_type="receipt") -> dict:
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
        " strftime('%Y-%m-%d','now'),?,?,?,?,?,?,?)",
        (rid, voucher_id, seq, document_id, vendor,
         face_usd_cents, face_mxn_centavos,
         usd_paid_cents, mxn_paid_centavos,
         usd_change_cents, mxn_change_centavos, payment_mode))
    mv = str(uuid.uuid4())
    # Each receipt is its own movement; receipts on the same date need
    # distinct same-day sequences so replay order is truthful
    # (spec 13). New events go after existing ones on that date.
    seq = conn.execute(
        "SELECT COALESCE(MAX(same_day_seq),-1)+1 FROM transactions"
        " WHERE date=? AND (from_account=? OR to_account=?)",
        (date, voucher_id, voucher_id)).fetchone()[0]
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
            conn.execute(
                "INSERT INTO transaction_documents (txn_id, document_id,"
                " role) VALUES (?, ?, 'support')", (txn_id, document_id))
    if document_id:
        conn.execute(
            "UPDATE documents SET state='filed', doc_type=?"
            " WHERE document_id=?", (doc_type, document_id))
    return {"receipt_id": rid, "movement_id": mv, "txn_ids": ids}
