# Database changes

Never make database changes without the user's explicit authorization. This
includes schema changes, new or modified migrations, and direct changes to
persisted database data. A general request to implement a feature is not
authorization to change the database: explain the specific database change and
obtain explicit authorization first.

Store application configuration in files on disk, not in the financial database,
unless the user explicitly authorizes otherwise.

Every migration must leave the previous release able to run against the
migrated database, because `upgrade.sh` rolls back to the prior image without
reversing migrations. Add tables, indexes, and nullable or defaulted columns;
remove or rename things only in a later release, after nothing uses them.

# UI copy

Prefer labels and fields alone when the workflow is clear. Where instructions
are needed, use brief, task-focused steps; do not add filler copy. The recipient
expense report includes a short completion guide. The English and Spanish text
must mirror each other, with full feature parity: every string, field, and
element in one exists in the other.

# Testing

Run tests with `.venv/bin/pytest`. There are three suites:

- **Unit/API (default)**: `.venv/bin/pytest -m "not browser and not integration"`.
  Run this for every application change.
- **Browser**: `.venv/bin/pytest -m browser`. Also run this when changing
  templates, JavaScript, CSS, browser workflows, printing, or client-side
  document handling.
- **Integration**: `.venv/bin/pytest -m integration`. Also run this when
  changing Drive backup, Podman, deployment, upgrade, restore, powerwash, local
  backup, or other system-integration code.

Run the full suite, `.venv/bin/pytest`, before pushing a finished change.
Report exactly which suite(s) you ran; never imply the full suite passed when
only a subset was run.

When adding tests, use the fastest suite that actually exercises the behavior:
API by default, browser only for real browser behavior, and integration only
for external/system integration. Suite membership is assigned centrally in
`tests/conftest.py`; the markers are declared in `pytest.ini`.
