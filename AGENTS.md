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
