# Claude guidelines for dioxengine

## Project overview

Engineering document workflows for Dioxycle, packaged as a **Dioxycle App**
for the apps.dioxycle.com portal (slug `dioxengine`). Workflow templates are
versioned DAGs of document types; a project instantiates a published version;
every node becomes a document with versioned, reviewable **structured
content** (typed sections: text or table). Staleness flags fire when an
upstream document gets a newer approved revision. Humans edit in a WYSIWYG
editor — **JSON never appears in the UI**; Word/Excel are export formats only
(not built yet).

This app is a **sanctioned complexity exception** to the dioxycle-apps size
guidance (recorded in the dioxycle-apps SKILL.md): it may be large, but every
other portal rule applies unchanged.

History: first built as a standalone Render app with its own auth and a
remote MCP server (see git history around commit b7287cd), then converted to
the portal shape. The MCP server code lives in that history — it comes back
when the portal grows a way to expose an app endpoint to claude.ai
(everything on the portal sits behind SSO today, so an external MCP client
cannot reach it). Until then, "Claude edits" happen through the same REST API
with `actor_kind='assistant'` plumbing already in place end to end.

## Portal contract (the important bits)

- Identity: the portal injects HMAC-signed `X-Dioxycle-User` /
  `X-Dioxycle-Signature` headers; `backend/dioxycle_auth.py` (copied verbatim
  from the skill) verifies them. **No auth code of our own.** Every non-
  `/healthz` endpoint depends on `track_user` (which wraps `current_user` and
  upserts a user directory row).
- Schema: `backend/migrations/*.sql`, applied by the portal in order. Never
  edit an applied migration; add `002_*.sql` etc. `create_all()` runs ONLY in
  local dev (no DATABASE_URL → SQLite) — keep it behind `IS_LOCAL_DEV`.
- Frontend: no `package.json` in the upload (deps come from the portal's
  dependency panel baked into `dioxycle-app-base:node20`). The root
  `package.json` here is a LOCAL DEV mirror of the panel — excluded from the
  zip. Relative URLs everywhere (`fetch('api/...')`, `base: './'`) because
  the portal proxies under `/_apps/dioxengine/`.
- 30 s request budget, port 8000, no subprocess/eval, writes only to `/data`,
  egress only per manifest (`allowed_egress: []` today).

## Key files

- `backend/main.py` — all routes under `/api/*` + `/healthz` + static SPA
- `backend/doc_service.py` — THE domain layer: access rules, DAG validation,
  staleness, draft lifecycle, section writes vs content_schema, comments,
  activity log. Every content mutation goes through it; `actor_kind`
  ('user' | 'assistant') is threaded through for attribution.
- `backend/models.py`, `backend/migrations/001_initial.sql` — keep in sync:
  models map the tables, the SQL owns the schema.
- `backend/seed.py` — electrolyzer example template (22 nodes / 27 edges);
  also `write_graph` used by the template PUT route.
- `frontend/src/components/DocumentEditor.jsx` — WYSIWYG editor: paper sheet,
  prose textareas, spreadsheet tables, anchored resolvable comments rail,
  autosave (900 ms debounce), 4 s polling that applies external edits when
  local state isn't dirty (orange flash + toast attribution).
- `frontend/src/components/Project.jsx` — SVG DAG (longest-path layering) +
  document list ordered by node_id (template order).
- `frontend/vite.config.js` — dev server signs the portal identity headers
  (DIOXYCLE_AUTH_SECRET, default test-secret) so local dev ≈ portal.

## Domain invariants (don't regress)

- Published template versions are frozen; projects instantiate published only.
- Document lifecycle: draft → submitted → approved | rejected; approval
  supersedes the prior approved rev; one open draft at a time; 409 while
  submitted.
- On submit, `based_on` snapshots upstream approved revs — powers staleness.
- Content is structured `{section_key: string | rows[]}` validated against
  the node's content_schema (`doc_service.validate_rows`).
- Role slots enforce-on-assign: unassigned author/reviewer = anyone may act.
- Access: template owners/users, project members; hidden resources 404.

## How to run locally

```bash
./run.sh        # backend :8000 (SQLite), vite :3001 (signs identity headers)
```

E2E check against real Postgres (validates the migration like the portal
will): `docker run postgres:16`, apply `001_initial.sql`, boot with
DATABASE_URL, run the lifecycle. See git log for the exact commands used.

## The iteration loop (how changes ship)

Three layers, three cadences:

1. **Content/structure** (documents, templates, projects, comments, skills,
   tools) — no deploy ever. Through the web UI or the 31 MCP tools (Claude
   can build whole workflows: create_template → update_template_graph →
   publish_template → create_project; write per-document skills/tools with
   set_document_skill / set_document_tools — allowed on published versions).
2. **App code** (this repo) — the `.zip` loop:
   ```bash
   npm run build          # sanity-check the frontend compiles
   rm -rf frontend/dist backend/data
   zip -qr ../dioxengine.zip . \
     -x '*.DS_Store' 'node_modules/*' 'backend/venv/*' 'backend/data/*' \
        'frontend/dist/*' 'package.json' 'package-lock.json' 'run.sh' \
        '.git/*' '.gitignore' 'CLAUDE.md' 'backend/.env' '*/__pycache__/*'
   ```
   Alexis uploads `~/Documents/DioXengine/dioxengine.zip` on the app's page
   at apps.dioxycle.com → AI review → deploy. New migrations
   (`backend/migrations/00N_*.sql`, append-only) are applied automatically
   before the new container starts. Also `git push origin main` +
   `git push team main:dioxengine-render` (mirror for Bastien).
3. **Portal changes** (`~/Documents/Dioxycle/dioxycle-apps`: review rules,
   /_mcp route, dependency panels, docker-compose env passthrough) — commit,
   push, then Alexis runs `git pull` + `deploy.sh` on the apps server. Env
   vars need BOTH the server `.env` (`~/deployments/dioxycle-apps/
   environments/.env`) and an entry in `docker-compose.yaml`.

MCP prod endpoint: `https://apps.dioxycle.com/_mcp/dioxengine`, Bearer =
the app's MCP_API_KEY portal secret, acting user via `X-Dioxengine-User`.
Configured in Alexis's `~/.claude.json` for this project dir. The pip panel
(base/requirements.txt) now includes mcp/openpyxl/python-docx.

## Domain additions (2026-07-15, post-call with Bastien)

- **Skills per document** (`document_type_nodes.skill`, migration 003): each
  document type carries its production recipe — which upstream documents to
  pull from, what to take from each, granularity, tools. Decision from the
  call: the skill lives ON the document node, not on the edges. Editable in
  the template editor AND on published versions (owner only — guidance, not
  structure): REST `PUT /api/template-nodes/{nid}/skill`, MCP
  `set_document_skill(template_version_id, node_key, skill)`. Exposed in
  get_document (web + MCP) and as a read-only collapsible panel in the
  document editor.
- **Uploaded deliverables** (`attachments.kind`, migration 003): an
  attachment can be flagged 'deliverable' = this file IS the document (the
  P&ID-from-AutoCAD case; Claude never generates it, engineers upload it).
  ★ toggle in the editor, auto-previewed; `upload_attachment(kind=…)`,
  `download_attachment` (≤4 MB, base64) so Claude can read it and fill
  downstream documents; `get_upstream_content` lists upstream attachments.
- **PFD visualization**: `frontend/src/components/FlowDiagram.jsx` — any
  table with from/to columns (streams, cables, I/O) renders as a process
  diagram: equipment typing from tags/service words (pump, exchanger,
  vessel, reactor…) with ISO-flavored glyphs, parallel streams fanned out,
  recycle loops routed below, wheel zoom + drag pan + fit, hover highlights
  the connected streams, row tooltip, SVG export. Literal colors on purpose
  (exported SVG must be self-contained).
- **Deleting workflows**: `DELETE /api/templates/{tid}` + MCP
  `delete_template` — owner only, 409 while any project instantiates any
  version. Project delete button on the project page (creator only). ORM
  cascade added for attachments (Postgres already cascades via FK).

## Document tools (`backend/app_tools.py`, migration 005)

Deterministic calcs live in Dioxycle Apps ("tools", 2026-07-06 meeting
decision); DioXengine attaches them to document types: `tools` JSON on the
node — [{name, description, url, method GET|POST, params}]. The assistant
sees them in get_document and calls them via `use_document_tool` — the HTTP
request is made by this backend (egress applies, no credentials on the
caller side), hosts restricted to the TOOL_ALLOWED_HOSTS allowlist (default
apps.dioxycle.com; +localhost in local dev). Editable like skills: template
editor (Tools block per node, published+owner saves via
`PUT /api/template-nodes/{nid}/tools`), MCP `set_document_tools`. Reference
the tool by name in the document's skill. Reference skills for W1/W2 now
ship in seed.py (REFERENCE_SKILLS) and are SET IN PROD on tv3/tv4
(2026-07-15, via MCP). The dummy pressure-drop app to point a first real
tool at doesn't exist yet — portal-side work.

## SharePoint two-way sync (`backend/sharepoint.py`, migration 004)

Documents LIVE on SharePoint for the rest of the company: folder per project
(`DioXengine/<project>/`), one file per document — rendered .xlsx (any doc
with tables), rendered .docx (text-only), or the uploaded deliverable as-is.
Renderers live in `backend/renderers.py` (shared with /export routes) with
schema-driven inverse parsers (parse_xlsx/parse_docx) that make pull-back
possible. Sync is conservative: remote-only change → pull into a draft
(attributed to the SharePoint modifier); local-only → push; both → conflict
reported, nothing clobbered; approved/submitted heads are pushed but never
pulled (locked); file deleted on SharePoint → re-pushed. `sharepoint_links`
table stores drive_item_id + etag + pushed_stamp per document. Entry points:
`POST /api/projects/{pid}/sharepoint/sync`, `GET /api/sharepoint/status`,
MCP `sharepoint_sync_project` / `sharepoint_status`, ⇅ button on the project
page (report modal). Auth: Entra client credentials, Sites.Selected
application permission granted per site — secrets MS_TENANT_ID /
MS_CLIENT_ID / MS_CLIENT_SECRET / SHAREPOINT_SITE
(`dioxycle.sharepoint.com:/sites/1tonperdaypilot` — display name "Path to
FOAK"); local dev reads `backend/.env` (gitignored). Manifest egress:
login.microsoftonline.com + graph.microsoft.com. STATE: verified live
end-to-end (2026-07-15): token, site lookup, file write + read-back +
delete on the real site all OK. Remaining for prod: set the 4 secrets on
the app's portal page + allow the egress at review, upload the zip.

## Status (2026-07-15) & next steps

- LIVE: app + MCP chain end-to-end; prod project "BOS 5000F2PBOS" (id 2)
  carries the real 5000F2PBOS data (26 equipment rows, datasheet register,
  HEXONIC + Alfa Laval offers, factual comparison, 2 open Claude comments).
- PENDING upload: zip with everything above (26 MCP tools, migration 003) —
  after the portal redeploy that fixes the review false positive.
- THEN (one MCP pass): publish W1 template v2 (streams section), recreate
  the project with data + streams, attach reference PDFs, and write the
  first real skills with Bastien (he wants to iterate on them himself).
- PARALLEL: SharePoint Entra registration (Sites.Selected application-only,
  Graph Explorer grant; no redirect URI) — Bastien is granting the app on
  the Engineering site ("Pass to Folk"); tenant ID + secret received.
- NEXT: per-type Dioxycle Word templates (port April's narrative_to_docx),
  workflow-2 generation in anger, a dummy pressure-drop app on the portal to
  exercise skills-calling-apps, demo to Raphaël.
