# To do

- **Merge per-page purchase log scans.** The scanner sends one upload per
  page, so a multi-sheet log arrives as separate documents and later sheets
  are marked "duplicate". Merge consecutive `FY##-NNNN-log` scans into one
  document automatically. Until then, capture log sheets together in
  `/capture`.
- **Employee password in Settings and backups:** show it to administrators, and
  include it in full backups and the Drive copy like the sync token. It lives
  in the host env file (`CASH_USER_PASSWORD`), which backups cannot see, so
  move it to `config/user-password.json` in the data directory: seed that file
  from the env value on first start, have Settings and `setup.sh` write it.
- **`report_runs`:** empty in production, and nothing reads or writes it
  since final reports were removed. In a later release, and only with explicit
  approval, drop it with a migration.
- **"Split receipt"** on a direct expense splits the payment; the label comes
  from the row fields shared with expense reports.
- **Unfile a document.** Nothing can take a filed document off its record now
  that Review's 15-second filing undo is gone (`80f8a74`), so a wrong scan
  filed as a signed voucher or addendum stays filed. Add a deliberate Unfile
  action with a confirmation and no time limit: it detaches the document from
  its transactions, returns it to the inbox, and puts the record back in
  Review. Decide which documents it covers (signed vouchers and addenda,
  receipts too?) and until when (while the signout is open?). Propose the
  design before building it.

# Decision pending: frontend direction

Two ideas for the frontend pull opposite ways. Pick one before starting; do not
build both. Nothing here has been started, and no code depends on the choice.

## Where things stand

- Flask serves Jinja pages (23 templates) and a JSON API (about 80 routes in
  `cash/web/`). The browser code is plain JavaScript: 18 files, about 4,800
  lines, no framework and no build step. Pages fetch JSON and render it in the
  browser. All maintained frontend and web code is 11,680 physical lines
  (`python tools/frontend_counts.py`).
- Every financial change goes through `run_command` (`cash/commands.py`): one
  database transaction, a client-made submission ID that makes a retry
  idempotent, and a strict replay check of the whole ledger afterward. Tests
  already drive the app through this API. The server computes every balance.
- Two features account for most of the client's complexity. **Autosave** saves
  expense report rows and the note about 650 ms after typing stops;
  a failed save is shown and sent again with the next edit. **Undo** is a toast
  after deleting a row or receipt that delays the delete. (Review's filing undo
  was removed.)
- Ledger-posting forms share one retry helper, `cashCommand` in `common.js`.
- Phone use matters only for Capture and the recipient's expense report. Ledger,
  Review, and Settings are desktop pages. Every screen has English and Mexican
  Spanish, which must mirror each other (`AGENTS.md`).

## Option 1: server-rendered (htmx)

Flask renders pages and each update as Jinja fragments, using a pinned,
vendored htmx and no build step. The server is the only authority and the
browser holds no draft state to reconcile with it. That requires dropping
autosave (changes save when the person presses a button) and Undo (deleting
asks first), because both depend on client-held state.

Cost: the interactive parts that remain (splitting a receipt, ordered receipt
pages, file upload, dialogs, focus) stay in JavaScript, so the client does not
disappear. An earlier attempt, branch `frontend/flask-htmx-rewrite` (deleted
2026-09-30, last commit `557bce3`, based on `0f19aa9`), converted every page. It
kept autosave, Undo, and the JSON API and added 47 HTML endpoints beside them. It
saved 19 lines of 11,739, grew source bytes by 4.6% and files from 64 to 90, and
conflicted with later work. Its own review called the result not convincing.
Lesson: dropping features is what removes code; adding a second interface to
the same actions does not.

## Option 2: client-heavy, API-first

Make every financial transaction of every kind (receipts, reimbursements,
returns, addenda, exchanges, withdrawals, donations, corrections, counts,
approval and settlement) an idempotent API command, and build the client on
those commands through the shared retry helper (`cashCommand`).
Anything reachable only through a page-specific route would need an API
equivalent first, so audit that before starting. The existing command layer
already works this way, so the main work is on the client and consistency
across pages.

Benefit: the same commands serve the browser, tests, scripts, and any future
client. Cost: the client stays
the larger code. It only gets smaller if features also go (see below).

## Questions that apply to either option

- **Keep autosave and Undo?** This is independent of where rendering lives.
  Dropping them removes most of the coordination code in `expense-report.js`
  and `review.js` in either option. Under option 1 it is required.
- **What is the goal: less code, or other callers of the API?** If only less
  code, dropping features may deliver most of it with no rewrite. If scripts or
  other clients matter, option 2 pays for itself.

## How careful to be

Stakes are low and risk is small, so do not hold the client's hand. Draft edits
are working data only; nothing reaches the ledger until the administrator
approves, and approval already runs as one idempotent, replay-checked command.
So a draft save does not need retry safety, a self-conflict guard, or a "Saving"
state machine. If a save fails, say so and let the person try again; if a row
changed on another screen, say so and let them reload. Keep idempotent retry
for the commands that post to the ledger (approval, settlement, addenda, and the
other transaction types), where a duplicate would cost real money.

## Constraints for either option

- The server stays authoritative for money; the client never decides a balance.
- No migration or stored-data change is needed, and none should be made without
  explicit approval (`AGENTS.md`). A release must still run on the previous
  image, because `upgrade.sh` rolls back images without reversing migrations.
- Keep English and Spanish in step, and no explanatory filler text on pages.
- Measure with `tools/frontend_counts.py` against `master` at the time. The goal
  is a smaller, simpler total, not a different framework.

# Decision pending: user accounts and cash operations

On ice as of 2026-10-19. Nothing here has been started, and no code, schema, or
migration has changed. Choose the direction before building.

## Current model

- Two shared secrets: `CASH_ADMIN_PASSWORD` and `CASH_USER_PASSWORD`.
- Sessions carry booleans `admin` and `user`, not an identity.
- `@admin_required` guards 64 routes. Domain functions take an `admin` boolean
  that means "administrator vs. recipient", not "more privileged staff".
- There is no user table, no actor on submissions or transactions, and no
  employee-to-recipient restriction. The shared employee password can open any
  `/FY##-NNNN` page.
- Non-financial settings already live in atomic 0600 JSON files under
  `config/` (`signout-recipients.json`, `voucher-defaults.json`). Accounts
  belong there under `AGENTS.md`, not in the financial database.
- Every financial command records a `submission_id` in `submissions`. A
  nullable actor there could attribute creates, edits, approvals, and voids
  without changing every financial table. That needs explicit database-change
  authorization.

## Goal

Another person can do cash operations. The open question is whether they are
simply another admin, or a preparer who can create and sign a voucher but
cannot file the signed paper or close the signout.

Cash must post when the operation happens: preparation creates the sparse
active row and paperwork is attached later. A draft voucher that posts only
after an admin approval would fight the replay model, so that option is out.

## Option 1: multiple admin accounts

Add username/password accounts, all with the admin role. The permission
structure does not change: every admin can create, capture, file, review,
approve, settle, correct ledger history, and use Settings, users, imports, and
backups.

Work: account file and password hashing, login page, Settings "People",
session identity, actor attribution, backup/restore, upgrade/rollback, password
recovery, and tests. Roughly 3-5 days plus 2-3 days for attribution.

Trade-off: simplest and matches the real authority. There is no separation of
duties; if they can sign and file, they are an admin.

If admins should not all have system administration, split cash admin from
system admin (users, Settings, imports, backups) rather than splitting
preparer from approver. Cash admins would still do the full cash workflow.

## Option 2: preparer plus admin signature

A preparer can create and print a signout voucher, and sign a "Prepared by"
line. The admin signs the "Approved by" line and is the only one who may scan
or file the signed voucher and addenda. The admin must touch every voucher.

The cash row still posts at creation. The gate is at closeout:
`closeouts.settle` already refuses to settle a signout until `has_voucher` is
true and no addenda are awaiting a filed document. So admin-only filing blocks
settlement and closure without changing when cash is recorded.

The preparer cannot file signed paper, use Review, approve or settle, correct
or void ledger history, or use Settings, users, imports, or backups.

Work: role and route subset, a separate "Prepared by" line on the printed
voucher (keep the Approver line for the admin), actor attribution, a post-save
landing page, and tests. Roughly one week.

Start with signouts only. Direct expenses, donations, exchanges, and
withdrawals currently redirect after save into admin-only Review or Ledger, so
they need a confirmation/print landing before a preparer can create them.

The app cannot verify that the admin signed before the scan. If that sequence
matters, the preparer must not have capture or file access for signed paper;
the admin scans it.

## Optional later: named employees and recipient scoping

Give employees usernames and tie each account to one or more signout recipient
slugs. Restrict `/FY##-NNNN` and `/signouts/<slug>` to those recipients.
Today the shared employee password deliberately does not isolate one person's
signout from another. This is separate from the sub-admin decision and is the
main security improvement of full multi-user.

## Open questions

- Multiple admins, or preparer plus admin signature?
- If preparer: signouts only, or also direct expenses, donations, exchanges,
  withdrawals, or addenda?
- Can a preparer scan or upload signed paper, or only print it?
- Should a preparer see reports, and if so, all reports or none?
- Do we record actor attribution from day one? The likely change is a nullable
  `submissions.actor` migration, with explicit approval.
- Do employees stay on the shared password, or get named and scoped accounts?
- Should every admin manage users, Settings, imports, and backups, or is there
  a separate cash-admin role?
- What is the recovery path if the only admin forgets their password?

## Constraints and notes

- Keep `CASH_ADMIN_PASSWORD` and `CASH_USER_PASSWORD` working for at least one
  release. `upgrade.sh` rolls back images without reversing migrations, and the
  old image ignores a users file.
- Store accounts in `config/users.json`, not SQLite: versioned scrypt hashes,
  per-user salts, atomic 0600 writes, no plaintext, never return passwords.
- Session state should carry user id, role, and an auth version. Revalidate
  active users so deactivation and password resets take effect before the
  90-day cookie expires.
- Include the users file in full backups and the Drive copy. Restore must still
  work when an older backup has no users file, falling back to the environment
  passwords.
- Preserve password-only `/api/login` compatibility for existing tests and
  rollback, or update the tests in the same release.
- Touch user-facing pages in both English and Spanish (`AGENTS.md`).
- No database change without explicit authorization, including the actor field.

## Relevant files

- `cash/web/auth.py`, `cash/web/app.py`, `cash/user_access.py`
- `cash/signout_recipients.py` (config-file pattern)
- `cash/commands.py` (`run_command` and submissions)
- `cash/signouts.py`, `cash/closeouts.py` (settlement gate)
- `cash/web/signee.py`, `cash/web/report_rows.py`, `cash/web/settings.py`
- `cash/templates/print.html`, `cash/templates/addendum.html`
- `tests/test_auth_persistence.py`, `tests/test_user_password.py`,
  `tests/test_signee.py`

This overlaps the existing "Employee password in Settings and backups" item
above. Do that work in the same release if accounts move to `config/`.
