"""Command-layer integration tests against real SQLite: idempotency,
voucher numbering, return-addendum limits, projected replay validation."""

import uuid
from pathlib import Path

import pytest

from cash import repo
from cash.commands import ValidationFailure, run_command
from cash.db import apply_migrations, connect
from cash.prep import (create_opening, create_withdrawal,
                       prepare_addendum, prepare_direct_expense,
                       prepare_signout)

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "cash.db")
    apply_migrations(c, MIGRATIONS)  # also seeds system accounts
    yield c
    c.close()


def sid():
    return str(uuid.uuid4())


class TestPreparation:
    def test_only_one_active_opening_balance(self, conn):
        run_command(
            conn, sid(), "opening",
            lambda c: create_opening(
                c, submission_id=sid(), date="2026-08-01",
                usd_cents=100_00))
        with pytest.raises(ValidationFailure) as error:
            run_command(
                conn, sid(), "second_opening",
                lambda c: create_opening(
                    c, submission_id=sid(), date="2026-08-02",
                    usd_cents=1_00))
        assert error.value.code == "opening_exists"

    def test_direct_expense_sparse_row(self, conn):
        run_command(conn, sid(), "open",
                    lambda c: create_opening(c, submission_id=sid(),
                                             date="2026-08-01",
                                             usd_cents=500_00))
        r = run_command(conn, sid(), "prep_direct",
                        lambda c: prepare_direct_expense(
                            c, submission_id=sid(), date="2026-08-02",
                            seq_no=0, recipient="Tortilleria",
                            purpose="Lunch supplies", notes="",
                            usd_cents=40_00))
        assert r["voucher_id"].startswith("FY26-")
        res = repo.replay_projected(conn)
        assert res.states["SAFE"].usd == 460_00

    def test_voucher_numbering_sequential(self, conn):
        run_command(conn, sid(), "open",
                    lambda c: create_opening(
                        c, submission_id=sid(), date="2026-08-01",
                        usd_cents=500_00))
        v1 = run_command(conn, sid(), "prep",
                         lambda c: prepare_direct_expense(
                             c, submission_id=sid(), date="2026-08-02",
                             seq_no=0, recipient="A", purpose="",
                             notes="", usd_cents=1_00))
        v2 = run_command(conn, sid(), "prep",
                         lambda c: prepare_direct_expense(
                             c, submission_id=sid(), date="2026-08-03",
                             seq_no=0, recipient="B", purpose="",
                             notes="", usd_cents=1_00))
        assert v1["voucher_id"] == "FY26-0001"
        assert v2["voucher_id"] == "FY26-0002"

    def test_new_same_day_movements_are_ordered_automatically(self, conn):
        run_command(conn, sid(), "open",
                    lambda c: create_opening(
                        c, submission_id=sid(), date="2026-08-01",
                        usd_cents=500_00))
        for recipient in ("A", "B"):
            run_command(conn, sid(), f"expense_{recipient}",
                        lambda c, name=recipient: prepare_direct_expense(
                            c, submission_id=sid(), date="2026-08-01",
                            seq_no=None, recipient=name, purpose="Supplies",
                            notes="", usd_cents=1_00))
        orders = [row[0] for row in conn.execute(
            "SELECT same_day_seq FROM transactions WHERE date='2026-08-01'"
            " ORDER BY txn_id")]
        assert orders == [0, 1, 2]

    def test_signout_creates_account_and_moves_cash(self, conn):
        run_command(conn, sid(), "open",
                    lambda c: create_opening(
                        c, submission_id=sid(), date="2026-08-01",
                        usd_cents=500_00, mxn_centavos=1550_00,
                        assigned_rate_milli=15500))
        r = run_command(conn, sid(), "prep_signout",
                        lambda c: prepare_signout(
                            c, submission_id=sid(), date="2026-08-02",
                            seq_no=0, recipient="Ana", purpose="Supplies",
                            notes="", usd_cents=100_00,
                            mxn_centavos=500_00))
        acct = r["account_id"]
        res = repo.replay_projected(conn)
        assert res.states["SAFE"].usd == 400_00
        assert res.states[acct].usd == 100_00
        assert res.states[acct].mxn == 500_00
        # proportional basis: 500/1550 * 100 USD = 32.258...
        assert str(res.states[acct].basis)[:8] == "32.25806"

    def test_return_addendum_limit(self, conn):
        run_command(conn, sid(), "open",
                    lambda c: create_opening(
                        c, submission_id=sid(), date="2026-08-01",
                        usd_cents=500_00))
        v = run_command(conn, sid(), "prep_signout",
                        lambda c: prepare_signout(
                            c, submission_id=sid(), date="2026-08-02",
                            seq_no=0, recipient="Ana", purpose="",
                            notes="", usd_cents=50_00))
        # try returning more than the signout holds
        with pytest.raises(ValidationFailure) as e:
            run_command(conn, sid(), "prep_add",
                        lambda c: prepare_addendum(
                            c, submission_id=sid(),
                            voucher_id=v["voucher_id"], date="2026-08-03",
                            seq_no=0, kind="return", reason="",
                            usd_cents=60_00))
        assert e.value.code == "return_exceeds_balance"
        # within limit succeeds
        ok = run_command(conn, sid(), "prep_add",
                         lambda c: prepare_addendum(
                             c, submission_id=sid(),
                             voucher_id=v["voucher_id"],
                             date="2026-08-03", seq_no=0, kind="return",
                             reason="", usd_cents=20_00))
        assert ok["addendum_id"].endswith("-A1")


class TestIdempotency:
    def test_retry_same_submission_returns_original(self, conn):
        sub = sid()
        run_command(conn, sid(), "open",
                    lambda c: create_opening(c, submission_id=sid(),
                                             date="2026-08-01",
                                             usd_cents=500_00))
        sub2 = sid()
        args = dict(submission_id=sub2, date="2026-08-02", seq_no=0,
                    recipient="Tortilleria", purpose="x", notes="",
                    usd_cents=1_00)
        first = run_command(conn, sub2, "prep",
                            lambda c: prepare_direct_expense(c, **args))
        again = run_command(conn, sub2, "prep",
                            lambda c: prepare_direct_expense(c, **args))
        assert first == again
        n = conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0]
        assert n == 1

    def test_safe_overdraw_rejected_by_replay(self, conn):
        run_command(conn, sid(), "open",
                    lambda c: create_opening(c, submission_id=sid(),
                                             date="2026-08-01",
                                             usd_cents=10_00))
        with pytest.raises(Exception):
            run_command(conn, sid(), "prep",
                        lambda c: prepare_direct_expense(
                            c, submission_id=sid(), date="2026-08-02",
                            seq_no=0, recipient="x", purpose="",
                            notes="", usd_cents=50_00))
        # rolled back entirely
        assert conn.execute("SELECT COUNT(*) FROM vouchers"
                            ).fetchone()[0] == 0

    def test_withdrawal(self, conn):
        run_command(conn, sid(), "open",
                    lambda c: create_opening(c, submission_id=sid(),
                                             date="2026-08-01",
                                             usd_cents=100_00))
        r = run_command(conn, sid(), "withdrawal",
                        lambda c: create_withdrawal(
                            c, submission_id=sid(), date="2026-08-02",
                            seq_no=0, usd_cents=250_00))
        res = repo.replay_projected(conn)
        assert res.states["SAFE"].usd == 350_00

class TestCompletion:
    def _seed_expense(self, conn):
        conn.execute(
            "INSERT INTO classes (class_code, name) VALUES"
            " ('GENERAL', 'General')")
        run_command(conn, sid(), "open",
                    lambda c: create_opening(c, submission_id=sid(),
                                             date="2026-08-01",
                                             usd_cents=500_00))
        return run_command(conn, sid(), "prep",
                           lambda c: prepare_direct_expense(
                               c, submission_id=sid(), date="2026-08-02",
                               seq_no=0, recipient="Vendor", purpose="",
                               notes="", usd_cents=40_00))

    def test_sparse_replacement_no_double_count(self, conn):
        from cash.prep import complete_direct_expense
        r = self._seed_expense(conn)
        before = repo.replay_projected(conn)
        assert before.states["SAFE"].usd == 460_00
        run_command(conn, sid(), "complete",
                    lambda c: complete_direct_expense(
                        c, submission_id=sid(),
                        voucher_id=r["voucher_id"], expected_version=1,
                        allocations=[
                            {"expense_account": "EXP_SUPPLIES",
                             "class_code": "GENERAL", "usd_cents": 40_00,
                             "mxn_centavos": 0, "project": "",
                             "description": ""}]))
        after = repo.replay_projected(conn)
        # cash moved exactly once through the movement
        assert after.states["SAFE"].usd == 460_00
        assert after.expense_value == before.expense_value
        n_active = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE posting_state"
            " ='active' AND voucher_id=?", (r["voucher_id"],)
        ).fetchone()[0]
        assert n_active == 1  # sparse voided, one final row active

    def test_vendor_voucher_serves_both_document_roles(self, conn):
        from cash.prep import complete_direct_expense
        r = self._seed_expense(conn)
        conn.execute(
            "INSERT INTO documents (document_id, rel_path, sha256,"
            " size_bytes, state) VALUES ('doc1', 'inbox/doc1.pdf',"
            " 'abc', 100, 'inbox')")
        run_command(conn, sid(), "complete",
                    lambda c: complete_direct_expense(
                        c, submission_id=sid(),
                        voucher_id=r["voucher_id"], expected_version=1,
                        document_id="doc1",
                        allocations=[
                            {"expense_account": "EXP_SUPPLIES",
                             "class_code": "GENERAL", "usd_cents": 40_00,
                             "mxn_centavos": 0, "project": "",
                             "description": ""}]))
        roles = conn.execute(
            "SELECT role FROM transaction_documents td JOIN"
            " transactions t ON t.txn_id = td.txn_id WHERE t.voucher_id=?"
            " ORDER BY role", (r["voucher_id"],)).fetchall()
        assert [x[0] for x in roles] == ["support", "voucher"]
        d = conn.execute("SELECT state, voucher_id, doc_type FROM"
                         " documents WHERE document_id='doc1'").fetchone()
        assert d["state"] == "filed" and d["doc_type"] == "voucher"

    def test_allocation_mismatch_rejected(self, conn):
        from cash.prep import complete_direct_expense
        r = self._seed_expense(conn)
        with pytest.raises(ValidationFailure) as e:
            run_command(conn, sid(), "complete",
                        lambda c: complete_direct_expense(
                            c, submission_id=sid(),
                            voucher_id=r["voucher_id"], expected_version=1,
                            allocations=[
                                {"expense_account": "EXP_SUPPLIES",
                                 "class_code": "GENERAL", "usd_cents": 41_00,
                                 "mxn_centavos": 0, "project": "",
                                 "description": ""}]))
        assert e.value.code == "allocation_mismatch"

    def test_stale_version_rejected(self, conn):
        from cash.prep import complete_direct_expense
        r = self._seed_expense(conn)
        from cash.repo import ConflictError
        with pytest.raises(ConflictError):
            run_command(conn, sid(), "complete",
                        lambda c: complete_direct_expense(
                            c, submission_id=sid(),
                            voucher_id=r["voucher_id"], expected_version=99,
                            allocations=[
                                {"expense_account": "EXP_SUPPLIES",
                                 "class_code": "GENERAL", "usd_cents": 40_00,
                                 "mxn_centavos": 0, "project": "",
                                 "description": ""}]))
