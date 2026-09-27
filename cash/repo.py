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


def pending_withdrawals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Withdrawals count as cash immediately but still need their receipt."""
    return conn.execute(
        "SELECT t.* FROM transactions t WHERE t.txn_type='bank_withdrawal'"
        " AND t.posting_state='active' AND NOT EXISTS ("
        " SELECT 1 FROM transaction_documents td WHERE td.txn_id=t.txn_id"
        " AND td.role='support')").fetchall()


def receipt_numbers(conn: sqlite3.Connection) -> dict[str, str]:
    """Receipt number for each receipt document. A direct expense's receipts
    are numbered without a ledger line pointing at them."""
    return {row["document_id"]: row["receipt_id"] for row in conn.execute(
        "SELECT receipt_id, document_id FROM receipts"
        " WHERE status!='void' AND document_id IS NOT NULL")}


def document_reference(txn, role: str, document_id: str,
                       numbers: dict[str, str]) -> str:
    """The name a person sees for a document on a transaction: FY27-0042,
    FY27-0042-A1, FY27-0042-R1, or Txn-123-receipt when nothing is numbered."""
    if role == "voucher" and (txn["addendum_id"] or txn["voucher_id"]):
        return txn["addendum_id"] or txn["voucher_id"]
    return (txn["receipt_id"] or numbers.get(document_id) or
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


def load_rows(conn: sqlite3.Connection,
              through_date: str | None = None) -> list[Row]:
    sql = ("SELECT txn_id, movement_id, date, same_day_seq,"
           " allocation_line, txn_type, from_account, to_account,"
           " usd_cents, mxn_centavos, receipt_face_usd_cents,"
           " receipt_face_mxn_centavos, assigned_rate_milli, voucher_id,"
           " receipt_id, addendum_id, related_txn_id, posting_state"
           " FROM transactions")
    params = []
    if through_date is not None:
        sql += " WHERE date <= ?"
        params.append(through_date)
    sql += " ORDER BY txn_id"
    out = []
    for r in conn.execute(sql, params):
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
    from .replay import ReplayResult, replay
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
