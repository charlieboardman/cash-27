"""Package lifecycle integration tests (spec 11, 12, 18 mandatory):
partial submission, return/correction/resubmit/withdraw, atomic N-item
acceptance."""

import io
import uuid
from pathlib import Path

import pytest
from PIL import Image

from cash import packages, repo
from cash.commands import ValidationFailure, run_command
from cash.db import apply_migrations, connect
from cash.prep import create_opening, prepare_signout

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "cash.db")
    apply_migrations(c, MIGRATIONS)
    c.execute("INSERT INTO classes (class_code, name) VALUES"
              " ('SUPPLIES', 'Supplies'), ('PROGRAMS', 'Programs')")
    yield c
    c.close()


def sid():
    return str(uuid.uuid4())


def claim(conn, voucher_id, doc_id, mxn_paid=100_00, date="2026-08-10"):
    return run_command(
        conn, sid(), "claim",
        lambda c: packages.create_claim(
            c, voucher_id=voucher_id, document_id=doc_id,
            claim_date=date, vendor="Vendor", description="thing",
            face_mxn_centavos=mxn_paid,
            usd_paid_cents=0, mxn_paid_centavos=mxn_paid,
            mxn_change_centavos=0, usd_change_cents=0, notes=""))


def doc(conn, n):
    did = f"doc-{n}"
    conn.execute(
        "INSERT INTO documents (document_id, rel_path, sha256,"
        " size_bytes, state) VALUES (?,?,?, 100, 'inbox')",
        (did, f"documents/{did}.pdf", f"h{n}"))
    return did


@pytest.fixture
def signout(conn):
    run_command(conn, sid(), "open",
                lambda c: create_opening(
                    c, submission_id=sid(), date="2026-08-01",
                    usd_cents=1000_00, mxn_centavos=1550_00,
                    assigned_rate_milli=15500))
    v = run_command(conn, sid(), "prep",
                    lambda c: prepare_signout(
                        c, submission_id=sid(), date="2026-08-02",
                        seq_no=0, recipient="Ana", purpose="",
                        notes="", usd_cents=0, mxn_centavos=1000_00))
    return v["voucher_id"]


class TestClaims:
    @pytest.mark.parametrize("usd,mxn,face_usd,face_mxn,mode", [
        (0, 0, 0, 0, "cash"),
        (-100, -100, 0, 0, "cash"),
        (1000, -6000, 0, 0, "cash"),
        (-200, 10000, 0, 0, "cash"),
        (1000, 0, -1, 0, "cash"),
        (1000, 0, 0, 0, "receipt_currency"),
    ])
    def test_cash_claim_validation(self, conn, signout, usd, mxn,
                                   face_usd, face_mxn, mode):
        with pytest.raises(ValidationFailure):
            run_command(conn, sid(), "claim", lambda c: packages.create_claim(
                c, voucher_id=signout, document_id=doc(conn, 50),
                claim_date="2026-08-10", vendor="Store", description="Supplies",
                usd_paid_cents=max(usd, 0), usd_change_cents=max(-usd, 0),
                mxn_paid_centavos=max(mxn, 0), mxn_change_centavos=max(-mxn, 0),
                face_usd_cents=face_usd, face_mxn_centavos=face_mxn,
                payment_mode=mode))

    def test_claim_draft_no_ledger_effect(self, conn, signout):
        before = repo.replay_projected(conn)
        claim(conn, signout, doc(conn, 1))
        after = repo.replay_projected(conn)
        assert before.states == after.states
        assert not after.movements.keys() - before.movements.keys()

    def test_partial_submission(self, conn, signout):
        c1 = claim(conn, signout, doc(conn, 1))["claim_id"]
        c2 = claim(conn, signout, doc(conn, 2))["claim_id"]
        pkg = run_command(conn, sid(), "submit",
                          lambda c: packages.submit_package(
                              c, voucher_id=signout, claim_ids=[c1]))
        # c2 remains draft
        states = conn.execute(
            "SELECT claim_id, state FROM signout_expense_claims"
        ).fetchall()
        st = {r["claim_id"]: r["state"] for r in states}
        assert st[c1] == "submitted" and st[c2] == "draft"
        # one unresolved package only
        c3 = claim(conn, signout, doc(conn, 3))["claim_id"]
        with pytest.raises(ValidationFailure):
            run_command(conn, sid(), "submit",
                        lambda c: packages.submit_package(
                            c, voucher_id=signout, claim_ids=[c3]))


class TestPackageReview:
    def _submitted(self, conn, signout, n=2):
        ids = [claim(conn, signout, doc(conn, i))["claim_id"]
               for i in range(1, n + 1)]
        pkg = run_command(conn, sid(), "submit",
                          lambda c: packages.submit_package(
                              c, voucher_id=signout, claim_ids=ids))
        return ids, pkg["package_id"]

    def _allocate(self, conn, claim_id, mxn=100_00, usd=0,
                  account="EXP_SUPPLIES", class_code="SUPPLIES"):
        conn.execute(
            "INSERT INTO signout_claim_allocations (claim_id, line_no,"
            " usd_cents, mxn_centavos, face_mxn_centavos, expense_account,"
            " class_code) VALUES (?, 0, ?, ?, 10000, ?, ?)",
            (claim_id, usd, mxn, account, class_code))

    def test_atomic_n_item_acceptance(self, conn, signout):
        ids, pid = self._submitted(conn, signout, 3)
        for cid in ids:
            self._allocate(conn, cid)
            run_command(conn, sid(), "verify",
                        lambda c, cid=cid: packages.verify_claim(
                            c, claim_id=cid, expected_content_version=1))
        before = repo.replay_projected(conn)
        res = run_command(conn, sid(), "accept",
                          lambda c: packages.accept_package(
                              c, submission_id=sid(), package_id=pid))
        assert len(res["accepted"]) == 3
        after = repo.replay_projected(conn)
        # 300 MXN spent from signout at 15.5 -> basis 19.3548...
        assert after.states[signout].mxn == 700_00
        # all receipts filed
        filed = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE state='filed'"
        ).fetchone()[0]
        assert filed == 3
        # class codes carried onto the transaction rows
        classes = conn.execute(
            "SELECT DISTINCT class_code FROM transactions WHERE"
            " voucher_id=? AND txn_type='expense'",
            (signout,)).fetchall()
        assert [c[0] for c in classes] == ["SUPPLIES"]

    def test_mixed_currency_receipt_keeps_explicit_split_tender(self, conn,
                                                                signout):
        created = run_command(
            conn, sid(), "mixed_claim",
            lambda c: packages.create_claim(
                c, voucher_id=signout, document_id=doc(conn, 20),
                claim_date="2026-08-10", vendor="Contractor",
                description="Work", face_usd_cents=10_00,
                face_mxn_centavos=150_00, usd_paid_cents=0,
                mxn_paid_centavos=200_00, usd_change_cents=0,
                mxn_change_centavos=0, notes=""))
        claim_id = created["claim_id"]
        package_id = run_command(
            conn, sid(), "submit",
            lambda c: packages.submit_package(
                c, voucher_id=signout, claim_ids=[claim_id]))["package_id"]
        run_command(
            conn, sid(), "allocate",
            lambda c: packages.set_claim_allocations(
                c, claim_id=claim_id, expected_content_version=1,
                allocations=[
                    {"expense_account": "EXP_SUPPLIES",
                     "class_code": "SUPPLIES", "usd_cents": 0,
                     "mxn_centavos": 50_00, "face_usd_cents": 10_00,
                     "face_mxn_centavos": 0, "project": "", "description": ""},
                    {"expense_account": "EXP_PROGRAMS",
                     "class_code": "PROGRAMS", "usd_cents": 0,
                     "mxn_centavos": 150_00, "face_usd_cents": 0,
                     "face_mxn_centavos": 150_00, "project": "", "description": ""},
                ]))
        run_command(conn, sid(), "verify", lambda c: packages.verify_claim(
            c, claim_id=claim_id, expected_content_version=1))
        run_command(conn, sid(), "accept", lambda c: packages.accept_package(
            c, submission_id=sid(), package_id=package_id))
        rows = conn.execute(
            "SELECT usd_cents, mxn_centavos, receipt_face_usd_cents,"
            " receipt_face_mxn_centavos FROM transactions WHERE receipt_id="
            " (SELECT final_receipt_id FROM signout_expense_claims WHERE"
            " claim_id=?) ORDER BY allocation_line", (claim_id,)).fetchall()
        assert [row["mxn_centavos"] for row in rows] == [50_00, 150_00]
        assert [row["receipt_face_usd_cents"] for row in rows] == [10_00, None]
        assert [row["receipt_face_mxn_centavos"] for row in rows] == [None, 150_00]

    def test_unverified_claim_blocks_acceptance(self, conn, signout):
        ids, pid = self._submitted(conn, signout, 2)
        for cid in ids:
            self._allocate(conn, cid)
        run_command(conn, sid(), "verify",
                    lambda c: packages.verify_claim(
                        c, claim_id=ids[0], expected_content_version=1))
        with pytest.raises(ValidationFailure) as e:
            run_command(conn, sid(), "accept",
                        lambda c: packages.accept_package(
                            c, submission_id=sid(), package_id=pid))
        assert e.value.code == "unverified"
        # no effects (atomic)
        n = conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
        assert n == 0

    def test_allocation_mismatch_blocks(self, conn, signout):
        ids, pid = self._submitted(conn, signout, 1)
        cid = ids[0]
        # admin enters allocations that don't sum to the claim (100 MXN)
        self._allocate(conn, cid, mxn=50_00)
        run_command(conn, sid(), "verify",
                    lambda c: packages.verify_claim(
                        c, claim_id=cid, expected_content_version=1))
        with pytest.raises(ValidationFailure) as e:
            run_command(conn, sid(), "accept",
                        lambda c: packages.accept_package(
                            c, submission_id=sid(), package_id=pid))
        assert e.value.code == "allocation_mismatch"

    def test_no_allocations_blocks_acceptance(self, conn, signout):
        ids, pid = self._submitted(conn, signout, 1)
        run_command(conn, sid(), "verify",
                    lambda c: packages.verify_claim(
                        c, claim_id=ids[0], expected_content_version=1))
        with pytest.raises(ValidationFailure) as e:
            run_command(conn, sid(), "accept",
                        lambda c: packages.accept_package(
                            c, submission_id=sid(), package_id=pid))
        assert e.value.code == "no_allocations"

    def test_missing_class_blocks(self, conn, signout):
        ids, pid = self._submitted(conn, signout, 1)
        cid = ids[0]
        self._allocate(conn, cid, class_code=None)
        run_command(conn, sid(), "verify",
                    lambda c: packages.verify_claim(
                        c, claim_id=cid, expected_content_version=1))
        with pytest.raises(ValidationFailure) as e:
            run_command(conn, sid(), "accept",
                        lambda c: packages.accept_package(
                            c, submission_id=sid(), package_id=pid))
        assert e.value.code == "incomplete_allocation"

    def test_verify_requires_submitted_package(self, conn, signout):
        cid = claim(conn, signout, doc(conn, 9))["claim_id"]
        with pytest.raises(ValidationFailure) as e:
            run_command(conn, sid(), "verify",
                        lambda c: packages.verify_claim(
                            c, claim_id=cid,
                            expected_content_version=1))
        assert e.value.code == "not_submitted"

    def test_return_then_resubmit(self, conn, signout):
        ids, pid = self._submitted(conn, signout, 1)
        run_command(conn, sid(), "return",
                    lambda c: packages.return_package(
                        c, package_id=pid, admin_summary="fix date",
                        item_notes={ids[0]: "date is wrong"}))
        p = conn.execute(
            "SELECT status FROM signout_reconciliation_packages WHERE"
            " package_id=?", (pid,)).fetchone()
        assert p["status"] == "needs_changes"
        flagged = conn.execute(
            "SELECT state, admin_note FROM signout_expense_claims WHERE"
            " claim_id=?", (ids[0],)).fetchone()
        assert flagged["state"] == "flagged"
        assert flagged["admin_note"] == "date is wrong"
        # Signee edits the flagged claim and resubmits the same package.
        run_command(conn, sid(), "resubmit",
                    lambda c: packages.update_claim(
                        c, voucher_id=signout, claim_id=ids[0],
                        expected_content_version=1,
                        document_id="doc-1", claim_date="2026-08-11",
                        vendor="Vendor", description="thing",
                        face_mxn_centavos=100_00,
                        usd_paid_cents=0, mxn_paid_centavos=100_00,
                        mxn_change_centavos=0, usd_change_cents=0,
                        notes=""))
        run_command(conn, sid(), "resubmit",
                    lambda c: packages.resubmit_package(
                        c, voucher_id=signout, package_id=pid))
        p = conn.execute(
            "SELECT status FROM signout_reconciliation_packages WHERE"
            " package_id=?", (pid,)).fetchone()
        assert p["status"] == "submitted"

    def test_withdraw(self, conn, signout):
        ids, pid = self._submitted(conn, signout, 1)
        r = run_command(conn, sid(), "withdraw",
                        lambda c: packages.withdraw_package(
                            c, voucher_id=signout, package_id=pid))
        state = conn.execute(
            "SELECT state FROM signout_expense_claims WHERE claim_id=?",
            (ids[0],)).fetchone()[0]
        assert state == "draft"
        # documents return to signee_draft, not stuck package_submitted
        dstate = conn.execute(
            "SELECT state FROM documents WHERE document_id ="
            " (SELECT document_id FROM signout_expense_claims WHERE"
            " claim_id=?)", (ids[0],)).fetchone()[0]
        assert dstate == "signee_draft"
