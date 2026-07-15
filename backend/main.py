"""DioXengine backend — Dioxycle Apps portal shape.

Identity comes from the portal (HMAC-signed headers, see dioxycle_auth.py);
the app never authenticates anyone. Schema comes from backend/migrations/
(applied by the portal before boot). Routes:
  /healthz
  /api/me, /api/users
  /api/templates..., /api/template-versions...
  /api/projects...
  /api/documents... (draft, submit, review, comments, activity)
  /api/seed-example
  static SPA (built frontend) at /
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import IS_LOCAL_DEV, Base, engine, get_db
from dioxycle_auth import DioxycleUser, current_user
from models import (
    ActivityLog, Document, DocumentTypeNode, Project, ProjectMember,
    TemplateEdge, TemplateOwner, TemplateUser, TemplateVersion, User,
    WorkflowTemplate,
)
import doc_service as svc
import seed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if IS_LOCAL_DEV:
    # Local SQLite only. On the portal the schema comes exclusively from
    # backend/migrations/ — create_all never runs there (DATABASE_URL is set).
    Path("./data").mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)

app = FastAPI(title="DioXengine")


def track_user(user: DioxycleUser = Depends(current_user),
               db: Session = Depends(get_db)) -> DioxycleUser:
    """Portal identity is the source of truth; we only record everyone who
    connects so the app has a directory to grant access from."""
    u = db.query(User).filter(User.email == user.email).first()
    if not u:
        db.add(User(email=user.email, name=user.name or "", role=user.role or ""))
    else:
        u.name = user.name or u.name
        u.role = user.role or u.role
        u.last_seen = datetime.utcnow()
    db.commit()
    return user


# ============ payload models ============

class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class NodeSpec(BaseModel):
    node_key: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    content_schema: dict = Field(default_factory=lambda: {"sections": []})
    author_role: str = ""
    reviewer_role: str = ""
    receiver_roles: list[str] = Field(default_factory=list)


class EdgeSpec(BaseModel):
    from_key: str
    to_key: str


class GraphPayload(BaseModel):
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    template_version_id: int


class AssignmentsUpdate(BaseModel):
    author_email: str = ""
    reviewer_email: str = ""
    receiver_emails: list[str] = Field(default_factory=list)


class DraftUpdate(BaseModel):
    content: dict


class ReviewDecision(BaseModel):
    decision: str = Field(pattern=r"^(approved|rejected)$")
    comment: str = ""


class AccessUpdate(BaseModel):
    owners: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)


class MembersUpdate(BaseModel):
    members: list[str] = Field(default_factory=list)


class CommentCreate(BaseModel):
    section_key: str = ""
    row_index: Optional[int] = None
    parent_id: Optional[int] = None
    body: str


# ============ health + identity ============

@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/me")
def me(user: DioxycleUser = Depends(track_user)):
    return {"email": user.email, "name": user.name, "role": user.role}


@app.get("/api/users")
def list_users(user: DioxycleUser = Depends(track_user), db: Session = Depends(get_db)):
    """Directory of everyone who has connected via the portal (backs the
    owner/member pickers). Access can still be granted to an email that
    hasn't connected yet."""
    return [{"email": u.email, "name": u.name}
            for u in db.query(User).order_by(User.email).all()]


# ============ templates ============

def _tv_public(tv: TemplateVersion) -> dict:
    return {"id": tv.id, "version_number": tv.version_number, "status": tv.status,
            "created_at": tv.created_at.isoformat() if tv.created_at else None,
            "published_at": tv.published_at.isoformat() if tv.published_at else None}


def _node_public(n: DocumentTypeNode) -> dict:
    return {"id": n.id, "node_key": n.node_key, "name": n.name,
            "description": n.description, "content_schema": n.content_schema,
            "author_role": n.author_role, "reviewer_role": n.reviewer_role,
            "receiver_roles": n.receiver_roles or []}


@app.get("/api/templates")
def list_templates(user: DioxycleUser = Depends(track_user), db: Session = Depends(get_db)):
    out = []
    for t in db.query(WorkflowTemplate).order_by(WorkflowTemplate.id.desc()).all():
        owners = svc.template_owners(db, t.id)
        usors = svc.template_users(db, t.id)
        is_owner = svc._email_in(user.email, owners)
        if not (is_owner or svc._email_in(user.email, usors)):
            continue
        out.append({"id": t.id, "name": t.name, "description": t.description,
                    "created_by": t.created_by, "owners": owners, "users": usors,
                    "is_owner": is_owner,
                    "versions": [_tv_public(v) for v in sorted(t.versions, key=lambda v: v.version_number)]})
    return out


@app.post("/api/templates")
def create_template(body: TemplateCreate, user: DioxycleUser = Depends(track_user),
                    db: Session = Depends(get_db)):
    t = WorkflowTemplate(name=body.name, description=body.description, created_by=user.email)
    db.add(t)
    db.flush()
    db.add(TemplateOwner(template_id=t.id, user_email=user.email))
    tv = TemplateVersion(template_id=t.id, version_number=1, status="draft", created_by=user.email)
    db.add(tv)
    db.commit()
    return {"template_id": t.id, "template_version_id": tv.id}


@app.post("/api/templates/{template_id}/versions")
def new_template_version(template_id: int, user: DioxycleUser = Depends(track_user),
                         db: Session = Depends(get_db)):
    svc.require_template_access(db, template_id, user, need_owner=True)
    src = (db.query(TemplateVersion).filter(TemplateVersion.template_id == template_id)
           .order_by((TemplateVersion.status == "published").desc(),
                     TemplateVersion.version_number.desc()).first())
    if not src:
        raise HTTPException(404, "Template not found")
    nxt = max(v.version_number for v in src.template.versions) + 1
    tv = TemplateVersion(template_id=template_id, version_number=nxt,
                         status="draft", created_by=user.email)
    db.add(tv)
    db.flush()
    id_map = {}
    for n in src.nodes:
        node = DocumentTypeNode(
            template_version_id=tv.id, node_key=n.node_key, name=n.name,
            description=n.description, content_schema=n.content_schema,
            author_role=n.author_role, reviewer_role=n.reviewer_role,
            receiver_roles=n.receiver_roles)
        db.add(node)
        db.flush()
        id_map[n.id] = node.id
    for e in db.query(TemplateEdge).filter_by(template_version_id=src.id).all():
        db.add(TemplateEdge(template_version_id=tv.id,
                            from_node_id=id_map[e.from_node_id], to_node_id=id_map[e.to_node_id]))
    db.commit()
    return {"template_version_id": tv.id, "version_number": nxt}


@app.get("/api/template-versions/{tvid}")
def get_template_version(tvid: int, user: DioxycleUser = Depends(track_user),
                         db: Session = Depends(get_db)):
    tv = db.get(TemplateVersion, tvid)
    if not tv:
        raise HTTPException(404, "Template version not found")
    is_owner = svc.require_template_access(db, tv.template_id, user)
    edges = db.query(TemplateEdge).filter_by(template_version_id=tvid).all()
    return {
        "id": tv.id, "template_id": tv.template_id,
        "template_name": tv.template.name,
        "template_description": tv.template.description,
        "version_number": tv.version_number, "status": tv.status,
        "owners": svc.template_owners(db, tv.template_id),
        "users": svc.template_users(db, tv.template_id),
        "is_owner": is_owner, "can_edit": is_owner and tv.status == "draft",
        "nodes": [_node_public(n) for n in tv.nodes],
        "edges": [{"id": e.id, "from_node_id": e.from_node_id, "to_node_id": e.to_node_id}
                  for e in edges],
    }


@app.put("/api/template-versions/{tvid}")
def update_template_version(tvid: int, body: GraphPayload,
                            user: DioxycleUser = Depends(track_user),
                            db: Session = Depends(get_db)):
    tv = db.get(TemplateVersion, tvid)
    if not tv:
        raise HTTPException(404, "Template version not found")
    svc.require_template_access(db, tv.template_id, user, need_owner=True)
    if tv.status != "draft":
        raise HTTPException(409, "Published template versions are frozen; create a new version")
    svc.validate_dag([n.model_dump() for n in body.nodes],
                     [e.model_dump() for e in body.edges])
    db.query(DocumentTypeNode).filter_by(template_version_id=tvid).delete()
    db.query(TemplateEdge).filter_by(template_version_id=tvid).delete()
    db.flush()
    seed.write_graph(db, tv, [n.model_dump() for n in body.nodes],
                     [(e.from_key, e.to_key) for e in body.edges])
    db.commit()
    return {"ok": True}


@app.post("/api/template-versions/{tvid}/publish")
def publish_template_version(tvid: int, user: DioxycleUser = Depends(track_user),
                             db: Session = Depends(get_db)):
    tv = db.get(TemplateVersion, tvid)
    if not tv:
        raise HTTPException(404, "Template version not found")
    svc.require_template_access(db, tv.template_id, user, need_owner=True)
    if tv.status != "draft":
        raise HTTPException(409, "Already published")
    if not tv.nodes:
        raise HTTPException(422, "Cannot publish an empty workflow")
    tv.status = "published"
    tv.published_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@app.delete("/api/template-versions/{tvid}")
def delete_template_version(tvid: int, user: DioxycleUser = Depends(track_user),
                            db: Session = Depends(get_db)):
    tv = db.get(TemplateVersion, tvid)
    if not tv:
        raise HTTPException(404, "Template version not found")
    svc.require_template_access(db, tv.template_id, user, need_owner=True)
    if tv.status != "draft":
        raise HTTPException(409, "Only draft versions can be deleted")
    template = tv.template
    db.delete(tv)
    db.flush()
    if not template.versions:
        db.delete(template)
    db.commit()
    return {"ok": True}


@app.get("/api/templates/{tid}/access")
def get_template_access(tid: int, user: DioxycleUser = Depends(track_user),
                        db: Session = Depends(get_db)):
    is_owner = svc.require_template_access(db, tid, user)
    return {"owners": svc.template_owners(db, tid),
            "users": svc.template_users(db, tid), "is_owner": is_owner}


@app.put("/api/templates/{tid}/access")
def set_template_access(tid: int, body: AccessUpdate,
                        user: DioxycleUser = Depends(track_user),
                        db: Session = Depends(get_db)):
    svc.require_template_access(db, tid, user, need_owner=True)
    owners = svc._norm_emails(body.owners)
    if not owners:
        raise HTTPException(422, "A template must have at least one owner")
    usors = [e for e in svc._norm_emails(body.users) if not svc._email_in(e, owners)]
    db.query(TemplateOwner).filter_by(template_id=tid).delete()
    db.query(TemplateUser).filter_by(template_id=tid).delete()
    for e in owners:
        db.add(TemplateOwner(template_id=tid, user_email=e))
    for e in usors:
        db.add(TemplateUser(template_id=tid, user_email=e))
    db.commit()
    return {"owners": owners, "users": usors}


# ============ projects ============

@app.get("/api/projects")
def list_projects(user: DioxycleUser = Depends(track_user), db: Session = Depends(get_db)):
    out = []
    for p in db.query(Project).order_by(Project.id.desc()).all():
        members = svc.project_members(db, p.id)
        if not svc._email_in(user.email, members):
            continue
        docs = p.documents
        n_approved = sum(1 for d in docs if svc.approved_version(d))
        out.append({
            "id": p.id, "name": p.name, "description": p.description,
            "created_by": p.created_by,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "template_name": p.template_version.template.name,
            "template_version": p.template_version.version_number,
            "members": members, "n_documents": len(docs), "n_approved": n_approved,
        })
    return out


@app.post("/api/projects")
def create_project(body: ProjectCreate, user: DioxycleUser = Depends(track_user),
                   db: Session = Depends(get_db)):
    tv = db.get(TemplateVersion, body.template_version_id)
    if not tv:
        raise HTTPException(404, "Template version not found")
    if tv.status != "published":
        raise HTTPException(422, "Projects can only be created from a published template version")
    owners = svc.template_owners(db, tv.template_id)
    if not (svc._email_in(user.email, owners)
            or svc._email_in(user.email, svc.template_users(db, tv.template_id))):
        raise HTTPException(403, "You are not allowed to create projects from this template")
    p = Project(name=body.name, description=body.description,
                template_version_id=tv.id, created_by=user.email)
    db.add(p)
    db.flush()
    db.add(ProjectMember(project_id=p.id, user_email=user.email))
    for n in tv.nodes:
        db.add(Document(project_id=p.id, node_id=n.id))
    db.commit()
    return {"project_id": p.id}


@app.get("/api/projects/{pid}")
def get_project(pid: int, user: DioxycleUser = Depends(track_user),
                db: Session = Depends(get_db)):
    p = svc.require_project_member(db, pid, user)
    edges = db.query(TemplateEdge).filter_by(template_version_id=p.template_version_id).all()
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "template_name": p.template_version.template.name,
        "template_version": p.template_version.version_number,
        "template_version_id": p.template_version_id,
        "created_by": p.created_by, "members": svc.project_members(db, pid),
        "can_manage_members": p.created_by == user.email,
        "edges": [{"from_node_id": e.from_node_id, "to_node_id": e.to_node_id}
                  for e in edges],
        "documents": [svc.document_summary(db, d)
                      for d in sorted(p.documents, key=lambda d: d.node_id)],
    }


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, user: DioxycleUser = Depends(track_user),
                   db: Session = Depends(get_db)):
    p = svc.require_project_member(db, pid, user)
    if p.created_by != user.email:
        raise HTTPException(403, "Only the project creator can delete it")
    db.query(ProjectMember).filter_by(project_id=pid).delete()
    db.delete(p)
    db.commit()
    return {"ok": True}


@app.get("/api/projects/{pid}/members")
def get_project_members(pid: int, user: DioxycleUser = Depends(track_user),
                        db: Session = Depends(get_db)):
    p = svc.require_project_member(db, pid, user)
    return {"members": svc.project_members(db, pid), "created_by": p.created_by,
            "can_manage_members": p.created_by == user.email}


@app.put("/api/projects/{pid}/members")
def set_project_members(pid: int, body: MembersUpdate,
                        user: DioxycleUser = Depends(track_user),
                        db: Session = Depends(get_db)):
    p = svc.require_project_member(db, pid, user)
    if p.created_by != user.email:
        raise HTTPException(403, "Only the project creator can manage members")
    members = svc._norm_emails(body.members)
    if not svc._email_in(p.created_by, members):
        members.append(p.created_by)
    db.query(ProjectMember).filter_by(project_id=pid).delete()
    for e in members:
        db.add(ProjectMember(project_id=pid, user_email=e))
    db.commit()
    return {"members": members}


# ============ documents ============

@app.get("/api/documents/{did}")
def get_document(did: int, user: DioxycleUser = Depends(track_user),
                 db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    head = svc.latest_version(doc)
    return {
        **svc.document_summary(db, doc),
        "project_id": doc.project_id, "project_name": doc.project.name,
        "content_schema": doc.node.content_schema,
        "can_edit": svc.can_act(doc.author_email, user),
        "can_review": svc.can_act(doc.reviewer_email, user),
        "latest_content": (head.content if head else {}) or {},
        "latest_status": head.status if head else None,
        "latest_version_number": head.version_number if head else None,
        "latest_updated_at": (head.updated_at.isoformat()
                              if head and head.updated_at else None),
        "versions": [svc.version_public(v) for v in reversed(doc.versions)],
        "upstream": [{
            "document_id": u.id, "name": u.node.name,
            "approved_version": (svc.approved_version(u).version_number
                                 if svc.approved_version(u) else None),
        } for u in svc.upstream_docs(db, doc)],
        "comments": [svc.comment_public(c) for c in doc.comments],
    }


@app.get("/api/documents/{did}/versions/{n}")
def get_document_version(did: int, n: int, user: DioxycleUser = Depends(track_user),
                         db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    v = next((v for v in doc.versions if v.version_number == n), None)
    if not v:
        raise HTTPException(404, "Version not found")
    return svc.version_public(v, with_content=True)


@app.put("/api/documents/{did}/assignments")
def update_assignments(did: int, body: AssignmentsUpdate,
                       user: DioxycleUser = Depends(track_user),
                       db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    doc.author_email = body.author_email.strip()
    doc.reviewer_email = body.reviewer_email.strip()
    doc.receiver_emails = [e.strip() for e in body.receiver_emails if e.strip()]
    db.commit()
    return {"ok": True}


@app.put("/api/documents/{did}/draft")
def save_draft(did: int, body: DraftUpdate, user: DioxycleUser = Depends(track_user),
               db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    draft = svc.save_draft(db, doc, user, body.content)
    return {"version_number": draft.version_number, "status": draft.status,
            "updated_at": draft.updated_at.isoformat() if draft.updated_at else None}


@app.post("/api/documents/{did}/submit")
def submit_document(did: int, user: DioxycleUser = Depends(track_user),
                    db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    v = svc.submit(db, doc, user)
    return {"version_number": v.version_number, "status": v.status}


@app.post("/api/documents/{did}/review")
def review_document(did: int, body: ReviewDecision,
                    user: DioxycleUser = Depends(track_user),
                    db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    v = svc.review(db, doc, user, body.decision, body.comment)
    return {"version_number": v.version_number, "status": v.status}


@app.post("/api/documents/{did}/comments")
def create_comment(did: int, body: CommentCreate,
                   user: DioxycleUser = Depends(track_user),
                   db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    c = svc.add_comment(db, doc, user, body.section_key, body.body,
                        row_index=body.row_index, parent_id=body.parent_id)
    return svc.comment_public(c)


@app.post("/api/documents/{did}/comments/{cid}/resolve")
def resolve_comment(did: int, cid: int, user: DioxycleUser = Depends(track_user),
                    db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    c = svc.resolve_comment(db, doc, user, cid)
    return svc.comment_public(c)


@app.get("/api/documents/{did}/activity")
def document_activity(did: int, limit: int = Query(50, le=200),
                      user: DioxycleUser = Depends(track_user),
                      db: Session = Depends(get_db)):
    svc.get_document_or_404(db, did, user)
    rows = (db.query(ActivityLog).filter(ActivityLog.document_id == did)
            .order_by(ActivityLog.id.desc()).limit(limit).all())
    return [{"actor_email": r.actor_email, "actor_kind": r.actor_kind,
             "action": r.action, "payload": r.payload,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]


@app.post("/api/seed-example")
def seed_example(user: DioxycleUser = Depends(track_user), db: Session = Depends(get_db)):
    t = seed.seed_example(db, owner_email=user.email)
    if t is None:
        raise HTTPException(409, "Example template already exists")
    return {"template_id": t.id}


# ============ static frontend (mounted after all API routes) ============

_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
