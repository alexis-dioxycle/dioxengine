"""MCP server for DioXengine — Claude Code transport.

Reached through the portal's `/_mcp/dioxengine` pass-through route (see the
dioxycle-apps SKILL.md, "MCP endpoints" — a sanctioned exception to the
portal-identity contract for this app). Auth is a static Bearer token: the
`MCP_API_KEY` portal secret. The acting identity comes from the
`X-Dioxengine-User` header (an @dioxycle.com email) — access control (project
membership, role slots) applies to that email exactly as in the web UI, and
every write is logged with actor_kind='assistant'.

Claude Code setup:
  claude mcp add --transport http dioxengine \
    https://apps.dioxycle.com/_mcp/dioxengine \
    --header "Authorization: Bearer <MCP_API_KEY>" \
    --header "X-Dioxengine-User: you@dioxycle.com"

Local dev (no DATABASE_URL): the token check is skipped and the acting user
defaults to dev@dioxycle.com.

Tools: list_projects, get_project, get_document, get_upstream_content,
update_text_section, update_table_section, append_table_rows, list_comments,
add_comment, reply_to_comment, resolve_comment, submit_document.
"""
import json
import logging
import os
import secrets as pysecrets
from dataclasses import dataclass
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from database import IS_LOCAL_DEV, SessionLocal
from models import Project, TemplateEdge, User
import doc_service as svc

logger = logging.getLogger(__name__)

MCP_API_KEY = os.getenv("MCP_API_KEY", "")

mcp = FastMCP(
    "DioXengine",
    stateless_http=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@dataclass
class ActingUser:
    email: str
    name: str = ""


_current_email: str = ""


class MCPAuthMiddleware:
    """Static Bearer gate + acting-user context.

    The portal's /_mcp/<slug> route forwards Authorization and
    X-Dioxengine-User untouched; everything else about the request is opaque
    to the portal. No token configured -> endpoint disabled (except local dev).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        global _current_email

        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return

        if scope["type"] == "http":
            if scope.get("path", "") == "":
                scope = dict(scope)
                scope["path"] = "/"
            headers = {k.decode().lower(): v.decode()
                       for k, v in scope.get("headers", [])}

            if IS_LOCAL_DEV and not MCP_API_KEY:
                _current_email = headers.get("x-dioxengine-user", "dev@dioxycle.com")
            else:
                if not MCP_API_KEY:
                    await JSONResponse({"error": "MCP is disabled: the MCP_API_KEY secret is not set"},
                                       status_code=503)(scope, receive, send)
                    return
                auth = headers.get("authorization", "")
                if not (auth.startswith("Bearer ")
                        and pysecrets.compare_digest(auth[7:], MCP_API_KEY)):
                    await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                    return
                email = headers.get("x-dioxengine-user", "").strip().lower()
                if not email or "@" not in email:
                    await JSONResponse(
                        {"error": "Set the X-Dioxengine-User header to your @dioxycle.com email"},
                        status_code=400)(scope, receive, send)
                    return
                _current_email = email

        await self.app(scope, receive, send)


# ============ tool helpers ============

def _require_user(db) -> ActingUser | None:
    if not _current_email:
        return None
    # Keep the directory warm so the acting email shows up in pickers.
    u = db.query(User).filter(User.email == _current_email).first()
    if not u:
        db.add(User(email=_current_email))
        db.commit()
    return ActingUser(email=_current_email)


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


def _run(fn):
    """Session + uniform error envelope around a tool body."""
    db = SessionLocal()
    try:
        me = _require_user(db)
        if not me:
            return _err("Not authenticated")
        return json.dumps(fn(db, me), default=str)
    except Exception as e:
        detail = getattr(e, "detail", None)
        return _err(str(detail) if detail else f"{type(e).__name__}: {e}")
    finally:
        db.close()


def _doc(db, me, document_id: int):
    return svc.get_document_or_404(db, document_id, me)


# ============ TOOLS ============

@mcp.tool()
def list_projects() -> str:
    """List the engineering projects the acting user is a member of, with the
    workflow template each one instantiates."""
    def body(db, me):
        out = []
        for p in db.query(Project).order_by(Project.id.desc()).all():
            members = svc.project_members(db, p.id)
            if not svc._email_in(me.email, members):
                continue
            out.append({
                "project_id": p.id, "name": p.name, "description": p.description,
                "template": p.template_version.template.name,
                "template_version": p.template_version.version_number,
                "members": members,
            })
        return {"projects": out}
    return _run(body)


@mcp.tool()
def get_project(project_id: int) -> str:
    """Overview of one project: every document in the workflow with its status
    (draft/submitted/approved), staleness flags (upstream changed since last
    approval), assigned author/reviewer, and open comment counts. Use this to
    orient before reading or editing documents.

    Args:
        project_id: The project to inspect (from list_projects).
    """
    def body(db, me):
        p = svc.require_project_member(db, project_id, me)
        docs = [svc.document_summary(db, d)
                for d in sorted(p.documents, key=lambda d: d.node_id)]
        edges = db.query(TemplateEdge).filter_by(
            template_version_id=p.template_version_id).all()
        return {
            "project_id": p.id, "name": p.name, "description": p.description,
            "documents": docs,
            "edges": [{"from_node_id": e.from_node_id, "to_node_id": e.to_node_id} for e in edges],
        }
    return _run(body)


@mcp.tool()
def get_document(document_id: int) -> str:
    """Read one document: its section schema (keys, titles, text vs table with
    typed columns), the current working content (open draft if any, else the
    latest revision), version history, upstream documents, and open comments.
    ALWAYS call this before editing — section keys and table columns must match
    the schema exactly.

    Args:
        document_id: The document to read (from get_project).
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        head = svc.latest_version(doc)
        open_comments = [svc.comment_public(c) for c in doc.comments
                         if c.status == "open" and not c.parent_id]
        return {
            **svc.document_summary(db, doc),
            "project_id": doc.project_id,
            "content_schema": doc.node.content_schema,
            "content": (head.content if head else {}) or {},
            "content_status": head.status if head else None,
            "versions": [svc.version_public(v) for v in reversed(doc.versions)],
            "upstream": [{
                "document_id": u.id, "name": u.node.name,
                "approved_version": (svc.approved_version(u).version_number
                                     if svc.approved_version(u) else None),
            } for u in svc.upstream_docs(db, doc)],
            "open_comments": open_comments,
        }
    return _run(body)


@mcp.tool()
def get_upstream_content(document_id: int) -> str:
    """Latest approved content of every upstream (parent) document of this
    document in the workflow DAG. This is the source material to translate
    from when generating or updating this document's content.

    Args:
        document_id: The downstream document you are working on.
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        out = []
        for u in svc.upstream_docs(db, doc):
            appr = svc.approved_version(u)
            out.append({
                "document_id": u.id, "name": u.node.name, "node_key": u.node.node_key,
                "approved_version": appr.version_number if appr else None,
                "content": (appr.content if appr else None),
                "content_schema": u.node.content_schema,
            })
        return {"upstream": out}
    return _run(body)


@mcp.tool()
def update_text_section(document_id: int, section_key: str, content: str) -> str:
    """Write a text section of the document's draft (creates the draft from the
    latest revision if none is open). The user sees the change live in the
    editor, attributed to the assistant. Refused while the document is under
    review.

    Args:
        document_id: The document to edit.
        section_key: The text section's key, exactly as in content_schema.
        content: The full new text for that section (replaces the old text).
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        draft = svc.set_section(db, doc, me, section_key, content, actor_kind="assistant")
        return {"ok": True, "version_number": draft.version_number, "section": section_key}
    return _run(body)


@mcp.tool()
def update_table_section(document_id: int, section_key: str, rows_json: str) -> str:
    """Replace ALL rows of a table section in the document's draft. Row keys
    must match the section's columns in content_schema. Prefer
    append_table_rows when only adding.

    Args:
        document_id: The document to edit.
        section_key: The table section's key.
        rows_json: JSON array of row objects, e.g. '[{"tag": "P-101", "service": "Anolyte feed"}]'.
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        rows = json.loads(rows_json)
        draft = svc.set_section(db, doc, me, section_key, rows, actor_kind="assistant")
        return {"ok": True, "version_number": draft.version_number,
                "section": section_key, "row_count": len(rows)}
    return _run(body)


@mcp.tool()
def append_table_rows(document_id: int, section_key: str, rows_json: str) -> str:
    """Append rows to a table section of the document's draft, keeping existing
    rows untouched.

    Args:
        document_id: The document to edit.
        section_key: The table section's key.
        rows_json: JSON array of row objects to add.
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        head = svc.latest_version(doc)
        current = list((head.content or {}).get(section_key, []) if head else [])
        new_rows = json.loads(rows_json)
        draft = svc.set_section(db, doc, me, section_key, current + new_rows,
                                actor_kind="assistant")
        return {"ok": True, "version_number": draft.version_number,
                "section": section_key, "row_count": len(current) + len(new_rows)}
    return _run(body)


@mcp.tool()
def list_comments(document_id: int, include_resolved: bool = False) -> str:
    """Comments on a document, threaded, anchored to a section (and possibly a
    table row). Open comments are review feedback to address: fix the content
    with the update tools, reply, then resolve_comment.

    Args:
        document_id: The document whose comments to list.
        include_resolved: Also return resolved threads (default false).
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        roots = [c for c in doc.comments if not c.parent_id
                 and (include_resolved or c.status == "open")]
        return {"comments": [{
            **svc.comment_public(c),
            "replies": [svc.comment_public(r) for r in doc.comments if r.parent_id == c.id],
        } for c in roots]}
    return _run(body)


@mcp.tool()
def add_comment(document_id: int, section_key: str, body_text: str, row_index: int = -1) -> str:
    """Leave a comment on a section (e.g. flag missing information or an
    assumption the user should confirm). Use row_index to anchor to a specific
    table row.

    Args:
        document_id: The document to comment on.
        section_key: The section the comment is about.
        body_text: The comment text.
        row_index: 0-based table row the comment targets, or -1 for the whole section.
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        c = svc.add_comment(db, doc, me, section_key, body_text,
                            row_index=None if row_index < 0 else row_index,
                            actor_kind="assistant")
        return {"ok": True, "comment_id": c.id}
    return _run(body)


@mcp.tool()
def reply_to_comment(comment_id: int, document_id: int, body_text: str) -> str:
    """Reply in a comment thread (e.g. explain how you addressed the feedback).

    Args:
        comment_id: The thread's root comment id (from list_comments).
        document_id: The document the comment belongs to.
        body_text: The reply text.
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        c = svc.add_comment(db, doc, me, "", body_text, parent_id=comment_id,
                            actor_kind="assistant")
        return {"ok": True, "comment_id": c.id}
    return _run(body)


@mcp.tool()
def resolve_comment(comment_id: int, document_id: int, reply: str = "") -> str:
    """Mark a comment thread resolved, optionally posting a final reply first.
    Only resolve after actually addressing the feedback in the document.

    Args:
        comment_id: The thread's root comment id.
        document_id: The document the comment belongs to.
        reply: Optional closing reply posted before resolving.
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        if reply.strip():
            svc.add_comment(db, doc, me, "", reply, parent_id=comment_id,
                            actor_kind="assistant")
        svc.resolve_comment(db, doc, me, comment_id, actor_kind="assistant")
        return {"ok": True, "comment_id": comment_id, "status": "resolved"}
    return _run(body)


@mcp.tool()
def submit_document(document_id: int) -> str:
    """Submit the document's draft for review, on behalf of the acting user
    (allowed only if they are the assigned author or the slot is unassigned).
    Ask the user before calling this — submitting freezes the draft until the
    reviewer decides.

    Args:
        document_id: The document to submit.
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        v = svc.submit(db, doc, me, actor_kind="assistant")
        return {"ok": True, "version_number": v.version_number, "status": v.status}
    return _run(body)
