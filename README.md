# DioXengine

Engineering document workflows for Dioxycle: versioned DAG templates →
projects → documents with structured content, review lifecycle, staleness
tracking, anchored comments — co-authored by engineers (WYSIWYG editor) and
Claude (built-in MCP server).

## Quick start

```bash
./run.sh
# → http://localhost:3001 (backend on :5006)
```

Sign in with any `@dioxycle.com` email (dev login), press **Seed
“Electrolyzer Basic Engineering”**, create a project, open a document.

## Claude connector

The backend exposes a remote MCP server at `/mcp` (OAuth via Microsoft, same
pattern as finance-dioxycle). Once deployed, add it in claude.ai →
Settings → Connectors with the app URL. Claude can then list projects, read
documents and their upstream sources, fill sections, and read/resolve
comments — everything lands in the same draft the web editor shows, live.

## Deploy (Render)

Blueprint in `render.yaml` (web service + Postgres). Set `APP_URL`,
`AZURE_*` (Entra app with redirect URI `$APP_URL/auth/microsoft/callback`),
and `MCP_CLIENT_ID/SECRET`. See `.env.example` for the full list.
