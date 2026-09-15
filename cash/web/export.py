"""Bookkeeper expense exports, valued from the full authoritative replay."""

from __future__ import annotations

import io
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, g, request, send_file
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from .. import repo
from ..prep import fiscal_year_for
from .app import require_admin

bp = Blueprint("export", __name__)

COLUMNS = ["date", "Account", "mxn", "ER", "usd", "calc_usd",
           "voucher_id", "receipt_id", "addendum_id", "class_code",
           "project", "recipient", "vendor", "description", "notes"]

PREFIXES = ("=", "+", "-", "@")


def _escape(v):
    if isinstance(v, str) and v[:1] in PREFIXES:
        return "'" + v
    return v


def _expense_rows(conn, since=None):
    """Include active expense allocations, including signed adjustments.

    Unallocated expenses remain visible in previews; report finalization still
    refuses them. Cash movements remain available to replay but are not rows
    in the bookkeeper's report.
    """
    sql = ("SELECT t.* FROM transactions t JOIN accounts a"
           " ON a.account_id=t.to_account WHERE a.kind='expense'"
           " AND t.posting_state='active'")
    params = []
    if since is not None:
        sql += " AND t.txn_id > ?"
        params.append(since)
    sql += " ORDER BY t.date, t.same_day_seq, t.txn_id"
    return conn.execute(sql, params).fetchall()


def _rounded(value, places):
    result = value.quantize(Decimal(places), rounding=ROUND_HALF_UP)
    return abs(result) if result == 0 else result


def _row_values(t, res):
    mid = t["movement_id"]
    mres = res.movements.get(mid)
    calc = None
    rate = None
    usd = Decimal(t["usd_cents"]) / 100
    mxn = Decimal(t["mxn_centavos"]) / 100
    if mres:
        calc = mres.calc_usd_by_row.get(str(t["txn_id"]))
        if str(t["txn_id"]) in mres.unresolved_rows:
            calc = None
        # Final replay valuation includes basis supplied by later funding.
        # The initial basis_out_by_row alone omits those resolved claims.
        if calc is not None and mxn and (basis := calc - usd):
            implied = mxn / basis
            if implied > 0:
                rate = _rounded(implied, "0.001")
    return [
        t["date"], t["to_account"], _rounded(mxn, "0.01"), rate,
        _rounded(usd, "0.01"), None if calc is None else _rounded(calc, "0.01"),
        t["voucher_id"], t["receipt_id"],
        t["addendum_id"], t["class_code"], t["project"], t["recipient"],
        t["vendor"], t["description"], t["notes"],
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

    txns = _expense_rows(g.db, since)

    wb = Workbook(write_only=True)
    by_fy: dict[int, list] = {}
    all_ws = wb.create_sheet("expenses")
    all_ws.append(COLUMNS)
    def append_expense(ws, values):
        cells = []
        for column, value in zip(COLUMNS, values):
            cell = WriteOnlyCell(ws, value=value)
            if column in ("mxn", "usd", "calc_usd", "ER"):
                cell.number_format = "0.000" if column == "ER" else "0.00"
            cells.append(cell)
        ws.append(cells)
    for t in txns:
        row = [_escape(v) if isinstance(v, str) else v
               for v in _row_values(t, res)]
        append_expense(all_ws, row)
        fy = fiscal_year_for(t["date"], fy_month)
        by_fy.setdefault(fy, []).append(row)
    for fy, rows in sorted(by_fy.items()):
        ws = wb.create_sheet(f"FY{fy}")
        ws.append(COLUMNS)
        for row in rows:
            append_expense(ws, row)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, mimetype="application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet", as_attachment=True,
        download_name="expenses.xlsx")
