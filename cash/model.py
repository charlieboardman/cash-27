"""Cash management domain types (spec sections 2-4).

Money is stored as integer minor units (USD cents, MXN centavos).
Basis and rate math uses Decimal at unlimited precision; binary float
never enters this layer. NULL (None) means unknown, distinct from zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal(0)

# Account kinds (spec section 2).
CASH_KINDS = ("safe", "signout")
EXTERNAL_KINDS = ("bank", "opening_equity", "donation", "reconciliation",
                  "fx_counterparty")

SAFE = "SAFE"
BANK = "BANK"
OPENING_EQUITY = "OPENING_EQUITY"
DONATION = "DONATION"
RECONCILIATION = "RECONCILIATION"
FX_COUNTERPARTY = "FX_COUNTERPARTY"
UNALLOCATED_EXPENSE = "UNALLOCATED_EXPENSE"
# Expense accounts used by the app/tests; the set is administrator-managed.
EXP_SUPPLIES = "EXP_SUPPLIES"
EXP_PROGRAMS = "EXP_PROGRAMS"
FY27_0001 = "FY27-0001"
FY27_0002 = "FY27-0002"


def amount_sign_errors(txn_type: str, usd: int, mxn: int) -> dict[str, str]:
    """Sign rules shared by editing, replay, and startup diagnostics."""
    if txn_type == "expense":
        return {}
    if txn_type == "exchange":
        return {field: message for field, invalid, message in (
            ("usd_cents", usd <= 0, "must be positive (USD paid)"),
            ("mxn_centavos", mxn >= 0, "must be negative (MXN received)"),
        ) if invalid}
    return {field: "cannot be negative" for field, amount in (
        ("usd_cents", usd), ("mxn_centavos", mxn)) if amount < 0}


@dataclass(frozen=True)
class Account:
    account_id: str
    name: str
    kind: str  # safe | signout | expense | one of EXTERNAL_KINDS
    active: bool = True
    parent_voucher_id: str | None = None

    @property
    def is_cash(self) -> bool:
        return self.kind in CASH_KINDS


@dataclass(frozen=True)
class Row:
    """One transaction row (spec section 2 "Transaction rows").

    Signed amounts are oriented from_account -> to_account: positive means
    the currency moved in that direction. usd and mxn sign independently.
    Negative legs are supported only for expenses and exchanges. Other row
    types use nonnegative amounts and encode direction in their accounts.
    Exchanges require strictly positive USD and strictly negative MXN.
    Several rows may share movement_id as one indivisible physical event.
    """

    row_id: str
    movement_id: str
    date: str               # ISO calendar date
    same_day_seq: int
    allocation_line: int    # deterministic order within the movement
    txn_type: str           # expense, signout, addendum, exchange, ...
    from_account: str
    to_account: str
    usd: int                # signed cents
    mxn: int                # signed centavos
    receipt_value_mxn: int | None = None  # receipt face value, minor units
    receipt_value_usd: int | None = None
    assigned_rate_milli: int | None = None  # MXN per USD * 1000, audit input
    voucher_id: str | None = None
    receipt_id: str | None = None
    addendum_id: str | None = None
    class_code: str | None = None
    related_row_id: str | None = None     # correction/reversal link
    void: bool = False


@dataclass(frozen=True)
class AccountState:
    """Replay-carried state of one physical cash account (spec section 3)."""

    usd: int = 0
    mxn: int = 0
    basis: Decimal = ZERO  # Decimal USD; full precision, never rounded

    @property
    def value_usd(self) -> Decimal:
        return Decimal(self.usd) / 100 + self.basis


@dataclass
class Claim:
    """Unresolved MXN basis claim against an expense movement (spec 3,
    "Cash-account deficits"). Created when an expense spends MXN that its
    SAFE or signout account does not yet hold. Satisfied FIFO by later
    incoming MXN basis."""

    movement_id: str
    row_id: str
    account: str
    mxn_unfunded: int        # centavos still lacking basis
    related_row_id: str | None = None
    basis_filled: Decimal = ZERO


@dataclass
class MovementResult:
    """Derived, replay-time facts about one movement (never stored)."""

    # Immutable physical SAFE balance immediately after this whole settlement.
    # Later funding can resolve expense valuation but must not change this snapshot.
    safe_balance_after: AccountState | None = None
    # Per-row (Decimal USD, expense valuation, expense rows only).
    calc_usd_by_row: dict[str, Decimal] = field(default_factory=dict)
    # Per-row basis effects: Decimal USD. Positive = basis leaves in the
    # row's positive orientation; negative = basis enters (change back).
    basis_out_by_row: dict[str, Decimal] = field(default_factory=dict)
    # Rows with an open basis claim at the end of full-history replay.
    unresolved_rows: set[str] = field(default_factory=set)
