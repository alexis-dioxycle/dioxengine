# Claude guidelines for dioxengine

## Project overview

Engineering document workflows for Dioxycle, as a standalone Render app (the
successor of the apps-portal Phase-1 prototype in `../dioXengine_repo`).
Workflow templates are versioned DAGs of document types; a project
instantiates a published version; every node becomes a document with
versioned, reviewable **structured content** (typed sections: text or table).
Staleness flags fire when an upstream document gets a newer approved revision.

Claude connects through the built-in MCP server and works inside the same
draft the humans see: reading upstream documents, filling sections, reading
and resolving comments. Humans edit in a WYSIWYG editor — **JSON never
appears in the UI**; Word/Excel are export formats only (Phase next).

Built to be ≈ identical in stack/feel to finance-dioxycle — copy patterns
from there when adding features.

## Tech stack

- **Backend** — FastAPI + SQLAlchemy + Postgres (prod) / SQLite (local), Python 3.11
- **Frontend** — React 19 + Vite + TypeScript, hand-rolled CSS (styles.css,
  IBM Plex Sans/Mono, engineering-register aesthetic; no Tailwind)
- **Auth** — Microsoft OAuth only in prod (AZURE_* env vars); dev escape hatch
  `POST /auth/dev-login` gated by ALLOW_DEV_LOGIN=1. JWT sessions.
- **MCP** — remote HTTP server at /mcp, OAuth 2.0 Authorization Code + PKCE;
  Claude's OAuth params tunnel through Microsoft's `state` (prefix `mcp.`),
  the shared /auth/microsoft/callback issues the MCP code. Same as finance.
- **Deploy** — single Docker container, Render Blueprint (`render.yaml`)

## Key files

### Backend
- `backend/main.py` — all routes (auth, templates, projects, documents,
  comments, activity, MCP OAuth, SPA)
- `backend/models.py` — SQLAlchemy models
- `backend/doc_service.py` — THE domain layer: access rules, DAG validation,
  staleness, draft lifecycle, section writes, comments. REST and MCP both go
  through it; never bypass it.
- `backend/mcp_server.py` — FastMCP + Bearer middleware + tools
- `backend/seed.py` — electrolyzer example template (22 nodes / 27 edges),
  idempotent; also `write_graph` used by the template PUT route
- `backend/auth.py` — JWT + Microsoft OAuth helpers

### Frontend
- `App.tsx` — hash router, auth gate, top nav
- `components/DocumentEditor.tsx` — the WYSIWYG editor: paper sheet, prose
  textareas, spreadsheet tables, anchored comments rail, autosave (900 ms
  debounce), 4 s polling that applies external (Claude/MCP) edits when the
  local state isn't dirty, with an orange flash + toast attribution
- `components/Project.tsx` — DAG (hand-rolled SVG, longest-path layering) +
  document list
- `components/Home.tsx`, `components/Login.tsx`, `utils/api.ts`, `types.ts`

## Domain invariants (don't regress)

- Published template versions are frozen; projects instantiate published only.
- Document lifecycle: draft → submitted → approved | rejected; approval
  supersedes the prior approved rev; one open draft at a time; no edits while
  submitted (409).
- On submit, `based_on` snapshots upstream approved revs — this powers
  staleness and provenance; keep it.
- Content is structured `{section_key: string | rows[]}` validated against the
  node's content_schema (doc_service.validate_rows). Never opaque blobs.
- Role slots enforce-on-assign: unassigned author/reviewer = anyone may act.
- Access: template owners/users, project members; hidden resources 404.
- Every content change goes through doc_service and lands in ActivityLog with
  actor_kind ('user' | 'assistant') — the UI attribution depends on it.

## How to run locally

```bash
./run.sh        # backend :5006, frontend :3001 (dev login enabled via .env)
```

Smoke check: sign in with any @dioxycle.com email, click “Seed …”, create a
project, open a document. MCP tools can be exercised without OAuth locally
(no MCP_CLIENT_ID → /mcp unauthenticated, tools run as the first user).

## Don't

- Don't add npm deps casually — the UI is deliberately dependency-light
  (react + react-dom only).
- Don't render JSON to users anywhere; the schema drives forms/tables.
- Don't let the assistant write outside the draft (submit/review stay
  human-initiated; submit_document via MCP acts as the authenticated user and
  must ask first — that's in the tool description).
- Don't edit applied DB structures casually; SQLite dev DB can be deleted,
  but prod needs migration thinking (create_all only adds new tables).
