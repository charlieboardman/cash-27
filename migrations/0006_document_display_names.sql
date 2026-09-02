-- Preserve a human-recognizable upload name. Existing documents retain an
-- empty name and are displayed with a short form of their document ID.

ALTER TABLE documents ADD COLUMN display_name TEXT NOT NULL DEFAULT '';
