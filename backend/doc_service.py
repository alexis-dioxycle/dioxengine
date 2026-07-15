"""Domain logic shared by the REST API and the MCP tools.

Everything that touches a document goes through here so the web UI and Claude
behave identically: same access rules, same draft lifecycle, same activity
trail. `actor_kind` distinguishes a human edit from an assistant edit - the
data path is deliberately the same.
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import (
    ActivityLog, Comment, Document, DocumentTypeNode, DocumentVersion,
    Project, ProjectMember, TemplateEdge, TemplateOwner, TemplateUser,
    WorkflowTemplate,
)

# `user` arguments below are the portal's DioxycleUser (dioxycle_auth) -
# only `.email` is used, so any object with an email attribute works.


def _norm_emails(emails):
    seen, out = set(), []
    for e in emails or []:
        e = (e or "").strip()
        if e and e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out


def _email_in(email, emails):
    e = (email or "").lower()
    return any((x or "").lower() == e for x in emails)


# ------------------------------------------------------------------ access

def template_owners(db: Session, template_id: int) -> list[str]:
    return [r.user_email for r in db.query(TemplateOwner)
            .filter(TemplateOwner.template_id == template_id).all()]


def template_users(db: Session, template_id: int) -> list[str]:
    return [r.user_email for r in db.query(TemplateUser)
            .filter(TemplateUser.template_id == template_id).all()]


def project_members(db: Session, project_id: int) -> list[str]:
    return [r.user_email for r in db.query(ProjectMember)
            .filter(ProjectMember.project_id == project_id).all()]


def require_template_access(db: Session, template_id: int, user, *,
                            need_owner: bool = False) -> bool:
    """Returns is_owner. Hidden resources 404; owner-only actions 403."""
    if not db.get(WorkflowTemplate, template_id):
        raise HTTPException(404, "Template not found")
    owners = template_owners(db, template_id)
    is_owner = _email_in(user.email, owners)
    if not (is_owner or _email_in(user.email, template_users(db, template_id))):
        raise HTTPException(404, "Template not found")
    if need_owner and not is_owner:
        raise HTTPException(403, "Only a template owner can do this")
    return is_owner


def require_project_member(db: Session, project_id: int, user) -> Project:
    p = db.get(Project, project_id)
    if not p or not _email_in(user.email, project_members(db, project_id)):
        raise HTTPException(404, "Project not found")
    return p


def can_act(assigned_email: str, user) -> bool:
    """Role slots are enforce-on-assign: empty slot = anyone may act."""
    return (not assigned_email) or assigned_email.lower() == user.email.lower()


# ------------------------------------------------------------------- graph

def validate_dag(nodes: list[dict], edges: list[dict]):
    keys = [n["node_key"] for n in nodes]
    if len(keys) != len(set(keys)):
        raise HTTPException(422, "Duplicate node keys")
    keyset = set(keys)
    for e in edges:
        if e["from_key"] not in keyset or e["to_key"] not in keyset:
            raise HTTPException(422, f"Edge references unknown node: {e['from_key']} -> {e['to_key']}")
        if e["from_key"] == e["to_key"]:
            raise HTTPException(422, f"Self-edge on {e['from_key']}")
    indeg = {k: 0 for k in keyset}
    out = {k: [] for k in keyset}
    for e in edges:
        indeg[e["to_key"]] += 1
        out[e["from_key"]].append(e["to_key"])
    queue = [k for k, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        k = queue.pop()
        seen += 1
        for m in out[k]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if seen != len(keyset):
        raise HTTPException(422, "Graph contains a cycle; the workflow must be a DAG")


def upstream_docs(db: Session, doc: Document) -> list[Document]:
    edges = db.query(TemplateEdge).filter(
        TemplateEdge.template_version_id == doc.node.template_version_id).all()
    from_nodes = [e.from_node_id for e in edges if e.to_node_id == doc.node_id]
    if not from_nodes:
        return []
    return db.query(Document).filter(
        Document.project_id == doc.project_id,
        Document.node_id.in_(from_nodes)).all()


# --------------------------------------------------------------- versions

def latest_version(doc: Document) -> DocumentVersion | None:
    return doc.versions[-1] if doc.versions else None


def approved_version(doc: Document) -> DocumentVersion | None:
    for v in reversed(doc.versions):
        if v.status == "approved":
            return v
    return None


def staleness(db: Session, doc: Document) -> list[str]:
    """Reasons this document's approved rev lags its upstream approvals."""
    approved = approved_version(doc)
    if not approved:
        return []
    based_on = approved.based_on or {}
    reasons = []
    for up in upstream_docs(db, doc):
        up_appr = approved_version(up)
        if not up_appr:
            continue
        seen_rev = based_on.get(str(up.id))
        if seen_rev is None or seen_rev < up_appr.version_number:
            seen = f"rev {seen_rev}" if seen_rev is not None else "no approved revision"
            reasons.append(f"{up.node.name} is now at rev {up_appr.version_number} "
                           f"(this document was based on {seen})")
    return reasons


def log(db: Session, *, document: Document | None = None, project_id: int | None = None,
        actor_email: str, actor_kind: str, action: str, payload: dict | None = None):
    db.add(ActivityLog(
        document_id=document.id if document else None,
        project_id=project_id or (document.project_id if document else None),
        actor_email=actor_email, actor_kind=actor_kind,
        action=action, payload=payload or {}))


# ------------------------------------------------------------------ drafts

def get_document_or_404(db: Session, document_id: int, user) -> Document:
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    require_project_member(db, doc.project_id, user)
    return doc


def open_draft(db: Session, doc: Document, user, actor_kind: str = "user") -> DocumentVersion:
    """Return the current draft, creating one (pre-filled from the latest
    content) if the head version is approved/rejected. 409 while submitted."""
    if not can_act(doc.author_email, user):
        raise HTTPException(403, f"Only the assigned author ({doc.author_email}) can edit this document")
    head = latest_version(doc)
    if head and head.status == "submitted":
        raise HTTPException(409, "A revision is under review; wait for the decision before editing")
    if head and head.status == "draft":
        return head
    v = DocumentVersion(
        document_id=doc.id,
        version_number=(head.version_number + 1) if head else 1,
        status="draft",
        content=dict(head.content or {}) if head else {},
        created_by=user.email,
    )
    db.add(v)
    db.flush()
    return v


def section_spec(doc: Document, section_key: str) -> dict:
    for s in (doc.node.content_schema or {}).get("sections", []):
        if s.get("key") == section_key:
            return s
    raise HTTPException(422, f"Unknown section '{section_key}' - valid keys: "
                        + ", ".join(s.get("key", "?") for s in (doc.node.content_schema or {}).get("sections", [])))


def validate_rows(spec: dict, rows: list) -> list:
    if not isinstance(rows, list):
        raise HTTPException(422, "Table content must be a list of row objects")
    cols = {c["key"] for c in spec.get("columns", [])}
    clean = []
    for r in rows:
        if not isinstance(r, dict):
            raise HTTPException(422, "Each row must be an object")
        unknown = set(r) - cols
        if unknown:
            raise HTTPException(422, f"Unknown columns {sorted(unknown)} - valid: {sorted(cols)}")
        clean.append({k: r.get(k, "") for k in cols})
    return clean


def set_section(db: Session, doc: Document, user, section_key: str,
                value, actor_kind: str = "user") -> DocumentVersion:
    """Write one section of the current draft (text string or full row list)."""
    spec = section_spec(doc, section_key)
    if spec.get("type") == "table":
        value = validate_rows(spec, value)
    elif not isinstance(value, str):
        raise HTTPException(422, f"Section '{section_key}' is text; expected a string")
    draft = open_draft(db, doc, user, actor_kind)
    content = dict(draft.content or {})
    content[section_key] = value
    draft.content = content
    draft.updated_at = datetime.utcnow()
    log(db, document=doc, actor_email=user.email, actor_kind=actor_kind,
        action="draft_edit", payload={"section": section_key})
    db.commit()
    db.refresh(draft)
    return draft


def save_draft(db: Session, doc: Document, user, content: dict,
               actor_kind: str = "user") -> DocumentVersion:
    """Full-content save (web editor autosave)."""
    sections = (doc.node.content_schema or {}).get("sections", [])
    known = {s["key"] for s in sections}
    unknown = set(content) - known
    if unknown:
        raise HTTPException(422, f"Unknown sections: {sorted(unknown)}")
    for s in sections:
        if s["key"] in content and s.get("type") == "table":
            content[s["key"]] = validate_rows(s, content[s["key"]])
    draft = open_draft(db, doc, user, actor_kind)
    draft.content = content
    draft.updated_at = datetime.utcnow()
    log(db, document=doc, actor_email=user.email, actor_kind=actor_kind,
        action="draft_edit", payload={"sections": sorted(content.keys())})
    db.commit()
    db.refresh(draft)
    return draft


def submit(db: Session, doc: Document, user, actor_kind: str = "user") -> DocumentVersion:
    if not can_act(doc.author_email, user):
        raise HTTPException(403, "Only the assigned author can submit")
    head = latest_version(doc)
    if not head or head.status != "draft":
        raise HTTPException(409, "Nothing to submit: no open draft")
    based_on = {}
    for up in upstream_docs(db, doc):
        appr = approved_version(up)
        if appr:
            based_on[str(up.id)] = appr.version_number
    head.status = "submitted"
    head.submitted_at = datetime.utcnow()
    head.based_on = based_on
    log(db, document=doc, actor_email=user.email, actor_kind=actor_kind,
        action="submit", payload={"version": head.version_number})
    db.commit()
    db.refresh(head)
    return head


def review(db: Session, doc: Document, user, decision: str,
           comment: str = "", actor_kind: str = "user") -> DocumentVersion:
    if decision not in ("approved", "rejected"):
        raise HTTPException(422, "Decision must be 'approved' or 'rejected'")
    if not can_act(doc.reviewer_email, user):
        raise HTTPException(403, f"Only the assigned reviewer ({doc.reviewer_email}) can review")
    head = latest_version(doc)
    if not head or head.status != "submitted":
        raise HTTPException(409, "No revision awaiting review")
    if decision == "approved":
        for v in doc.versions:
            if v.status == "approved":
                v.status = "superseded"
    head.status = decision
    head.reviewed_by = user.email
    head.reviewed_at = datetime.utcnow()
    head.review_comment = comment
    log(db, document=doc, actor_email=user.email, actor_kind=actor_kind,
        action="review", payload={"version": head.version_number, "decision": decision})
    db.commit()
    db.refresh(head)
    return head


# ---------------------------------------------------------------- comments

def add_comment(db: Session, doc: Document, user, section_key: str,
                body: str, row_index: int | None = None,
                parent_id: int | None = None, actor_kind: str = "user") -> Comment:
    if not (body or "").strip():
        raise HTTPException(422, "Empty comment")
    if parent_id:
        parent = db.get(Comment, parent_id)
        if not parent or parent.document_id != doc.id:
            raise HTTPException(404, "Parent comment not found")
        section_key, row_index = parent.section_key, parent.row_index
    else:
        section_spec(doc, section_key)  # validates the anchor
    c = Comment(document_id=doc.id, section_key=section_key, row_index=row_index,
                parent_id=parent_id, author_email=user.email,
                author_kind=actor_kind, body=body.strip())
    db.add(c)
    log(db, document=doc, actor_email=user.email, actor_kind=actor_kind,
        action="comment", payload={"section": section_key})
    db.commit()
    db.refresh(c)
    return c


def resolve_comment(db: Session, doc: Document, user, comment_id: int,
                    actor_kind: str = "user") -> Comment:
    c = db.get(Comment, comment_id)
    if not c or c.document_id != doc.id:
        raise HTTPException(404, "Comment not found")
    root = db.get(Comment, c.parent_id) if c.parent_id else c
    root.status = "resolved"
    root.resolved_by = user.email
    root.resolved_at = datetime.utcnow()
    log(db, document=doc, actor_email=user.email, actor_kind=actor_kind,
        action="resolve_comment", payload={"comment_id": root.id})
    db.commit()
    db.refresh(root)
    return root


def comment_public(c: Comment) -> dict:
    return {
        "id": c.id, "section_key": c.section_key, "row_index": c.row_index,
        "parent_id": c.parent_id, "author_email": c.author_email,
        "author_kind": c.author_kind, "body": c.body, "status": c.status,
        "resolved_by": c.resolved_by,
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def version_public(v: DocumentVersion | None, with_content: bool = False) -> dict | None:
    if v is None:
        return None
    out = {
        "version_number": v.version_number, "status": v.status,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
        "submitted_at": v.submitted_at.isoformat() if v.submitted_at else None,
        "reviewed_by": v.reviewed_by,
        "reviewed_at": v.reviewed_at.isoformat() if v.reviewed_at else None,
        "review_comment": v.review_comment, "based_on": v.based_on or {},
    }
    if with_content:
        out["content"] = v.content or {}
    return out


def document_summary(db: Session, doc: Document) -> dict:
    head = latest_version(doc)
    appr = approved_version(doc)
    reasons = staleness(db, doc)
    open_comments = sum(1 for c in doc.comments if c.status == "open" and not c.parent_id)
    return {
        "id": doc.id, "node_id": doc.node_id, "node_key": doc.node.node_key,
        "name": doc.node.name, "description": doc.node.description,
        "author_email": doc.author_email, "reviewer_email": doc.reviewer_email,
        "receiver_emails": doc.receiver_emails or [],
        "author_role": doc.node.author_role, "reviewer_role": doc.node.reviewer_role,
        "latest": version_public(head), "approved": version_public(appr),
        "stale": bool(reasons), "stale_reasons": reasons,
        "open_comments": open_comments,
    }
