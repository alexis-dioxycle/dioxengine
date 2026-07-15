"""Deterministic tools attached to document types.

A tool is a small Dioxycle Apps endpoint (pressure drop, line sizing,
equipment calc...) declared on a template node:

    {"name": "pressure_drop", "description": "ΔP for a straight line segment",
     "url": "https://apps.dioxycle.com/_apps/line-sizer/api/pressure-drop",
     "method": "GET",           # GET (query params) or POST (JSON body)
     "params": "fluid, flow_kgh, diameter_mm, length_m"}   # doc for the caller

The assistant sees a document's tools in get_document and calls them through
use_document_tool — the HTTP request is made by THIS backend, so the portal's
egress rules apply and the caller never needs the target app's credentials.
Hosts are restricted by the TOOL_ALLOWED_HOSTS allowlist; deterministic calcs
stay in apps ("tools"), per the 2026-07-06 meeting decision.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from fastapi import HTTPException

from database import IS_LOCAL_DEV

MAX_RESPONSE_BYTES = 200_000


def _allowed_hosts() -> set[str]:
    hosts = {h.strip().lower() for h in
             os.getenv("TOOL_ALLOWED_HOSTS", "apps.dioxycle.com").split(",") if h.strip()}
    if IS_LOCAL_DEV:
        hosts |= {"localhost", "127.0.0.1"}
    return hosts


def validate_tools(tools: list) -> list:
    if not isinstance(tools, list):
        raise HTTPException(422, "tools must be a list")
    clean, seen = [], set()
    for t in tools:
        if not isinstance(t, dict):
            raise HTTPException(422, "Each tool must be an object")
        name = (t.get("name") or "").strip()
        url = (t.get("url") or "").strip()
        method = (t.get("method") or "GET").strip().upper()
        if not name or not url:
            raise HTTPException(422, "Each tool needs a name and a url")
        if name.lower() in seen:
            raise HTTPException(422, f"Duplicate tool name '{name}'")
        seen.add(name.lower())
        if method not in ("GET", "POST"):
            raise HTTPException(422, f"Tool '{name}': method must be GET or POST")
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if not url.startswith("https://") and not IS_LOCAL_DEV:
            raise HTTPException(422, f"Tool '{name}': url must be https")
        if host not in _allowed_hosts():
            raise HTTPException(422, f"Tool '{name}': host '{host}' is not in the allowed "
                                     f"list ({', '.join(sorted(_allowed_hosts()))})")
        clean.append({"name": name, "description": (t.get("description") or "").strip(),
                      "url": url, "method": method,
                      "params": (t.get("params") or "").strip()})
    return clean


def call_tool(tool: dict, params: dict) -> dict:
    """Execute one declared tool with the given params. GET -> query string,
    POST -> JSON body. Returns {status, body} with the body parsed as JSON
    when possible."""
    url = tool["url"]
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host not in _allowed_hosts():  # re-check at call time, not just at save
        raise HTTPException(422, f"Tool host '{host}' is not allowed")
    data = None
    headers = {"Accept": "application/json"}
    if tool.get("method", "GET") == "GET":
        if params:
            sep = "&" if urllib.parse.urlparse(url).query else "?"
            url = url + sep + urllib.parse.urlencode(
                {k: v if isinstance(v, str) else json.dumps(v) for k, v in params.items()})
    else:
        data = json.dumps(params or {}).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=tool.get("method", "GET"))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(MAX_RESPONSE_BYTES + 1)
            status = r.status
    except urllib.error.HTTPError as e:
        body = e.read(MAX_RESPONSE_BYTES + 1)
        status = e.code
    except urllib.error.URLError as e:
        raise HTTPException(502, f"Tool call failed: {e.reason}")
    truncated = len(body) > MAX_RESPONSE_BYTES
    text = body[:MAX_RESPONSE_BYTES].decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = text
    return {"status": status, "body": parsed, "truncated": truncated}
