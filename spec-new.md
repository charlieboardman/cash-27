# Cash Management System

## 1. Purpose

This application manages physical USD and MXN for a small nonprofit operating
in Mexico. It records where cash moved, what authorized it, what evidence
supports it, and how the resulting expense should be classified.

The accounting books are denominated in USD. The application therefore tracks
the USD cost basis carried by MXN without storing or propagating a running
exchange rate.

The system must always be able to answer:

1. How much USD and MXN is physically in the safe and each signout account?
2. What USD basis belongs to the MXN still held there?
3. What USD-denominated expense reached each account and class?
4. Which voucher, receipt, addendum, and PDF explain each movement?

SQLite is authoritative. CSV is generated reporting output only.

## 2. Core model

### Accounts

An account is a source or destination of value.

- `SAFE`: the main physical cash pool.
- `FY##-NNNN`: one physical cash pool for each employee signout.
- Expense accounts: accounting destinations, not physical cash.
- `BANK`: external source of USD withdrawals.
- `OPENING_EQUITY`: source of genuine initial balances.
- `DONATION`: source of donated cash.
- `RECONCILIATION`: counterparty for approved corrections.
- `FX_COUNTERPARTY`: the other side of a currency exchange.
- `UNALLOCATED_EXPENSE`: temporary destination for a direct expense whose cash
  moved before its final account and class were known.

`SAFE` and signout accounts can hold both USD and MXN. Only those accounts carry
physical-cash balances and MXN basis.

### Transaction rows

One transaction row moves value between `from_account` and `to_account`. Every
row contains signed USD and MXN amounts. Either amount may be zero, positive,
or negative.

Positive means the currency moved from `from_account` to `to_account`. Negative
means it moved in the opposite direction. USD and MXN are signed independently.
One row can therefore pay USD toward an expense while receiving MXN change back
into the paying cash account.

For either currency:

```text
from_balance_after = from_balance_before - signed_amount
to_balance_after   = to_balance_before   + signed_amount
```

Several rows may share a `movement_id` when one physical payment is split among
several accounts or classes. The rows are one indivisible event. They share the
same date and sequence, evaluate against the same pre-movement cash state, and
apply their aggregate physical movement once. There is no separate parent
money row to double-count.

### Workflow state is not cash state

Direct-expense vouchers and addenda may be `pending`, `complete`, or `void`.
Transaction rows are `active`, `superseded`, or `void`. A signout's open/closed
state is derived from ledger replay and its current document and receipt facts;
the compatibility status column is not authoritative.

An active sparse row participates in balances even when its PDF, final expense
account, class, description, or notes are incomplete. The physical tender facts
required by that workflow must be known before the row becomes active.
`Complete` means the paperwork and accounting review are complete; it does not
mean the cash only then began to exist.

A `superseded` row is a provisional allocation replaced by final allocation
rows under the same `movement_id`. It remains visible as history but does not
participate in replay. This is the normal completion path for a sparse direct
expense and is distinct from a void.

This system intentionally permits an administrator to correct a mistaken
historical entry in place. Editing a row retains its monotonic transaction ID,
increments its version, updates its timestamp, and validates the entire
projected replay before commit. The system does not need to retain an immutable
copy of every prior field value. A row may instead be voided when the recorded
entry should not participate at all, for example because it was a duplicate or
the recorded movement did not happen.

Transaction rows are never hard-deleted. An edit reason is required when an
administrator changes a date, same-day order, account, currency amount,
assigned rate, posting state, or movement relationship. A reason is optional
for spelling, description, notes, project, or other non-monetary presentation
corrections.

Use an in-place historical edit when the ledger was entered incorrectly. Use a
new return, reversal, or correction row when the old ledger entry was truthful
and a separate real-world cash movement happened later. This distinction keeps
the ledger practical without inventing physical events merely to repair data
entry mistakes.

## 3. Currency mathematics

### Cash-account state

Replay carries three values for every physical cash account:

```text
usd_balance
mxn_balance
mxn_usd_basis
```

Its USD-denominated book value is:

```text
cash_value_usd = usd_balance + mxn_usd_basis
```

An average MXN-per-USD rate may be displayed when quantity and basis are
positive:

```text
display_rate = mxn_balance / mxn_usd_basis
```

That rate is never stored as account state and never feeds another calculation.

### USD-only transactions

USD is already the functional currency.

- A USD-only expense has `calc_usd = usd`.
- A USD transfer moves the same cents between cash accounts.
- A bank withdrawal adds exactly the withdrawn cents to `SAFE`.
- No rate or MXN-basis logic is involved.

### MXN leaving a positive pool

MXN leaving a positive cash pool carries its proportional USD basis:

```text
mxn_basis_out =
    existing_mxn_usd_basis * mxn_out / existing_mxn_balance
```

The calculation uses full-precision quantity and basis. It never calculates a
rounded average rate and reuses it.

### Vendor settlement and MXN change

For a purchase denominated in MXN, orient USD and MXN from the paying cash
account toward the expense account:

```text
receipt_value_mxn = usd * implied_transaction_rate + mxn
```

When `usd > 0`:

```text
implied_transaction_rate = (receipt_value_mxn - mxn) / usd
```

If `mxn > 0`, net pesos left the nonprofit's existing pool. They use that
pool's proportional historical basis. The vendor rate is informational only.

If `usd > 0` and `mxn < 0`, net pesos entered the paying pool as change. Their
new USD basis is:

```text
incoming_mxn_basis_usd =
    usd * (-mxn) / (receipt_value_mxn - mxn)
```

The expense value is:

```text
calc_usd = usd - incoming_mxn_basis_usd
```

For a USD-denominated receipt, the corresponding settlement identity is:

```text
receipt_value_usd = usd + mxn / implied_transaction_rate
```

A vendor rate may establish the basis of newly received MXN, but it never
revalues MXN that the nonprofit already owned.

When one receipt is split among several accounting allocations, the receipt
still represents one physical settlement. Every account/class/project line
records its explicit USD cash, MXN cash, USD receipt-value portion, and MXN
receipt-value portion. The respective sums must equal the receipt's whole
tender and value facts. The server must not infer either currency by
proportional allocation because actual mixed-currency attribution is a factual
part of the settlement.

Replay first computes the aggregate cash and basis effect once against the
pre-movement state. It then attributes the resulting USD and MXN basis effects
to the allocation rows according to their apportioned currency quantities. It
must never run the whole receipt or the whole returned-change calculation once
per allocation row.

If an unusual settlement cannot truthfully be represented by net signed
amounts—for example, gross MXN tender and a larger MXN return whose separate
bases matter—record separate linked movements. Do not invent gross facts from a
net amount.

### Exchanges

An exchange is:

```text
SAFE or FY##-NNNN -> FX_COUNTERPARTY
usd > 0
mxn < 0
receipt_value_mxn = 0
```

All outgoing USD becomes incoming MXN basis, so `calc_usd = 0`. The exchange
rate is derivable but is not account state.

### Cash transfers

Signouts, positive addenda, and returns transfer physical currency between
tracked cash accounts. USD transfers at face value. MXN carries proportional
basis from the source pool to the destination pool. Consolidated cash value
does not change.

### Exceptional MXN inflows

MXN entering without a recorded USD-funded acquisition needs an explicit basis
source:

- Initial MXN: administrator supplies the historical MXN-per-USD rate.
- Positive cash correction: administrator supplies an approved rate and reason.
- Donation into a positive pool: use that pool's current unrounded basis
  proportion.
- Donation into an empty pool: administrator supplies an approved rate.
- Transfer from another cash pool: carry source basis; no rate is supplied.

For an assigned rate:

```text
incoming_mxn_basis_usd = incoming_mxn / assigned_rate
```

The assigned rate is retained on that event as audit input. It is not
propagated.

### Cash-account deficits

`SAFE` and signout accounts may become negative when a real expense was paid
with cash that has not yet been documented as entering that account. A USD
deficit is already valued and needs no special basis treatment.

An expense may take either account negative in MXN. An MXN cash-to-cash
transfer may not overdraw its source: doing so would create positive destination
inventory with unknown basis. For an allowed MXN expense deficit:

1. Consume available positive MXN and its proportional basis.
2. Retain the unfunded quantity as an unresolved basis claim on the expense.
3. Apply later incoming MXN basis to unresolved claims FIFO.
4. Let a linked reversal cancel its own claim first.
5. Put any remaining basis into positive signout inventory.
6. Keep `calc_usd` unresolved until the entire expense has basis.

Deficit claims are deterministic replay results, not mutable stored caches.

A cash account with a negative MXN balance has zero MXN USD basis. Incoming MXN
basis goes to that account's open claims first. Basis enters inventory only
after all claims are closed and the balance is positive.

### Universal row value and conservation

Let `signed_mxn_basis_outflow_usd` be positive when MXN basis moves in the
row's positive orientation and negative when it moves opposite that
orientation:

```text
row_value_usd = usd + signed_mxn_basis_outflow_usd
```

For an expense this is `calc_usd`. For a transfer it is value moved, not
expense. For an exchange it is zero.

Every valid replay satisfies:

```text
opening_tracked_cash_value
+ external_value_entering
- closing_tracked_cash_value
= resolved_expense_value
  + basis_held_on_unresolved_claims
  + other_external_value_leaving
```

`basis_held_on_unresolved_claims` is basis already assigned to a still-open
deficit claim. It is stated separately because that expense's `calc_usd`
remains unresolved until the claim is fully funded.

## 4. Precision

- Store USD as signed integer cents.
- Store MXN as signed integer centavos.
- Store receipt face values as integer minor units.
- Store exceptional assigned rates as integer thousandths of MXN per USD.
- Use `NULL` for unknown or inapplicable, distinct from known zero.
- Use Python `Decimal` for every division, basis, rate, and report calculation.
- Never use binary floating point for money or rates.
- Run replay calculations in an explicit decimal context with 50 significant
  digits and `ROUND_HALF_EVEN` internal context rounding.
- Do not quantize basis between replay rows. Unavoidable context rounding from
  non-terminating division remains at the 50-digit working precision.
- Full-history conservation is valid when the absolute residual is no more than
  `1E-24` USD. A larger residual is a replay validation failure.
- Round final displayed USD values to cents using `ROUND_HALF_UP`.
- Display rates may be rounded to three decimals but never reused.
- Send API money values as decimal strings.

## 5. Identities and numbering

- `transaction_id`: positive integer assigned from one global monotonic
  sequence. It never resets and is never reused.
- `movement_id`: immutable UUID for one physical event.
- `submission_id`: immutable UUID for one user command and its retries.
- `voucher_id`: authorization and reconciliation identity.
- `receipt_id`: one accepted receipt under a voucher.
- `addendum_id`: one addendum under a voucher.
- `document_id`: one normalized PDF.
- `claim_id`: one signee receipt draft/submission item.
- `package_id`: one submitted set of signout claims.

Voucher numbers are sequential by fiscal year: `FY27-0001`. Numbers are never
reused. Receipts append `-R1`, `-R2`, and so on. Addenda append `-A1`, `-A2`,
and so on.

## 6. SQLite model

Use Python's standard-library `sqlite3`, foreign keys, `STRICT` tables where
available, numbered SQL migrations, WAL mode, `synchronous=FULL`, and a
configured busy timeout. Mutations that validate projected replay use
`BEGIN IMMEDIATE`.

### `accounts`

Stores account ID, name, kind, active flag, and optional parent voucher for a
signout account. Historical accounts are deactivated, not deleted.

### `classes`

Stores class code, name, and active flag. Historical classes are deactivated,
not deleted.

### `vouchers`

Stores voucher ID/type, numbering fiscal year, recipient, purpose, notes,
authorized USD/MXN, workflow status, version, void information, and timestamps.
Voucher type is `direct_expense` or `signout`.

### `addenda`

Stores addendum ID, parent voucher, sequence, `positive` or `return` kind,
movement ID, signed-document ID once filed, reason, workflow status, version,
and timestamps.

### `receipts`

Stores receipt ID, parent voucher, sequence, document ID, independent USD and
MXN face-value components, USD/MXN tender and change, vendor, reviewed date,
status, version, and timestamps. One receipt may support several allocation
rows. A vendor-signed direct-expense voucher needs no receipt row.

The receipt row stores the whole face value exactly once. When the receipt is
split, each transaction allocation row stores its own
independent allocated USD/MXN face components; each currency must sum to the
receipt's corresponding whole component. For example, a MXN 1,000 receipt
split MXN 600 to Programs and MXN 400 to Supplies has one MXN 1,000 receipt and
allocation face shares of 600 and 400. Replay evaluates the rows as one vendor settlement under their shared
`movement_id`; it must not treat each row as another copy of the whole receipt.

For a vendor-signed direct-expense voucher with no receipt row, the allocation
face shares sum to the purchase face value recorded on the voucher.

### `documents`

Stores document ID, a safe human-facing upload name, nullable type while
unassigned, nullable voucher, opaque relative object path, SHA-256, size, parsed
page count, state, and timestamps. Every normalized PDF is immutable and stored
once as `documents/<document_id>.pdf`; workflow changes never move or rename it.
The upload name identifies documents in the interface and is never used as a
filesystem path.

Document states are:

- `inbox`: independent unassigned admin document;
- `signee_draft`: editable signout receipt document;
- `package_submitted`: locked in a submitted package;
- `filed`;
- `discarded`; or
- `missing`.

Every uploaded PDF receives an independent `document_id` and review lifecycle,
including when several PDFs arrive in one request.

### `transactions`

Every row stores:

- transaction, movement, and submission IDs;
- real-world date, same-day sequence, and allocation line;
- human/workflow transaction type;
- from and to account IDs;
- signed USD cents and MXN centavos;
- nullable voucher, receipt, and addendum IDs;
- nullable class and allocated receipt face value/currency;
- exceptional assigned-rate input when allowed;
- active/superseded/void posting state;
- optional related transaction for corrections;
- recipient, vendor, description, project, and notes;
- version, the latest applicable edit reason, void information, and timestamps.

Transaction IDs are assigned from a single monotonic integer sequence across
all fiscal years and never reset. Historical edits do not change a row's ID.

### `transaction_documents`

Links one transaction to any number of filed documents: transaction ID,
document ID, role (`voucher` or `support`), position, and timestamp. A
document may appear for the same transaction under several roles. Documents
are never deleted while linked. The vendor-signed direct-expense voucher uses
one document under both `voucher` and `support` roles.

Initial transaction labels are `bank_withdrawal`, `exchange`, `expense`,
`signout`, `addendum`, `opening`, `donation`, and `adjustment`. Labels select
validation and UI language; accounts and signs determine arithmetic.

Final expense allocations require an expense account and class. A sparse
direct expense may temporarily point to `UNALLOCATED_EXPENSE` with no class.
Final reports are blocked while active unallocated expenses remain in the
period.

### `signout_expense_claims`

Stores claim ID, voucher ID, nullable package ID, receipt document, date,
vendor, description, independent USD/MXN face values, tender/change facts, notes, claim/review state,
administrator correction note, content version, verified version, final
receipt/movement IDs after acceptance, and timestamps.

Claims do not participate in ledger replay.

### `signout_claim_allocations`

Stores administrator-entered draft allocations for a claim: line number,
explicit USD/MXN cash and face-value portions, expense account, class, project,
description, accounting notes, and version. The four component sums must
conserve the submitted receipt facts. These become transaction rows only when
the package is accepted.

### `signout_reconciliation_packages`

Stores package ID, voucher, sequence, status, version, submission/review times,
administrator summary, and final accepting submission ID.

States are:

```text
submitted -> in_review -> accepted
                       -> needs_changes -> submitted
submitted/needs_changes -> withdrawn
```

Only one unresolved package may exist per signout. Acceptance is final from the
signee workflow's perspective. An administrator may later repair a mistaken
accepted ledger entry through the historical-edit workflow, or record a new
correction when a separate physical event occurred.

### `submissions`

Stores submission ID, command kind, in-progress/committed state, canonical
response, and timestamps. The record and every command effect commit together.
A retry returns the original result and IDs.

### `ledger_state`

Stores one singleton monotonic `ledger_revision`. Any committed insertion,
historical edit, supersession, or void of a transaction increments it once,
regardless of how many allocation rows the command changes.

### `report_runs`

Stores one row for each finalized report: period, report revision, ledger
revision, generated path/hash, notes, and timestamps. Every finalized report is
retained as its own file in the persistent data root. Regeneration creates a new
report revision rather than overwriting or deleting an earlier artifact.

A preview is generated from one SQLite snapshot for immediate display or
download. It may show unallocated expenses, unresolved basis claims, and other
finalization blockers. It receives no report revision, creates no `report_runs`
row, and is not retained by the server after the response completes.

Finalization is blocked while the period contains an active unallocated expense
or an unresolved basis claim that affects the period. A finalized report is the
artifact intended for delivery or later reliance.

A report records the ledger revision from the SQLite snapshot it used. This
identifies which live-ledger version produced the report even when an older
transaction is later edited. The generated report file and hash remain the
authoritative retained copy of what was delivered at that revision; the
application need not reconstruct an old database state from the revision alone.
The report list marks a report as `current` when its ledger revision equals the
current ledger revision and `outdated` otherwise. `Outdated` does not alter or
invalidate the retained file; it means only that the live ledger has since
changed.

## 7. Transaction workflows

### Direct expense

Before a direct expense is submitted, its final tender is known: purchase face
value and currency, USD paid, MXN paid, and any change received. The preparation
page creates a voucher and an active sparse movement:

```text
SAFE -> UNALLOCATED_EXPENSE
```

The cash movement participates immediately. The row is sparse only with respect
to evidence and final accounting allocation, not the physical tender. Review
later marks the provisional row `superseded`. When the voucher itself is the
receipt, its final allocation rows share one movement ID. When several distinct
receipts support the voucher, each receipt receives its own receipt ID and
movement ID and may have several allocation rows. Across all receipts, aggregate
signed USD/MXN exactly equals the prepared movement; there is no retained
active parent cash row to double-count.

The normal document is one vendor-signed direct-expense voucher containing both
internal authorization and vendor acknowledgement. Assigning it to the sparse
item automatically files one document under both the `voucher` and `support`
roles of the transaction.

There is no separate “also use this as receipt” step and no `receipt_id` when
the voucher is the only receipt. Any number of distinct vendor receipts may be
attached instead. Each has its own recorded USD/MXN value, explicit USD/MXN
cash used, document, and optional accounting splits.

### Signout

The preparation page creates the voucher, creates its signout account, and
records:

```text
SAFE -> FY##-NNNN
```

USD and/or MXN move immediately. MXN carries SAFE basis. The signed custody
voucher is attached later in review.

### Expense from signout

Accepted signee receipt claims create:

```text
FY##-NNNN -> expense account
```

One receipt may split among accounts, classes, and projects. A package may
produce a positive remaining balance or a legitimate deficit; either condition
keeps the voucher open but does not suppress truthful accepted expenses.

### Positive addendum

The original signout's administrator workspace allocates the next addendum
number, records additional cash, and prints the addendum:

```text
SAFE -> FY##-NNNN
```

The row is active immediately. MXN carries SAFE basis and first resolves any
existing signout-account deficits. The signed addendum is required for
completion.

### Return addendum

When the administrator physically receives returned cash, the preparation page
records:

```text
FY##-NNNN -> SAFE
```

It allocates the next addendum number and prints the return addendum for
signatures. The row is active immediately and cannot exceed the signout's
positive physical balance. Its signed document is required for completion.

### Exchange

New transaction records an exchange by entering the source cash account, date,
USD given, MXN received, and notes. It creates the complete active transaction
from `SAFE` or from any signout account as the source. The same transaction may
also be created document-first from its casa de cambio receipt in Review. No
sparse exchange row is prepared first.

### Bank withdrawal

A withdrawal is recorded from New transaction by entering the date, USD
received, and notes. It may also be created document-first from its bank
evidence in Review. Both routes create:

```text
BANK -> SAFE
```

No sparse withdrawal row is prepared first.

### Opening, donation, and correction

These are explicit administrator workflows. They require their applicable
basis input, evidence, explanation, and full replay validation.

Opening cash is a one-time first-login setup task, not a routine cash event.
It records the first date covered by the ledger, the physical USD/MXN already
on hand, and the carried historical rate for starting MXN. Once saved, the
setup control disappears. The opening transaction remains editable from its
ledger detail. Saving an edit requires a reason and an explicit confirmation
that every later transaction will be replayed and finalized reports will become
outdated.

A donation is a separate recurring task reached from the low-frequency `Other`
tab on New transaction. It must never share a form or event selector with
opening cash. MXN donated into a positive pool inherits that pool's exact
unrounded basis proportion; MXN donated into an empty pool requires an approved
rate.

### Historical edits and physical corrections

An administrator may edit an existing transaction when its recorded date,
order, amount, account, class, description, project, linkage, or evidence was
entered incorrectly. The edit operates on the stable transaction ID, requires
`expected_version`, increments the ledger revision, and commits only if full
replay remains valid. Final expense allocation rows may be replaced as a set so
movement consistency remains atomic.

A physical correction is different: it records cash found short or over after
the earlier ledger entries were already truthful. It creates a new transaction
against `RECONCILIATION`, retains its evidence and explanation, and may link to
the transaction that prompted the count. Each nonzero currency discrepancy is
its own correction movement. If both USD and MXN differ, the system creates two
movements regardless of whether their directions are the same or opposite.
Both corrections may link to the same physical count document, but each has its
own recorded explanation and applicable MXN basis input. The physical-count
interface commits all movements, their shared entered explanation, and their
optional evidence document as one atomic command against the ledger revision
that was compared. If that revision is stale, nothing is recorded and the
administrator must compare the count again.

## 8. New transactions and printing

The authenticated new-transaction page is `/transactions/new` (`/voucher`
remains a compatibility route). Saving means the stated cash movement occurred,
so its active ledger row is created immediately.

It supports:

- direct-expense voucher;
- signout voucher;
- currency exchange from `SAFE` or an open signout;
- bank withdrawal into `SAFE`.

Each type reveals only its relevant fields. If cash has not moved, do not save
the transaction. Positive and return addenda are created inside the original
signout's permanent administrator workspace, where the voucher, prior addenda,
receipt submissions, documents, balance, and ledger activity remain grouped.

The page allocates identifiers transactionally and commits the record. Voucher
transactions open the relevant print page; standalone exchange and withdrawal
movements open their ledger detail. Print forms are prefilled from authoritative
data. Editing a print form changes only the rendered paper.

Every transaction has a stable `/transactions/<transaction_id>` entry point.
An unfinished direct expense opens its Review editor; a signout or addendum
opens the voucher-scoped signout workspace; a completed standalone movement
opens its ledger detail. Vouchers and addenda may be printed or reprinted from
their workspaces at any time.

Voucher and addendum templates are deployer-owned HTML. Rendering uses global
`$TOKEN` substitution in the browser and produces one US Letter page. The
application does not generate a print PDF. Names and values may print; signature
areas remain blank for wet signatures.

The direct-expense voucher separately records receipt value and tender. Receipt
value has USD and MXN boxes, either or both of which may be filled. Tender has
USD handed to the vendor, MXN handed to the vendor, and change returned in
either currency. A mixed-currency vendor acknowledgement is not converted into
a fictional single receipt currency. A one-currency receipt paired with tender
in the other currency retains the inputs needed to derive the implied vendor
rate under the master equation. The final tender fields are required and
prefilled before printing. Signature areas remain blank so the paper can
receive wet authorization and vendor acknowledgement. The signed paper can
serve as the sole receipt even when MXN change establishes new basis.

## 9. Capture and document inbox

`/capture` accepts PDF, JPEG, and PNG. Each capture card is one document and may
contain several ordered pages. The user may preview, add, remove, and reorder
pages. The same camera-first control used by the signee reconciliation page is
used here: the user can take one or more photos, choose existing photos or PDFs,
or combine both. Oversized images may be resized in the browser to shorten the
upload, but PDFs and reasonably sized images pass through unchanged. Uploads use
ordinary multipart form data; the server validates the parts and constructs the
final PDF.

Every normalized PDF receives an immutable object and an independent database
record. An administrator upload begins in logical `inbox` state without a type;
a signee upload begins in `signee_draft`. Later filing changes only database
state, type, and relationships. Documents uploaded in the same request may be
shown adjacent while retaining independent review lifecycles.

The server enforces file, request, document, and page limits while streaming;
detects type from content; parses every PDF; and builds real multi-page PDFs.
Concatenating PDF byte strings is forbidden.

## 10. Ledger view

`/ledger` is the browsing view of the authoritative transaction history. It
lists replayed transactions ordered by real-world date and
same-day sequence, filterable by period or fiscal year, account, voucher,
type, and class. Each row expands to its allocation lines, linked documents,
and superseded/void/correction relationships. A transaction drill-down offers
the administrator a validated historical edit or void action. The XLSX export
links live here. The view follows the review shell's keyboard conventions.

The transaction table labels expense `calc_usd` as `Calculated USD` and shows
it only for expense rows. A prepared direct expense is labeled `Incomplete`
until review is complete. A cash-given-out row is labeled `Incomplete` only
until its signed voucher is filed; the row is then complete even though the
employee may still hold cash. An addendum row follows its own paperwork status
rather than the parent voucher. Completed active rows have no status label.
Exceptional history is labeled `Replaced` for superseded provisional rows and
`Voided` for void rows. Superseded and void rows remain visible but do not
participate in replay.

### Current balances

`/balances` is the present-state view of tracked cash, separated from the
ledger's history. It shows the replayed SAFE USD/MXN balance with its MXN
basis and every signout with its derived open/closed state, the
reasons an open signout remains open, and its USD/MXN balance. The bank is
an external source rather than a tracked pool; a withdrawal injects cash
and no bank balance is shown. A
one-line total states tracked cash everywhere: SAFE plus open signouts.
An unfinished signout is labeled `Open` until receipt submission and cash
return are finished, including when its current balance is zero; closed
signouts remain listed at reduced emphasis.

The SAFE card includes a physical count widget. Entering the counted cash
shows the per-currency delta against the books live; nothing is persisted. A
correction action beside the delta records a required explanation and may
attach the count sheet or photo. When both USD and MXN have a nonzero delta, the
action creates two separate correction movements even when both currencies are
short or both are over. The movements and shared physical-count document commit
atomically. Editing the entered count invalidates the comparison, as does any
intervening ledger command. Corrections or historical edits associated with an
existing row are reached by drilling into that row in the ledger.

## 11. Admin review

`/review` is a keyboard-first worklist with two explicit modes. `Transactions`
contains active ledger movements that still need documents or accounting.
`Receipt submissions` contains employee expense-report packages awaiting an
atomic decision; a package is never presented as a ledger row.

Open items include:

- sparse direct-expense, signout, and addendum rows in Transactions;
- submitted signout reconciliation packages in Receipt submissions;
- opening, donation, correction, and recovery tasks.

Each worklist line shows problem badges describing the blocker (for example:
no voucher, unallocated, basis unresolved, package verification progress,
age). The list filters and sorts by badge, age, voucher, and amount.

While an item is open on a wide screen, the worklist remains visible with its
selected position marked.
Committing an item moves focus to the next open item; a `Commit & next`
action commits and opens it directly. Quick-jump keys reach packages and the
filter box.

On wide screens the review shell is a three-part spreadsheet workspace: a
narrow evidence rail on the left, a full-width filtered table of open
ledger-like rows in the center, and a right-side administrative workspace for
the selected row. With no row selected the table takes the available space and
the administrative workspace is absent. The evidence rail lists every
available inbox document newest first and contains an always-available
PDF/image upload drop zone.
Selecting a document expands its preview and actions inline within that same
document card; there is no separate preview window. Selecting evidence never
requires typing or copying a document ID. Likely matches may be promoted when
evidence exists, but unmatched inbox documents are never hidden.

Inbox documents appear only in the evidence rail, never as rows in the open
work sheet. Selecting an inbox document may expose document-specific actions
in that rail. `Record bank withdrawal` and `Record currency exchange` are two
explicit actions; they are not hidden behind a generic cash-movement label or
type selector. These actions create a financial record directly rather than
representing the document itself as a ledger-like row.

The evidence rail has compact browsing and expanded reading widths. Selecting
a document opens the reading width and a viewport-height preview without
covering the work sheet; the administrator may collapse it again. Inbox cards
show their upload names and may be dragged onto explicit document targets in
the administrative workspace. A local PDF or image may also be dropped directly on a
target, which uploads and assigns it in one action. An assigned document is
shown by name and as a PDF preview inside that workspace, never merely as an
anonymous `document attached` state.

Selecting a transaction highlights its stable table row and opens its
administrative workspace on the right. The summary row remains the sole
prominent presentation of its date, type, person, purpose, receipt value,
native-currency cash, and blockers. The workspace repeats only the identity and
cash amounts needed to maintain context, then contains the unfinished actions:
document linking, account/class assignment, verification, or an exception
decision. A submitted reconciliation package uses a compact receipt sheet
inside the workspace whose receipt rows expose their account/class and
verification controls.

Below the wide-screen breakpoint, the administrative workspace becomes a
right-side overlay. On a phone, the transaction list, selected transaction
workspace, and document queue are separate full-screen layers. The mobile
transaction list reduces each spreadsheet row to its record, person/purpose,
USD/MXN cash, and blocker badges. Document targets open the queue as a picker;
tapping a document assigns it and returns to the transaction. Camera/file
upload remains available from that picker. Drag-and-drop is an enhancement for
pointer devices and is never required to complete a mobile workflow. Closing a
workspace returns to the same transaction list rather than navigating away.

The related document renders in the adjacent rail, not in a modal. A
possible duplicate (same vendor, similar amount, nearby date) produces a
warning before commit. Unsaved changes block navigation with an explicit
discard-or-continue choice. A short undo window applies only to
review-state actions such as returning a package or replacing an
attachment. Saved ledger mistakes are repaired through the validated
historical-edit workflow; separate later cash events use corrections.
Voucher and addendum filing undo is enforced by the server for 15 seconds and
removes only the documents linked by that just-completed filing action; older
evidence linked to the same transaction is left untouched.

Row editors are intentionally different:

- Direct expense: attach the signed voucher, optionally attach a vendor
  receipt or several distinct receipts, and show each allocation as a quiet
  vertical list of `USD amount`, `MXN amount`, `Class code`, `Account code`,
  optional `Project`, and the applicable USD/MXN receipt-value portions. Each
  receipt may split independently. All receipt cash and value components must
  conserve the prepared voucher totals. Project accepts new text and offers a
  keyboard- and pointer-selectable list of distinct projects already present
  in active ledger rows or saved receipt allocations.
- Signout: attach the signed custody voucher and either attach a separate
  receipt, leave the receipt for later signee submission, or explicitly use the
  voucher itself as the receipt.
- Addendum: attach signed document to the exact existing addendum.
- Exchange: create a complete exchange from one inbox receipt.
- Withdrawal: create a complete withdrawal from one inbox document.
- Reconciliation package: verify N receipt claims one by one.

Forms use natural fields such as `USD paid`, `MXN paid`, and `MXN change` and
show the derived signed movement. USD-only work reveals no rate or MXN-basis
controls. Exceptional rate inputs appear only where required.

Contextual bodies offering attachment also accept direct upload. Uploads pass
through the same capture pipeline (content detection, PDF parsing, multi-page
assembly, hashing, limits) and attach to the item in one command. An uploaded
document the user abandons without committing falls back to inbox state;
nothing is lost.

An attachment may be replaced until the item is completed. After completion,
an administrator may attach, detach, or replace evidence through the historical
edit workflow. This does not create a fictional cash correction.

The package reviewer sees every submitted receipt as a compact spreadsheet row
and a verified count. Expanding one receipt exposes its accounting and decision
controls. The administrator may fill or split accounting allocations but may
not silently rewrite the signee's factual
date, vendor, face value, or settlement. A mismatch receives an item note and
causes the package to be returned.

If every item is verified at its current version, acceptance atomically creates
all receipt IDs, transaction rows, allocations, and filed documents. Otherwise
the whole package returns with no ledger effects. Unchanged verified items stay
locked; changed items lose verification.

After acceptance, the signee's submitted claim remains an unchanged record of
what the signee reported. If an administrator later edits the resulting ledger
transaction, the claim is not rewritten. The signee and admin views show the
original submitted facts, an administrator-correction note, and the current
ledger result together.

Every action works with ordinary buttons, Tab/Shift-Tab, arrow keys in lists,
Enter/Space, and Escape. Optional shortcuts duplicate visible controls. Status
uses text or icons in addition to color.

### Plain language and information restraint

Every page is organized around the action or decision it affords. Information
is prominent only when it helps the user complete that action; supporting
history and advanced accounting detail use progressive disclosure. Meeting a
data-availability requirement does not mean presenting every available fact as
an equally prominent dashboard card.

User-facing copy describes real-world work in language suitable for people
without accounting or technical training. Internal model terms may remain in
code, exports, and clearly advanced administrator details, but ordinary screens
prefer terms such as account code, class code, receipt submission, cash
remaining, and attached document. Important amounts use a conventional,
readable typeface at normal text size; monospaced type is reserved for short
technical identifiers and keyboard hints.

## 12. Signee reconciliation

`/FY##-NNNN` is the predictable, voucher-scoped signee page. It is protected by
the shared employee password; the initial release uses no per-person account,
secret URL, QR token, or revocation system.

The page shows only that signout:

- purpose and cash originally received;
- accepted addenda, expenses, and returns;
- authoritative USD/MXN balance;
- the effect of a submitted package;
- unsubmitted receipt drafts; and
- projected native-currency remainder and warnings.

These amounts are presented as a short, dated cash-activity history rather than
unexplained totals. It begins with the cash originally received and includes
additional cash, cash returned, and approved receipts, followed by cash
remaining. A cash movement appears when it happened even if its signed
paperwork still needs administrator review; internal paperwork state must not
make the balance look inexplicable to the signee.

The signee interface uses plain real-world language such as `receipt total`,
`cash handed over`, `change received`, and `cash used`. It does not expose
internal terms such as claim, package, face value, custody, authoritative,
allocation, ledger replay, or basis.

The signee page follows the same visual grammar as admin review: a narrow
receipt-document rail sits beside a large spreadsheet-like table of receipt
rows. Each row shows its date, vendor, description, receipt total, USD/MXN cash
used, status, and document. Adding or correcting a receipt opens a structured
inline row editor rather than requiring spreadsheet-style cell entry. The
camera/file control stays in the document rail and the selected or existing
receipt renders there. On phones the rail stacks above the receipt sheet and
the inline editor becomes a single-column form.

The signee creates receipt drafts containing:

- receipt PDF/images;
- date, vendor, and description;
- independent USD and MXN receipt-value portions (either or both may be used);
- USD/MXN paid and change received; and
- optional notes.

The receipt-file control is optimized for phones. The signee may take one
camera photo per page or choose several existing JPEG, PNG, and PDF files.
Selected parts appear as an ordered page list with previews, removal, and
move-earlier/move-later controls. Large camera images may be resized in the
browser to reduce upload time while preserving receipt legibility; PDFs pass
through unchanged. Files are uploaded as ordinary ordered multipart data, not
base64. The server detects their real types and combines them into one
validated, hashed, multi-page receipt PDF.

The page derives signed net movements as `paid - change`. The signee never sees
or enters account codes, classes, basis, or exchange rates.

Drafts remain in the signee queue until the signee selects some or all and
presses `Submit receipts`. The selected claims become one reconciliation
package and appear together as one admin work item. Unselected drafts remain
for a later package.

Only one unresolved package is allowed per signout. The signee may create more
drafts while it is reviewed but cannot submit another package yet.

The administrator may:

- accept the entire package;
- return it with a summary and per-item correction notes.

The signee may withdraw an eligible package while it is not actively under
review. Its claims return to ordinary drafts and the withdrawn package remains
as history.

A returned package remains the one unresolved package. The signee edits flagged
claims and resubmits that same versioned package. Verified unchanged items need
not be reviewed again.

Draft and submitted claims affect only projected balances. Accepted packages
create authoritative transactions. The voucher remains open while it has
unresolved packages, undisposed drafts, positive cash, or a deficit. Actual
cash return is recorded by the administrator through a return addendum.

For a signout, `closed` means the entire reconciliation is closed: the signed
custody voucher and required signed addenda are filed, no unresolved package or
draft remains, and the cash balance is exactly zero with no deficit claim.
Filing the signed custody voucher by itself does not close the signout. The
application derives this state synchronously from one ledger replay plus its
document, claim, and package facts whenever the view is loaded; there is no
independent open/closed flag to drift. Once closed, the employee page is
read-only. Any later authorized history correction that makes a condition
false causes the same projection to show it as open again.

## 13. Replay, ordering, and reporting

Replay reads one SQLite snapshot, groups active rows by `movement_id`, and
orders movements by real-world date and explicit same-day sequence. Allocation
line is only an internal deterministic order within one movement.

When another movement on the same date touches the same cash account, the server
places a new event after the existing events automatically. Routine forms do
not expose same-day sequence. If a backdated event actually happened earlier
that day, the administrator may change its order from the ledger history
editor. The server validates the complete projected history. Backdated events
simply cause deterministic replay.

The fiscal year runs September 1 through August 31 and is named for the calendar
year in which it ends. Thus September 1, 2026 through August 31, 2027 is FY27.
Fiscal year controls voucher numbering and report selection, not storage. One
genuine initial balance seeds the continuous transaction history; fiscal
boundaries do not create cash movements.
The transaction ledger is additionally available as an on-demand XLSX export
built from the live database at request time. The transactions sheet follows
the CSV column contract, adds `posting_state` and `related_txn_id` columns,
and includes one tab per fiscal year plus a full-ledger tab. Money and basis
values are exported as decimal strings and superseded or void rows remain
identifiable. The export is a full live-ledger snapshot and does not create a
report revision. Because historical rows may be edited, a transaction-ID cursor
is not a safe synchronization mechanism; consumers should fetch the full file
and compare the reported `ledger_revision`. The workbook includes a metadata
tab containing that revision and its generation timestamp.

## 14. API contract

Successful JSON uses `{"ok": true, "data": ...}`. Errors contain a stable
code, human message, field errors, and optional details. Money is sent as
decimal strings. Unknown request fields are rejected.

Every mutation has a UUID `submission_id`; edits include `expected_version`.

Required endpoint families are:

- session login/logout and CSRF state;
- independent document capture, inbox listing, streaming, and discard;
- review worklist, contextual item retrieval, and commit;
- voucher/addendum preparation, retrieval, completion, reopening, and void;
- signee claim CRUD, package submit/resubmit/withdraw, and projected state;
- admin package item coding/verification, return, and atomic acceptance;
- ledger browsing with filters and drill-down, built on transaction query;
- current-balance state for SAFE and every signout with derived
  open/closed reasons, plus same-day ordering;
- physical SAFE count comparison without persistence and revision-checked,
  atomic correction commit;
- transient report preview and versioned finalized-report generation/retrieval;
- on-demand full XLSX transaction-ledger export carrying the current
  `ledger_revision`;
- voucher/addendum print pages; and
- non-sensitive health state.

There is no unrestricted arbitrary transaction-row patch endpoint. Historical
edits use typed commands that validate the fields applicable to the transaction
kind, check `expected_version`, and replay the complete projected history.

## 15. Documents, atomicity, and recovery

All normalized PDFs live in one opaque immutable object store as
`documents/<document_id>.pdf`, regardless of whether their database state is
inbox, signee draft, submitted, filed, or discarded. SQLite stores the safe
data-root-relative object path, original display name, content hash, size, page
count, workflow state, document type, and business relationships. Symlinked
targets, traversal, and silent overwrite are forbidden.

Capture constructs and validates a PDF under a temporary UUID-derived name,
flushes it, and publishes the final object name atomically without overwrite.
Only then does it insert the document row. A handled database failure removes
the unpublished object; a process crash can leave only an unreferenced object,
which startup recovery reports. Once recorded, document bytes and object paths
never change. Filing, unfiling, submitting, returning, and discarding are
database-only changes. Replacing a document creates a new document ID.

Startup recovery reports unreferenced objects, missing referenced objects, and
hash mismatches. Missing objects are marked `missing`; no retained document is
silently deleted.

Each command uses `BEGIN IMMEDIATE`, reloads versions, validates projected full
replay, allocates all IDs, writes the canonical response, and commits everything
or nothing. Reconciliation-package acceptance is one transaction regardless of
item count. Returning a package changes review state only.

## 16. Authentication and accepted access model

New transactions, review, reports, configuration, and ledger APIs require one
shared administrative password. Employee receipt pages and all of their APIs
require a second shared employee password. That password is common to all
employees and prevents unauthenticated internet traffic; it does not isolate
one employee's signout from another. Sessions are signed, rolling, `HttpOnly`,
`SameSite=Lax`, and `Secure` under HTTPS. Every state-changing request except
login requires CSRF protection. Login attempts are rate-limited.

The predictable `/FY##-NNNN` signee page exposes no signout directory or admin
capabilities. A successful employee login establishes a signed session and CSRF
token and returns the user to the requested signout. An administrator session
may also open these pages without signing in again.

## 17. Operations

Use Python 3.12+, Flask, one Gunicorn worker, vanilla JavaScript, minimal CSS,
standard-library `sqlite3`, numbered SQL migrations, and `Decimal` arithmetic.
Keep domain commands, repositories, replay, document filing, and HTTP adapters
separate. Routes must not contain embedded replay algorithms or filesystem
commit protocols.

The persistent data root contains the SQLite database, immutable document
objects, database-import staging, reports, templates, and backups. Use SQLite's
online backup API; do not copy a live WAL database as a standalone file. Back
up every document object not already recorded as missing, including discarded
objects, and deduplicate archive payloads by hash. Back up every retained
finalized report artifact and verify it against the hash in `report_runs`.
Restore while stopped, then run migrations, integrity checks, document and
report verification, and full replay.

The authenticated Settings page also provides logical database export and
import for remote maintenance. Export is taken from a consistent online SQLite
snapshot. Import is two-step: an uploaded SQL export is first rebuilt away from
the live database, migrated to the current schema, checked for the exact
application schema, SQLite integrity, foreign keys, document/report hashes, and
full ledger replay. Activation requires the administrator password and an
explicit confirmation phrase. With one application worker and other work
stopped, activation retains a pre-import database in backups and atomically
replaces the live database. A database-only export does not replace the full
backup because it does not contain document or report files.

Production creates one portable full-backup archive nightly and immediately
before an application upgrade. The archive contains the consistent SQLite
snapshot, every retained document object deduplicated by hash, hash-verified
finalized reports, and a manifest of payload paths, sizes, and SHA-256 hashes.
A host-managed systemd timer invokes a host-managed rclone remote to upload
uniquely named archives to Google Drive. The application container receives neither the rclone
token nor permission to control host systemd. Instead, the host job writes
non-secret status and bounded activity records beneath the persistent backup
directory. Settings presents backup health and recent activity. A missing,
failed, never-successful, or overdue backup produces a warning on every
administrator page.

Settings may intentionally disable automatic backup work only after an explicit
warning and confirmation. The host timer remains installed and records disabled
heartbeats so the application can distinguish that choice from a stopped timer.
While enabled, a failed pre-upgrade Drive backup stops the upgrade before source
or application code changes. While explicitly disabled, upgrades remain
possible and the administrator warning remains visible.

Production is one rootless Podman container behind Caddy on a public,
systemd-based Linux VM, with one bind-mounted data directory. The interactive
setup script collects the public domain, administrator and shared employee
passwords, paths to administrator-provided `name,code` account and class CSV
files, and an rclone Google Drive destination. It generates
and retains the session secret, keeps private copies of the CSVs outside the
checkout, mounts them read-only into the container, installs missing host
packages, configures automatic HTTPS, application boot startup, and the nightly
backup timer, builds and starts the container, and verifies application and
initial-backup health. Container startup atomically adds,
renames, or reactivates matching codes and never deletes database codes omitted
from a CSV. The separate upgrade script creates the pre-upgrade backup, pulls
only fast-forward source changes, builds a commit-tagged image, switches the
Quadlet image reference, and checks health. It must preserve persistent state,
existing secrets, and installed CSV copies. Tailscale is not part of production.

## 18. Validation and tests

Every mutation validates account direction, minor units, identities, ownership,
receipt/allocation totals, movement consistency, cash-deficit rules, return and
reversal limits, assigned basis inputs, document state, package versions,
same-day order, and full replay conservation.

Package acceptance additionally requires every item to be verified at its
current content version and every draft allocation to sum to the submitted
claim. It creates all N transactions or none.

Mandatory tests cover:

- USD-only expense, transfer, and withdrawal;
- pure MXN and mixed-currency expense;
- USD payment with MXN change;
- exchange as zero expense;
- exceptional and transferred MXN basis;
- SAFE and signout expense-deficit creation/FIFO basis resolution;
- sparse direct expense replacement without double-counting;
- split-receipt face allocation and one-settlement evaluation;
- vendor-signed voucher serving both document roles;
- preparation of direct expense, signout, and both addenda;
- exchange/withdrawal created only from inbox review;
- independent documents from one upload;
- draft/projected versus authoritative signout balances;
- partial package submission;
- package return, correction, resubmission, and withdrawal;
- atomic N-item package acceptance;
- keyboard-only review;
- idempotent retries and stale-version rejection;
- validated historical edit, void, and superseded-row behavior;
- September/August FY27 boundary behavior;
- same-direction and opposite-direction USD/MXN count discrepancies as
  separate per-currency movements with shareable count evidence;
- transient previews create no retained artifact, while every finalized report
  revision is retained and hash-verified;
- immutable document publication/recovery, full backup archive integrity,
  host backup status,
  confirmed disable behavior, and failed pre-upgrade enforcement;
- logical database export, rejected invalid import, validated atomic import,
  and retained pre-import database; and
- 50-digit replay and `1E-24` conservation tolerance for generated valid
  histories.

## 19. Initial limitations

- One shared administrator identity.
- No automated publication of the ledger or human-readable document tree to
  external services. Google Drive currently receives disaster-recovery
  archives, not a live or editable ledger copy. Future Google Drive and Google
  Sheets publishers are optional derived views and are not part of the
  authoritative application.
- Predictable, non-secret signee URLs.
- One unresolved reconciliation package per signout.
- One SQLite writer at a time.
- Full replay at the expected modest data volume.
- Recoverable rather than absolute SQLite/PDF atomicity.
- Reverse currency exchange is out of scope.
- Direct database edits can bypass safeguards; stop and back up first.
