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
