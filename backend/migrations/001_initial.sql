-- DioXengine initial schema.
-- Template layer: versioned, frozen-on-publish DAGs of document types.
-- Instance layer: projects instantiate a published version; each node becomes
-- a document with versioned structured content, comments and an activity log.

CREATE TABLE users (
    id          SERIAL PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT '',
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workflow_templates (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE template_versions (
    id             SERIAL PRIMARY KEY,
    template_id    INTEGER NOT NULL REFERENCES workflow_templates(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'draft',      -- draft | published
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at   TIMESTAMPTZ,
    UNIQUE (template_id, version_number)
);

CREATE TABLE document_type_nodes (
    id                  SERIAL PRIMARY KEY,
    template_version_id INTEGER NOT NULL REFERENCES template_versions(id) ON DELETE CASCADE,
    node_key            TEXT NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    content_schema      JSONB NOT NULL DEFAULT '{}',
    author_role         TEXT NOT NULL DEFAULT '',
    reviewer_role       TEXT NOT NULL DEFAULT '',
    receiver_roles      JSONB NOT NULL DEFAULT '[]',
    UNIQUE (template_version_id, node_key)
);

CREATE TABLE template_edges (
    id                  SERIAL PRIMARY KEY,
    template_version_id INTEGER NOT NULL REFERENCES template_versions(id) ON DELETE CASCADE,
    from_node_id        INTEGER NOT NULL REFERENCES document_type_nodes(id) ON DELETE CASCADE,
    to_node_id          INTEGER NOT NULL REFERENCES document_type_nodes(id) ON DELETE CASCADE
);

CREATE TABLE template_owners (
    template_id INTEGER NOT NULL REFERENCES workflow_templates(id) ON DELETE CASCADE,
    user_email  TEXT NOT NULL,
    PRIMARY KEY (template_id, user_email)
);

CREATE TABLE template_users (
    template_id INTEGER NOT NULL REFERENCES workflow_templates(id) ON DELETE CASCADE,
    user_email  TEXT NOT NULL,
    PRIMARY KEY (template_id, user_email)
);

CREATE TABLE projects (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    template_version_id INTEGER NOT NULL REFERENCES template_versions(id),
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE project_members (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_email TEXT NOT NULL,
    PRIMARY KEY (project_id, user_email)
);

CREATE TABLE documents (
    id              SERIAL PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    node_id         INTEGER NOT NULL REFERENCES document_type_nodes(id),
    author_email    TEXT NOT NULL DEFAULT '',
    reviewer_email  TEXT NOT NULL DEFAULT '',
    receiver_emails JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE document_versions (
    id             SERIAL PRIMARY KEY,
    document_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'draft',      -- draft | submitted | approved | rejected | superseded
    content        JSONB NOT NULL DEFAULT '{}',
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    submitted_at   TIMESTAMPTZ,
    reviewed_by    TEXT,
    reviewed_at    TIMESTAMPTZ,
    review_comment TEXT NOT NULL DEFAULT '',
    based_on       JSONB NOT NULL DEFAULT '{}',
    UNIQUE (document_id, version_number)
);

CREATE TABLE comments (
    id           SERIAL PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_key  TEXT NOT NULL,
    row_index    INTEGER,
    parent_id    INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    author_email TEXT NOT NULL,
    author_kind  TEXT NOT NULL DEFAULT 'user',          -- user | assistant
    body         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',          -- open | resolved (root comments only)
    resolved_by  TEXT,
    resolved_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE activity_log (
    id          SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    actor_email TEXT NOT NULL DEFAULT '',
    actor_kind  TEXT NOT NULL DEFAULT 'user',           -- user | assistant
    action      TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_document_versions_doc ON document_versions(document_id);
CREATE INDEX idx_comments_doc ON comments(document_id);
CREATE INDEX idx_activity_doc ON activity_log(document_id);
CREATE INDEX idx_documents_project ON documents(project_id);
