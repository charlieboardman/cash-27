"""Cash management domain types (spec sections 2-4).

Money is stored as integer minor units (USD cents, MXN centavos).
Basis and rate math uses Decimal at unlimited precision; binary float
never enters this layer. NULL (None) means unknown, distinct from zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal(0)

SIGNOUT_SUFFIX = "signout"

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
FXC = "FX_COUNTERPARTY"


def allocate_minor_proportionally(total: int,
                                  shares: list[int]) -> list[int]:
    """Split signed minor units by positive face shares without drift.

    Integer division assigns the base amounts. Remaining cents go to the
    largest fractional remainders, with allocation order as the stable
    tie-breaker. The result always sums exactly to ``total`` and works for
    opposite-direction change as well as positive tender.
    """
    if not shares or any(share <= 0 for share in shares):
        raise ValueError("positive allocation shares required")
    whole = sum(shares)
    magnitude = abs(total)
    amounts = [(magnitude * share) // whole for share in shares]
    remainder = magnitude - sum(amounts)
    ranked = sorted(
        range(len(shares)),
        key=lambda index: (-(magnitude * shares[index] % whole), index))
    for index in ranked[:remainder]:
        amounts[index] += 1
    sign = -1 if total < 0 else 1
    return [sign * amount for amount in amounts]


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
    "Signout deficits"). Created when an expense spends MXN a signout
    does not hold. Satisfied FIFO by later incoming MXN basis."""

    movement_id: str
    account: str
    mxn_unfunded: int        # centavos still lacking basis
    related_row_id: str | None = None
    basis_filled: Decimal = ZERO


@dataclass
class MovementResult:
    """Derived, replay-time facts about one movement (never stored)."""

    # Per-row (Decimal USD, expense valuation, expense rows only).
    calc_usd_by_row: dict[str, Decimal] = field(default_factory=dict)
    # Per-row basis effects: Decimal USD. Positive = basis leaves in the
    # row's positive orientation; negative = basis enters (change back).
    basis_out_by_row: dict[str, Decimal] = field(default_factory=dict)
    # Per (row_id, account): portion of valuation carried by claims.
    # Zero Decimal -> that row is fully funded (calc_usd resolved).
    claim_carried: dict[tuple[str, str], Decimal] = field(default_factory=dict)
