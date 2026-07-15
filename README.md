# DioXengine

Engineering document workflows for Dioxycle, as a Dioxycle App
(apps.dioxycle.com/dioxengine): versioned DAG templates → projects →
documents with structured content, draft/submit/approve lifecycle, staleness
tracking, anchored resolvable comments, and an activity trail that
distinguishes human edits from assistant edits.

## Use it

Open **apps.dioxycle.com/dioxengine**. Seed the example template
("Electrolyzer Basic Engineering"), create a project, open a document: text
sections edit like prose, table sections edit like a spreadsheet, comments
anchor to a section or a table row. Submit for review; the reviewer approves
or rejects; approving an upstream document flags downstream ones as stale.

## Develop

```bash
./run.sh   # backend :8000 (SQLite), frontend :3001 (fake portal identity)
```

See `CLAUDE.md` for the portal contract, domain invariants, and packaging.
