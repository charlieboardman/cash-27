-- Existing documents stay ready. Only newly queued captures get a job.
CREATE TABLE document_processing (
    document_id TEXT PRIMARY KEY REFERENCES documents(document_id),
    status TEXT NOT NULL CHECK (status IN ('queued','running','complete','failed','cancelled')),
    source_rel_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE INDEX document_processing_pending ON document_processing(status, captured_at);
