"""Repositories: load domain objects from SQLite, insert rows inside
command transactions. No replay or filing logic here (spec 16)."""

from __future__ import annotations

import sqlite3

from .model import Account, Row


def pending_exchanges(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Active exchanges still needing receipt review, including older entries."""
    return conn.execute(
        "SELECT t.* FROM transactions t WHERE t.txn_type='exchange'"
        " AND t.posting_state='active' AND NOT EXISTS ("
        " SELECT 1 FROM signout_closeouts sc WHERE sc.settlement_txn_id=t.txn_id)"
        " AND NOT EXISTS ("
        " SELECT 1 FROM transaction_documents td WHERE td.txn_id=t.txn_id"
        " AND td.role='support')").fetchall()


def pending_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Safe counts still needing their signed sheet: each count's first
    adjustment, with the count's USD and MXN differences summed."""
    return conn.execute(
        "SELECT t.*, (SELECT SUM(CASE WHEN o.to_account='SAFE' THEN o.usd_cents"
        "  ELSE -o.usd_cents END) FROM transactions o WHERE o.submission_id=t.submission_id"
        "  AND o.txn_type='adjustment' AND o.notes LIKE 'Safe count%') AS delta_usd,"
        " (SELECT SUM(CASE WHEN o.to_account='SAFE' THEN o.mxn_centavos"
        "  ELSE -o.mxn_centavos END) FROM transactions o WHERE o.submission_id=t.submission_id"
        "  AND o.txn_type='adjustment' AND o.notes LIKE 'Safe count%') AS delta_mxn"
        " FROM transactions t WHERE t.txn_type='adjustment' AND t.posting_state='active'"
        " AND t.notes LIKE 'Safe count%' AND t.txn_id=("
        "  SELECT MIN(f.txn_id) FROM transactions f WHERE f.submission_id=t.submission_id"
        "  AND f.txn_type='adjustment' AND f.notes LIKE 'Safe count%')"
        " AND NOT EXISTS (SELECT 1 FROM transaction_documents td JOIN transactions o"
        "  ON o.txn_id=td.txn_id WHERE o.submission_id=t.submission_id"
        "  AND td.role='support')").fetchall()


def pending_withdrawals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Withdrawals count as cash immediately but still need their receipt."""
    return conn.execute(
        "SELECT t.* FROM transactions t WHERE t.txn_type='bank_withdrawal'"
        " AND t.posting_state='active' AND NOT EXISTS ("
        " SELECT 1 FROM transaction_documents td WHERE td.txn_id=t.txn_id"
        " AND td.role='support')").fetchall()


def has_opening(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM transactions WHERE txn_type='opening'"
        " AND posting_state='active' LIMIT 1").fetchone())


def ledger_revision(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT ledger_revision FROM ledger_state WHERE singleton_id=1").fetchone()[0]


def code_options(conn: sqlite3.Connection, *, projects: bool = True) -> dict:
    """Active class and expense-account codes to choose from, and the project
    names already used (suggestions, offered only to administrators)."""
    return {
        "classes": [dict(row) for row in conn.execute(
            "SELECT class_code, name FROM classes WHERE active=1"
            " ORDER BY name, class_code")],
        "expense_accounts": [dict(row) for row in conn.execute(
            "SELECT account_id, name FROM accounts WHERE kind='expense'"
            " AND active=1 AND account_id!='UNALLOCATED_EXPENSE'"
            " ORDER BY name, account_id")],
        "projects": [row["project"] for row in conn.execute(
            "SELECT DISTINCT project FROM transactions WHERE posting_state='active'"
            " AND TRIM(project)!='' ORDER BY project COLLATE NOCASE")] if projects else [],
    }


def cash_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Active cash accounts, the SAFE first."""
    return conn.execute(
        "SELECT account_id, name FROM accounts WHERE kind IN ('safe','signout')"
        " AND active=1 ORDER BY CASE WHEN kind='safe' THEN 0 ELSE 1 END,"
        " name, account_id").fetchall()


def receipt_numbers(conn: sqlite3.Connection) -> dict[str, str]:
    """The name of each receipt document: its receipt number, with an "x" for
    a signed no-receipt statement (FY27-0042-R3x), or FY27-0042-log for the
    signout's purchase log, and Txn-123 for a safe count sheet. A direct expense's receipts
    are numbered without a ledger line pointing at them."""
    names = {row["document_id"]: (f"{row['voucher_id']}-log" if row["doc_type"] == "purchase_log"
                                  else row["receipt_id"] + ("x" if row["doc_type"] == "statement" else ""))
             for row in conn.execute(
                 "SELECT r.receipt_id, r.voucher_id, r.document_id, d.doc_type FROM receipts r"
                 " JOIN documents d USING(document_id)"
                 " WHERE r.status!='void' AND r.document_id IS NOT NULL")}
    # A count sheet is named by its count's first transaction, the code it carries.
    names.update({row["document_id"]: f"Txn-{row['txn_id']}" for row in conn.execute(
        "SELECT td.document_id, MIN(td.txn_id) AS txn_id FROM transaction_documents td"
        " JOIN documents d USING(document_id) WHERE d.doc_type='physical_count'"
        " GROUP BY td.document_id")})
    return names


def document_reference(txn, role: str, document_id: str,
                       numbers: dict[str, str]) -> str:
    """The name a person sees for a document on a transaction: FY27-0042,
    FY27-0042-A1, FY27-0042-R1, FY27-0042-R3x, or Txn-123-receipt when
    nothing is numbered."""
    if role == "voucher" and (txn["addendum_id"] or txn["voucher_id"]):
        return txn["addendum_id"] or txn["voucher_id"]
    return (numbers.get(document_id) or txn["receipt_id"] or
            f"{txn['voucher_id'] or 'Txn-' + str(txn['txn_id'])}-receipt")


def load_accounts(conn: sqlite3.Connection) -> dict[str, Account]:
    out = {}
    for r in conn.execute(
            "SELECT account_id, name, kind, active, parent_voucher_id "
            "FROM accounts"):
        out[r["account_id"]] = Account(
            account_id=r["account_id"], name=r["name"], kind=r["kind"],
            active=bool(r["active"]),
            parent_voucher_id=r["parent_voucher_id"])
    return out


def load_rows(conn: sqlite3.Connection) -> list[Row]:
    out = []
    for r in conn.execute(
            "SELECT txn_id, movement_id, date, same_day_seq,"
            " allocation_line, txn_type, from_account, to_account,"
            " usd_cents, mxn_centavos, receipt_face_usd_cents,"
            " receipt_face_mxn_centavos, assigned_rate_milli, voucher_id,"
            " receipt_id, addendum_id, related_txn_id, posting_state"
            " FROM transactions ORDER BY txn_id"):
        out.append(Row(
            row_id=str(r["txn_id"]),
            movement_id=r["movement_id"], date=r["date"],
            same_day_seq=r["same_day_seq"],
            allocation_line=r["allocation_line"], txn_type=r["txn_type"],
            from_account=r["from_account"], to_account=r["to_account"],
            usd=r["usd_cents"], mxn=r["mxn_centavos"],
            receipt_value_mxn=r["receipt_face_mxn_centavos"],
            receipt_value_usd=r["receipt_face_usd_cents"],
            assigned_rate_milli=r["assigned_rate_milli"],
            voucher_id=r["voucher_id"], receipt_id=r["receipt_id"],
            addendum_id=r["addendum_id"],
            related_row_id=(str(r["related_txn_id"])
                            if r["related_txn_id"] is not None else None),
            # Both void and superseded rows remain queryable history but do
            # not participate in the authoritative replay.
            void=(r["posting_state"] != "active")))
    return out


def insert_rows(conn: sqlite3.Connection, submission_id: str,
                rows: list[Row]) -> list[int]:
    """Insert transaction rows inside the caller's transaction. Returns
    assigned txn_ids in insertion order (monotonic export cursor)."""
    ids = []
    for r in rows:
        cur = conn.execute(
            "INSERT INTO transactions (movement_id, submission_id, date,"
            " same_day_seq, allocation_line, txn_type, from_account,"
            " to_account, usd_cents, mxn_centavos, voucher_id,"
            " receipt_id, addendum_id, class_code, receipt_face_usd_cents,"
            " receipt_face_mxn_centavos, assigned_rate_milli, related_txn_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r.movement_id, submission_id, r.date, r.same_day_seq,
             r.allocation_line, r.txn_type, r.from_account, r.to_account,
             r.usd, r.mxn, r.voucher_id, r.receipt_id, r.addendum_id,
             r.class_code,
             r.receipt_value_usd, r.receipt_value_mxn,
             r.assigned_rate_milli,
             int(r.related_row_id) if r.related_row_id else None))
        ids.append(cur.lastrowid)
    return ids


def replay_projected(conn: sqlite3.Connection):
    """Load full current history and replay it (spec 14: every command
    validates projected full replay)."""
    from .replay import replay
    accounts = load_accounts(conn)
    rows = load_rows(conn)
    return replay(rows, accounts)


def assert_versions(conn: sqlite3.Connection, table: str, id_col: str,
                    checks: list[tuple[str, int]]):
    """Optimistic concurrency: expected_version must match current."""
    for row_id, expected in checks:
        cur = conn.execute(
            f"SELECT version FROM {table} WHERE {id_col} = ?",
            (row_id,)).fetchone()
        if cur is None:
            raise LookupError(f"{table} {row_id} missing")
        if cur[0] != expected:
            raise ConflictError(
                f"{table} {row_id} version {cur[0]} != "
                f"expected {expected}")


class ConflictError(Exception):
    """Stale expected_version or other optimistic-concurrency failure."""
