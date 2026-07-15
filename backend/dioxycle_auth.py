"""Dioxycle portal auth contract.

The portal authenticates the user upstream and injects two headers on
every request:
  - X-Dioxycle-User:      JSON {"id", "email", "name", "role"}
  - X-Dioxycle-Signature: HMAC-SHA256 hex digest of the raw user header,
                          keyed with the DIOXYCLE_AUTH_SECRET env var.

Apps never authenticate users themselves; they only verify the signature.
Use as a FastAPI dependency:  user: DioxycleUser = Depends(current_user)
"""

import hashlib
import hmac
import json
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass
class DioxycleUser:
    id: str
    email: str
    name: str
    role: str


def _secret() -> bytes:
    return os.environ.get("DIOXYCLE_AUTH_SECRET", "").encode()


def current_user(
    x_dioxycle_user: str | None = Header(default=None),
    x_dioxycle_signature: str | None = Header(default=None),
) -> DioxycleUser:
    if not x_dioxycle_user or not x_dioxycle_signature:
        raise HTTPException(status_code=401, detail="Missing Dioxycle identity headers")

    expected = hmac.new(_secret(), x_dioxycle_user.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_dioxycle_signature.strip().lower()):
        raise HTTPException(status_code=401, detail="Invalid identity signature")

    try:
        payload = json.loads(x_dioxycle_user)
        return DioxycleUser(
            id=str(payload["id"]),
            email=payload["email"],
            name=payload.get("name", ""),
            role=payload.get("role", ""),
        )
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=401, detail="Malformed identity header")
