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

Tools — read: list_projects, get_project, get_document, get_upstream_content,
list_templates. Write (draft): update_text_section, update_table_section,
append_table_rows. Collaborate: list_comments, add_comment, reply_to_comment,
resolve_comment, submit_document, review_document (ask first). Build:
create_template, update_template_graph, publish_template, create_project,
add_project_members, seed_reference_templates, new_template_version,
set_document_skill, set_document_tools, delete_template (ask first). Files:
list_attachments, upload_attachment, download_attachment. Tools:
use_document_tool (call a Dioxycle App endpoint attached to a document).
SharePoint: sharepoint_status, sharepoint_sync_project (two-way, folder per
project).
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
    typed columns), its SKILL (the recipe for producing it — which upstream
    documents to pull from and what to take from each; follow it when
    generating content), its TOOLS (deterministic Dioxycle Apps endpoints —
    call them via use_document_tool for calculated values instead of
    estimating), the current working content (open draft if any, else the
    latest revision), version history, upstream documents, and open comments.
    ALWAYS call this before editing — section keys and table columns must
    match the schema exactly.

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
            "skill": doc.node.skill or "",
            "tools": doc.node.tools or [],
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
        from models import Attachment as _Att
        doc = _doc(db, me, document_id)
        out = []
        for u in svc.upstream_docs(db, doc):
            appr = svc.approved_version(u)
            atts = db.query(_Att).filter_by(document_id=u.id).order_by(_Att.id).all()
            out.append({
                "document_id": u.id, "name": u.node.name, "node_key": u.node.node_key,
                "approved_version": appr.version_number if appr else None,
                "content": (appr.content if appr else None),
                "content_schema": u.node.content_schema,
                # An upstream may live as an uploaded file rather than
                # structured sections (kind='deliverable', e.g. a P&ID PDF):
                # fetch it with download_attachment and extract from it.
                "attachments": [{"id": a.id, "filename": a.filename,
                                 "content_type": a.content_type,
                                 "kind": a.kind or "reference"} for a in atts],
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


# ============ CREATION TOOLS ============
# Claude can also build the structure itself: templates (the workflow DAGs),
# projects, links. Same invariants as the web routes: published versions are
# frozen, projects instantiate published versions only, DAG must be acyclic.

from models import (  # noqa: E402
    Document, DocumentTypeNode, ProjectMember, TemplateOwner, TemplateVersion,
    WorkflowTemplate,
)
import seed as seed_mod  # noqa: E402


@mcp.tool()
def list_templates() -> str:
    """List workflow templates visible to the acting user, with their versions
    (draft/published) and the documents each version defines."""
    def body(db, me):
        out = []
        for t in db.query(WorkflowTemplate).order_by(WorkflowTemplate.id.desc()).all():
            owners = svc.template_owners(db, t.id)
            if not (svc._email_in(me.email, owners)
                    or svc._email_in(me.email, svc.template_users(db, t.id))):
                continue
            out.append({
                "template_id": t.id, "name": t.name, "description": t.description,
                "owners": owners,
                "versions": [{
                    "template_version_id": v.id, "version": v.version_number,
                    "status": v.status,
                    "documents": [{"node_key": n.node_key, "name": n.name}
                                  for n in v.nodes],
                } for v in sorted(t.versions, key=lambda v: v.version_number)],
            })
        return {"templates": out}
    return _run(body)


@mcp.tool()
def create_template(name: str, description: str = "") -> str:
    """Create a new workflow template (v1 as an editable draft, owned by the
    acting user). Then define its documents and links with
    update_template_graph, and publish_template when ready.

    Args:
        name: Template name (e.g. "BOS Full Workflow").
        description: One-line purpose.
    """
    def body(db, me):
        t = WorkflowTemplate(name=name, description=description, created_by=me.email)
        db.add(t)
        db.flush()
        db.add(TemplateOwner(template_id=t.id, user_email=me.email))
        tv = TemplateVersion(template_id=t.id, version_number=1, status="draft",
                             created_by=me.email)
        db.add(tv)
        db.commit()
        return {"ok": True, "template_id": t.id, "template_version_id": tv.id}
    return _run(body)


@mcp.tool()
def update_template_graph(template_version_id: int, graph_json: str) -> str:
    """Replace the whole graph (document types + links) of a DRAFT template
    version. Published versions are frozen — create a new version instead.

    graph_json shape:
      {"nodes": [{"node_key": "el", "name": "Sized Equipment List",
                  "description": "...", "author_role": "...", "reviewer_role": "...",
                  "skill": "How to produce this document: pull ... from the PFD, ...",
                  "content_schema": {"sections": [
                    {"key": "notes", "title": "Notes", "type": "text"},
                    {"key": "vessels", "title": "Vessels", "type": "table",
                     "columns": [{"key": "item", "label": "Item", "type": "text"}]}]}}],
       "edges": [{"from_key": "pfd", "to_key": "el"}]}   // upstream -> downstream

    Each node's optional "skill" is its production recipe (upstream documents
    to read, what to extract from each, granularity, tools). Skills stay
    editable after publication via set_document_skill.

    Args:
        template_version_id: The draft version to write (from list_templates
            or create_template).
        graph_json: The full graph as JSON (replaces the previous graph).
    """
    def body(db, me):
        tv = db.get(TemplateVersion, template_version_id)
        if not tv:
            raise ValueError("Template version not found")
        svc.require_template_access(db, tv.template_id, me, need_owner=True)
        if tv.status != "draft":
            raise ValueError("This version is published and frozen; create a new draft version first")
        g = json.loads(graph_json)
        nodes = g.get("nodes", [])
        edges = g.get("edges", [])
        svc.validate_dag(nodes, edges)
        db.query(DocumentTypeNode).filter_by(template_version_id=tv.id).delete()
        db.query(TemplateEdge).filter_by(template_version_id=tv.id).delete()
        db.flush()
        import app_tools
        clean = [{
            "node_key": n["node_key"], "name": n["name"],
            "description": n.get("description", ""),
            "skill": n.get("skill", ""),
            "tools": app_tools.validate_tools(n.get("tools", [])),
            "author_role": n.get("author_role", ""),
            "reviewer_role": n.get("reviewer_role", ""),
            "receiver_roles": n.get("receiver_roles", []),
            "content_schema": n.get("content_schema", {"sections": []}),
        } for n in nodes]
        seed_mod.write_graph(db, tv, clean, [(e["from_key"], e["to_key"]) for e in edges])
        db.commit()
        return {"ok": True, "template_version_id": tv.id,
                "nodes": len(nodes), "edges": len(edges)}
    return _run(body)


@mcp.tool()
def publish_template(template_version_id: int) -> str:
    """Publish a draft template version. Published versions are frozen and can
    be instantiated into projects.

    Args:
        template_version_id: The draft version to publish.
    """
    def body(db, me):
        from datetime import datetime as _dt
        tv = db.get(TemplateVersion, template_version_id)
        if not tv:
            raise ValueError("Template version not found")
        svc.require_template_access(db, tv.template_id, me, need_owner=True)
        if tv.status != "draft":
            raise ValueError("Already published")
        if not tv.nodes:
            raise ValueError("Cannot publish an empty workflow")
        tv.status = "published"
        tv.published_at = _dt.utcnow()
        db.commit()
        return {"ok": True, "template_version_id": tv.id, "status": "published"}
    return _run(body)


@mcp.tool()
def create_project(template_version_id: int, name: str, description: str = "",
                   members: str = "") -> str:
    """Create a project from a PUBLISHED template version: every document type
    becomes a real document. The acting user becomes creator and member.

    Args:
        template_version_id: A published version (from list_templates).
        name: Project name (e.g. "BOS 5000F2PBOS").
        description: Optional one-liner.
        members: Optional comma-separated extra member emails.
    """
    def body(db, me):
        tv = db.get(TemplateVersion, template_version_id)
        if not tv:
            raise ValueError("Template version not found")
        if tv.status != "published":
            raise ValueError("Projects can only be created from a published template version")
        if not (svc._email_in(me.email, svc.template_owners(db, tv.template_id))
                or svc._email_in(me.email, svc.template_users(db, tv.template_id))):
            raise ValueError("You are not allowed to create projects from this template")
        p = Project(name=name, description=description,
                    template_version_id=tv.id, created_by=me.email)
        db.add(p)
        db.flush()
        emails = svc._norm_emails([me.email] + [e for e in members.split(",") if e.strip()])
        for e in emails:
            db.add(ProjectMember(project_id=p.id, user_email=e))
        for n in tv.nodes:
            db.add(Document(project_id=p.id, node_id=n.id))
        db.commit()
        return {"ok": True, "project_id": p.id, "members": emails,
                "documents": len(tv.nodes)}
    return _run(body)


@mcp.tool()
def add_project_members(project_id: int, emails: str) -> str:
    """Add members to a project (they see it and can work on its documents).

    Args:
        project_id: The project.
        emails: Comma-separated emails to add.
    """
    def body(db, me):
        p = svc.require_project_member(db, project_id, me)
        current = svc.project_members(db, p.id)
        added = []
        for e in svc._norm_emails(emails.split(",")):
            if not svc._email_in(e, current):
                db.add(ProjectMember(project_id=p.id, user_email=e))
                added.append(e)
        db.commit()
        return {"ok": True, "added": added, "members": svc.project_members(db, p.id)}
    return _run(body)


@mcp.tool()
def seed_reference_templates(which: str = "all") -> str:
    """Create the built-in reference templates if they don't exist yet
    (idempotent). 'workflow1' = BOS Procurement (PFD → equipment list →
    datasheets → offers → comparison, schemas from the real 5000F2PBOS docs);
    'workflow2' = Control & Safety (narrative → CLD register + long-format
    CEM); 'electrolyzer' = the full basic-engineering example.

    Args:
        which: 'workflow1' | 'workflow2' | 'electrolyzer' | 'all'.
    """
    def body(db, me):
        created = []
        if which in ("workflow1", "all"):
            if seed_mod.seed_workflow1(db, owner_email=me.email):
                created.append("workflow1")
        if which in ("workflow2", "all"):
            if seed_mod.seed_workflow2(db, owner_email=me.email):
                created.append("workflow2")
        if which in ("electrolyzer", "all"):
            if seed_mod.seed_example(db, owner_email=me.email):
                created.append("electrolyzer")
        return {"ok": True, "created": created or "nothing (already present)"}
    return _run(body)


@mcp.tool()
def review_document(document_id: int, decision: str, comment: str = "") -> str:
    """Approve or reject the submitted revision of a document, acting as the
    user (allowed only if they are the assigned reviewer or the slot is
    unassigned). ALWAYS confirm with the user before calling this — approval
    supersedes the previous approved revision and unlocks downstream work.

    Args:
        document_id: The document under review.
        decision: 'approved' or 'rejected'.
        comment: Review comment (required for rejections: say why).
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        v = svc.review(db, doc, me, decision, comment, actor_kind="assistant")
        return {"ok": True, "version_number": v.version_number, "status": v.status}
    return _run(body)


# ============ ATTACHMENT TOOLS ============

from models import Attachment  # noqa: E402
import base64 as _b64  # noqa: E402


@mcp.tool()
def list_attachments(document_id: int) -> str:
    """List the files attached to a document (the human-readable originals:
    PFD drawing PDF, vendor offer PDFs, issued datasheets...). They render
    inline in the web editor.

    Args:
        document_id: The document.
    """
    def body(db, me):
        _doc(db, me, document_id)
        rows = db.query(Attachment).filter_by(document_id=document_id).order_by(Attachment.id).all()
        return {"attachments": [{
            "id": a.id, "filename": a.filename, "content_type": a.content_type,
            "size_bytes": a.size_bytes, "uploaded_by": a.uploaded_by,
            "kind": a.kind or "reference",
        } for a in rows]}
    return _run(body)


@mcp.tool()
def upload_attachment(document_id: int, filename: str, content_base64: str,
                      content_type: str = "application/pdf",
                      kind: str = "reference") -> str:
    """Attach a file to a document (max 15 MB). PDFs render inline in the web
    editor — use this to give a document its real drawing or original file.

    Args:
        document_id: The document to attach to.
        filename: File name shown to users (e.g. "5000F2PBOS-PFD-001-rev002.pdf").
        content_base64: The file bytes, base64-encoded.
        content_type: MIME type (default application/pdf).
        kind: 'reference' (supporting original, default) or 'deliverable'
            (this file IS the document — e.g. an AutoCAD P&ID exported to PDF
            that no structured section will replace).
    """
    def body(db, me):
        doc = _doc(db, me, document_id)
        if kind not in ("reference", "deliverable"):
            raise ValueError("kind must be 'reference' or 'deliverable'")
        data = _b64.b64decode(content_base64)
        if len(data) > 15 * 1024 * 1024:
            raise ValueError("Attachment too large (max 15 MB)")
        a = Attachment(document_id=document_id, filename=filename,
                       content_type=content_type, size_bytes=len(data),
                       data=data, uploaded_by=me.email, kind=kind)
        db.add(a)
        svc.log(db, document=doc, actor_email=me.email, actor_kind="assistant",
                action="attach", payload={"filename": filename, "kind": kind})
        db.commit()
        return {"ok": True, "attachment_id": a.id, "size_bytes": len(data)}
    return _run(body)


@mcp.tool()
def download_attachment(document_id: int, attachment_id: int) -> str:
    """Read an attachment's bytes, base64-encoded (max 4 MB through MCP —
    bigger files: open the document in the web UI). Use this to extract
    information from an uploaded original — e.g. a P&ID PDF drawn in AutoCAD
    and attached as the document's deliverable: save it locally, read it, then
    fill the downstream documents from what it contains.

    Args:
        document_id: The document the file is attached to.
        attachment_id: The attachment (from list_attachments).
    """
    def body(db, me):
        _doc(db, me, document_id)
        a = db.get(Attachment, attachment_id)
        if not a or a.document_id != document_id:
            raise ValueError("Attachment not found")
        if (a.size_bytes or 0) > 4 * 1024 * 1024:
            raise ValueError(f"Attachment is {a.size_bytes} bytes — too large for MCP "
                             "(max 4 MB); download it from the web editor instead")
        return {"filename": a.filename, "content_type": a.content_type,
                "size_bytes": a.size_bytes, "kind": a.kind or "reference",
                "content_base64": _b64.b64encode(a.data).decode()}
    return _run(body)


@mcp.tool()
def set_document_skill(template_version_id: int, node_key: str, skill: str) -> str:
    """Write the SKILL of a document type — the recipe for producing that
    document: which upstream documents to pull from, what to take from each,
    the granularity expected, which tools/apps to use. Works on PUBLISHED
    versions too (template owners only): refining a skill is guidance, not a
    structural change, and immediately applies to every project on that
    version.

    Args:
        template_version_id: The template version (from list_templates or a
            document's project).
        node_key: The document type's key within that version (e.g. "sel").
        skill: The full skill text (replaces the previous one). Markdown
            welcome; write it like instructions to the engineer/assistant who
            will produce the document.
    """
    def body(db, me):
        tv = db.get(TemplateVersion, template_version_id)
        if not tv:
            raise ValueError("Template version not found")
        svc.require_template_access(db, tv.template_id, me, need_owner=True)
        node = next((n for n in tv.nodes if n.node_key == node_key), None)
        if not node:
            raise ValueError(f"No document type '{node_key}' in this version — "
                             "valid keys: " + ", ".join(n.node_key for n in tv.nodes))
        node.skill = skill
        db.commit()
        return {"ok": True, "node_key": node_key, "skill_chars": len(skill)}
    return _run(body)


@mcp.tool()
def delete_template(template_id: int) -> str:
    """Delete a whole workflow template (all its versions). Refused while any
    project instantiates one of its versions. DESTRUCTIVE — always confirm
    with the user before calling this.

    Args:
        template_id: The template to delete (from list_templates).
    """
    def body(db, me):
        svc.require_template_access(db, template_id, me, need_owner=True)
        t = db.get(WorkflowTemplate, template_id)
        version_ids = [v.id for v in t.versions]
        blocking = (db.query(Project)
                    .filter(Project.template_version_id.in_(version_ids)).all()
                    if version_ids else [])
        if blocking:
            raise ValueError(f"{len(blocking)} project(s) use this template "
                             f"({', '.join(p.name for p in blocking[:5])}) — delete them first")
        db.query(TemplateOwner).filter_by(template_id=template_id).delete()
        from models import TemplateUser as _TU
        db.query(_TU).filter_by(template_id=template_id).delete()
        name = t.name
        db.delete(t)
        db.commit()
        return {"ok": True, "deleted": name}
    return _run(body)


@mcp.tool()
def use_document_tool(document_id: int, tool_name: str, params_json: str = "{}") -> str:
    """Call one of the deterministic tools attached to a document (listed in
    get_document's "tools", with their expected params). Use these for
    calculated values — pressure drops, sizing, ratings — instead of
    estimating: deterministic calcs live in Dioxycle Apps, the assistant
    orchestrates. The HTTP call is made by the DioXengine backend against an
    allowlisted host; the result is returned verbatim.

    Args:
        document_id: The document whose tool to call.
        tool_name: The tool's name, exactly as listed in get_document.
        params_json: JSON object of parameters, e.g. '{"flow_kgh": 120, "diameter_mm": 25}'.
            GET tools receive them as query parameters, POST tools as the JSON body.
    """
    def body(db, me):
        import app_tools
        doc = _doc(db, me, document_id)
        tools = doc.node.tools or []
        tool = next((t for t in tools if t.get("name") == tool_name), None)
        if not tool:
            raise ValueError(f"No tool '{tool_name}' on this document — available: "
                             + (", ".join(t.get("name", "?") for t in tools) or "none"))
        params = json.loads(params_json) if params_json.strip() else {}
        if not isinstance(params, dict):
            raise ValueError("params_json must be a JSON object")
        result = app_tools.call_tool(tool, params)
        svc.log(db, document=doc, actor_email=me.email, actor_kind="assistant",
                action="tool_call", payload={"tool": tool_name, "status": result["status"]})
        db.commit()
        return result
    return _run(body)


@mcp.tool()
def set_document_tools(template_version_id: int, node_key: str, tools_json: str) -> str:
    """Attach deterministic tools (Dioxycle Apps endpoints) to a document
    type — replaces its full tool list. Works on PUBLISHED versions too
    (template owners only), like set_document_skill.

    tools_json: JSON array, e.g.
      [{"name": "pressure_drop", "description": "ΔP for a line segment",
        "url": "https://apps.dioxycle.com/_apps/line-sizer/api/pressure-drop",
        "method": "GET", "params": "fluid, flow_kgh, diameter_mm, length_m"}]
    Hosts must be on the TOOL_ALLOWED_HOSTS allowlist (default
    apps.dioxycle.com). Reference the tools by name in the document's skill.

    Args:
        template_version_id: The template version (from list_templates).
        node_key: The document type's key within that version.
        tools_json: The full replacement tool list as a JSON array.
    """
    def body(db, me):
        import app_tools
        tv = db.get(TemplateVersion, template_version_id)
        if not tv:
            raise ValueError("Template version not found")
        svc.require_template_access(db, tv.template_id, me, need_owner=True)
        node = next((n for n in tv.nodes if n.node_key == node_key), None)
        if not node:
            raise ValueError(f"No document type '{node_key}' in this version — "
                             "valid keys: " + ", ".join(n.node_key for n in tv.nodes))
        node.tools = app_tools.validate_tools(json.loads(tools_json))
        db.commit()
        return {"ok": True, "node_key": node_key, "tools": [t["name"] for t in node.tools]}
    return _run(body)


@mcp.tool()
def sharepoint_status() -> str:
    """Check the SharePoint integration: configured? site reachable? Returns
    the site name and URL when the Sites.Selected grant is in place."""
    def body(db, me):
        import sharepoint
        return sharepoint.status()
    return _run(body)


@mcp.tool()
def sharepoint_sync_project(project_id: int) -> str:
    """Two-way sync of every document in a project against its
    DioXengine/<project>/ folder on the company SharePoint site. Per
    document: pushes local changes (the rendered .xlsx/.docx, or the
    uploaded deliverable as-is), pulls edits made on SharePoint back into a
    draft (approved/submitted documents are locked and never pulled), and
    reports conflicts (both sides changed — nothing is clobbered). Returns
    the per-document report; relay conflicts and locks to the user.

    Args:
        project_id: The project to sync (from list_projects).
    """
    def body(db, me):
        import sharepoint
        p = svc.require_project_member(db, project_id, me)
        if not sharepoint.configured():
            raise ValueError(sharepoint.status()["detail"])
        return sharepoint.sync_project(db, p, me)
    return _run(body)


@mcp.tool()
def new_template_version(template_id: int) -> str:
    """Create a new DRAFT version of a template, copying the latest published
    graph. Use when a published template needs schema changes: edit the new
    draft with update_template_graph, then publish_template.

    Args:
        template_id: The template (from list_templates).
    """
    def body(db, me):
        svc.require_template_access(db, template_id, me, need_owner=True)
        src = (db.query(TemplateVersion).filter(TemplateVersion.template_id == template_id)
               .order_by((TemplateVersion.status == "published").desc(),
                         TemplateVersion.version_number.desc()).first())
        if not src:
            raise ValueError("Template not found")
        nxt = max(v.version_number for v in src.template.versions) + 1
        tv = TemplateVersion(template_id=template_id, version_number=nxt,
                             status="draft", created_by=me.email)
        db.add(tv)
        db.flush()
        id_map = {}
        for n in src.nodes:
            node = DocumentTypeNode(
                template_version_id=tv.id, node_key=n.node_key, name=n.name,
                description=n.description, content_schema=n.content_schema,
                skill=n.skill, tools=n.tools,
                author_role=n.author_role, reviewer_role=n.reviewer_role,
                receiver_roles=n.receiver_roles)
            db.add(node)
            db.flush()
            id_map[n.id] = node.id
        for e in db.query(TemplateEdge).filter_by(template_version_id=src.id).all():
            db.add(TemplateEdge(template_version_id=tv.id,
                                from_node_id=id_map[e.from_node_id],
                                to_node_id=id_map[e.to_node_id]))
        db.commit()
        return {"ok": True, "template_version_id": tv.id, "version_number": nxt,
                "copied_from_version": src.version_number}
    return _run(body)
