"""Full-history replay (spec section 12).

Rows are grouped by movement_id; movements run in (date, same_day_seq,
movement_id) order; rows within a movement run in (allocation_line,
row_id) order against the same pre-movement state. An expense event
(rows sharing one expense account under one movement) is settled once
per funding cash account so USD tender, pool MXN, and change back into
that account form one settlement (spec 3 "Vendor settlement").

Deficit claims are pure replay results: an expense spending MXN beyond
a signout's positive stock leaves the balance negative, carries no basis
for the unfunded part on the expense, and opens a FIFO claim that later
incoming MXN basis fills first (a linked reversal cancels its own claim
first). A negative MXN balance always carries zero basis.

Conservation counters (spec 3 "Universal row value") accumulate value
entering/leaving tracked cash through external counterparties and the
resolved expense value. SAFE may never go negative in either currency;
no cash account may go negative in USD.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .basis import basis_from_assigned_rate, proportional_basis_out
from .model import Account, AccountState, Claim, MovementResult, Row

ZERO = Decimal(0)
CENT = Decimal(100)

EXTERNAL_KINDS = ("bank", "opening_equity", "donation", "reconciliation",
                  "fx_counterparty")


class ReplayError(Exception):
    pass


class ValidationError(ReplayError):
    """A history violates a hard invariant."""


@dataclass
class ReplayResult:
    states: dict[str, AccountState]
    movements: dict[str, MovementResult] = field(default_factory=dict)
    open_claims: list[Claim] = field(default_factory=list)
    external_in: Decimal = ZERO
    external_out: Decimal = ZERO
    expense_value: Decimal = ZERO       # resolved expense value
    claims_basis_held: Decimal = ZERO   # filled basis parked on open claims


def replay(rows: list[Row], accounts: dict[str, Account],
           strict: bool = True) -> ReplayResult:
    """Replay active rows. accounts maps account_id -> Account for every
    account referenced by any row; cash accounts hold state."""
    active = [r for r in rows if not r.void]
    by_movement: dict[str, list[Row]] = defaultdict(list)
    for r in active:
        for acct in (r.from_account, r.to_account):
            if acct not in accounts:
                raise ValidationError(f"unknown account {acct}")
        by_movement[r.movement_id].append(r)

    def mkey(m: list[Row]):
        return (m[0].date, m[0].same_day_seq, m[0].movement_id)

    movements = sorted(by_movement.values(), key=mkey)
    for m in movements:
        m.sort(key=lambda r: (r.allocation_line, r.row_id))
        for r in m:
            if (r.date, r.same_day_seq) != (m[0].date, m[0].same_day_seq):
                raise ValidationError(
                    f"movement {m[0].movement_id} rows disagree on "
                    f"date/sequence")
    _check_same_day_order(movements, accounts)

    states = {a: AccountState() for a, acct in accounts.items()
              if acct.is_cash}
    result = ReplayResult(states=states)
    for movement in movements:
        ctx = _Ctx(movement, accounts, states, result)
        ctx.run()
        if strict:
            for a, st in states.items():
                kind = accounts[a].kind
                if st.usd < 0:
                    raise ValidationError(
                        f"{a} USD negative after {movement[0].movement_id}")
                if st.mxn < 0 and kind != "signout":
                    raise ValidationError(
                        f"{a} MXN negative after "
                        f"{movement[0].movement_id}")
    result.claims_basis_held = sum((c.basis_filled
                                    for c in result.open_claims), ZERO)
    return result


class _Ctx:
    def __init__(self, movement, accounts, states, result):
        self.rows = movement
        self.accounts = accounts
        self.states = states
        self.result = result
        self.pre = dict(states)
        self.mres = MovementResult()
        self.mres_unfunded: dict[tuple[str, str], int] = {}
        result.movements[movement[0].movement_id] = self.mres

    # ---- movement orchestration --------------------------------------

    def run(self):
        groups: dict[tuple, list[Row]] = {}
        for r in self.rows:
            for key in self._group_keys(r):
                groups.setdefault(key, []).append(r)
        for key in sorted(groups, key=str):
            grows = groups[key]
            if self._is_expense_group(grows):
                self._apply_expense_group(grows)
            else:
                for r in grows:
                    self._apply_stock(r)
                    self._external_leg(r, ZERO, 0)

    def _expense_acct(self, r: Row) -> str | None:
        if self.accounts[r.to_account].kind == "expense":
            return r.to_account
        if self.accounts[r.from_account].kind == "expense":
            return r.from_account
        return None

    def _is_expense_group(self, rows) -> bool:
        return any(self._expense_acct(r) is not None for r in rows)

    def _group_keys(self, r: Row):
        ea = self._expense_acct(r)
        if ea is not None:
            return [("e", ea)]
        attached = []
        for other in self.rows:
            oea = self._expense_acct(other)
            if oea and oea in (r.from_account, r.to_account):
                attached.append(("e", oea))
        return attached or [("x", r.row_id)]

    # ---- expense event settlement -------------------------------------

    def _apply_expense_group(self, rows: list[Row]):
        cash_accts = {a for r in rows for a in (r.from_account, r.to_account)
                      if self.accounts[a].is_cash}
        usd_tender: dict[str, int] = defaultdict(int)
        mxn_tender: dict[str, int] = defaultdict(int)
        receipt_mxn = None
        receipt_usd = None
        for r in rows:
            if r.receipt_value_mxn is not None:
                receipt_mxn = r.receipt_value_mxn
            if r.receipt_value_usd is not None:
                receipt_usd = r.receipt_value_usd
            for acct in cash_accts:
                usd_tender[acct] += self._leg(r, acct, r.usd)
                mxn_tender[acct] += self._leg(r, acct, r.mxn)

        # Precompute incoming (change) basis per funding account.
        change: dict[str, Decimal] = {}
        for acct in cash_accts:
            u, m = usd_tender[acct], mxn_tender[acct]
            if u > 0 and m < 0:
                face = receipt_mxn if receipt_mxn is not None else 0
                if face - m <= 0 and face != 0:
                    raise ValidationError(
                        f"movement {rows[0].movement_id}: USD-funded "
                        f"share of receipt not positive")
                denom = face - m if face else -m
                change[acct] = Decimal(u) / CENT * Decimal(-m) / denom

        for r in rows:
            self._apply_stock(r, change=change)

        for r in rows:
            if self._expense_acct(r) is None:
                self._external_leg(r, ZERO, 0)
                continue
            calc = self._row_calc(r, receipt_mxn, receipt_usd)
            self.mres.calc_usd_by_row[r.row_id] = calc
            claim_part = self._claim_part(r, calc)
            cash_acct = r.from_account if r.usd >= 0 else r.to_account
            if claim_part:
                self.mres.claim_carried[(r.row_id, cash_acct)] = claim_part
            # The expense is fully valued at creation: the funded
            # portion carries pool basis now; the unfunded portion is
            # valued when incoming MXN fills the claim at the incoming
            # rate (spec 3 steps 2-3). Adding claim_part here would
            # double-count it when the claim closes.
            self.result.expense_value += calc
            self._external_leg(r, ZERO, 0)

    def _row_calc(self, r: Row, receipt_mxn, receipt_usd) -> Decimal:
        """calc_usd per spec 3 for one expense row.

        The vendor's implied rate prices only MXN the vendor gives back
        as change (mxn < 0). MXN spent from the pool (mxn > 0) carries
        the pool's proportional historical basis; the vendor rate is
        informational only. The universal form is usd + basis_out,
        where basis_out is negative for change."""
        usd = Decimal(r.usd) / CENT
        basis_out = self.mres.basis_out_by_row.get(r.row_id, ZERO)
        if receipt_usd is not None:
            return Decimal(receipt_usd) / CENT
        if receipt_mxn is not None and r.usd > 0 and r.mxn < 0:
            denom = receipt_mxn - r.mxn
            if denom <= 0:
                raise ValidationError(
                    f"row {r.row_id}: USD-funded receipt share "
                    f"not positive")
            return usd * Decimal(receipt_mxn) / Decimal(denom)
        return usd + basis_out

    def _claim_part(self, r: Row, calc: Decimal) -> Decimal:
        """Portion of this row's calc_usd attributable to unfunded
        deficit quantity: the share of the MXN leg that carried no pool
        basis (spec: calc_usd unresolved until the entire expense has
        basis)."""
        acct = r.from_account if r.usd >= 0 else r.to_account
        if not self.accounts[acct].is_cash or r.mxn <= 0 or r.usd != 0:
            return ZERO
        unfunded = self.mres_unfunded.get((r.row_id, acct), 0)
        if not unfunded:
            return ZERO
        return calc * Decimal(unfunded) / Decimal(r.mxn)

    # ---- stock movement ------------------------------------------------

    def _apply_stock(self, r: Row, change=None):
        if not hasattr(self, "_last_incoming"):
            self._last_incoming = {}
        if r.usd:
            if self.accounts[r.from_account].is_cash:
                self._move_usd(r.from_account, -r.usd)
            if self.accounts[r.to_account].is_cash:
                self._move_usd(r.to_account, r.usd)

        basis_out_cash = ZERO
        basis_in = ZERO
        dst_for_basis = None
        if r.mxn:
            if r.mxn > 0:
                out_acct, in_acct = r.from_account, r.to_account
            else:
                # mxn < 0: quantity returned with the USD tender; it
                # lands back in the cash side of the row (change or
                # exchange receipt).
                out_acct = in_acct = None
                for acct in (r.from_account, r.to_account):
                    if self.accounts[acct].is_cash:
                        in_acct = acct
                qty = abs(r.mxn)
                dst_for_basis = in_acct
                if r.assigned_rate_milli is not None:
                    basis_in = basis_from_assigned_rate(
                        qty, r.assigned_rate_milli)
                elif change is not None and in_acct in change:
                    basis_in = change[in_acct]
                elif r.txn_type == "exchange":
                    basis_in = Decimal(r.usd) / CENT
                else:
                    basis_in = ZERO
                if in_acct is not None:
                    if self.accounts[in_acct].kind == "signout":
                        basis_in = self._fill_claims_first(
                            in_acct, qty, basis_in, r)
                    self._give_mxn(in_acct, qty, basis_in)
                self.mres.basis_out_by_row[r.row_id] = \
                    self.mres.basis_out_by_row.get(r.row_id, ZERO) \
                    - basis_in
                self._last_incoming[r.row_id] = (dst_for_basis, basis_in)
                return
            qty = r.mxn
            dst_for_basis = in_acct
            if self.accounts[out_acct].is_cash:
                basis_out_cash = self._take_mxn(out_acct, qty, r)
            if self.accounts[in_acct].is_cash:
                if change is not None and in_acct in change:
                    basis_in = change[in_acct]
                elif r.assigned_rate_milli is not None:
                    basis_in = basis_from_assigned_rate(
                        qty, r.assigned_rate_milli)
                else:
                    basis_in = basis_out_cash
                if self.accounts[in_acct].kind == "signout":
                    basis_in = self._fill_claims_first(
                        in_acct, qty, basis_in, r)
                self._give_mxn(in_acct, qty, basis_in)

        self.mres.basis_out_by_row[r.row_id] = \
            self.mres.basis_out_by_row.get(r.row_id, ZERO) + basis_out_cash
        self._last_incoming[r.row_id] = (dst_for_basis, basis_in)

    def _leg(self, r: Row, acct: str, amt: int) -> int:
        if acct == r.from_account:
            return amt
        if acct == r.to_account:
            return -amt
        return 0

    def _move_usd(self, acct, delta):
        if not self.accounts[acct].is_cash:
            return
        st = self.states[acct]
        self.states[acct] = AccountState(st.usd + delta, st.mxn, st.basis)

    def _take_mxn(self, acct, qty, row) -> Decimal:
        st = self.states[acct]
        spend = min(qty, max(st.mxn, 0))
        taken = proportional_basis_out(st.basis, st.mxn, spend) \
            if spend > 0 and st.mxn > 0 else ZERO
        new_mxn = st.mxn - qty
        new_basis = st.basis - taken
        if new_mxn <= 0:
            new_basis = ZERO  # negative-or-empty pool: zero basis
        self.states[acct] = AccountState(st.usd, new_mxn, new_basis)
        unfunded = 0
        if qty > spend and new_mxn < 0 and \
                self.accounts[acct].kind == "signout":
            unfunded = qty - spend
            existing = next(
                (c for c in self.result.open_claims
                 if c.movement_id == row.movement_id and c.account == acct),
                None)
            if existing:
                existing.mxn_unfunded += unfunded
            else:
                self.result.open_claims.append(Claim(
                    movement_id=row.movement_id, account=acct,
                    mxn_unfunded=unfunded,
                    related_row_id=row.related_row_id))
        cash_side = row.from_account if row.usd >= 0 else row.to_account
        self.mres_unfunded[(row.row_id, cash_side)] = \
            self.mres_unfunded.get((row.row_id, cash_side), 0) + unfunded
        return taken

    def _give_mxn(self, acct, qty, basis):
        st = self.states[acct]
        self.states[acct] = AccountState(st.usd, st.mxn + qty,
                                         st.basis + basis)

    def _fill_claims_first(self, acct, qty, incoming_basis, r) -> Decimal:
        claims = [c for c in self.result.open_claims if c.account == acct]
        related = None
        if r.related_row_id:
            related = next((c for c in claims
                            if c.related_row_id == r.related_row_id), None)
        ordered = ([related] if related else []) + \
                  [c for c in claims if c is not related]
        remaining_qty = qty
        remaining_basis = incoming_basis
        for c in ordered:
            if remaining_qty <= 0:
                break
            cover = min(remaining_qty, c.mxn_unfunded)
            share_basis = incoming_basis * Decimal(cover) / Decimal(qty)
            c.basis_filled += share_basis
            c.mxn_unfunded -= cover
            remaining_qty -= cover
            remaining_basis -= share_basis
            if c.mxn_unfunded == 0:
                self.result.open_claims.remove(c)
                self.result.expense_value += c.basis_filled
        return remaining_basis if remaining_qty > 0 else ZERO

    # ---- conservation counters -----------------------------------------

    def _external_leg(self, r: Row, _a, _b):
        kf = self.accounts[r.from_account].kind
        kt = self.accounts[r.to_account].kind
        usd = Decimal(r.usd) / CENT
        basis_in_credited = ZERO
        incoming = getattr(self, "_last_incoming", {}).get(r.row_id)
        if incoming and incoming[0] is not None:
            basis_in_credited = incoming[1]
        if r.txn_type == "exchange":
            # Value-neutral within tracked cash: the USD leg leaving and
            # the MXN basis returning are the same value. Neither side
            # of the conservation identity moves.
            return
        if kf in EXTERNAL_KINDS and self.accounts[r.to_account].is_cash:
            self.result.external_in += usd + basis_in_credited
        elif kt in EXTERNAL_KINDS and self.accounts[r.from_account].is_cash:
            basis_out = self.mres.basis_out_by_row.get(r.row_id, ZERO)
            self.result.external_out += usd + basis_out


def _check_same_day_order(movements, accounts):
    """Spec 13: when two movements on the same date share a same-day
    sequence and touch the same cash account, their order is
    ambiguous. The administrator must place the new event truthfully;
    replay refuses to guess by UUID."""
    by_slot: dict[tuple[str, int], list[list[Row]]] = defaultdict(list)
    for m in movements:
        by_slot[(m[0].date, m[0].same_day_seq)].append(m)
    for (date, seq), group in by_slot.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a = {acct for r in group[i] for acct in
                     (r.from_account, r.to_account)
                     if accounts[acct].is_cash}
                b = {acct for r in group[j] for acct in
                     (r.from_account, r.to_account)
                     if accounts[acct].is_cash}
                if a & b:
                    raise ValidationError(
                        f"movements {group[i][0].movement_id} and "
                        f"{group[j][0].movement_id} share date {date} "
                        f"sequence {seq} and cash account(s) "
                        f"{sorted(a & b)}; assign distinct same-day "
                        f"sequences to place them truthfully")
