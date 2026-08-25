"""Basis mathematics (spec section 3).

All functions are pure Decimal; centavos convert by /100 only at
boundaries. Rules implemented:

- MXN leaving a positive pool carries proportional basis at full precision
  (never a rounded average rate).
- USD payment with MXN change: incoming change basis =
  usd * (-mxn) / (receipt_value_mxn - mxn); calc_usd = usd - that.
- Exchange: receipt_value_mxn = 0, so all outgoing USD becomes incoming
  MXN basis and calc_usd = 0.
- Exceptional inflows with an assigned milli-rate: basis = mxn / rate.
- Universal row value: row_value_usd = usd + signed basis outflow.
"""

from __future__ import annotations

from decimal import Decimal

ZERO = Decimal(0)
CENTAVOS = Decimal(100)
MILLI = Decimal(1000)


def proportional_basis_out(basis_before: Decimal, mxn_before: int,
                           mxn_out: int) -> Decimal:
    """Basis carried away by mxn_out leaving a positive pool.

    mxn_before > 0 is a precondition; callers handle the empty/deficit
    case (spec 3 "Signout deficits")."""
    return basis_before * Decimal(mxn_out) / Decimal(mxn_before)


def change_basis(usd: int, mxn_net: int, receipt_value_mxn: int) -> Decimal:
    """USD basis of MXN change (spec: usd * (-mxn) / (receipt - mxn)).

    mxn_net < 0 and usd > 0 are preconditions. Denominator is the USD-
    funded share of the receipt; it is positive whenever usd > 0,
    including exchanges (receipt 0, mxn -M -> denominator M).
    """
    return (Decimal(usd) / CENTAVOS * Decimal(-mxn_net)
            / Decimal(receipt_value_mxn - mxn_net))


def basis_from_assigned_rate(mxn_in: int, rate_milli: int) -> Decimal:
    """incoming_mxn_basis_usd = incoming_mxn / assigned_rate.

    Rates are stored as thousandths of MXN per USD."""
    return (Decimal(mxn_in) / CENTAVOS * MILLI / Decimal(rate_milli))


def display_rate(mxn: int, basis: Decimal) -> Decimal | None:
    """Average MXN-per-USD when displayable (quantity and basis positive).

    Never stored, never feeds a calculation (spec 3)."""
    if mxn > 0 and basis > 0:
        return Decimal(mxn) / CENTAVOS / basis
    return None
