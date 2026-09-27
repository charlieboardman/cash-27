"""Every form the app prints with a code is exactly one page (a purchase log
is one page per sheet, each sheet carrying the code), so a scan can always be
split by the code on each page and no page of a form lacks its code.

These limits on the text that prints keep each form to one page even when a
field is filled with the widest letters. tests/test_paper.py prints every
form with every field at its limit and checks for one page. Anything longer
belongs in another document.
"""

from __future__ import annotations

from .commands import ValidationFailure

NAME = 60                # voucher recipient or vendor, approver
VOUCHER_PURPOSE = 100
VOUCHER_NOTES = 200
VOUCHER_NOTES_LINES = 5
REASON = 200             # addendum reason, row explanation, count explanation
ROW_VENDOR = 60
ROW_PURPOSE = 120


def check(value, field: str, label: str, limit: int, lines: int = 1) -> str:
    """The stripped text, or a refusal naming the limit."""
    text = str(value or "").strip()
    if len(text) > limit or text.count("\n") >= lines:
        shape = f"{lines} lines and {limit} characters" if lines > 1 else f"{limit} characters"
        raise ValidationFailure(
            "too_long", f"{label} can be at most {shape} so the paper fits on one page",
            {field: f"at most {shape}"})
    return text
