"""Money on the wire: decimal strings in and out, integer minor units inside.

Every request amount goes through to_minor and every response amount through
to_text, so the whole API agrees on blank-means-zero, two decimal places, and
the error a person sees.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .commands import ValidationFailure


def to_minor(value, field="amount", *, allow_negative=True) -> int:
    """Decimal string (or number) -> cents/centavos. Blank means zero."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValidationFailure("bad_amount", f"Enter a number for {field}",
                                {field: "invalid amount"}) from exc
    if not amount.is_finite():
        raise ValidationFailure("bad_amount", f"Enter a number for {field}",
                                {field: "invalid amount"})
    if amount.as_tuple().exponent < -2:
        raise ValidationFailure("bad_amount", f"Use at most two decimal places for {field}",
                                {field: "two decimals maximum"})
    if amount < 0 and not allow_negative:
        raise ValidationFailure("bad_amount", f"{field} cannot be negative",
                                {field: "negative"})
    return int(amount * 100)


def optional_minor(value, field="amount", **options) -> int | None:
    """Like to_minor, but blank means "not given" rather than zero."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return to_minor(value, field, **options)


def to_text(minor) -> str | None:
    """Cents/centavos -> "1234.56" (None stays None)."""
    return None if minor is None else f"{Decimal(minor) / 100:.2f}"


def rate_text(milli) -> str | None:
    """MXN-per-USD rate stored in thousandths -> "17.250"."""
    return None if milli is None else f"{Decimal(milli) / 1000:.3f}"
