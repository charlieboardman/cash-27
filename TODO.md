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
- **Retry commands as one idea.** A command that gets no answer (the reply is
  lost, the connection drops) must be retried with its original submission ID,
  payload, and client date, so the server treats it as the same command and
  never makes a second transaction. Only a definite rejection (422) frees the
  payload for correction, and an acknowledgement frees it for the next
  command. The expense report does this in its own `drain`; every other page
  makes its own submission IDs. Check which of them keep one across a lost
  reply, then make this one small helper in `common.js` that every form uses,
  with lost-acknowledgement tests (reload, a newer edit made while waiting).

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
  `drain` in `expense-report.js` keeps an unanswered request and retries it
  with the same submission ID. **Undo** is a toast after deleting a row or
  receipt that delays the delete.
- Other pages make their own submission IDs and have no shared retry helper
  (see the retry-command item above).
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
those commands through one shared retry helper (the retry-command item above).
Anything reachable only through a page-specific route would need an API
equivalent first, so audit that before starting. The existing command layer
already works this way, so the main work is on the client and consistency
across pages.

Benefit: the same commands serve the browser, tests, scripts, and any future
client, and one retry helper replaces per-page versions. Cost: the client stays
the larger code. It only gets smaller if features also go (see below).

## Questions that apply to either option

- **Keep autosave and Undo?** This is independent of where rendering lives.
  Dropping them removes most of the coordination code in `expense-report.js`
  and `review.js` in either option. Under option 1 it is required.
- **What is the goal: less code, or other callers of the API?** If only less
  code, dropping features may deliver most of it with no rewrite. If scripts or
  other clients matter, option 2 pays for itself.

## Constraints for either option

- The server stays authoritative for money; the client never decides a balance.
- No migration or stored-data change is needed, and none should be made without
  explicit approval (`AGENTS.md`). A release must still run on the previous
  image, because `upgrade.sh` rolls back images without reversing migrations.
- Keep English and Spanish in step, and no explanatory filler text on pages.
- Measure with `tools/frontend_counts.py` against `master` at the time. The goal
  is a smaller, simpler total, not a different framework.
