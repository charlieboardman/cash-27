# Setting up the Cash 27 Drive backup

This guide sets up the nightly backup of Cash 27 into Google Drive, from
scratch. It takes about ten minutes. When it is done, the Google account you
use will have a `Cash 27` folder that fills itself every night with:

- the database, as `.db` and `.sql` files;
- every document (receipts, vouchers, forms, addenda, count sheets);
- a spreadsheet per fiscal year with the Expense report and Cash ledger, each
  row linking to its documents.

The script runs inside Google, under that account. It only reads from Cash 27
and never changes anything there. Nothing needs to be installed, and the
script does not need to be "deployed": a nightly timer runs it.

## Before you start

- Cash 27 is upgraded to a release with **Settings → Drive backup**.
- You can sign in to Cash 27 as the administrator.
- You know which Google account should own the backup. Everything is stored
  in that account's Drive and counts against its storage.
- You have this repository's `gas/Code.gs` and `gas/appsscript.json` at hand,
  either in a checkout or on GitHub (open the file and use the **Copy raw
  file** button).

## 1. Get the access token from Cash 27

1. Sign in to Cash 27 and open **Settings**.
2. In the **Drive backup** card, click **Generate token**.
3. Leave this tab open. You will click **Copy** in step 4.

The token is what lets the script read from Cash 27. It stays visible here, so
you can come back for it any time.

## 2. Create the script project

1. Go to [script.google.com](https://script.google.com).
2. Check the profile picture in the top-right corner: it must be the account
   that will own the backup. If you are signed in to several Google accounts,
   Apps Script can mix them up; use a browser profile or private window signed
   in to that account only.
3. Click **New project**. An editor opens with a file called `Code.gs`
   containing `function myFunction() {}`.
4. Click **Untitled project** at the top and rename it, for example
   **Cash 27 Drive backup**.

## 3. Paste in the two files

**The script:**

1. In the editor, click inside `Code.gs`, select everything (Ctrl+A, or ⌘A on
   a Mac) and delete it.
2. Paste the whole contents of `gas/Code.gs`.
3. Save with Ctrl+S (⌘S) or the disk icon.

**The manifest,** which sets the time zone and the permissions the script asks
for:

1. In the left sidebar, click **Project Settings** (the gear icon).
2. Tick **Show "appsscript.json" manifest file in editor**.
3. Go back to the **Editor** (the `< >` icon). A file called `appsscript.json`
   is now listed.
4. Open it, select everything, delete it, and paste the whole contents of
   `gas/appsscript.json`.
5. Save.

## 4. Tell the script where Cash 27 is

1. Open **Project Settings** again and scroll to **Script Properties**.
2. Click **Add script property** and add these two:

   | Property     | Value                                                   |
   | ------------ | ------------------------------------------------------- |
   | `CASH_URL`   | Your Cash 27 address, such as `https://cash.example.org` |
   | `CASH_TOKEN` | The token: in Cash 27, click **Copy** and paste it here   |

   Use the address you open Cash 27 with, including `https://`, with nothing
   after the domain.
3. Click **Save script properties**.

## 5. Run `setup` and grant permissions

1. Go back to the **Editor**.
2. In the toolbar, pick **setup** in the function dropdown (next to **Debug**)
   and click **Run**.
3. Google asks for authorization:
   1. Click **Review permissions** and choose the backup account.
   2. Google warns **"Google hasn't verified this app"**. That is expected:
      this is your own script, not a published app. Click **Advanced**, then
      **Go to Cash 27 Drive backup (unsafe)**.
   3. Review the list and click **Allow**. The script asks to:
      - see and manage your Drive files, to write the backup folder;
      - manage your spreadsheets, to write the Sync log and yearly Sheets;
      - connect to an external service, to read from Cash 27;
      - run while you are away, for the nightly timer;
      - send email as you, to tell you about problems;
      - see your email address, to know where to send them.
4. The **Execution log** at the bottom ends with a line like
   `Ready. Drive folder: https://drive.google.com/...`.

`setup` created the `Cash 27` folder in your Drive, a `Sync log` spreadsheet
inside it, and a timer that runs the backup every night between 2 and 3 a.m.
Pacific time. It also checked that the address and token work. Running it
again is safe: it never makes a second folder or a second timer.

## 6. Make the first copy

1. Pick **nightlySync** in the function dropdown and click **Run**.
2. Wait for **Execution completed** in the log. The first run copies every
   document, so it takes longer than later ones.
3. Check the result:
   - In Drive, open **Cash 27 → Sync log**. The **Runs** tab has a row with
     **OK** in the Result column.
   - **Cash 27 → Documents** holds your PDFs.
   - **Cash 27 → FY27** (the current fiscal year) holds **FY27 Ledger** and a
     **Database** folder with a `.db` and a `.sql` file.
   - In Cash 27, **Settings → Drive backup** says **Last completed** with
     today's time.

If the first run stops with an error about exceeding the maximum execution
time, run **nightlySync** again: it carries on where it stopped.

That's it. The backup now runs by itself every night.

## What happens each night

1. The database is downloaded as `.db` and `.sql`. A new pair is kept only if
   something changed since the last one.
2. The list of every file a full restore needs is fetched from Cash 27.
3. Each fiscal year's spreadsheet is rewritten if its data changed, including
   past years that received late entries.
4. Any document or setting Drive does not have yet is copied. Every file's
   SHA-256 is checked before it is stored.
5. Database copies are pruned: all of them for 30 days, then the first of each
   month forever.
6. Cash 27 is told the run finished.

## Keeping an eye on it

- **Sync log → Runs**: one row per night, with what was stored, how many files
  were copied, how many spreadsheets were updated, and any problems.
- **Email**: any problem is emailed to the backup account straight away.
- **Cash 27**: if no run finishes for 36 hours, administrators see a warning
  at the top of every page.
- **In the script project**: **Executions** (left sidebar) lists every run
  with its log; **Triggers** (the alarm-clock icon) shows the nightly timer.

## Things to leave alone

- **Don't delete or edit files in `Documents/`.** Older database copies may
  refer to any of them. A deleted document is copied again the next night,
  but only while Cash 27 still has it.
- **Don't delete the `Sync log`.** It records which database copies exist.
  If it goes, a new one is started, but older copies are no longer pruned or
  checked.
- **Don't edit the yearly spreadsheets.** They are rewritten each night and
  your changes would be lost. Use **File → Make a copy**, or copy a tab, to
  work with the data.
- Renaming or moving the `Cash 27` folder is fine; the script finds it by its
  ID, not its name.

## Changing things later

- **A new token**: generate or paste it in Cash 27, then update `CASH_TOKEN`
  in **Project Settings → Script Properties**. The script fails with "answered
  401" until the two match.
- **A new Cash 27 address**: update `CASH_URL` the same way.
- **The run time**: change `RUN_HOUR` near the top of `Code.gs` (0–23, local
  time), save, and run **setup** again to replace the timer. The time zone is
  `timeZone` in `appsscript.json`.
- **A newer version of the script**: paste the new `gas/Code.gs` (and
  `appsscript.json`, if it changed) over the old one and save. Run **setup**
  again if the release notes say so. Google may ask for permission again if
  the script needs new ones.
- **Pause the backup**: in **Triggers**, delete the `nightlySync` trigger. Run
  **setup** to bring it back. Cash 27 starts warning after 36 hours.
- **Stop for good**: delete the trigger, then revoke the token in Cash 27.
  The Drive folder stays until you delete it.

## When something goes wrong

| Message | What to do |
| --- | --- |
| `Set the CASH_URL and CASH_TOKEN script properties first` | Add both in step 4 and check their names. |
| `... answered 401: A valid sync token is required` | `CASH_TOKEN` does not match Cash 27. Copy it again from Settings. |
| `DNS error`, `Address unavailable`, or `answered 404` | `CASH_URL` is wrong or Cash 27 is down. Open it in a browser. |
| `Exceeded maximum execution time` | The first run had a lot to copy. Run **nightlySync** again. |
| `Database snapshot ... is missing from Drive` | A database copy was deleted or trashed. Restore it from Drive's Trash if you can. This is reported once. |
| `The Drive copy of ... did not match` | A document in Drive was changed. The old copy is kept as `DIFFERS ...` and the right one copied again. |
| `Authorization is required` | The script changed and needs permission again. Run **setup** from the editor. |

## Restoring from the backup

If the Cash 27 server is lost, download the `Cash 27` folder from Drive and
follow **Restore from the Drive backup** in the main
[README](../README.md#restore-from-the-drive-backup).


## Readable PDF names and existing backups

Deploy the server release with document naming metadata before updating
`Code.gs`. Keep the existing Drive folder and script properties. On the next
run, GAS adopts existing PDFs, preserving their Drive file IDs, and creates:

- **Documents:** current PDFs named by their filing reference, such as
  `FY27-0001-R2.pdf`. Unfiled documents use their document ID.
- **Originals:** distinct processing inputs retained by Cash 27, under their
  immutable filenames. These files were already part of the backup.
- **Object links:** shortcuts named with the immutable object filenames,
  pointing to the actual PDFs. The shortcuts contain no extra PDF copies.

Each later run follows these shortcuts and updates the PDFs' readable names.
Ledger spreadsheet cells link directly to the target PDFs. Renames preserve
those links; replaced missing targets cause the spreadsheet links to refresh.

Do not revert to the older `Code.gs` after adoption: it locates PDFs by their
immutable filenames. The new script refuses document reconciliation when the
server does not supply the required naming metadata. Conflicts and ambiguous
matches appear in the existing sync log and prevent reporting a successful run.
Interrupted adoption can resume on the next run; do not delete the backup to
restart it.

For a manual restore, download the **complete Cash 27 folder**, including the
actual Documents and Originals PDFs, database snapshots, and Config. Supply
all ZIP parts to `restore-drive.sh`. Restore uses the selected database snapshot
and PDF hashes, independent of their readable names or folder placement. It
requires neither shortcut metadata nor an additional restore manifest.

After rollout, verify that an adopted PDF keeps its Drive ID, opens and downloads
under its readable filename, and remains linked from the ledger. Download the
complete folder and test restoration to an isolated installation before relying
on the new layout for recovery.
