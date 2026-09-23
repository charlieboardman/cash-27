"""CSV/XLSX previews and downloads of retained finalized revisions (spec 13).

Previews are generated from one SQLite snapshot and returned directly;
they create no file or database row. Finalized reports are idempotent,
numbered, hashed, and retained under the persistent data root.
"""

from __future__ import annotations

from datetime import date
import hashlib
import io
import uuid

from flask import (Blueprint, current_app, g, render_template, request,
                   send_file)

from .. import repo
from ..commands import ValidationFailure, run_command
from ..prep import fiscal_year_for
from .app import ok, err, require_admin
from .export import (export_rows, export_values, EXPORT_TYPES, EXPORT_NAMES, render_csv, render_xlsx,
                     retained_xlsx, MIMETYPES)

bp = Blueprint("reports", __name__)


@bp.get("/reports")
def reports_page():
    guard = require_admin()
    if guard:
        return guard
    fy_month = current_app.config["FY_START_MONTH"]
    return render_template(
        "reports.html",
        current_fy=fiscal_year_for(date.today().isoformat(), fy_month),
        fy_start_month=fy_month)


def _period_rows(g_db, fy_year, fy_month, export_type):
    return [t for t in export_rows(g_db, export_type)
        if fiscal_year_for(t["date"], fy_month) == fy_year]


def _ledger_revision(conn) -> int:
    return conn.execute(
        "SELECT ledger_revision FROM ledger_state WHERE singleton_id=1"
    ).fetchone()[0]


def _finalization_blockers(rows, replay_result) -> tuple[int, int]:
    unallocated = sum(
        1 for txn in rows
        if txn["to_account"] == "UNALLOCATED_EXPENSE"
        and txn["posting_state"] == "active")
    movement_ids = {txn["movement_id"] for txn in rows}
    unresolved = sum(
        1 for claim in replay_result.open_claims
        if claim.movement_id in movement_ids)
    return unallocated, unresolved


@bp.post("/api/reports/<int:fy_year>/generate")
def generate(fy_year):
    guard = require_admin()
    if guard:
        return guard
    fy_month = current_app.config["FY_START_MONTH"]
    data = request.get_json(force=True, silent=True) or {}
    allowed = {"final", "notes", "submission_id", "format", "export_type"}
    unknown = set(data) - allowed
    if unknown:
        return err("unknown_fields", f"Unknown fields: {sorted(unknown)}",
                   400, {key: "unknown" for key in unknown})
    want_final = bool(data.get("final"))
    format = data.get("format", "csv")
    if not isinstance(format, str) or format not in MIMETYPES:
        return err("bad_format", "Choose CSV or XLSX.", 400)

    export_type = data.get("export_type", "expenses")
    if not isinstance(export_type, str) or export_type not in EXPORT_TYPES:
        return err("bad_export_type", "Choose Expense report or Cash ledger.", 400)
    columns = EXPORT_TYPES[export_type]

    if not want_final:
        rows = _period_rows(g.db, fy_year, fy_month, export_type)
        replay_result = repo.replay_projected(g.db)
        values = export_values(rows, replay_result, export_type)
        content = (render_csv(values, columns=columns) if format == "csv" else
                   render_xlsx(values, columns=columns, sheet_name=f"FY{fy_year}"))
        unallocated, unresolved = _finalization_blockers(
            rows, replay_result)
        response = send_file(
            io.BytesIO(content), mimetype=MIMETYPES[format], as_attachment=True,
            download_name=f"FY{fy_year}-{EXPORT_NAMES[export_type]}-preview.{format}")
        response.headers["X-Ledger-Revision"] = str(_ledger_revision(g.db))
        response.headers["X-Report-Unallocated"] = str(unallocated)
        response.headers["X-Report-Unresolved-Basis"] = str(unresolved)
        return response

    try:
        submission_id = str(uuid.UUID(data["submission_id"]))
    except (KeyError, ValueError, TypeError):
        return err("bad_request",
                   "Finalization requires a UUID submission_id", 400,
                   {"submission_id": "required UUID"})

    data_root = current_app.config["DATA_ROOT"]

    def finalize(conn):
        rows = _period_rows(conn, fy_year, fy_month, export_type)
        replay_result = repo.replay_projected(conn)
        unallocated, unresolved = _finalization_blockers(
            rows, replay_result)
        if unallocated:
            raise ValidationFailure(
                "unallocated",
                f"{unallocated} unallocated expense(s) in period")
        if unresolved:
            raise ValidationFailure(
                "basis_unresolved",
                f"{unresolved} unresolved basis claim(s) in period")

        period = f"FY{fy_year}"
        revision = conn.execute(
            "SELECT COALESCE(MAX(revision),0)+1 FROM report_runs WHERE"
            " period=?", (period,)).fetchone()[0]
        content = render_csv(export_values(rows, replay_result, export_type), columns=columns)
        digest = hashlib.sha256(content).hexdigest()
        relative_path = f"reports/FY{fy_year}-r{revision}.csv"
        output_path = data_root / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output_path.open("xb") as report_file:
                report_file.write(content)
        except FileExistsError as exc:
            raise ValidationFailure(
                "report_path_exists",
                f"Refusing to overwrite {relative_path}") from exc

        ledger_revision = _ledger_revision(conn)
        cursor = conn.execute(
            "INSERT INTO report_runs (period, revision, ledger_revision,"
            " generated_path, sha256, notes, export_type) VALUES (?,?,?,?,?,?,?)",
            (period, revision, ledger_revision, relative_path, digest,
             str(data.get("notes", "")), export_type))
        return {"report_id": cursor.lastrowid, "period": period,
                "revision": revision, "ledger_revision": ledger_revision,
                "sha256": digest, "status": "final", "rows": len(rows),
                "export_type": export_type}

    result = run_command(
        g.db, submission_id, "finalize_report",
        finalize)
    return ok(result)


@bp.get("/api/reports")
def list_reports():
    guard = require_admin()
    if guard:
        return guard
    current_revision = _ledger_revision(g.db)
    rows = g.db.execute(
        "SELECT * FROM report_runs ORDER BY period, revision"
    ).fetchall()
    reports = []
    for row in rows:
        report = dict(row)
        report["freshness"] = (
            "current" if row["ledger_revision"] == current_revision
            else "outdated")
        reports.append(report)
    return ok({"ledger_revision": current_revision, "reports": reports})


@bp.get("/api/reports/<int:report_id>/download")
def download(report_id):
    guard = require_admin()
    if guard:
        return guard
    format = request.args.get("format", "csv")
    if format not in MIMETYPES:
        return err("bad_format", "Choose CSV or XLSX.", 400)
    r = g.db.execute(
        "SELECT generated_path, sha256, period FROM report_runs WHERE"
        " report_id=?", (report_id,)).fetchone()
    if not r:
        return err("not_found", "Report not found", 404)
    path = current_app.config["DATA_ROOT"] / r["generated_path"]
    if not path.exists():
        return err("missing", "Report file missing", 410)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != r["sha256"]:
        return err("hash_mismatch", "Report file corrupted", 410)
    if format == "xlsx":
        content = retained_xlsx(content, sheet_name=r["period"])
    return send_file(io.BytesIO(content), mimetype=MIMETYPES[format], as_attachment=True,
                     download_name=path.with_suffix(f".{format}").name)
