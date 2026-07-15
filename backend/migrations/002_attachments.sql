-- Attachments: the real files behind a document (PFD drawing PDF, vendor
-- offer PDFs, issued datasheets...). Stored in the per-app DB (containers
-- have no persistent volume). The structured sections remain the source of
-- truth the DAG operates on; attachments are the human-readable originals.

CREATE TABLE attachments (
    id           SERIAL PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    data         BYTEA NOT NULL,
    uploaded_by  TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_attachments_doc ON attachments(document_id);
