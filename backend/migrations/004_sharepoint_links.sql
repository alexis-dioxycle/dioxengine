-- SharePoint two-way sync: one row per document = the file that represents
-- it on the SharePoint site (rendered .docx/.xlsx, or the uploaded
-- deliverable pushed as-is). etag + pushed_stamp let the sync tell which
-- side changed since the last push: remote-only change -> pull back into the
-- draft; local-only -> push; both -> conflict (reported, nothing clobbered).

CREATE TABLE sharepoint_links (
    document_id    INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,              -- 'docx' | 'xlsx' | 'attachment'
    attachment_id  INTEGER,                    -- when kind='attachment'
    file_name      TEXT NOT NULL,
    folder_path    TEXT NOT NULL DEFAULT '',
    drive_item_id  TEXT NOT NULL,
    etag           TEXT NOT NULL DEFAULT '',
    web_url        TEXT NOT NULL DEFAULT '',
    pushed_stamp   TEXT NOT NULL DEFAULT '',   -- local head fingerprint at push time
    last_pushed_at TIMESTAMPTZ,
    last_pulled_at TIMESTAMPTZ
);
