"""The codes printed on the app's paper, as text and as QR codes.

    FY27-0042        voucher
    FY27-0042-A1     addendum
    FY27-0042-R3x    no-receipt statement for receipt 3
    FY27-0042-log    purchase log
    Txn-123          safe count sheet

A vendor's receipt (FY27-0042-R3) carries no code. Parsing is exact: a code
matches only as the whole string, so paths, URLs, and partial matches are
never mistaken for one. No imports beyond the standard library, because the
QR worker runs this in its own process.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

_CODE = re.compile(
    r"(?P<voucher>FY[0-9]{2}-[0-9]{4,})"
    r"(?:-A(?P<addendum>[1-9][0-9]*)|-R(?P<statement>[1-9][0-9]*)x|(?P<log>-log))?"
    r"|Txn-(?P<txn>[1-9][0-9]*)", re.ASCII)


@dataclass(frozen=True)
class Code:
    kind: str                       # voucher, addendum, statement, log, count
    text: str
    voucher_id: str | None = None
    addendum_id: str | None = None
    receipt_seq: int | None = None
    txn_id: int | None = None


def parse(text) -> Code | None:
    match = _CODE.fullmatch(text) if isinstance(text, str) else None
    if not match:
        return None
    if match["txn"]:
        return Code("count", text, txn_id=int(match["txn"]))
    voucher = match["voucher"]
    if match["addendum"]:
        return Code("addendum", text, voucher_id=voucher, addendum_id=text)
    if match["statement"]:
        return Code("statement", text, voucher_id=voucher,
                    receipt_seq=int(match["statement"]))
    if match["log"]:
        return Code("log", text, voucher_id=voucher)
    return Code("voucher", text, voucher_id=voucher)


def statement(voucher_id: str, receipt_seq: int) -> str:
    return f"{voucher_id}-R{receipt_seq}x"


def log(voucher_id: str) -> str:
    return f"{voucher_id}-log"
