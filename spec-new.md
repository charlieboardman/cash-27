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

Vouchers and addenda may be `pending`, `complete`, or `void`. Transaction rows
are `active`, `superseded`, or `void`.

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
still represents one physical settlement. The administrator enters a positive
face-value share for each account/class/project line, and those shares must sum
to the receipt's whole face value. The server apportions the aggregate signed
USD and MXN tender facts across the lines in the same proportions. Any
minor-unit remainder is assigned deterministically by allocation-line order.

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

### Signout deficits

`SAFE` may never become negative in USD or MXN. A signout may become negative
when a real employee expense exceeds the cash currently documented as issued.

USD deficits are already valued. For an MXN deficit:

1. Consume available positive MXN and its proportional basis.
2. Retain the unfunded quantity as an unresolved basis claim on the expense.
3. Apply later incoming MXN basis to unresolved claims FIFO.
4. Let a linked reversal cancel its own claim first.
5. Put any remaining basis into positive signout inventory.
6. Keep `calc_usd` unresolved until the entire expense has basis.

Deficit claims are deterministic replay results, not mutable stored caches.

A signout account with a negative MXN balance has zero MXN USD basis. Incoming
MXN basis goes to open claims first. Basis enters inventory only after all
claims are closed and the balance is positive.

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

Stores receipt ID, parent voucher, sequence, document ID, face denomination and
value, vendor, reviewed date, status, version, and timestamps. One receipt may
support several allocation rows. A vendor-signed direct-expense voucher needs
no receipt row.

The receipt row stores the whole face value exactly once. When the receipt is
split, each transaction allocation row stores its own
`allocated_face_minor`; those shares must sum to the receipt's whole face
value. For example, a MXN 1,000 receipt split MXN 600 to Programs and MXN 400
to Supplies has one MXN 1,000 receipt and allocation face shares of 600 and
400. Replay evaluates the rows as one vendor settlement under their shared
`movement_id`; it must not treat each row as another copy of the whole receipt.

For a vendor-signed direct-expense voucher with no receipt row, the allocation
face shares sum to the purchase face value recorded on the voucher.

### `documents`

Stores document ID, nullable type while unassigned, nullable voucher, relative
path, SHA-256, size, parsed page count, state, and timestamps.

Document states are:

- `inbox`: independent unassigned admin document;
- `signee_draft`: editable signout receipt document;
- `package_submitted`: locked in a submitted package;
- `staged`: being filed by a recoverable commit;
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
vendor, description, face value, tender/change facts, notes, claim/review state,
administrator correction note, content version, verified version, final
receipt/movement IDs after acceptance, and timestamps.

Claims do not participate in ledger replay.

### `signout_claim_allocations`

Stores administrator-entered draft allocations for a claim: line number,
positive allocated face value, expense account, class, project, description,
accounting notes, and version. The server derives each line's signed USD/MXN
share deterministically from the claim's aggregate tender facts and allocated
face shares. These become transaction rows only when the package is accepted.

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
later marks the provisional row `superseded` and creates one or more final
expense rows under the same `movement_id`, with aggregate signed USD/MXN equal
to the prepared movement.

The normal document is one vendor-signed direct-expense voucher containing both
internal authorization and vendor acknowledgement. Assigning it to the sparse
item automatically files one document under both the `voucher` and `support`
roles of the transaction.

There is no separate “also use this as receipt” step and no `receipt_id`.
A separate vendor receipt is optional and may be attached additionally.

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

The preparation page selects the existing signout, allocates the next addendum
number, records additional cash, and prints the addendum:

```text
SAFE -> FY##-NNNN
```

The row is active immediately. MXN carries SAFE basis and first resolves
signout deficits. The signed addendum is required for completion.

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

An exchange is created only from its casa de cambio receipt in the admin inbox.
Review enters the date, USD given, MXN received, and notes, then creates the
complete active transaction from `SAFE` or from any signout account as the
source. No sparse exchange row is prepared first.

### Bank withdrawal

A withdrawal is created from its inbox evidence. Review enters the date and USD
received, then creates:

```text
BANK -> SAFE
```

No sparse withdrawal row is prepared first.

### Opening, donation, and correction

These are explicit administrator workflows. They require their applicable
basis input, evidence, explanation, and full replay validation.

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
own explanation, approval, and applicable MXN basis input.

## 8. Preparation and printing

The authenticated preparation page is `/voucher`. It is the one place for
paperwork that must exist before its signed copy returns.

It supports:

- direct-expense voucher;
- signout voucher;
- positive addendum; and
- return addendum.

Each type reveals only its relevant fields and substeps. Submitting means the
stated cash movement occurred, so the sparse row is active immediately. If cash
has not moved, do not submit the preparation form.

The page allocates identifiers transactionally, commits the sparse record, and
opens the relevant print page. Print forms are prefilled from authoritative
data. Editing a print form changes only the rendered paper.

Voucher and addendum templates are deployer-owned HTML. Rendering uses global
`$TOKEN` substitution in the browser and produces one US Letter page. The
application does not generate a print PDF. Names and values may print; signature
areas remain blank for wet signatures.

The direct-expense voucher includes fields for vendor, purchase face
value/currency, USD/MXN paid, change returned, purpose, and acknowledgement.
The final tender fields are required and prefilled before printing. Signature
areas remain blank so the paper can receive wet authorization and vendor
acknowledgement. The signed paper can serve as the sole receipt even when MXN
change establishes new basis.

## 9. Capture and document inbox

`/capture` accepts PDF, JPEG, and PNG. Each capture card is one document and may
contain several ordered pages. The user may preview, add, remove, and reorder
pages.

Every normalized PDF enters the admin inbox untyped. Its eventual destination
determines its document type. Documents uploaded in the same request may be
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
the administrator a validated historical edit or void action. A header strip
shows the replayed balances:
SAFE USD/MXN with MXN basis, BANK, and every open signout. The XLSX export
links live here. The view follows the review shell's keyboard conventions.

The header includes a physical SAFE count widget. Entering the counted cash
shows the per-currency delta against the books live; nothing is persisted. A
`New correction` action beside the balances prefills a correction from that
delta; opening and donation entries live here too. When both USD and MXN have a
nonzero delta, the action creates two separate correction drafts even when both
currencies are short or both are over. The drafts may share the physical count
document. Corrections or historical edits associated with an existing row are
reached by drilling into that row.

## 11. Admin review

`/review` is a keyboard-first worklist of open accounting and document items.

Open items include:

- sparse direct-expense, signout, and addendum rows;
- independent inbox documents;
- submitted signout reconciliation packages;
- opening, donation, correction, and recovery tasks.

Each worklist line shows problem badges describing the blocker (for example:
no voucher, unallocated, basis unresolved, package verification progress,
age). The list filters and sorts by badge, age, voucher, and amount.

While an item is open, the worklist remains visible with position marked.
Committing an item moves focus to the next open item; a `Commit & next`
action commits and opens it directly. Quick-jump keys reach the inbox,
packages, and the filter box.

On wide screens the review shell is a persistent three-pane desk: the filtered
worklist on the left, the contextual decision workspace in the center, and an
evidence rail on the right. The evidence rail keeps the current PDF visible,
lists every available inbox document newest first, and contains an always-
available PDF/image upload drop zone. Selecting evidence never requires typing
or copying a document ID. Likely matches may be promoted when evidence exists,
but unmatched inbox documents are never hidden. Narrow screens collapse the
three panes into explicit Work, Decision, and Evidence views without losing any
action.

Contextual bodies show projected cash balances that update as amounts are
entered, and the related document renders beside the form, not in a modal. A
possible duplicate (same vendor, similar amount, nearby date) produces a
warning before commit. Unsaved changes block navigation with an explicit
discard-or-continue choice. A short undo window applies only to
review-state actions such as returning a package or replacing an
attachment. Saved ledger mistakes are repaired through the validated
historical-edit workflow; separate later cash events use corrections.

Selecting a row opens a separate contextual body containing every field,
document, code, and decision still needed. The shared shell provides worklist
navigation, document viewing, warnings, versions, and accept/return actions.

Contextual bodies are intentionally different:

- Direct expense: attach signed voucher, optionally attach vendor receipt,
  verify the prepared tender/face facts, and fill positive face-value shares,
  accounts, classes, projects, and descriptions.
- Signout: attach signed custody voucher and fill missing descriptive facts.
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

The package reviewer sees one receipt and its submitted facts at a time,
previous/next controls, and a verified count. The administrator may fill or
split accounting allocations but may not silently rewrite the signee's factual
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
prefer terms such as spending category, purpose category, receipt submission,
cash remaining, and attached document. Important amounts use a conventional,
readable typeface at normal text size; monospaced type is reserved for short
technical identifiers and keyboard hints.

## 12. Signee reconciliation

`/FY##-NNNN` is the predictable, voucher-scoped signee page. The initial release
uses no secret URL, QR token, or revocation system.

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

The signee creates receipt drafts containing:

- receipt PDF/images;
- date, vendor, and description;
- receipt face value and denomination;
- USD/MXN paid and change received; and
- optional notes.

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

For a signout, `complete` means the entire reconciliation is closed: the signed
custody voucher and required signed addenda are filed, no unresolved package or
draft remains, and the cash balance is exactly zero with no deficit claim.
Filing the signed custody voucher by itself does not complete the signout. Once
the voucher is complete, the page becomes read-only until an administrator
reopens it. A historical edit that causes any completion condition to become
false automatically reopens the signout in the same database command.

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
- transaction query, validated historical edit/void, and explicit physical
  correction;
- replayed balances and same-day ordering;
- physical SAFE count comparison without persistence;
- transient report preview and versioned finalized-report generation/retrieval;
- on-demand full XLSX transaction-ledger export carrying the current
  `ledger_revision`;
- voucher/addendum print pages; and
- non-sensitive health state.

There is no unrestricted arbitrary transaction-row patch endpoint. Historical
edits use typed commands that validate the fields applicable to the transaction
kind, check `expected_version`, and replay the complete projected history.

## 15. Documents, atomicity, and recovery

Unassigned PDFs live under `inbox/<document_id>.pdf`. Filed voucher documents
live under `documents/<fiscal-year>/<voucher-id>/`; standalone evidence lives
under `documents/<fiscal-year>/standalone/`. SQLite stores only validated
data-root-relative paths. Symlinked targets, traversal, and silent overwrite
are forbidden.

Filing uses a recoverable protocol:

1. Start with a normalized, hashed inbox document.
2. Begin the SQLite transaction.
3. Write staged document and business metadata.
4. Move the PDF to its final nonconflicting path.
5. Commit SQLite.
6. Clear obsolete inbox state.

Startup recovery reconciles staged rows, orphan files, and missing/hash-mismatched
documents. No inbox or signee document is deleted without a recorded
disposition.

Each command uses `BEGIN IMMEDIATE`, reloads versions, validates projected full
replay, allocates all IDs, writes the canonical response, and commits everything
or nothing. Reconciliation-package acceptance is one transaction regardless of
item count. Returning a package changes review state only.

## 16. Authentication and accepted access model

Admin preparation, review, reports, configuration, and ledger APIs require one
shared administrative password. Sessions are signed, rolling, `HttpOnly`,
`SameSite=Lax`, and `Secure` under HTTPS. Every state-changing request except
login requires CSRF protection. Login attempts are rate-limited.

The predictable `/FY##-NNNN` signee page and its claim/package APIs do not
require the admin password. They expose no signout directory or admin
capabilities, but anyone who knows or guesses another valid voucher ID can view
or alter that signout's drafts. Visiting the page establishes an anonymous
signed session and CSRF token for form submission; it is not access control.
This risk is accepted initially.

## 17. Operations

Use Python 3.12+, Flask, one Gunicorn worker, vanilla JavaScript, minimal CSS,
standard-library `sqlite3`, numbered SQL migrations, and `Decimal` arithmetic.
Keep domain commands, repositories, replay, document filing, and HTTP adapters
separate. Routes must not contain embedded replay algorithms or filesystem
commit protocols.

The persistent data root contains the SQLite database, configuration, inbox,
documents, staging, reports, templates, and backups. Use SQLite's online backup
API; do not copy a live WAL database as a standalone file. Back up filed PDFs
and current inbox/signee documents by hash. Back up every retained finalized
report artifact and verify it against the hash in `report_runs`. Restore while
stopped, then run migrations, integrity checks, document and report
verification, and full replay.

Production is one container behind a TLS-terminating reverse proxy with one
bind-mounted data directory. Rebuilding the image must never remove persistent
state.

## 18. Validation and tests

Every mutation validates account direction, minor units, identities, ownership,
receipt/allocation totals, movement consistency, SAFE nonnegativity, return and
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
- signout deficit creation/FIFO resolution;
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
- document staging/recovery and SQLite backup/integrity; and
- 50-digit replay and `1E-24` conservation tolerance for generated valid
  histories.

## 19. Initial limitations

- One shared administrator identity.
- No automated sync of the ledger to external services. A future Google Apps
  Script or similar consumer may fetch the XLSX export endpoint on a timer,
  but it is a separate service and not part of this application.
- Predictable, non-secret signee URLs.
- One unresolved reconciliation package per signout.
- One SQLite writer at a time.
- Full replay at the expected modest data volume.
- Recoverable rather than absolute SQLite/PDF atomicity.
- Reverse currency exchange is out of scope.
- Direct database edits can bypass safeguards; stop and back up first.
