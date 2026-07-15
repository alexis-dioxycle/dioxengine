"""SharePoint two-way sync via Microsoft Graph (client credentials).

The SharePoint site is where documents LIVE for the rest of the company:
every project gets a folder (DioXengine/<project>/) holding one file per
document - the rendered .xlsx (any document with table sections), the
rendered .docx (text-only documents), or the uploaded deliverable pushed
as-is (the AutoCAD-P&ID case). The sync is two-way and conservative:

  remote changed, local untouched  -> PULL: parse the file back into a draft
                                      (skipped and reported when the head is
                                      approved/submitted - those are locked)
  local changed, remote untouched  -> PUSH: re-render and upload
  both changed                     -> CONFLICT: touch nothing, report it
  approved documents               -> pushed (final render), never pulled

Auth: Entra app registration with the Sites.Selected APPLICATION permission,
granted on the one site we use (no delegated scopes, no redirect URI).
Config (portal secrets / backend/.env in local dev):
  MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET
  SHAREPOINT_SITE   e.g. "dioxycle.sharepoint.com:/sites/engineering"
Egress needed (manifest): login.microsoftonline.com, graph.microsoft.com.
"""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from models import Attachment, SharePointLink
import doc_service as svc
import renderers

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
BASE_FOLDER = os.getenv("SHAREPOINT_BASE_FOLDER", "DioXengine")
SIMPLE_UPLOAD_MAX = 3_500_000  # Graph's simple-PUT limit is 4 MB; stay under


class GraphError(Exception):
    def __init__(self, status, message):
        super().__init__(f"Graph {status}: {message}")
        self.status = status


def configured() -> bool:
    return all(os.getenv(k) for k in
               ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET", "SHAREPOINT_SITE"))


# ------------------------------------------------------------------- token

_token_cache = {"token": "", "exp": 0.0}


def _token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["exp"] - 120:
        return _token_cache["token"]
    data = urllib.parse.urlencode({
        "client_id": os.environ["MS_CLIENT_ID"],
        "client_secret": os.environ["MS_CLIENT_SECRET"],
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }).encode()
    url = f"https://login.microsoftonline.com/{os.environ['MS_TENANT_ID']}/oauth2/v2.0/token"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        raise GraphError(e.code, f"token request failed: {e.read().decode()[:200]}")
    _token_cache["token"] = payload["access_token"]
    _token_cache["exp"] = time.time() + int(payload.get("expires_in", 3600))
    return _token_cache["token"]


def _req(method: str, path: str, *, data: bytes | None = None,
         content_type: str = "application/json", raw: bool = False,
         extra_headers: dict | None = None):
    url = path if path.startswith("https://") else GRAPH + path
    headers = {"Authorization": "Bearer " + _token()}
    if data is not None:
        headers["Content-Type"] = content_type
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read()
            if raw:
                return body
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        try:
            detail = json.loads(detail).get("error", {}).get("message", detail)
        except Exception:
            pass
        raise GraphError(e.code, detail)


# ------------------------------------------------------------ site / drive

_ids = {"site": "", "drive": ""}


def _site_id() -> str:
    if not _ids["site"]:
        site = os.environ["SHAREPOINT_SITE"]
        _ids["site"] = _req("GET", f"/sites/{site}")["id"]
    return _ids["site"]


def _drive_id() -> str:
    if not _ids["drive"]:
        _ids["drive"] = _req("GET", f"/sites/{_site_id()}/drive")["id"]
    return _ids["drive"]


def status() -> dict:
    """Config + connectivity check, safe to expose to the UI."""
    if not configured():
        missing = [k for k in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET", "SHAREPOINT_SITE")
                   if not os.getenv(k)]
        return {"configured": False, "detail": f"Missing settings: {', '.join(missing)}"}
    try:
        site = _req("GET", f"/sites/{os.environ['SHAREPOINT_SITE']}")
        return {"configured": True, "ok": True, "site_name": site.get("displayName"),
                "web_url": site.get("webUrl")}
    except GraphError as e:
        hint = ("the site exists but the app has no Sites.Selected grant on it yet"
                if e.status == 403 else str(e))
        return {"configured": True, "ok": False, "status": e.status, "detail": hint}


# ------------------------------------------------------------------ files

def _enc_path(path: str) -> str:
    return urllib.parse.quote(path)


def _ensure_folder(path: str):
    """Create every segment of `path` under the drive root (idempotent)."""
    parent = ""
    for seg in path.split("/"):
        target = "/items/root/children" if not parent else f"/items/root:/{_enc_path(parent)}:/children"
        try:
            _req("POST", f"/drives/{_drive_id()}{target}",
                 data=json.dumps({"name": seg, "folder": {},
                                  "@microsoft.graph.conflictBehavior": "fail"}).encode())
        except GraphError as e:
            if e.status != 409:  # nameAlreadyExists is the happy path
                raise
        parent = f"{parent}/{seg}" if parent else seg


def _upload(path: str, data: bytes) -> dict:
    drive = _drive_id()
    if len(data) <= SIMPLE_UPLOAD_MAX:
        return _req("PUT", f"/drives/{drive}/items/root:/{_enc_path(path)}:/content",
                    data=data, content_type="application/octet-stream")
    session = _req("POST", f"/drives/{drive}/items/root:/{_enc_path(path)}:/createUploadSession",
                   data=json.dumps({"item": {"@microsoft.graph.conflictBehavior": "replace"}}).encode())
    url = session["uploadUrl"]
    chunk = 3_276_800  # multiple of 320 KiB as Graph requires
    item = {}
    for off in range(0, len(data), chunk):
        part = data[off:off + chunk]
        item = _req("PUT", url, data=part, content_type="application/octet-stream",
                    extra_headers={"Content-Range": f"bytes {off}-{off + len(part) - 1}/{len(data)}"})
    return item


def _get_item(item_id: str) -> dict:
    return _req("GET", f"/drives/{_drive_id()}/items/{item_id}")


def _download(item_id: str) -> bytes:
    return _req("GET", f"/drives/{_drive_id()}/items/{item_id}/content", raw=True)


def folder_web_url(path: str) -> str:
    try:
        return _req("GET", f"/drives/{_drive_id()}/items/root:/{_enc_path(path)}").get("webUrl", "")
    except GraphError:
        return ""


# ------------------------------------------------------------------- sync

def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|#%]', "_", name).strip() or "file"


def _representation(db, doc):
    """(kind, file_name, attachment | None) - what this document IS on the
    site: the deliverable file as-is, else .xlsx when there are tables,
    else .docx."""
    deliv = (db.query(Attachment).filter_by(document_id=doc.id, kind="deliverable")
             .order_by(Attachment.id.desc()).first())
    if deliv:
        return "attachment", _safe(deliv.filename), deliv
    base = _safe(f"{doc.project.name}-{doc.node.node_key.upper()}").replace(" ", "_")
    sections = (doc.node.content_schema or {}).get("sections", [])
    if any(s.get("type") == "table" for s in sections):
        return "xlsx", f"{base}.xlsx", None
    return "docx", f"{base}.docx", None


def _local_stamp(kind, head, att) -> str:
    if kind == "attachment":
        return f"att{att.id}:{att.size_bytes}"
    if not head:
        return "empty"
    return f"v{head.version_number}:{head.updated_at.isoformat() if head.updated_at else ''}"


def _render(kind, doc, head, att) -> bytes:
    if kind == "attachment":
        return att.data
    if kind == "xlsx":
        return renderers.render_xlsx(doc, head)
    return renderers.render_docx(doc, head)


def sync_document(db, doc, me, folder: str) -> dict:
    """One document against its SharePoint file. Returns a report row."""
    head = svc.latest_version(doc)
    kind, file_name, att = _representation(db, doc)
    link = db.get(SharePointLink, doc.id)
    name = doc.node.name
    local_stamp = _local_stamp(kind, head, att)
    local_changed = (not link) or link.pushed_stamp != local_stamp or link.file_name != file_name

    remote_changed = False
    if link and link.file_name == file_name:
        try:
            item = _get_item(link.drive_item_id)
            remote_changed = item.get("eTag", "") != link.etag
        except GraphError as e:
            if e.status == 404:  # file deleted on SharePoint -> re-push
                link = None
                local_changed = True
            else:
                raise

    # ---- pull: remote moved, local didn't
    if link and remote_changed and not local_changed:
        locked = head and head.status in ("approved", "superseded", "submitted") \
            and kind != "attachment"
        if locked:
            return {"document": name, "action": "locked",
                    "detail": f"changed on SharePoint but rev {head.version_number} is "
                              f"{head.status} - approved documents are not pulled"}
        data = _download(link.drive_item_id)
        modifier = (item.get("lastModifiedBy", {}).get("user", {}) or {}).get("email", "")
        if kind == "attachment":
            att.data = data
            att.size_bytes = len(data)
            svc.log(db, document=doc, actor_email=modifier or me.email, actor_kind="user",
                    action="sharepoint_pull", payload={"filename": file_name})
        else:
            parse = renderers.parse_xlsx if kind == "xlsx" else renderers.parse_docx
            content = parse(data, doc.node.content_schema or {})
            draft = svc.save_draft(db, doc, me, content, actor_kind="user")
            svc.log(db, document=doc, actor_email=modifier or me.email, actor_kind="user",
                    action="sharepoint_pull", payload={"filename": file_name})
            head = draft
        link.etag = item.get("eTag", "")
        link.pushed_stamp = _local_stamp(kind, head, att)
        link.last_pulled_at = datetime.utcnow()
        db.commit()
        return {"document": name, "action": "pulled",
                "detail": f"updated from {file_name}" + (f" (edited by {modifier})" if modifier else "")}

    # ---- conflict: both sides moved - touch nothing
    if link and remote_changed and local_changed:
        return {"document": name, "action": "conflict",
                "detail": f"{file_name} changed on SharePoint AND in the app since the "
                          "last sync - resolve in the app, then sync again to push"}

    # ---- push: local moved (or never pushed)
    if local_changed:
        if kind != "attachment" and not (head and head.content):
            return {"document": name, "action": "skipped", "detail": "no content yet"}
        data = _render(kind, doc, head, att)
        item = _upload(f"{folder}/{file_name}", data)
        if link is None:
            link = db.get(SharePointLink, doc.id)  # re-check after 404 reset
        if link is None:
            link = SharePointLink(document_id=doc.id, kind=kind, file_name=file_name,
                                  drive_item_id=item["id"])
            db.add(link)
        link.kind = kind
        link.attachment_id = att.id if att else None
        link.file_name = file_name
        link.folder_path = folder
        link.drive_item_id = item["id"]
        link.etag = item.get("eTag", "")
        link.web_url = item.get("webUrl", "")
        link.pushed_stamp = local_stamp
        link.last_pushed_at = datetime.utcnow()
        svc.log(db, document=doc, actor_email=me.email, actor_kind="user",
                action="sharepoint_push", payload={"filename": file_name})
        db.commit()
        return {"document": name, "action": "pushed", "detail": file_name,
                "web_url": link.web_url}

    return {"document": name, "action": "up_to_date", "detail": file_name}


def sync_project(db, project, me) -> dict:
    """Sync every document of the project against DioXengine/<project>/."""
    if not configured():
        return {"ok": False, "error": status()["detail"]}
    folder = f"{BASE_FOLDER}/{_safe(project.name)}"
    _ensure_folder(folder)
    report = []
    for doc in sorted(project.documents, key=lambda d: d.node_id):
        try:
            report.append(sync_document(db, doc, me, folder))
        except Exception as e:  # one bad document must not sink the others
            db.rollback()
            detail = getattr(e, "detail", None) or str(e)
            report.append({"document": doc.node.name, "action": "error", "detail": str(detail)})
    return {"ok": True, "folder": folder, "folder_url": folder_web_url(folder),
            "report": report}
