"""DioXengine backend.

Routes by area:
  /health
  /auth/microsoft/login, /auth/microsoft/callback, /auth/dev-login (ALLOW_DEV_LOGIN=1), /me
  /users
  /templates..., /template-versions...
  /projects...
  /documents... (draft, submit, review, comments, activity)
  /mcp + OAuth endpoints (/.well-known/*, /authorize, /oauth/token, /oauth/register)
  static SPA (built frontend)
"""
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Path("./data").mkdir(exist_ok=True)

from fastapi import Body, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.routing import Route as StarletteRoute

from database import Base, SessionLocal, engine, get_db
from models import (
    ActivityLog, Comment, Document, DocumentTypeNode, DocumentVersion, Project,
    ProjectMember, TemplateEdge, TemplateOwner, TemplateUser, TemplateVersion,
    User, WorkflowTemplate,
)
import auth as auth_mod
import doc_service as svc
import seed
from auth import (
    ALLOW_DEV_LOGIN, ALLOWED_DOMAIN, APP_URL, create_jwt, exchange_code_for_token,
    fetch_ms_profile, get_current_user, is_azure_configured,
    microsoft_authorize_url, upsert_user, upsert_user_from_ms,
)
from mcp_server import (
    MCP_CLIENT_ID, MCP_CLIENT_SECRET, MCPAuthMiddleware, create_auth_code,
    create_mcp_token, mcp, validate_auth_code,
)

Base.metadata.create_all(bind=engine)

# ---- MCP wiring ----
_mcp_starlette = mcp.streamable_http_app()
_mcp_asgi_handler = _mcp_starlette.routes[0].endpoint


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        logger.info("MCP session manager started")
        yield


app = FastAPI(title="DioXengine", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)
app.router.routes.insert(0, StarletteRoute("/mcp", endpoint=MCPAuthMiddleware(_mcp_asgi_handler)))


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


class DevLogin(BaseModel):
    email: str
    name: str = ""


# ============ health + auth ============

@app.get("/health")
def health():
    return {"status": "ok", "azure_configured": is_azure_configured(),
            "allow_dev_login": ALLOW_DEV_LOGIN}


def _redirect_uri() -> str:
    return f"{APP_URL.rstrip('/')}/auth/microsoft/callback"


# The MCP authorize flow rides the same Microsoft callback as the web login:
# Claude's OAuth params tunnel through Microsoft's `state`, prefixed "mcp.".
def _encode_mcp_state(redirect_uri: str, state: str, code_challenge: str,
                      code_challenge_method: str) -> str:
    payload = json.dumps({
        "redirect_uri": redirect_uri, "state": state,
        "code_challenge": code_challenge, "code_challenge_method": code_challenge_method,
    }, separators=(",", ":"))
    return "mcp." + base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_mcp_state(s: str) -> Optional[dict]:
    if not s.startswith("mcp."):
        return None
    try:
        raw = s[len("mcp."):]
        raw += "=" * (-len(raw) % 4)
        return json.loads(base64.urlsafe_b64decode(raw))
    except Exception:
        return None


@app.get("/auth/microsoft/login")
def ms_login(redirect_to: str = ""):
    if not is_azure_configured():
        raise HTTPException(500, "Azure OAuth not configured (set AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET)")
    return RedirectResponse(microsoft_authorize_url(_redirect_uri(), redirect_to or "/"))


@app.get("/auth/microsoft/callback")
async def ms_callback(code: str = "", state: str = "", db: Session = Depends(get_db)):
    if not code:
        raise HTTPException(400, "Missing authorization code")
    mcp_params = _decode_mcp_state(state)
    try:
        tok = await exchange_code_for_token(code, _redirect_uri())
        profile = await fetch_ms_profile(tok["access_token"])
        user = upsert_user_from_ms(db, profile)
    except HTTPException as e:
        if mcp_params and mcp_params.get("redirect_uri"):
            sep = "&" if "?" in mcp_params["redirect_uri"] else "?"
            err = urlencode({"error": "access_denied", "error_description": str(e.detail),
                             "state": mcp_params.get("state", "")})
            return RedirectResponse(f"{mcp_params['redirect_uri']}{sep}{err}")
        raise
    except Exception as e:
        logger.exception("OAuth callback failed")
        raise HTTPException(500, f"OAuth callback failed: {e}")

    if mcp_params:
        auth_code = create_auth_code(
            mcp_params.get("code_challenge", ""), mcp_params.get("code_challenge_method", "S256"),
            mcp_params["redirect_uri"], user.id)
        params = {"code": auth_code}
        if mcp_params.get("state"):
            params["state"] = mcp_params["state"]
        sep = "&" if "?" in mcp_params["redirect_uri"] else "?"
        return RedirectResponse(f"{mcp_params['redirect_uri']}{sep}{urlencode(params)}")

    jwt_token = create_jwt(user)
    target = state if state.startswith("/") else "/"
    return RedirectResponse(f"{APP_URL}/#/auth/callback?token={jwt_token}&next={target}")


@app.post("/auth/dev-login")
def dev_login(body: DevLogin, db: Session = Depends(get_db)):
    if not ALLOW_DEV_LOGIN:
        raise HTTPException(403, "Dev login is disabled")
    user = upsert_user(db, body.email, body.name)
    return {"access_token": create_jwt(user), "token_type": "bearer",
            "user": {"id": user.id, "email": user.email, "name": user.name}}


@app.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


@app.get("/users")
def list_users(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Directory of everyone who has signed in (backs owner/member pickers).
    Access can still be granted to an email that hasn't logged in yet."""
    return [{"email": u.email, "name": u.name}
            for u in db.query(User).filter(User.is_active == True).order_by(User.email).all()]


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


@app.get("/templates")
def list_templates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
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


@app.post("/templates")
def create_template(body: TemplateCreate, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    t = WorkflowTemplate(name=body.name, description=body.description, created_by=user.email)
    db.add(t)
    db.flush()
    db.add(TemplateOwner(template_id=t.id, user_email=user.email))
    tv = TemplateVersion(template_id=t.id, version_number=1, status="draft", created_by=user.email)
    db.add(tv)
    db.commit()
    return {"template_id": t.id, "template_version_id": tv.id}


@app.post("/templates/{template_id}/versions")
def new_template_version(template_id: int, user: User = Depends(get_current_user),
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


@app.get("/template-versions/{tvid}")
def get_template_version(tvid: int, user: User = Depends(get_current_user),
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


@app.put("/template-versions/{tvid}")
def update_template_version(tvid: int, body: GraphPayload,
                            user: User = Depends(get_current_user),
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


@app.post("/template-versions/{tvid}/publish")
def publish_template_version(tvid: int, user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)):
    from datetime import datetime
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


@app.delete("/template-versions/{tvid}")
def delete_template_version(tvid: int, user: User = Depends(get_current_user),
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


@app.get("/templates/{tid}/access")
def get_template_access(tid: int, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    is_owner = svc.require_template_access(db, tid, user)
    return {"owners": svc.template_owners(db, tid),
            "users": svc.template_users(db, tid), "is_owner": is_owner}


@app.put("/templates/{tid}/access")
def set_template_access(tid: int, body: AccessUpdate,
                        user: User = Depends(get_current_user),
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

@app.get("/projects")
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
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


@app.post("/projects")
def create_project(body: ProjectCreate, user: User = Depends(get_current_user),
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


@app.get("/projects/{pid}")
def get_project(pid: int, user: User = Depends(get_current_user),
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


@app.delete("/projects/{pid}")
def delete_project(pid: int, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    p = svc.require_project_member(db, pid, user)
    if p.created_by != user.email:
        raise HTTPException(403, "Only the project creator can delete it")
    db.query(ProjectMember).filter_by(project_id=pid).delete()
    db.delete(p)
    db.commit()
    return {"ok": True}


@app.get("/projects/{pid}/members")
def get_project_members(pid: int, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    p = svc.require_project_member(db, pid, user)
    return {"members": svc.project_members(db, pid), "created_by": p.created_by,
            "can_manage_members": p.created_by == user.email}


@app.put("/projects/{pid}/members")
def set_project_members(pid: int, body: MembersUpdate,
                        user: User = Depends(get_current_user),
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

@app.get("/documents/{did}")
def get_document(did: int, user: User = Depends(get_current_user),
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


@app.get("/documents/{did}/versions/{n}")
def get_document_version(did: int, n: int, user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    v = next((v for v in doc.versions if v.version_number == n), None)
    if not v:
        raise HTTPException(404, "Version not found")
    return svc.version_public(v, with_content=True)


@app.put("/documents/{did}/assignments")
def update_assignments(did: int, body: AssignmentsUpdate,
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    doc.author_email = body.author_email.strip()
    doc.reviewer_email = body.reviewer_email.strip()
    doc.receiver_emails = [e.strip() for e in body.receiver_emails if e.strip()]
    db.commit()
    return {"ok": True}


@app.put("/documents/{did}/draft")
def save_draft(did: int, body: DraftUpdate, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    draft = svc.save_draft(db, doc, user, body.content)
    return {"version_number": draft.version_number, "status": draft.status,
            "updated_at": draft.updated_at.isoformat() if draft.updated_at else None}


@app.post("/documents/{did}/submit")
def submit_document(did: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    v = svc.submit(db, doc, user)
    return {"version_number": v.version_number, "status": v.status}


@app.post("/documents/{did}/review")
def review_document(did: int, body: ReviewDecision,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    v = svc.review(db, doc, user, body.decision, body.comment)
    return {"version_number": v.version_number, "status": v.status}


@app.post("/documents/{did}/comments")
def create_comment(did: int, body: CommentCreate,
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    c = svc.add_comment(db, doc, user, body.section_key, body.body,
                        row_index=body.row_index, parent_id=body.parent_id)
    return svc.comment_public(c)


@app.post("/documents/{did}/comments/{cid}/resolve")
def resolve_comment(did: int, cid: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    doc = svc.get_document_or_404(db, did, user)
    c = svc.resolve_comment(db, doc, user, cid)
    return svc.comment_public(c)


@app.get("/documents/{did}/activity")
def document_activity(did: int, limit: int = Query(50, le=200),
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    svc.get_document_or_404(db, did, user)
    rows = (db.query(ActivityLog).filter(ActivityLog.document_id == did)
            .order_by(ActivityLog.id.desc()).limit(limit).all())
    return [{"actor_email": r.actor_email, "actor_kind": r.actor_kind,
             "action": r.action, "payload": r.payload,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]


@app.post("/seed-example")
def seed_example(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = seed.seed_example(db, owner_email=user.email)
    if t is None:
        raise HTTPException(409, "Example template already exists")
    return {"template_id": t.id}


# ============ MCP OAuth endpoints ============

@app.get("/.well-known/oauth-protected-resource")
def oauth_protected_resource(request: Request):
    base_url = str(request.base_url).rstrip("/")
    return {"resource": f"{base_url}/mcp", "authorization_servers": [base_url],
            "bearer_methods_supported": ["header"]}


@app.get("/.well-known/oauth-authorization-server")
def oauth_metadata(request: Request):
    base_url = str(request.base_url).rstrip("/")
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/oauth/register",
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "grant_types_supported": ["authorization_code"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
    }


@app.post("/oauth/register")
def oauth_register():
    return {
        "client_id": MCP_CLIENT_ID,
        "client_secret": MCP_CLIENT_SECRET,
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }


@app.get("/authorize")
def oauth_authorize(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    state: str = Query(""),
    code_challenge: str = Query(""),
    code_challenge_method: str = Query("S256"),
    scope: str = Query(""),
):
    import html as html_lib
    if response_type != "code":
        raise HTTPException(400, "unsupported_response_type")
    if MCP_CLIENT_ID and client_id != MCP_CLIENT_ID:
        raise HTTPException(400, "invalid_client_id")
    if not is_azure_configured():
        raise HTTPException(500, "Microsoft OAuth is not configured; the MCP connector requires it")
    ms_state = _encode_mcp_state(redirect_uri, state, code_challenge, code_challenge_method)
    ms_url = html_lib.escape(microsoft_authorize_url(_redirect_uri(), ms_state), quote=True)
    return HTMLResponse(f"""<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect to DioXengine</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; display:flex; justify-content:center; align-items:center; min-height:100vh; margin:0; background:#f8fafc; }}
  .card {{ background:white; border-radius:12px; padding:40px; max-width:420px; width:100%; box-shadow:0 4px 24px rgba(0,0,0,0.08); text-align:center; }}
  h1 {{ font-size:22px; color:#1e293b; margin-bottom:8px; }}
  p {{ color:#64748b; margin-bottom:24px; line-height:1.5; font-size:14px; }}
  .msbtn {{ display:flex; align-items:center; justify-content:center; gap:10px; border:1px solid #cbd5e1; border-radius:8px; padding:12px; font-size:15px; font-weight:500; color:#334155; text-decoration:none; }}
  .msbtn:hover {{ background:#f8fafc; }}
</style></head><body>
<div class="card">
  <h1>Connect Claude to DioXengine</h1>
  <p>Sign in with your <strong>@{ALLOWED_DOMAIN}</strong> Microsoft account to let Claude read and edit engineering documents on your behalf.</p>
  <a class="msbtn" href="{ms_url}">
    <svg width="18" height="18" viewBox="0 0 21 21" aria-hidden="true">
      <rect x="1" y="1" width="9" height="9" fill="#f25022"/>
      <rect x="11" y="1" width="9" height="9" fill="#7fba00"/>
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef"/>
      <rect x="11" y="11" width="9" height="9" fill="#ffb900"/>
    </svg>
    Sign in with Microsoft
  </a>
</div></body></html>""")


@app.post("/oauth/token")
def oauth_token(
    grant_type: str = Form(...),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    code_verifier: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    db: Session = Depends(get_db),
):
    if grant_type != "authorization_code":
        raise HTTPException(400, "unsupported_grant_type")
    if MCP_CLIENT_ID:
        if client_id != MCP_CLIENT_ID or client_secret != MCP_CLIENT_SECRET:
            raise HTTPException(401, "invalid_client")
    auth_data = validate_auth_code(code, code_verifier, redirect_uri)
    if not auth_data:
        raise HTTPException(400, "invalid_grant")
    user = db.query(User).filter(User.id == auth_data["user_id"]).first()
    if not user or not user.is_active:
        raise HTTPException(400, "user_not_active")
    return {"access_token": create_mcp_token(user.id), "token_type": "Bearer",
            "expires_in": 3600}


# ============ static frontend ============

_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/assets", StaticFiles(directory=_static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        index = _static_dir / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse({"detail": "Frontend not built"}, status_code=404)
