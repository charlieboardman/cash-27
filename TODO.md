# To do

- **Merge per-page purchase log scans.** The scanner sends one upload per
  page, so a multi-sheet log arrives as separate documents and later sheets
  are marked "duplicate". Merge consecutive `FY##-NNNN-log` scans into one
  document automatically. Until then, capture log sheets together in
  `/capture`.
- **Reports:** "Save final report" keeps the older export columns (`date`,
  `mxn`, ...), while the bookkeeper view and package use `Date`,
  `Payee/Vendor`, `Category`, ... Make the retained report match.
- **Old claims/packages code:** `signouts.py`, `closeouts.py`,
  `expense_drafts.py`, and `signee.py` still read the tables from before
  `expense_report_drafts`. If production has no rows in
  `signout_expense_claims` and `signout_reconciliation_packages`, remove it.
- **"Split receipt"** on a direct expense splits the payment; the label comes
  from the row fields shared with expense reports.
