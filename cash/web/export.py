"""On-demand XLSX transaction-ledger export (spec 12, 14): built from
the live DB at request time; CSV column contract + posting_state and
related_txn_id; one tab per fiscal year plus a full-ledger tab; money as
decimal strings; optional `since_txn_id` cursor; formula-prefix
escaping on text fields."""

from __future__ import annotations

import io
from decimal import Decimal

from flask import Blueprint, g, request, send_file
from openpyxl import Workbook

from .. import repo
from ..prep import fiscal_year_for
from .app import ok, err, require_admin

bp = Blueprint("export", __name__)

COLUMNS = ["txn_id", "date", "same_day_seq", "movement_id", "txn_type",
           "from_account", "to_account", "usd", "mxn", "calc_usd",
           "basis_usd_outflow", "receipt_face", "receipt_currency",
           "receipt_face_usd", "receipt_face_mxn",
           "voucher_id", "receipt_id", "addendum_id", "class_code",
           "project", "recipient", "vendor", "description", "notes",
           "posting_state", "related_txn_id"]

TEXT_COLS = {"movement_id", "txn_type", "from_account", "to_account",
             "voucher_id", "receipt_id", "addendum_id", "class_code",
             "project", "recipient", "vendor", "description", "notes",
             "posting_state", "receipt_currency"}

PREFIXES = ("=", "+", "-", "@")


def _escape(v):
    if isinstance(v, str) and v[:1] in PREFIXES:
        return "'" + v
    return v


def _row_values(t, res, fy_month):
    mid = t["movement_id"]
    mres = res.movements.get(mid)
    calc = None
    basis_out = None
    if mres:
        calc = mres.calc_usd_by_row.get(str(t["txn_id"]))
        basis_out = mres.basis_out_by_row.get(str(t["txn_id"]))
        if str(t["txn_id"]) in mres.unresolved_rows:
            calc = None
    return [
        t["txn_id"], t["date"], t["same_day_seq"], t["movement_id"],
        t["txn_type"], t["from_account"], t["to_account"],
        f"{t['usd_cents'] / 100:.2f}", f"{t['mxn_centavos'] / 100:.2f}",
        None if calc is None else str(calc),
        None if basis_out is None else str(basis_out),
        t["receipt_face_minor"] / 100 if t["receipt_face_minor"] else None,
        t["receipt_face_currency"],
        (t["receipt_face_usd_cents"] / 100
         if t["receipt_face_usd_cents"] is not None else None),
        (t["receipt_face_mxn_centavos"] / 100
         if t["receipt_face_mxn_centavos"] is not None else None),
        t["voucher_id"], t["receipt_id"],
        t["addendum_id"], t["class_code"], t["project"], t["recipient"],
        t["vendor"], t["description"], t["notes"], t["posting_state"],
        t["related_txn_id"],
    ]


@bp.get("/api/export/transactions.xlsx")
def export_xlsx():
    guard = require_admin()
    if guard:
        return guard
    from flask import current_app
    fy_month = current_app.config["FY_START_MONTH"]
    since = request.args.get("since_txn_id", type=int)
    res = repo.replay_projected(g.db)

    sql = "SELECT * FROM transactions"
    params = []
    if since is not None:
        sql += " WHERE txn_id > ?"
        params.append(since)
    sql += " ORDER BY txn_id"
    txns = g.db.execute(sql, params).fetchall()

    wb = Workbook(write_only=True)
    by_fy: dict[int, list] = {}
    all_ws = wb.create_sheet("transactions")
    all_ws.append(COLUMNS)
    for t in txns:
        row = [_escape(v) if isinstance(v, str) else v
               for v in _row_values(t, res, fy_month)]
        all_ws.append(row)
        fy = fiscal_year_for(t["date"], fy_month)
        by_fy.setdefault(fy, []).append(row)
    for fy, rows in sorted(by_fy.items()):
        ws = wb.create_sheet(f"FY{fy}")
        ws.append(COLUMNS)
        for row in rows:
            ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from flask import current_app as ca
    return send_file(
        buf, mimetype="application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet", as_attachment=True,
        download_name="transactions.xlsx")
