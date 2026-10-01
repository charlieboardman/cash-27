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
    case (spec 3 "Cash-account deficits")."""
    return basis_before * Decimal(mxn_out) / Decimal(mxn_before)


def basis_from_assigned_rate(mxn_in: int, rate_milli: int) -> Decimal:
    """incoming_mxn_basis_usd = incoming_mxn / assigned_rate.

    Rates are stored as thousandths of MXN per USD."""
    return (Decimal(mxn_in) / CENTAVOS * MILLI / Decimal(rate_milli))
