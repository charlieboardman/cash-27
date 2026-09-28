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
- **Old claims/packages code:** `signouts.py`, `closeouts.py`,
  `expense_drafts.py`, and `signee.py` still read the tables from before
  `expense_report_drafts`. If production has no rows in
  `signout_expense_claims` and `signout_reconciliation_packages`, remove it.
- **"Split receipt"** on a direct expense splits the payment; the label comes
  from the row fields shared with expense reports.
