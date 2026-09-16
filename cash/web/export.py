"""Bookkeeper expense exports, valued from the full authoritative replay."""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import Blueprint, g, request, send_file
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from .. import repo
from ..prep import fiscal_year_for
from .app import require_admin

bp = Blueprint("export", __name__)

COLUMNS = ["date", "Account", "mxn", "ER", "usd", "calc_usd",
           "Safe Balance USD", "Safe Balance MXN",
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
    sql += " ORDER BY t.date, t.same_day_seq, t.movement_id, t.allocation_line, t.txn_id"
    return conn.execute(sql, params).fetchall()


def _rounded(value, places):
    result = value.quantize(Decimal(places), rounding=ROUND_HALF_UP)
    return abs(result) if result == 0 else result


def _row_values(t, res):
    mid = t["movement_id"]
    mres = res.movements.get(mid)
    safe = mres.safe_balance_after if mres else None
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
        None if safe is None else _rounded(Decimal(safe.usd) / 100, "0.01"),
        None if safe is None else _rounded(Decimal(safe.mxn) / 100, "0.01"),
        t["voucher_id"], t["receipt_id"],
        t["addendum_id"], t["class_code"], t["project"], t["recipient"],
        t["vendor"], t["description"], t["notes"],
    ]


MIMETYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
NUMBER_FORMATS = {"mxn": "0.00", "ER": "0.000", "usd": "0.00", "calc_usd": "0.00",
                  "Safe Balance USD": "0.00", "Safe Balance MXN": "0.00"}


def expense_values(txns, replay_result):
    return [[_escape(v) if isinstance(v, str) else v
             for v in _row_values(t, replay_result)] for t in txns]


def render_csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return buf.getvalue().encode()


def render_xlsx(rows, *, columns=COLUMNS, sheet_name="expenses", fy_month=None):
    wb = Workbook(write_only=True)
    all_ws = wb.create_sheet(sheet_name)
    all_ws.append(columns)
    by_fy = {}

    def append_expense(ws, values):
        cells = []
        for column, value in zip(columns, values):
            cell = WriteOnlyCell(ws, value=value)
            if column in NUMBER_FORMATS:
                cell.number_format = NUMBER_FORMATS[column]
            cells.append(cell)
        ws.append(cells)

    for row in rows:
        append_expense(all_ws, row)
        if fy_month is not None:
            fy = fiscal_year_for(row[columns.index("date")], fy_month)
            by_fy.setdefault(fy, []).append(row)
    for fy, values in sorted(by_fy.items()):
        ws = wb.create_sheet(f"FY{fy}")
        ws.append(columns)
        for row in values:
            append_expense(ws, row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def retained_xlsx(content, *, sheet_name):
    """Convert the verified saved CSV, never regenerate from today's ledger.

    Keep its original headers and text escaping, including older report formats.
    Only known amount/rate columns become numeric Excel cells.
    """
    reader = csv.reader(io.StringIO(content.decode()))
    columns = next(reader)
    rows = []
    for row in reader:
        values = []
        for column, value in zip(columns, row):
            if column in NUMBER_FORMATS and value:
                try:
                    number = Decimal(value)
                    if number.is_finite():
                        value = number
                except InvalidOperation:
                    pass
            values.append(None if value == "" else value)
        rows.append(values)
    return render_xlsx(rows, columns=columns, sheet_name=sheet_name)


@bp.get("/api/export/transactions.csv", defaults={"format": "csv"})
@bp.get("/api/export/transactions.xlsx", defaults={"format": "xlsx"})
def export_expenses(format):
    guard = require_admin()
    if guard:
        return guard
    from flask import current_app
    fy_month = current_app.config["FY_START_MONTH"]
    since = request.args.get("since_txn_id", type=int)
    res = repo.replay_projected(g.db)
    rows = expense_values(_expense_rows(g.db, since), res)
    content = (render_csv(rows) if format == "csv" else
               render_xlsx(rows, fy_month=fy_month))
    return send_file(io.BytesIO(content), mimetype=MIMETYPES[format],
                     as_attachment=True, download_name=f"expenses.{format}")
