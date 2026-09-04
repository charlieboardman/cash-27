-- Reserve monotonic document IDs independently from transaction IDs.
CREATE TABLE document_id_sequence (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    last_value INTEGER NOT NULL CHECK (last_value >= 0)
) STRICT;

INSERT INTO document_id_sequence (singleton_id, last_value)
SELECT 1, COALESCE(MAX(
    CASE
        WHEN length(document_id) >= 7
             AND substr(document_id, 1, 1) = 'D'
             AND substr(document_id, 2) NOT GLOB '*[^0-9]*'
        THEN CAST(substr(document_id, 2) AS INTEGER)
    END
), 0)
FROM documents;
