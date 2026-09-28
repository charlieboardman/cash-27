"""The Reports page. Downloads come from /api/export/transactions.*; nothing
is saved on the server (spec 13)."""

from __future__ import annotations

from flask import Blueprint, current_app, g, render_template

from ..prep import fiscal_year_for
from .app import admin_required

bp = Blueprint("reports", __name__)


@bp.get("/reports")
@admin_required
def reports_page():
    fy_month = current_app.config["FY_START_MONTH"]
    first = g.db.execute("SELECT MIN(date) FROM transactions"
                         " WHERE posting_state='active'").fetchone()[0]
    # The browser supplies the current year from its own date.
    return render_template(
        "reports.html",
        first_fy=fiscal_year_for(first, fy_month) if first else "",
        fy_start_month=fy_month)
