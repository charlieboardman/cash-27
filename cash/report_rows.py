"""Cash paperwork and composite purchase logs in the existing report draft.

Optional format-1 JSON fields keep working paper separate from cash postings.
No ledger movement is created until a settlement is approved. Addendum numbers
are reserved in the draft so printed paper never names a different movement.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from . import expense_drafts as drafts
from . import prep, signouts
from .commands import ValidationFailure
from .money import to_minor, to_text
from .repo import ConflictError


def review_started(draft):
    """Corrections remain under review; existing prepared paper also counts."""
    return (
        draft["status"] in ("submitted", "approved")
        or bool(draft["review"]["summary"])
        or bool(draft.get("settlement_papers"))
    )


def require_submission(conn, voucher_id):
    draft, _ = drafts.load(conn, voucher_id)
    if not review_started(draft):
        raise ValidationFailure(
            "report_not_submitted", "Submit the expense report before settling it"
        )


def settlement_facts(conn, voucher_id):
    draft, _ = drafts.load(conn, voucher_id)
    pending = [
        r
        for r in draft["rows"]
        if not r["posted"] and r["kind"] in ("expense", "discrepancy")
    ]
    state = signouts.summary(conn, voucher_id)
    usd = state["usd_cents"] - sum(r["usd"] for r in pending)
    mxn = state["mxn_centavos"] - sum(r["mxn"] for r in pending)
    # An outstanding positive USD/negative MXN pair represents a currency
    # settlement, not two separate cash payments. Closeouts handles the FX.
    if usd > 0 > mxn:
        amounts = {"positive": (0, 0), "return": (0, 0)}
    else:
        amounts = {
            "positive": (max(-usd, 0), max(-mxn, 0)),
            "return": (max(usd, 0), max(mxn, 0)),
        }
    payload = {
        "rows": [
            {
                k: r.get(k)
                for k in (
                    "id",
                    "usd",
                    "mxn",
                    "date",
                    "vendor",
                    "purpose",
                    "document_id",
                    "splits",
                    "purchases",
                )
            }
            for r in pending
        ],
        "ledger": [state["usd_cents"], state["mxn_centavos"]],
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    return fingerprint, amounts


def settlement_stages(conn, voucher_id):
    draft, _ = drafts.load(conn, voucher_id)
    fingerprint, amounts = settlement_facts(conn, voucher_id)
    stages = []
    for kind, (usd, mxn) in amounts.items():
        if not (usd or mxn) and not any(r.get("rounding") == kind and not r["posted"] for r in draft["rows"]):
            continue
        paper = draft.get("settlement_papers", {}).get(kind, {})
        status = (
            "calculated"
            if not paper
            else "outdated"
            if paper["fingerprint"] != fingerprint
            else "filed"
            if paper.get("document_id")
            else "prepared"
        )
        stages.append(
            {
                **paper,
                "kind": kind,
                "status": status,
                "usd": to_text(usd),
                "mxn": to_text(mxn),
            }
        )
    return stages


def prepare_settlement(conn, voucher_id, kind, *, date, approver_name=""):
    if kind not in ("positive", "return"):
        raise ValidationFailure("bad_kind", "Unknown settlement kind")
    draft, version = drafts.load(conn, voucher_id)
    drafts._editable(conn, voucher_id, draft, admin=True)
    require_submission(conn, voucher_id)
    try:
        from datetime import date as calendar_date

        calendar_date.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise ValidationFailure("bad_date", "Enter a valid settlement date") from exc
    fingerprint, amounts = settlement_facts(conn, voucher_id)
    if not any(amounts[kind]) and not any(r.get("rounding") == kind and not r["posted"] for r in draft["rows"]):
        raise ValidationFailure("bad_amount", "No settlement required")
    current = draft.get("settlement_papers", {}).get(kind)
    # Retrying preparation must not keep allocating a different paper number.
    if current and current["fingerprint"] == fingerprint:
        return current
    paper_id, seq = prep._next_addendum(conn, voucher_id, kind)
    paper = {
        "paper_id": paper_id,
        "fingerprint": fingerprint,
        "document_id": None,
        "date": date,
        "approver_name": approver_name,
    }
    draft.setdefault("settlement_papers", {})[kind] = paper
    draft["last_settlement_seq"] = seq
    drafts._store(conn, voucher_id, draft, version)
    return paper


def file_settlement(conn, voucher_id, kind, document_id):
    draft, version = drafts.load(conn, voucher_id)
    drafts._editable(conn, voucher_id, draft, admin=True)
    fingerprint, _ = settlement_facts(conn, voucher_id)
    paper = draft.get("settlement_papers", {}).get(kind)
    if not paper or paper["fingerprint"] != fingerprint:
        raise ValidationFailure(
            "paperwork_needed", "Generate current settlement paperwork first"
        )
    prep._require_inbox_document(conn, document_id)
    doc = conn.execute(
        "SELECT voucher_id FROM documents WHERE document_id=?", (document_id,)
    ).fetchone()
    if doc["voucher_id"] not in (None, voucher_id):
        raise ValidationFailure(
            "bad_document", "That document belongs to another report"
        )
    paper["document_id"] = document_id
    conn.execute(
        "UPDATE documents SET voucher_id=? WHERE document_id=?",
        (voucher_id, document_id),
    )
    drafts._store(conn, voucher_id, draft, version)


def calculated_settlement(conn, voucher_id, *, date, resolution_note=""):
    stages = settlement_stages(conn, voucher_id)
    if any(s["status"] != "filed" for s in stages):
        raise ValidationFailure(
            "paperwork_needed",
            "File current signed settlement paperwork before approval",
        )
    _, amounts = settlement_facts(conn, voucher_id)
    papers = {}
    for stage in stages:
        prep._require_inbox_document(conn, stage["document_id"])
        papers[stage["kind"]] = {
            "paper_id": stage["paper_id"],
            "document_id": stage["document_id"],
        }
    return {
        "date": date,
        "confirmed": True,
        "return_usd_cents": amounts["return"][0],
        "return_mxn_centavos": amounts["return"][1],
        "reimburse_usd_cents": amounts["positive"][0],
        "reimburse_mxn_centavos": amounts["positive"][1],
        "resolution_note": resolution_note,
        "settlement_papers": papers,
    }


def purchase_log(draft):
    return next((r for r in draft["rows"] if "purchases" in r), None)


def clean_purchases(items):
    if not isinstance(items, list) or len(items) > 100:
        raise ValidationFailure("bad_purchases", "Enter at most 100 purchases")
    result = []
    for item in items:
        if not isinstance(item, dict) or set(item) - {
            "date",
            "vendor",
            "purpose",
            "usd",
            "mxn",
        }:
            raise ValidationFailure("bad_purchases", "Invalid purchase fields")
        day = str(item.get("date") or "")
        if day:
            try:
                date.fromisoformat(day)
            except ValueError as exc:
                raise ValidationFailure(
                    "bad_date", "Enter a valid purchase date"
                ) from exc
        usd, mxn = (to_minor(item.get(k) or "0", k.upper()) for k in ("usd", "mxn"))
        if usd < 0 or mxn < 0:
            raise ValidationFailure("bad_amount", "Enter positive purchase amounts")
        vendor, purpose = (
            str(item.get("vendor") or "").strip(),
            str(item.get("purpose") or "").strip(),
        )
        if len(vendor) > 120 or len(purpose) > 200:
            raise ValidationFailure("bad_purchases", "Purchase details are too long")
        result.append(
            {
                "date": day,
                "vendor": vendor,
                "purpose": purpose,
                "usd": to_text(usd),
                "mxn": to_text(mxn),
            }
        )
    return result


def save_log(conn, voucher_id, *, admin, data, document_id=None, deleting=False):
    draft, version = drafts.load(conn, voucher_id)
    drafts._editable(conn, voucher_id, draft, admin=admin)
    row = purchase_log(draft)
    if deleting:
        if not row:
            raise ValidationFailure("not_found", "Purchase log not found")
        return drafts.delete_row(
            conn,
            voucher_id=voucher_id,
            row_id=row["id"],
            expected_rev=row["rev"],
            admin=admin,
        )
    created = row is None
    if created:
        if any(r.get("document_kind") == "log" for r in draft["rows"]):
            raise ValidationFailure("one_log", "This report already has a purchase log")
        row = drafts._new_row("expense")
        row.update(
            purchases=[],
            date=date.today().isoformat(),
            purpose="Purchase log",
            no_receipt_reason="Lost receipts",
        )
        draft["rows"].append(row)
    if row["posted"]:
        raise ValidationFailure("row_approved", "This purchase log is approved")
    fields = {}
    if "items" in data:
        if not created and data.get("rev") != row["rev"]:
            raise ConflictError(
                "This purchase log changed on another screen; reload it"
            )
        items = clean_purchases(data["items"])
        changed = items != row["purchases"]
        if changed and row["document_id"]:
            fields["document_id"] = None
        row["purchases"] = items
        fields.update(
            usd=sum(to_minor(i["usd"], "USD") for i in items),
            mxn=sum(to_minor(i["mxn"], "MXN") for i in items),
            date=next((i["date"] for i in items if i["date"]), row["date"]),
            class_code=data.get("class_code") or None,
            expense_account=data.get("expense_account") or None,
        )
    if document_id:
        fields.update(document_id=document_id, document_kind="log")
    drafts._apply(conn, voucher_id, row, fields, admin=admin)
    drafts._one_log(draft)
    if not created:
        row["rev"] += 1
    drafts._touch(draft)
    drafts._store(conn, voucher_id, draft, version)
    return {"row_id": row["id"]}


def validate_purchases(row):
    if "purchases" not in row:
        return
    if not row["purchases"]:
        raise ValidationFailure(
            "bad_purchases", "Add at least one purchase to the purchase log"
        )
    if (
        sum(to_minor(i["usd"], "USD") for i in row["purchases"]) != row["usd"]
        or sum(to_minor(i["mxn"], "MXN") for i in row["purchases"]) != row["mxn"]
    ):
        raise ValidationFailure("bad_purchases", "Purchase totals do not match the log")
    for item in row["purchases"]:
        if not item["date"] or not item["vendor"] or not item["purpose"]:
            raise ValidationFailure(
                "bad_purchases", "Complete each purchase date, vendor and description"
            )
        if not (to_minor(item["usd"], "USD") or to_minor(item["mxn"], "MXN")):
            raise ValidationFailure(
                "bad_amount", "Enter the amount spent for each purchase"
            )


def view(conn, voucher_id, *, document, admin=False):
    """Shared read-only row facts; document URLs remain voucher scoped."""
    draft, _ = drafts.load(conn, voucher_id)
    stages = settlement_stages(conn, voucher_id)
    for stage in stages:
        stage["document"] = document(stage.get("document_id"))
        stage["filename"] = (stage["document"] or {}).get("display_name", "")
        candidate = conn.execute(
            "SELECT q.document_id FROM document_qr q JOIN documents d USING(document_id)"
            " WHERE q.detected_id=? AND q.review_kind='settlement' AND q.status='matched'"
            " AND d.state='inbox' ORDER BY d.created_at DESC LIMIT 1",
            (stage.get("paper_id"),),
        ).fetchone()
        stage["matched_document"] = (
            document(candidate["document_id"]) if candidate else None
        )
    vouchers = [
        document(r["document_id"])
        for r in conn.execute(
            "SELECT DISTINCT td.document_id FROM transaction_documents td JOIN transactions t USING(txn_id)"
            " WHERE t.voucher_id=? AND t.txn_type='signout' AND td.role='voucher' AND t.posting_state='active'",
            (voucher_id,),
        )
    ]
    reimbursed = (signouts.summary(conn, voucher_id).get("closeout") or {}).get(
        "reimbursement_addendum_id"
    )
    advances = []
    for row in conn.execute(
        "SELECT a.*,t.date,t.usd_cents,t.mxn_centavos FROM addenda a"
        " JOIN transactions t ON t.addendum_id=a.addendum_id"
        " WHERE a.voucher_id=? AND t.posting_state='active' ORDER BY a.seq",
        (voucher_id,),
    ):
        advances.append(
            {
                "addendum_id": row["addendum_id"],
                "kind": row["kind"],
                "status": row["status"],
                "date": row["date"],
                "reason": row["reason"],
                "document_id": row["document_id"],
                "document": document(row["document_id"]),
                "usd": to_text(row["usd_cents"]),
                "mxn": to_text(row["mxn_centavos"]),
                "settlement": row["addendum_id"] == reimbursed,
            }
        )
    row = purchase_log(draft)
    log = None
    if row:
        candidate = conn.execute(
            "SELECT q.document_id FROM document_qr q JOIN documents d USING(document_id)"
            " WHERE q.voucher_id=? AND q.review_kind='log' AND q.status='matched'"
            " AND d.state='inbox' ORDER BY d.created_at DESC LIMIT 1",
            (voucher_id,),
        ).fetchone()
        log = {
            "row_id": row["id"],
            "items": row["purchases"],
            "rev": row["rev"],
            "posted": row["posted"],
            "class_code": row["class_code"],
            "expense_account": row["expense_account"],
            "document": document(row["document_id"]),
            "usd": to_text(row["usd"]),
            "mxn": to_text(row["mxn"]),
            "matched_document": document(candidate["document_id"])
            if candidate
            else None,
        }

    def matched(code):
        row = conn.execute(
            "SELECT q.document_id FROM document_qr q JOIN documents d USING(document_id)"
            " WHERE q.detected_id=? AND q.status='matched' AND d.state='inbox'"
            " ORDER BY d.created_at DESC LIMIT 1",
            (code,),
        ).fetchone()
        return document(row["document_id"]) if row else None

    for advance in advances:
        advance["matched_document"] = matched(advance["addendum_id"])
    balance = drafts.balance(
        conn,
        voucher_id,
        {
            **draft,
            "rows": [
                r for r in draft["rows"] if r["kind"] in ("expense", "discrepancy")
            ],
        },
    )
    return {
        "log": log,
        "balance": {
            "usd": to_text(balance["usd"]),
            "mxn": to_text(balance["mxn"]),
            "balanced": balance["balanced"],
            "implied_exchange": balance["implied_exchange"],
        },
        "voucher_candidate": matched(voucher_id),
        "voucher_documents": vouchers,
        "advances": advances,
        "settlement_review": review_started(draft),
        "stages": stages if admin and review_started(draft) else [],
        "ready": all(s["status"] == "filed" for s in stages),
    }
