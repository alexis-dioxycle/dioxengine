"""Auth — Microsoft OAuth only (same pattern as finance-dioxycle), JWT sessions.

If AZURE_* env vars are set, /auth/microsoft/* in main.py is live and the
Login page shows the Microsoft button (driven by `azure_configured` on
/health). Local dev escape hatch: ALLOW_DEV_LOGIN=1 enables
POST /auth/dev-login {email} → JWT with no password. Strictly off in prod.
"""
import os
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import get_db
from models import User

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me-1234567890")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24 * 7

AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")

APP_URL = os.getenv("APP_URL", "http://localhost:5006")
ALLOWED_DOMAIN = os.getenv("ALLOWED_EMAIL_DOMAIN", "dioxycle.com")
ALLOW_DEV_LOGIN = os.getenv("ALLOW_DEV_LOGIN", "0") == "1"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/dev-login", auto_error=False)


def is_azure_configured() -> bool:
    return all([AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET])


def microsoft_authorize_url(redirect_uri: str, state: str = "") -> str:
    params = {
        "client_id": AZURE_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": "openid profile email User.Read",
        "state": state,
    }
    return f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/authorize?{urlencode(params)}"


async def exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    token_url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": AZURE_CLIENT_ID,
        "client_secret": AZURE_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email User.Read",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data=data)
        resp.raise_for_status()
        return resp.json()


async def fetch_ms_profile(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def upsert_user(db: Session, email: str, name: str = "", ms_oid: str | None = None) -> User:
    email = email.lower().strip()
    if not email:
        raise HTTPException(status_code=400, detail="No email")
    if ALLOWED_DOMAIN and not email.endswith(f"@{ALLOWED_DOMAIN}"):
        raise HTTPException(status_code=403, detail=f"Only @{ALLOWED_DOMAIN} accounts are allowed")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, name=name or email.split("@")[0], ms_oid=ms_oid)
        db.add(user)
    else:
        if ms_oid and not user.ms_oid:
            user.ms_oid = ms_oid
        if name:
            user.name = name
    user.last_login = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return user


def upsert_user_from_ms(db: Session, ms_profile: dict) -> User:
    email = (ms_profile.get("mail") or ms_profile.get("userPrincipalName") or "").lower()
    name = ms_profile.get("displayName") or ""
    return upsert_user(db, email, name, ms_profile.get("id"))


def create_jwt(user: User) -> str:
    payload = {
        "user_id": user.id,
        "email": user.email,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_jwt(token)
    user = db.query(User).filter(User.id == payload.get("user_id")).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="User not found or inactive")
    return user
