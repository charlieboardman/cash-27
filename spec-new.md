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

Negative amounts are permitted only on expense and exchange rows. Signouts,
addenda, and other cash movements use nonnegative USD and MXN amounts, with
direction determined by their source and destination accounts. For example,
a return addendum moves a positive amount from the signout account to `SAFE`.
Both history editing and replay enforce this restriction.
Exchanges require strictly positive USD paid and strictly negative MXN received;
neither leg may be zero. Startup logs all active transaction IDs that violate
these sign rules, without changing recorded amounts. This diagnostic does not
repair invalid history or bypass replay validation.

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
state is derived from ledger replay, current document and receipt facts, and
its closeout confirmation. No cached open/closed status is maintained.

An active sparse row participates in balances even when its PDF, final expense
account, class, description, or notes are incomplete. A direct voucher records
its payment; review codes that same row in place. Signout receipts may be
recorded in their own currency until conversion is settled. `Complete` means
the paperwork and accounting review are complete; it does not mean the cash
only then began to exist.

A `superseded` row is a provisional allocation that earlier releases replaced
with final rows when a direct expense was completed. Such rows remain visible
as history but do not participate in replay. New completions code the row in
place and supersede nothing.

This system intentionally permits an administrator to correct a mistaken
historical entry in place. Editing a row retains its monotonic transaction ID,
increments its version, updates its timestamp, and validates the entire
projected replay before commit. Active, allocated expenses must retain a class
code when edited; a replacement class must be active. Pending unallocated
expenses can still be saved without a class until review. The system does not
need to retain an immutable copy of every prior field value. A row may instead
be voided when the recorded entry should not participate at all, for example
because it was a duplicate or the recorded movement did not happen. A direct
expense's lines are one payment, so voiding any of them voids all of them. A
voucher left with no active rows becomes `void`, with its receipt numbers and
any saved review progress.

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
- `document_id`: one normalized PDF, assigned from the global monotonic
  `D000001` sequence and never reused.
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

The receipt row stores the whole face value exactly once, when there is one:
only receipts paid in one currency with change in the other record it. When
such a receipt is split, each transaction allocation row stores its share of
the face value, and the shares must sum to the whole. For example, $20 paid for
a MXN 260 purchase with MXN 100 change, split MXN 160 to Programs and MXN 100 to
Supplies, has one MXN 260 receipt and allocation face shares of 160 and 100.
Replay evaluates the rows as one vendor settlement under their shared
`movement_id`; it must not treat each row as another copy of the whole receipt.

Direct-expense vouchers store authorized USD/MXN and never require a purchase
value. Review enters their spending as rows, like an expense report (§12): a
receipt total is asked for, and stored in the change currency, only when a
row's USD and MXN have opposite signs. Ordinary spending records no purchase
value.
Signout receipts always retain their values. Their `payment_mode` distinguishes
known cash tender from `receipt_currency` entries awaiting aggregate settlement.

### `signout_closeouts`

Stores an explicit confirmation, submission ID, date/time, any generated
settlement transaction, return and reimbursement addendum IDs, an admin
resolution note, and a snapshot/hash of the confirmed voucher, movement, receipt,
claim, package, addendum, and expense-report declaration facts.
Later changes invalidate the confirmation. Filing signed paperwork for the
existing movements does not invalidate the financial confirmation.

### `documents`

Stores document ID, a safe human-facing upload name, nullable type while
unassigned, nullable voucher, relative path to an immutable object, SHA-256, size,
parsed page count, state, and timestamps. Every normalized PDF is stored once
with its UTC capture timestamp, document ID, and a sanitized upload-name hint,
for example
`documents/20260904T172305Z--D000123--office-depot.pdf`. Independent files in
one upload add a batch position such as `--01` or `--02`. Workflow changes never
move or rename the object. The original upload name is retained separately for
display and is never used directly as a filesystem path.

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

### `expense_report_drafts`

One row per signout: the expense report being written and reviewed (§12) as a
single JSON document, a version, and an update time. The document carries a
`format` number; code reads every format ever written and writes only the
current one, so the draft's shape can change without a migration. Drafts are
working data and never participate in replay. Approval posts their rows through
the same receipt and transaction tables as any accepted receipt.

### `signout_expense_claims`

Written by the report format before `expense_report_drafts`. Kept as history
and read only to present a report started before that migration. Stores claim ID, voucher ID, nullable package ID, receipt document, date,
vendor, description, independent USD/MXN face values, tender/change facts, notes, claim/review state,
administrator correction note, content version, verified version, final
receipt/movement IDs after acceptance, and timestamps.

Claims do not participate in ledger replay.

### `signout_claim_allocations`

Stores administrator-entered draft allocations for a claim: line number,
explicit USD/MXN cash and face-value portions, expense account, class, project,
description, accounting notes, and version. The four component sums must
conserve the submitted receipt facts. Written by the earlier report format; kept as history.

### `signout_reconciliation_packages`

Written by the earlier report format; kept as history. Stores package ID, voucher, sequence, status, version, submission/review times,
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

The preparation page creates a voucher and an active sparse movement for the
amount paid:

```text
SAFE -> UNALLOCATED_EXPENSE
```

The payment participates immediately. Review codes that row in place: it keeps
its transaction ID, date, and same-day order, and gets its expense account,
class, description, and project. Splitting adds further rows to the same
movement. The amount is what the voucher paid; the lines must add up to it.
The only addition is change received in a currency the voucher did not pay,
entered as a negative amount with its receipt total. Spending less than the
voucher and returning the rest is a signout with a return addendum, not a
direct expense.

A signed authorization voucher is always required. A vendor-signed voucher may
also serve as a receipt: review's explicit, initially unchecked “Also file as a
receipt” checkbox files it under both the `voucher` and `support` roles. Any
number of separate receipts may be attached as well, whether or not that box is
checked. Each separate receipt gets a receipt ID (`FY27-0042-R1`, `-R2`, ...)
holding its document and no amounts, and is attached to every line of the
payment; no line's `receipt_id` points at it. Attaching receipts never changes
the accounting: the payment is always one movement. A receipt that arrives
after completion is added from the ledger detail and numbered the same way.

### Signout

The preparation page creates the voucher, creates its signout account, and
records:

```text
SAFE -> FY##-NNNN
```

USD and/or MXN move immediately. MXN carries SAFE basis. The signed custody
voucher is attached later in review.

### Expense from signout

Approved expense report rows create:

```text
FY##-NNNN -> expense account
```

One receipt may split among accounts and classes. An approved report may
produce a positive remaining balance or a legitimate deficit; either condition
keeps the voucher open but does not suppress truthful accepted expenses.

### Positive addendum

The signout's expense report page, with administrator tools, allocates the next addendum
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
USD given, MXN received, and notes. It creates an active transaction from `SAFE`
or from any signout account as the source, immediately reflecting the cash
movement. The transaction remains incomplete in Review until an administrator
checks it and files its required receipt. Its review workspace offers the same
document-queue selection, PDF preview, and upload controls as expenses. Existing
active exchanges without supporting documents also require review. The same
transaction may be created and reviewed document-first from its casa de cambio
receipt in Review. No sparse exchange row is prepared first.

### Bank withdrawal

A withdrawal is recorded from New transaction by entering the date, USD
received, and notes. It may also be created document-first from its bank
evidence in Review. Both routes create:

```text
BANK -> SAFE
```

No sparse withdrawal row is prepared first. Cash is active immediately, while
an unreceipted withdrawal remains incomplete in Review. This includes existing
active withdrawals without a supporting document. The ledger's Paperwork
section offers `Attach receipt`, leading to the existing transaction's Review
workspace. Selecting an inbox document or uploading and filing a receipt links
that evidence without creating another transaction or changing balances.

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
signout's expense report page (`/FY##-NNNN`), where the voucher, prior addenda,
receipt submissions, documents, balance, and ledger activity remain grouped.

The page allocates identifiers transactionally and commits the record. Voucher
transactions open the relevant print page; exchanges and withdrawals without
receipts open Review. Print forms are
prefilled from authoritative data. Editing a print form changes only the
rendered paper.

Every transaction has a stable `/transactions/<transaction_id>` entry point.
An unfinished direct expense, exchange, or withdrawal opens its Review editor; a signout or
addendum opens the signout's expense report page; a completed standalone
movement opens its ledger detail. Vouchers and addenda may be printed or
reprinted from their workspaces at any time.

Voucher and addendum templates are deployer-owned HTML. Rendering uses global
`$TOKEN` substitution in the browser and produces one US Letter page. The
application does not generate a print PDF. Names and values may print; signature
areas remain blank for wet signatures.

Settings provides a default approver name in `config/voucher-defaults.json`
under the mounted data directory, editable by administrators from the app.
The setting is read live and never stored in the financial database. New
direct-expense and signout vouchers, manual addenda, and settlement addenda
capture the selected
approver name when created. Their forms prefill the current default and allow
an override, including an empty name for handwriting. Names must be single-line
text of at most 60 characters. The default begins blank; existing documents
retain blank approvers. Per-document selections are saved atomically as JSON in
`config/voucher-approvers/<submission-id>.json` before their existing creation
command commits, and retrieved using the existing transaction submission ID.
A failed command can leave an unused file; retrying it replaces that file.
Committed retries keep the original selection. No schema or migration changes
are required. Full backups include these files; database-only exports do not.
Changing the default never rewrites old documents or their reprints. Approver names print on the same kind of filled
signature line as recipient/vendor names. All printed names and purpose/reason
values use 22px type; dates and signature space retain their existing layout.

Vouchers show one set of authorized USD/MXN amounts and one large Notes area.
They do not display or require receipt value. Direct-expense review codes the
amount paid and may add change received in another currency; an MXN purchase
paid with USD that returns pesos needs the MXN purchase value to derive the
returned pesos' basis. “Save” opens the recorded transaction workspace without printing;
“Save and print voucher” opens its printable voucher. Both create the same
immediately active transaction. Signature areas remain blank so the paper can
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
a signee upload begins in `signee_draft`. With recognition enabled, accepting an
admin upload also enqueues a durable `document_processing` job in the same
transaction. The HTTP response confirms storage without running a model. A
separate, single-job worker runs QR recognition and conservative receipt
orientation in short-lived subprocesses with a half-core CPU quota and bounded
RAM/swap. Queued/running jobs cannot be filed, used as claim evidence, or used
in document-first transaction creation. Deleting during processing cannot
resurrect the document. Restarts resume interrupted work; repeated interruption
or decoder failure leaves the original for manual review.

Every form the app prints with a code is exactly one page, and every page
carries the form's code: vouchers, addenda, no-receipt statements, and count
sheets are single pages, and a purchase log is one coded page per sheet. This
is a hard requirement (`cash/paper.py`). The text that prints is limited at
entry so a form stays one page even with every field full of the widest
letters: names (recipient or vendor on a voucher, approver) 60 characters,
voucher purpose 100, voucher notes 200 characters on at most 5 lines, addendum
reasons and row and count explanations 200, row vendor 60, and row purpose 120.
`tests/test_paper.py` prints every coded form with every field at its limit
and checks it is one page carrying its code. Anything longer belongs in
another document.

Recognition reads the code on every page. Because no page of an app form lacks
its code, a scan is split into documents wherever the code changes from one
page to the next: several papers scanned as one stack come apart, consecutive
sheets with the same code (a purchase log) stay together, and consecutive
pages without a code, such as a two-page vendor receipt, form one document of
their own. A page with more than one code counts as uncoded. The scan's own
document keeps the first piece; each other piece gets its own document ID and
is matched by its own code.

Capture and Review poll while visible, showing `Document processing…` until
the final name, QR suggestion, and preview become available. Background updates
preserve unsaved review fields and focus. QR matching remains a suggestion;
recognition does not approve transactions. Rotating creates a new immutable PDF
object and hash, preserving the original for safe concurrent backup snapshots.
Unchanged PDFs retain their original object. Later filing changes only database
state, type, and relationships. Documents uploaded in the same request retain
independent review lifecycles. Scanner upload retries retain their canonical
acceptance response and cannot create duplicate jobs.

The server enforces file, request, document, and page limits while streaming;
detects type from content; parses every PDF; and builds real multi-page PDFs.
Concatenating PDF byte strings is forbidden.

## 10. Ledger view

`/ledger` is the browsing view of the authoritative transaction history. It
lists replayed transactions ordered by real-world date and
same-day sequence, filterable by period or fiscal year, account, voucher,
type, and class. Filters apply as they change and are kept in the page
address; account and class are chosen from lists. A transaction drill-down
shows its documents and edit history and offers a validated historical edit or
void action. The edit form shows only the fields that kind of transaction can
change (the server enforces the same list); accounts implied by the workflow,
such as a signout's source and destination, stay fixed. Exports live on the
Reports page, which the ledger links to. The view follows the review shell's
keyboard conventions.

The transaction table labels expense `calc_usd` as `Calculated USD` and shows
it only for expense rows. A prepared direct expense is labeled `Incomplete`
until review is complete. A cash-given-out row is labeled `Incomplete` only
until its signed voucher is filed; the row is then complete even though the
employee may still hold cash. An addendum row follows its own paperwork status
rather than the parent voucher. Completed active rows have no status label.
Exceptional history is labeled `Replaced` for superseded provisional rows from
earlier releases and `Void` for void rows. Superseded and void rows remain
visible but do not participate in replay. A transaction's documents are listed
by reference (`FY27-0042`, `FY27-0042-R1`, `FY27-0042-A1`, `Txn-123-receipt`),
the names the bookkeeper package uses; a voucher also filed as a receipt is
listed once.

### Current balances

`/balances` is the present-state view of tracked cash, separated from the
ledger's history. It shows the replayed SAFE USD/MXN balance with its MXN
basis and every signout with its derived open/closed state, the
reasons an open signout remains open, its USD/MXN balance, and the recorded USD
value of any MXN held. The bank is
an external source rather than a tracked pool; a withdrawal injects cash
and no bank balance is shown. A
one-line total states tracked cash everywhere: SAFE plus open signouts.
The open-signout summary shows their combined recorded USD value: physical USD
plus the engine's MXN basis, summed with Decimal precision before display rounding.
An unfinished signout is labeled `Open` until receipt submission and cash
return are finished, including when its current balance is zero; closed
signouts remain listed at reduced emphasis.

The SAFE card includes a physical count widget. Entering the counted cash
shows the per-currency delta against the books; nothing is persisted until
`Save count`. Every count is saved, so each leaves a signed sheet on file. A
count that matches posts one 0/0 adjustment (notes `Safe count`), with an
optional explanation. Otherwise an explanation is required, and when both USD
and MXN have a nonzero delta the count creates two separate correction
movements even when both currencies are short or both are over. The movements
commit atomically. A count is known by its first transaction number, `Txn-123`.
After saving, `Print count sheet` prints it prefilled for signatures: counted,
recorded, and difference in USD and MXN, the explanation, "counted by" and
"witnessed by" lines, and the QR code `Txn-123`. The count waits in Review
(`Needs count sheet`) until the signed sheet is filed; it is filed on every
adjustment of the count, named `Txn-123`, and a scanned sheet's QR code
suggests its count. Counts saved by earlier releases (notes `Physical SAFE
count`) are not asked for a sheet. Editing the entered count invalidates the
comparison, as does any intervening ledger command. Corrections or historical edits associated with an
existing row are reached by drilling into that row in the ledger.

## 11. Admin review

`/review` is a keyboard-first worklist with two explicit modes. `Transactions`
contains active ledger movements that still need documents or accounting.
`Expense reports` lists submitted expense reports awaiting approval; each opens
on its own page (§12) and is never presented as a ledger row.

Open items include:

- sparse direct-expense, signout, and addendum rows in Transactions;
- submitted expense reports in Expense reports;
- opening, donation, correction, and recovery tasks.

Each worklist line shows problem badges describing the blocker (for example:
no voucher, unallocated, basis unresolved, report awaiting approval,
age). The list filters and sorts by badge, age, voucher, and amount.

While an item is open on a wide screen, the worklist remains visible with its
selected position marked.
Committing an item moves focus to the next open item; a `Commit & next`
action commits and opens it directly. A quick-jump key reaches the filter box.

On wide screens the review shell is a three-part spreadsheet workspace: a
document panel on the left, a filtered table of open
ledger-like rows in the center, and a right-side administrative workspace for
the selected row. With no row selected the table takes the available space and
the administrative workspace is absent. The evidence rail lists every
available inbox document newest first with thumbnail, readable name, and matching
status. A compact header holds the count and Upload action, with a drop target
and multi-page Capture link below. Selecting a document replaces the list with
a separate preview inside the same panel, keeping its width stable. Preview
controls include Back to documents, previous/next, and Open full size. Delete
lives in the preview's overflow menu. Selecting evidence never requires typing
or copying a document ID, and does not itself file it.

Inbox documents appear only in the document panel, never as ledger rows.
Every document slot attaches the same ways: `Choose from inbox` puts the
panel into a picking state where one click attaches a document, `Upload`
captures and attaches a file, and a file or inbox card can be dropped on it.
From a document's preview, `Attach to transaction…` searches existing
transactions awaiting paperwork by date, type, amount, person, or record,
listing an open record first; attaching opens that workspace with the document
staged, as a voucher or a receipt for a direct expense. Attaching never files
paper: the record's own save or file action does, including for addenda.
Capture links each inbox document to its preview. The open record and tab are
kept in the page address.
`Create new transaction…` is a separate expandable menu with `Create new bank
withdrawal` and `Create new currency exchange` actions. Those create a financial
record and file its evidence together, rather than attaching to an existing row.

Account and class selections start blank for new expense allocations and
receipt submissions, including added splits. `Save progress` persists a direct
expense's review fields, splits, and document selections independently of
completion. Either code may still be blank and documents are optional when
saving progress. The voucher remains pending, its original cash movement stays
active, and selected documents remain in the inbox. Reopening Review restores
the saved progress. `Save & review next` still validates all allocations and
required paperwork before posting final rows and marking the voucher complete.

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

- Direct expense: attach the signed voucher, optionally mark it “Also file as
  a receipt”, and attach any number of separate receipts. One row shows the
  payment as `USD amount`, `MXN amount`, `Class code`, `Account code`, and
  optional `Project`. The amounts are the voucher's; a negative amount in a
  currency it did not pay records change received. The payment may split into
  parts with their own codes. Project accepts new text and offers a keyboard-
  and pointer-selectable list of distinct projects already present in active
  ledger rows or saved receipt allocations.
- Signout: attach the signed custody voucher. Its receipts come through the
  expense report.
- Addendum: attach signed document to the exact existing addendum.
- Exchange or withdrawal: attach a receipt to the existing active transaction.
  Alternatively, create and receipt a new transaction through the separate
  document-first creation menu.

Receipt amounts use one signed USD and one signed MXN field per row, where a
negative amount is change received, in both Review and expense reports. The
receipt total appears only for mixed-sign rows, and a split's parts must add
up to the row. USD-only work reveals no rate or MXN-basis controls.
Exceptional rate inputs appear only where required. Administrators may add an
optional project; its field suggests projects already used in the ledger.

Contextual bodies offering attachment also accept direct upload. Uploads pass
through the same capture pipeline (content detection, PDF parsing, multi-page
assembly, hashing, limits) and attach to the item in one command. An uploaded
document the user abandons without committing falls back to inbox state;
nothing is lost.

An attachment may be replaced until the item is completed. After completion,
an administrator may add a receipt to a direct expense from its ledger detail;
it is numbered like the others and creates no cash correction.

Expense reports are reviewed and approved on their own page (§12), not in the
Review drawer.

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

## 12. Expense reports

`/FY##-NNNN` is a signout's expense report: one page for its recipient and the
administrator. The recipient reaches it with the shared employee password; the
initial release uses no per-person account, secret URL, QR token, or revocation
system. The administrator sees the same report with approval, correction,
paperwork, and settlement tools and without the English/Spanish toggle. The
link is case-insensitive: any casing redirects to the canonical uppercase URL.

The report is a list of rows. Each row is one of:

- **Receipt**: an optional receipt document, date, vendor, purpose, signed USD
  and MXN (positive paid, negative change received), and class and account
  codes. When USD and MXN have opposite signs the row also needs a **receipt
  total** in the change currency so replay can value the change (§3); no other
  row asks for it. **Split receipt** divides a receipt into two or more parts,
  each with its own USD/MXN amount, purpose, codes, and receipt-total share for
  mixed-sign rows. The parts must add up to the row. A row without a receipt
  needs an explanation and, before approval, a signed no-receipt statement.
- **Cash returned** or **Reimbursement requested**: a nonnegative USD/MXN
  declaration of cash left over or personal money spent, with an optional note.

Rows are added with one button and edited in place. Each row saves itself
shortly after typing stops; blank amounts mean zero. Each row carries its own
revision, so an edit made from a stale screen is refused rather than
overwriting a newer one, while edits to different rows never conflict. A lost
save response is retried with the same submission ID. Several rows can be
selected, or all at once, to set class and account codes together; the
recipient may code rows and the administrator may change the codes. Camera
images and PDF pages normalize into one hashed PDF per receipt, which can be
replaced or removed until the row is approved.

**Left according to this report** is the signout's ledger balance less every
unapproved receipt row, less declared returns, plus declared reimbursements,
until a confirmed settlement has moved that cash in the ledger. When something
is left over or overspent the page offers the matching declaration row. A
report-level note can explain anything else.

The report is stored in `expense_report_drafts` (§6) and posts nothing while
it is a draft. **Submit** checks every unapproved row (a date, an amount spent,
a purpose, a receipt or explanation, a receipt total where required, and splits
that add up) and locks the report for the recipient. The recipient may reopen
it; the administrator may request corrections with a summary and optional
per-row notes, which returns it to draft. The administrator may edit a
submitted report. **Approve** also requires valid codes on every line and a
receipt or signed no-receipt statement on every receipt row, then in
one command posts each unapproved receipt row as a receipt with one expense
transaction per line (§7) and files its document, and accepts the
declarations. Adding a row after approval returns the report to draft.

Each receipt row gets its receipt number (`FY27-0042-R3`) the first time it is
saved, so paper can name it before approval, and approval posts it under that
number. Numbers are never reused, so a deleted row leaves a gap. Approved
receipt rows keep their amounts, codes, and dates; later corrections use the
ledger. A row approved without a receipt can still take one, by the recipient
or the administrator, whenever the signout is not void, including after
settlement. The administrator may also choose a captured inbox document. A
row approved with a no-receipt statement keeps it on record when the real
receipt turns up: the receipt takes the next receipt number, with no amounts
of its own, and supports the same ledger lines.
Filing it never changes the report's status and never invalidates a confirmed
settlement: the confirmation compares everything except receipt documents. A
filed receipt is never added to.

A row without a receipt is approved only with a signed **no-receipt
statement** in the receipt's place. The statement is printed from the report,
prefilled from the row (voucher, recipient, receipt number, date, vendor,
amount, purpose, and the explanation), in Spanish and English, one page per
row. It has two signature blocks, each with its own sentence: the recipient's
("I spent this cash as described above. No receipt was available.") and the
administrator's ("I requested documentation for this expense and none was
provided."), so an administrator who signs alone vouches only for what they
know. Either or both may sign. Its QR code is the receipt number with an `x`
(`FY27-0042-R3x`), which is also its name everywhere, so a statement is never
mistaken for a vendor receipt. A scanned statement is matched to its row and
offered there to the administrator. When a recipient cannot be reached, the
administrator adds an "Unaccounted cash" row for the balance and signs its
statement.

A signout may have one **purchase log**: blank sheets printed from the report
(`Print purchase log`), each with the code `FY27-0042-log`, on which the
recipient writes purchases that had no receipt (date, vendor, what, MXN, USD,
and the vendor's initials) with a total per sheet. All the sheets are filed
together as one PDF on one receipt row, `Attach purchase log`, whose amount is
the log's total; the document is named `FY27-0042-log`. A report has at most
one log row. Sheets that arrive before approval mean scanning the whole log
again and replacing the row's document. After approval the log's total is
final; a sheet found later is documentation only and takes the next receipt
number with no amounts, like a receipt found after a statement. A scanned log
sheet is matched to its signout and offered to the administrator on the
report: as the log row's document, as a new log row, or after approval as
that missing sheet.

A receipt is always staged whole before it is attached: the row's `Add
receipt` or `Replace` opens the same ordered page picker as Capture, and every
photo or file is combined, in order, into one PDF. Pages are never appended to
a receipt already attached.

A report started before `expense_report_drafts` existed is presented from its
unaccepted earlier claims and declaration, which are never modified; the first
edit stores the draft, after which the earlier workflow cannot post that
signout's receipts.

Settlement previews require an approved report with no unapproved rows, filed
signed paperwork, and no other open receipt work. They prefill the declared
return and reimbursement. The administrator must explain a report note or,
when the report declares a return or reimbursement, any different amount. An
explanation alone does not write a balancing adjustment: unexplained currency
balances still block confirmation. Positive remaining USD paired with negative
MXN can be proposed as an effective USD-to-MXN exchange. This is an inferred
settlement rate for review, not independent proof of what a vendor charged.

Confirmation atomically records any reimbursement from SAFE as a positive
addendum, then a required currency exchange, then any return to SAFE as a return
addendum. Both addenda are supplied for printing when both directions occur.
Only an addendum that gives the recipient additional cash asks for the
recipient's signature; reimbursement and return addenda are signed by the
approver. The admin confirms the actual cash handoff; a declaration alone never
changes the safe. Previews bind amounts, resolution notes, report facts, and the
ledger revision so changed records require another preview.

For $100 paid with MXN 100 change, the receipt row is USD +100 / MXN -100 with
a MXN 1,500 receipt total, establishing 16 MXN/USD. MXN 100 actual cash left
generates the return addendum. For a $20 advance with MXN 400 of receipt rows
and MXN 100 left, settlement proposes $20 funding MXN 500 at 25: $16 expense and
$4 returned peso basis.

A signout becomes closed automatically when every row is approved, required
signed documents are filed, cash and peso basis are zero with no open basis
claims, and its settlement confirmation matches the current facts, including
the report. Filing the signed original voucher alone does not close it. Later
material corrections invalidate that confirmation and reopen the derived state.
Existing closeouts retain their hashes when no draft has been stored.

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
Ledger and Reports share two export types, independently selectable from
CSV/XLSX format. Ledger defaults to **Cash ledger**; Reports defaults to
**Expense report**. Both omit superseded and void rows. Unallocated expenses
remain visible in previews and block finalization, as do unresolved expense
basis claims, for either type.

The expense report contains only active expense allocations, including negative
expense adjustments. Its columns are `date`, `Account`, `mxn`, `ER`, `usd`,
`calc_usd`, `voucher_id`, `receipt_id`, `addendum_id`, `class_code`, `project`,
`recipient`, `vendor`, `description`, `notes`. `Account` is the expense code.

The cash ledger contains every active transaction, including openings, bank
withdrawals, donations, signouts, return/positive addenda, exchanges, adjustments,
and expenses. Its columns are `date`, `Activity`, `From`, `To`, `mxn`, `ER`,
`usd`, `calc_usd`, `Safe Balance USD`, `Safe Balance MXN`, then the same voucher,
receipt, addendum, code, project, recipient, vendor, description, and notes fields.
Activity uses readable labels; From and To contain account IDs. The signed USD
and MXN amounts retain the ledger's from/to convention. Calculated USD is only
filled for expense rows; cash transfers must not look like expense valuation.

Expense ER is MXN per USD derived from the final engine valuation of the peso
portion: `mxn / (calc_usd - usd)`, using unrounded values. Exchange ER is pesos
received divided by USD paid. ER is blank for USD-only expenses or unresolved/
undefined valuation. ER has three decimals and monetary values have two,
rounded half up only for presentation. Stored records and engine precision are
unchanged. Text fields retain formula-prefix escaping; Excel amounts and ER are
numeric, while account/class codes remain text.

Safe balances are physical USD and MXN in SAFE immediately after the movement,
from the full authoritative replay, excluding cash held in signout accounts.
Allocations of the same settlement share its ending safe balance. Earlier
movements count even when outside fiscal-year or transaction-ID filters; later
funding does not change historical cash balances. Both formats order rows by
date, same-day sequence, movement ID, allocation line, and transaction ID.
Internal ordering fields are not exported.

`/api/export/transactions.csv` and `/api/export/transactions.xlsx` accept
`export_type=expenses` (API default for existing links) or `cash_ledger`.
Downloads are named `expenses` or `cash-ledger` with the selected extension.
Ledger workbooks have a sheet for all years and one per fiscal year.

The Reports generate endpoint accepts `export_type: "expenses"` (default) or
`"cash_ledger"` and `format: "csv"` (default) or `"xlsx"` for previews. A preview
covers only the selected fiscal year, but replays all active history. Its
filename includes the type; XLSX has one fiscal-year sheet. Finalization retains
one numbered CSV snapshot and stores `report_runs.export_type`. Report numbers
remain sequential across both types within each fiscal year. Existing report
metadata defaults to expenses; existing files are never rewritten.

Saved reports display their type and offer `?format=csv` or `?format=xlsx`.
XLSX downloads verify the stored CSV hash and convert its saved contents,
preserving historical headers and values rather than replaying today's ledger.
Downloading another format creates no new revision or retained file.

The read-only bookkeeper view at `/reports/bookkeeper` and portable package at
`/api/export/bookkeeper.zip` accept `export_type`, optional fiscal ending year
`fy`, and inclusive `start`/`end` ISO dates. Both require admin authentication,
read one database snapshot and reuse the export row selection and valuation.
They do not write database data, migrations, retained report files or originals.

The human-facing columns are Date, Payee/Vendor, Category, Class, Description,
MXN, ER, USD, Calc USD and Documents. Cash ledger additionally shows Activity,
From, To and the two safe balances. Calc USD is subtly highlighted. Categories
and classes show existing codes and names. Rows from one movement/receipt stay
together when sorting and filtering; matching a category/class keeps the whole
purchase visible. Document links show reference IDs, with no separate Reference
column. Existing transaction links and receipt/addendum document references
resolve evidence irrespective of upload names. Signout purchases also include
parent voucher and nonvoid addendum evidence. Unattached references and missing
PDFs are explicit; generated unsigned print pages are never substituted for scans.

The ZIP contains the selected period as XLSX, CSV, self-contained HTML, PDFs and
a JSON manifest associating rows with documents. Browser search/category/class
filters do not narrow downloads. XLSX contains a linked Documents index; the
HTML and workbook PDF links are relative to the extracted directory. PDF content
is deduplicated by document ID, copied with bounded buffers, and verified against
its stored SHA-256. Missing documents are flagged and excluded; a corrupt PDF
aborts the package. Original filenames and finalized report snapshots remain
unchanged. These packages are working snapshots, not QBO import files.

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
- expense report view, row create/update/delete, bulk codes, note, submit,
  reopen, corrections, and atomic approval;
- ledger browsing with filters and drill-down, built on transaction query;
- current-balance state for SAFE and every signout with derived
  open/closed reasons, plus same-day ordering;
- physical SAFE count comparison without persistence and revision-checked,
  atomic correction commit;
- transient report preview and versioned finalized-report generation/retrieval;
- on-demand XLSX expense export for the bookkeeper;
- voucher/addendum print pages; and
- non-sensitive health state.

There is no unrestricted arbitrary transaction-row patch endpoint. Historical
edits use typed commands that validate the fields applicable to the transaction
kind, check `expected_version`, and replay the complete projected history.

## 15. Documents, atomicity, and recovery

All normalized PDFs live in one immutable object store, regardless of whether
their database state is inbox, signee draft, submitted, filed, or discarded.
Object names combine UTC capture time, monotonic document ID, optional upload
batch position, and a sanitized non-authoritative upload-name hint. SQLite
stores the unique safe data-root-relative object path, display/download name,
content hash, size, page count, workflow state, document type, and business
relationships. The database enforces one document record per object path.
Symlinked targets, traversal, and silent overwrite are forbidden.

Capture durably reserves the next document ID before processing bytes; a failed
upload may therefore leave a harmless numbering gap, but IDs are never reused.
It constructs and validates the PDF under a unique temporary name, flushes it,
and publishes the final object name atomically without overwrite. Only then
does it insert the document row. A handled database failure removes the
unpublished object; a process crash can leave only an unreferenced object,
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

Settings offers an administrator-only, CSRF-protected user-password change with
matching new-password and confirmation fields. It replaces only the literal
`CASH_USER_PASSWORD=` line in the existing installed environment file, atomically,
with mode 0600. No password table, separate settings file or database write is
introduced. Compose mounts the existing configuration directory writable in the
app container; the helper derives its location from the existing Compose env
file, so upgrading does not require setup again. Every employee login rereads
that line. A configured file that is missing, unreadable, empty or ambiguous
fails closed rather than accepting a stale environment password. Passwords are
nonempty single-line strings with no surrounding whitespace (maximum 1024
characters), and never returned by Settings. Other environment values are
preserved; admin authentication continues using its startup configuration.
Development without a file path keeps its supplied password but cannot save a
new one. Existing signed sessions are not invalidated by a password change.

Both login endpoints accept an optional `remember` boolean. Remembered sessions
use an expiring signed cookie with a 90-day inactivity lifetime, refreshed on
requests. Otherwise the cookie is a temporary browser-session cookie. The admin
login form defaults this option on; the employee form leaves it off. Cookies
remain HttpOnly, SameSite=Lax, and Secure in production. Logout clears the cookie;
normal upgrades retain the configured signing key and therefore remembered
sessions. Password changes alone do not revoke existing sessions.

The administrator app is installable using a public web manifest, PNG home-screen
icons, an Apple touch icon, and standalone display metadata. Its start URL is `/`,
using the existing authenticated landing-page behavior. The icon is plain Cash
text, and navigation has no decorative diamond logo. The app remains online-only:
no service worker, offline transaction storage, or synchronization queue is added.

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
objects and originals retained by processing jobs, and deduplicate archive payloads by hash. Back up every retained
finalized report artifact and verify it against the hash in `report_runs`.
Restore while stopped, then run migrations, integrity checks, document and
report verification, and full replay.

The authenticated Settings page also provides logical database export and
import for remote maintenance. Export is taken from a consistent online SQLite
snapshot. Import is two-step: an uploaded SQL export is first rebuilt away from
the live database, migrated to the current schema, checked for the exact
application schema, SQLite integrity, foreign keys, document/report hashes, and
full ledger replay. Activation requires the administrator password and an
explicit confirmation phrase. Import activation also takes the document-worker
lock; if a model is running, the administrator must wait for it to finish.
With one application worker and other work
stopped, activation retains a pre-import database in backups and atomically
replaces the live database. A database-only export does not replace the full
backup because it does not contain document or report files.

Production recovery currently uses managed DigitalOcean Droplet backups,
enabled by the operator in DigitalOcean, plus local portable application
archives. The application and setup do not claim to enable or verify provider
backups. The authoritative data root and local archives must be on the backed-up
Droplet disk; separately attached Volumes are not covered by Droplet backups.
Document a restore rehearsal using a temporary Droplet without replacing live
production or changing its DNS.

A local archive service creates a portable full archive at startup and every
24 hours after success, retrying after 15 minutes on failure. A verified local
archive is also mandatory immediately before an application upgrade; failure
stops the upgrade before source or application changes. The archive contains
the consistent SQLite snapshot, retained document objects deduplicated by hash,
hash-verified finalized reports, and a manifest of application revision, schema
version, payload paths, sizes, and SHA-256 hashes. Archive construction,
verification, staged restoration, migrations, integrity checks, document/report
verification, and full replay belong to the Cash 27 core.

The local service uses the application image, has no network or external
credentials, and exposes no API. Scheduled and one-shot archive creation share
a filesystem lock. Publish an archive only after staged restore verification;
prune older archives only after a successful publication. Retention is bounded
and separate: keep seven scheduled, three pre-upgrade, and three manual archives
by default under backups/local/. Interval, retry delay, and retention counts
are configurable positive integers. Do not rotate database-import safety
copies, pre-restore archives, or unrelated files. Keep temporary archive and
verification data on the persistent disk rather than a small container tmpfs.

No Google transport, OAuth setup, rclone dependency, Google health dashboard,
disable flag, or offsite-backup warning banner is bundled. Local activity goes
to the service log. Its container health check assesses recent verified local
scheduled archives, not offsite protection. Local archives share the host's
fate until captured by a provider backup or copied elsewhere.

Complete local-archive recovery must not depend on Flask being healthy. A host
helper selects a local archive, pauses scheduled work, verifies and exercises
the selected archive before stopping Flask, then stops writers and creates a
current-state safety archive. It restores into staging, installs immutable files
before replacing the database, restarts the stack, and checks health. A restore
or health failure reinstates the safety archive. If the damaged current database
cannot produce a verified safety archive, recovery preserves its stopped raw
database, WAL, documents, and reports in a forensic archive before proceeding.
Database-only browser export/import remains a separate maintenance feature.

The future Google Apps Script backup design is documented in FUTURE-PLUGINS.md,
not implemented here. It sends a fresh complete SQLite snapshot, compares a
snapshot manifest against the destination's verified inventory, uploads only
missing PDFs and retained report artifacts in resumable batches, and commits
the backup only when all required objects exist. Manual initial file uploads
are supported by the proposed indexing protocol. It needs neither tarballs nor
binary diffs. Human-readable Drive folders and a Google Sheet remain separate
rebuildable views. Neither future feature blocks deployment.

Production is one rootless Podman Compose project on a public, systemd-based
Linux VM. A root-launched bootstrap creates a locked runtime user, retains the
Git checkout under root ownership, installs host packages and network settings,
enables the runtime user's lingering systemd manager, and drops privileges for
configuration, image builds, Podman, and every long-running service.
Root-invoked operational commands delegate to that recorded runtime identity;
upgrades retain Git and checkout ownership under root. Long-running services
are the application, Caddy, and local archives, using a bind-mounted persistent
data directory.

The interactive setup collects the public domain, administrator and shared
employee passwords, and paths to administrator-provided name,code account and
class CSV files. Account and class codes are text identifiers: numeric codes,
including a single digit and leading zeros, are valid. Dotted codes such as
`7.5` are also valid; `4.10` and `4.1` remain distinct text identifiers. Both CSV
import and Settings use the same validation: 1-32 letters, digits, periods, or
underscores, beginning with a letter or digit, with letters uppercased and
spaces made underscores.
Setup validates every CSV row in the built image before starting services,
without mounting or changing the live database.
It retains the session secret, keeps private CSV copies outside
the checkout, mounts those read-only, installs host packages, configures HTTPS
and Compose reboot startup, builds the image, and verifies application health.
Startup atomically adds, renames, or reactivates matching codes and never
deletes codes omitted from a CSV. The upgrade script creates a local pre-upgrade
archive, pulls only fast-forward source changes, builds the commit-tagged image,
atomically switches its private Compose reference, recreates services, and
checks health. It preserves persistent state, existing secrets, installed CSVs,
and Caddy certificate state. Code rollback does not reverse migrations.
Tailscale is not part of production.

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
- exchanges/withdrawals created with or without receipts; later attachment
  preserves balances and transaction counts;
- independent documents from one upload;
- online archive creation against a live WAL database, with no network or
  credentials in the local archive service;
- full archive verification, safe extraction, staged restore, and failure
  without replacement of live data;
- local archive retention, failed verification without publication or pruning,
  retry after failed scheduled creation, and unsafe restore-path rejection;
- restore orchestration order, current-state safety archive, and health check;
- Compose setup, commit-tagged upgrade, and automatic image rollback;
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
- monotonic document IDs, readable UTC object names, immutable document
  publication/recovery, full backup archive integrity,
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
