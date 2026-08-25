"""Versioned CSV reports (spec 13): period snapshots retained as
numbered revisions; finalization blocked while the period contains
active unallocated expenses or unresolved basis claims."""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

from flask import Blueprint, current_app, g, request, send_file

from .. import repo
from ..commands import ValidationFailure
from ..prep import fiscal_year_for
from .app import ok, err, require_admin
from .export import COLUMNS, _escape, _row_values

bp = Blueprint("reports", __name__)


def _period_rows(g_db, fy_year, fy_month):
    return [t for t in g_db.execute(
        "SELECT * FROM transactions ORDER BY date, same_day_seq, txn_id")
        if fiscal_year_for(t["date"], fy_month) == fy_year]


@bp.post("/api/reports/<int:fy_year>/generate")
def generate(fy_year):
    guard = require_admin()
    if guard:
        return guard
    fy_month = current_app.config["FY_START_MONTH"]
    data = request.get_json(force=True, silent=True) or {}
    want_final = bool(data.get("final"))

    rows = _period_rows(g.db, fy_year, fy_month)
    res = repo.replay_projected(g.db)

    if want_final:
        unallocated = sum(1 for t in rows
                          if t["to_account"] == "UNALLOCATED_EXPENSE"
                          and t["posting_state"] == "active")
        if unallocated:
            return err("unallocated",
                       f"{unallocated} unallocated expense(s) in period",
                       422)
        if res.open_claims:
            return err("basis_unresolved",
                       f"{len(res.open_claims)} unresolved basis claim(s)",
                       422)

    rev = g.db.execute(
        "SELECT COALESCE(MAX(revision),0)+1 FROM report_runs WHERE"
        " period = ?", (f"FY{fy_year}",)).fetchone()[0]

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    for t in rows:
        w.writerow([_escape(v) if isinstance(v, str) else v
                    for v in _row_values(t, res, fy_month)])
    content = buf.getvalue().encode()

    out_dir = current_app.config["DATA_ROOT"] / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"FY{fy_year}-r{rev}.csv"
    out_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    cutoff = max((t["date"] for t in rows), default="")
    g.db.execute(
        "INSERT INTO report_runs (period, revision, replay_cutoff_date,"
        " generated_path, sha256, status, notes) VALUES (?,?,?,?,?,?,?)",
        (f"FY{fy_year}", rev, cutoff, f"reports/FY{fy_year}-r{rev}.csv",
         digest, "final" if want_final else "preview",
         data.get("notes", "")))
    g.db.commit()
    return ok({"period": f"FY{fy_year}", "revision": rev,
               "sha256": digest, "status": "final" if want_final
               else "preview", "rows": len(rows)})


@bp.get("/api/reports")
def list_reports():
    guard = require_admin()
    if guard:
        return guard
    rows = g.db.execute(
        "SELECT * FROM report_runs ORDER BY period, revision"
    ).fetchall()
    return ok({"reports": [dict(r) for r in rows]})


@bp.get("/api/reports/<int:report_id>/download")
def download(report_id):
    guard = require_admin()
    if guard:
        return guard
    r = g.db.execute(
        "SELECT generated_path, sha256, status FROM report_runs WHERE"
        " report_id = ?", (report_id,)).fetchone()
    if not r:
        return err("not_found", "Report not found", 404)
    path = current_app.config["DATA_ROOT"] / r["generated_path"]
    if not path.exists():
        return err("missing", "Report file missing", 410)
    if hashlib.sha256(path.read_bytes()).hexdigest() != r["sha256"]:
        return err("hash_mismatch", "Report file corrupted", 410)
    return send_file(path, mimetype="text/csv", as_attachment=True,
                     download_name=path.name)
