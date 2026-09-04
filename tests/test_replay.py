"""Conformance tests for §3/§4/§17 mandatory cases. All values integer
minor units (usd cents, mxn centavos) and Decimal basis; no float."""

from decimal import Decimal

import pytest

from cash.model import (Account, Row, SAFE, BANK, EXP_SUPPLIES,
                        EXP_PROGRAMS, FY27_0001, RECONCILIATION,
                        OPENING_EQUITY, DONATION, FX_COUNTERPARTY,
                        UNALLOCATED_EXPENSE)
from cash.replay import ValidationError, replay


@pytest.fixture
def accts():
    accounts = [
        Account("SAFE", "Safe", "safe"),
        Account("BANK", "Bank", "bank"),
        Account("OPENING_EQUITY", "Opening equity", "opening_equity"),
        Account("DONATION", "Donation", "donation"),
        Account("RECONCILIATION", "Reconciliation", "reconciliation"),
        Account("FX_COUNTERPARTY", "FX counterparty", "fx_counterparty"),
        Account("EXP_SUPPLIES", "Supplies", "expense"),
        Account("EXP_PROGRAMS", "Programs", "expense"),
        Account("FY27-0001", "Signout 1", "signout",
                parent_voucher_id="FY27-0001"),
        Account("FY27-0002", "Signout 2", "signout",
                parent_voucher_id="FY27-0002"),
        Account("UNALLOCATED_EXPENSE", "Unallocated", "expense"),
    ]
    for a in accounts:
        assert a.account_id
    return {a.account_id: a for a in accounts}


def open_safe(usd=0, mxn=0, date="2026-08-01"):
    """Opening row from OPENING_EQUITY -> SAFE with optional assigned MXN
    rate. mxn is centavos; rate 15.5 -> 15500."""
    return Row(row_id=f"o-{usd}-{mxn}", movement_id=f"m-open-{usd}-{mxn}",
               date=date, same_day_seq=0, allocation_line=0,
               txn_type="opening", from_account=OPENING_EQUITY,
               to_account=SAFE, usd=usd, mxn=mxn,
               assigned_rate_milli=(15500 if mxn else None))


def expense(row_id, mv, date, seq, line, from_acct, to_acct, usd, mxn,
            receipt_mxn=None, voucher=None, related=None):
    return Row(row_id=row_id, movement_id=mv, date=date, same_day_seq=seq,
               allocation_line=line, txn_type="expense",
               from_account=from_acct, to_account=to_acct, usd=usd,
               mxn=mxn, receipt_value_mxn=receipt_mxn,
               voucher_id=voucher, related_row_id=related)


def transfer(row_id, mv, date, seq, line, f, t, usd, mxn, ttype="signout"):
    return Row(row_id=row_id, movement_id=mv, date=date, same_day_seq=seq,
               allocation_line=line, txn_type=ttype, from_account=f,
               to_account=t, usd=usd, mxn=mxn)


def state(r, acct):
    s = r.states[acct]
    return (s.usd, s.mxn, s.basis)


class TestUSDOnly:
    def test_usd_expense(self, accts):
        rows = [open_safe(usd=100_00),
                expense("e1", "m1", "2026-08-01", 1, 0, SAFE,
                        EXP_SUPPLIES, 40_00, 0)]
        r = replay(rows, accts)
        assert r.states[SAFE].usd == 60_00
        assert r.states[SAFE].mxn == 0
        assert r.states[SAFE].basis == 0
        assert r.movements["m1"].calc_usd_by_row["e1"] == Decimal("40.00")
        assert r.expense_value == Decimal("40.00")
        assert not r.open_claims

    def test_bank_withdrawal(self, accts):
        rows = [open_safe(usd=100_00),
                transfer("w1", "m2", "2026-08-02", 0, 0, BANK, SAFE,
                         250_00, 0, "bank_withdrawal")]
        r = replay(rows, accts)
        assert r.states[SAFE].usd == 350_00
        assert r.external_in == Decimal("350.00")


class TestMXNPoolBasis:
    def test_pure_mxn_expense_uses_pool_basis(self, accts):
        rows = [
            open_safe(mxn=1550_00),  # MXN 1550 at 15.5 -> $100
            expense("e1", "m1", "2026-08-01", 1, 0, SAFE, EXP_SUPPLIES,
                    0, 310_00, receipt_mxn=310_00),  # spend MXN 310
        ]
        r = replay(rows, accts)
        assert r.states[SAFE].mxn == 1240_00
        assert r.states[SAFE].basis == Decimal("80.00")
        assert r.movements["m1"].calc_usd_by_row["e1"] == Decimal("20.00")

    def test_mixed_currency_expense(self, accts):
        # pay $5 + MXN 232.50 for MXN 310 receipt; pool at 15.5
        rows = [
            open_safe(usd=10_00, mxn=1550_00),
            expense("e1", "m1", "2026-08-01", 1, 0, SAFE, EXP_SUPPLIES,
                    5_00, 232_50, receipt_mxn=310_00),
        ]
        r = replay(rows, accts)
        # pool basis out: 232.50/15.5 = $15.00 exactly at full precision
        calc = r.movements["m1"].calc_usd_by_row["e1"]
        assert calc == Decimal("20.00")

    def test_mixed_currency_pool_rate_not_vendor_rate(self, accts):
        """MXN spent from the pool carries the pool's historical basis,
        not the vendor's implied rate (spec 3)."""
        rows = [
            # pool at 16.0: opening MXN 1600 with assigned rate 16000
            Row("o1", "mo", "2026-08-01", 0, 0, "opening",
                OPENING_EQUITY, SAFE, 10_00, 1600_00,
                assigned_rate_milli=16000),
            # vendor receipt 310 MXN; pay $5 + MXN 232.50
            # vendor implied rate = (310-232.50)/5 = 15.5
            expense("e1", "m1", "2026-08-01", 1, 0, SAFE, EXP_SUPPLIES,
                    5_00, 232_50, receipt_mxn=310_00),
        ]
        r = replay(rows, accts)
        # MXN leg at pool rate 16: 232.50/16 = 14.53125; + $5 = 19.53125
        assert r.movements["m1"].calc_usd_by_row["e1"] == \
            Decimal("19.53125")
        # NOT the vendor-rate answer 20.00
        assert r.movements["m1"].calc_usd_by_row["e1"] != \
            Decimal("20.00")


class TestUSDWithMXNChange:
    def test_change_basis(self, accts):
        # Receipt MXN 420; pay $20; MXN change 84 back into SAFE.
        # Vendor settled at (420+84)/20 = 25.2 MXN/USD.
        rows = [
            open_safe(usd=50_00),
            expense("e1", "m1", "2026-08-01", 1, 0, SAFE, EXP_SUPPLIES,
                    20_00, -84_00, receipt_mxn=420_00),
        ]
        r = replay(rows, accts)
        # incoming change basis = 20 * 84 / (420+84) = 20/6 = 3.333...
        assert r.states[SAFE].basis == Decimal("3.333333333333333333333333333")
        # calc_usd = 20 - 3.333.. = 16.666..
        assert r.movements["m1"].calc_usd_by_row["e1"] == \
            Decimal("16.66666666666666666666666667")
        assert r.states[SAFE].mxn == +84_00  # change came in


class TestExchange:
    def test_exchange_zero_expense_all_usd_becomes_basis(self, accts):
        rows = [
            open_safe(usd=100_00),
            transfer("x1", "mx", "2026-08-02", 0, 0, SAFE, FX_COUNTERPARTY,
                     40_00, -700_00, "exchange"),  # MXN 700 in for $40
        ]
        r = replay(rows, accts)
        assert r.states[SAFE].usd == 60_00
        assert r.states[SAFE].mxn == 700_00
        assert r.states[SAFE].basis == Decimal("40.00")
        assert "mx" in r.movements
        assert r.movements["mx"].calc_usd_by_row == {}
        assert r.expense_value == 0


class TestAssignedRateInflows:
    def test_assigned_rate_initial(self, accts):
        rows = [open_safe(usd=0, mxn=3875_00)]  # MXN 3875 @15.5 = $250
        r = replay(rows, accts)
        assert r.states[SAFE].basis == Decimal("250.00")

    def test_donation_empty_pool_requires_assigned(self, accts):
        rows = [Row("d1", "md", "2026-08-03", 0, 0, "donation",
                    DONATION, SAFE, 0, 1000_00,
                    assigned_rate_milli=16_250)]
        r = replay(rows, accts)
        # 1000 / 16.25 = 61.538461...
        assert r.states[SAFE].basis.quantize(Decimal("0.0001")) == \
            Decimal("61.5385")

    def test_donation_into_positive_pool_inherits_unrounded_basis(self, accts):
        rows = [
            open_safe(mxn=1550_00),
            Row("d1", "md", "2026-08-02", 0, 0, "donation",
                DONATION, SAFE, 0, 775_00),
        ]
        r = replay(rows, accts)
        assert r.states[SAFE].mxn == 2325_00
        assert r.states[SAFE].basis == Decimal("150.00")


class TestSignoutDeficitFIFO:
    def test_deficit_creates_claim_and_fifo_resolves(self, accts):
        rows = [
            open_safe(mxn=1550_00),
            # signout MXN 1000
            transfer("s1", "ms", "2026-08-01", 1, 0, SAFE, FY27_0001,
                     0, 1000_00, "signout"),
            # overspend MXN 1300 -> 300 unfunded
            expense("e1", "me", "2026-08-02", 0, 0, FY27_0001,
                    EXP_PROGRAMS, 0, 1300_00, receipt_mxn=1300_00),
        ]
        r = replay(rows, accts)
        assert r.states[FY27_0001].mxn == -300_00
        assert r.states[FY27_0001].basis == 0  # zero-basis invariant
        assert len(r.open_claims) == 1
        claim = r.open_claims[0]
        assert claim.mxn_unfunded == 300_00
        # Later MXN 500 enters the signout with assigned rate 15.5
        rows.append(transfer("a1", "ma", "2026-08-03", 0, 0, SAFE,
                             FY27_0001, 0, 500_00, "addendum"))
        r2 = replay(rows, accts)
        assert not r2.open_claims
        assert r2.states[FY27_0001].mxn == 200_00
        # 300 filled claim, then 200 in inventory at 15.5
        assert r2.states[FY27_0001].basis == \
            Decimal("200") / Decimal("15.5")

    def test_deficit_conservation(self, accts):
        """The unfunded portion of a deficit expense is valued at the
        incoming rate when the claim fills, not at the pool rate.
        expense_value must equal the funded portion (pool basis) plus
        the claim's filled basis."""
        rows = [
            open_safe(mxn=1550_00),  # $100 basis at 15.5
            transfer("s1", "ms", "2026-08-01", 1, 0, SAFE, FY27_0001,
                     0, 1000_00, "signout"),  # basis 64.516...
            expense("e1", "me", "2026-08-02", 0, 0, FY27_0001,
                    EXP_PROGRAMS, 0, 1300_00, receipt_mxn=1300_00),
        ]
        r = replay(rows, accts)
        # funded 1000 MXN at pool rate 15.5 = 64.516...; unfunded 300
        # carries no basis yet
        assert r.expense_value == Decimal("1000") / Decimal("15.5")
        assert r.claims_basis_held == 0
        # incoming MXN 500 at 15.5 fills the 300 claim (19.3548...) and
        # leaves 200 in inventory (12.9032...)
        rows.append(transfer("a1", "ma", "2026-08-03", 0, 0, SAFE,
                             FY27_0001, 0, 500_00, "addendum"))
        r2 = replay(rows, accts)
        assert not r2.open_claims
        # expense = funded 64.516... + claim basis 19.3548... = 83.871...
        assert r2.expense_value == \
            Decimal("1300") / Decimal("15.5")
        # conservation: opening 100 + 0 in - closing = expense
        closing = (Decimal(r2.states[SAFE].usd) / 100
                   + r2.states[SAFE].basis
                   + Decimal(r2.states[FY27_0001].usd) / 100
                   + r2.states[FY27_0001].basis)
        residual = (Decimal("100") - closing - r2.expense_value)
        assert residual.copy_abs() < Decimal("1e-20"), residual


class TestSafeNegativity:
    def test_safe_mxN_negative_raises(self, accts):
        rows = [open_safe(mxn=1550_00),
                expense("e1", "me", "2026-08-02", 0, 0, SAFE,
                        EXP_SUPPLIES, 0, 2000_00)]
        with pytest.raises(ValidationError):
            replay(rows, accts)

    def test_usd_negative_raises(self, accts):
        rows = [open_safe(usd=10_00),
                expense("e1", "me", "2026-08-02", 0, 0, SAFE,
                        EXP_SUPPLIES, 50_00, 0)]
        with pytest.raises(ValidationError):
            replay(rows, accts)


class TestMovementGrouping:
    def test_movement_rows_share_pre_movement_state(self, accts):
        rows = [
            open_safe(usd=100_00),
            transfer("s1", "ms", "2026-08-01", 1, 0, SAFE, FY27_0001,
                     40_00, 0, "signout"),
            expense("e1", "me", "2026-08-02", 0, 0, FY27_0001,
                    EXP_SUPPLIES, 30_00, 0),
        ]
        r = replay(rows, accts)
        assert r.states[SAFE].usd == 60_00
        assert r.states[FY27_0001].usd == 10_00
        assert r.expense_value == Decimal("30.00")


class TestConservation:
    def test_conservation_identity(self, accts):
        rows = [
            open_safe(usd=100_00, mxn=3875_00),  # $350 value in
            expense("e1", "m1", "2026-08-01", 1, 0, SAFE, EXP_SUPPLIES,
                    20_00, -84_00, receipt_mxn=420_00),
            transfer("x1", "mx", "2026-08-02", 0, 0, SAFE, FX_COUNTERPARTY,
                     15_00, -300_00, "exchange"),
        ]
        r = replay(rows, accts)
        opening = Decimal(0)
        ext_in = r.external_in
        ext_out = r.external_out
        expense_val = r.expense_value
        claims_carried = r.claims_basis_held
        closing = (Decimal(r.states[SAFE].usd) / 100
                   + r.states[SAFE].basis)
        # Conservation (spec 3): opening + ext_in - closing carries
        # expense + external-out + claims. Decimal context rounding of
        # non-terminating terms (e.g. 10/3 change basis) can leave an
        # exact-zero residual only in exact arithmetic; within the 28-
        # digit context the residual is at most one rounding unit.
        residual = (opening + ext_in - closing
                    - expense_val - ext_out - claims_carried)
        assert residual.copy_abs() < Decimal("1e-20"), residual


class TestVoids:
    def test_voided_row_ignored(self, accts):
        good = open_safe(usd=100_00)
        bad = expense("e1", "m1", "2026-08-01", 1, 0, SAFE, EXP_SUPPLIES,
                      40_00, 0)
        import dataclasses
        bad_void = dataclasses.replace(bad, void=True)
        r = replay([good, bad_void], accts)
        assert r.states[SAFE].usd == 100_00
        assert "m1" not in r.movements
