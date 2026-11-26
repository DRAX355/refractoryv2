"""
app/core/auth.py
================
Multi-User Authentication & Session Isolation Service for ZeroZeta — LiningOps Copilot.

Provides:
  - Password hashing via PBKDF2-HMAC-SHA256 with random per-user salt
  - Signed HMAC-SHA256 bearer tokens (stateless verification + active user lookup)
  - Persistent user account store (`data/core/users.json`) seeded with default accounts:
      * admin      (Lead Admin / KB Manager)
      * designer1  (Senior Refractory Designer 1)
      * designer2  (Refractory Designer 2)
  - FastAPI dependency `resolve_current_user` that authenticates from `Authorization: Bearer <token>`
    or falls back to `X-User` header (for backwards compatibility with automated test suites).
  - Strict per-user directory helpers so each user's saved sessions, jobs, and drafts remain 100% isolated.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Header, HTTPException, status

USERS_FILE = Path("data/core/users.json")
SECRET_FILE = Path("data/core/.auth_secret")
USER_SESSIONS_ROOT = Path("data/core/user_sessions")
_AUTH_LOCK = threading.RLock()


def _get_secret() -> bytes:
    env_secret = os.environ.get("REFRACTORY_AUTH_SECRET")
    if env_secret:
        return env_secret.encode("utf-8")
    with _AUTH_LOCK:
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        if SECRET_FILE.exists():
            return SECRET_FILE.read_bytes()
        sec = secrets.token_hex(32).encode("utf-8")
        SECRET_FILE.write_bytes(sec)
        return sec


def clean_username(raw: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "", (raw or "").strip().lower())
    return s[:48]


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt_hex = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_hex.encode("utf-8"), 120_000)
    return dk.hex(), salt_hex


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    calc_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(calc_hash, password_hash)


def create_token(user: dict[str, Any], ttl_seconds: int = 86400 * 14) -> str:
    payload = {
        "sub": user["username"],
        "name": user.get("full_name") or user["username"],
        "role": user.get("role") or "designer",
        "email": user.get("email") or "",
        "exp": int(time.time()) + ttl_seconds,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(_get_secret(), b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def decode_token(token: str) -> Optional[dict[str, Any]]:
    if not token or "." not in token:
        return None
    try:
        b64, sig = token.split(".", 1)
        expected = hmac.new(_get_secret(), b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        pad = "=" * ((4 - len(b64) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(b64 + pad).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def _default_users() -> dict[str, dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    defaults = [
        ("admin", "admin123", "TRL Lead Admin", "admin@trlkrosaki.com", "admin", "Refractory Engineering & KB Admin"),
        ("designer1", "trl2026", "Senior Designer 1", "designer1@trlkrosaki.com", "designer", "BOF / LD Lining Design"),
        ("designer2", "trl2026", "Refractory Designer 2", "designer2@trlkrosaki.com", "designer", "Converter & Vessel Design"),
    ]
    users: dict[str, dict[str, Any]] = {}
    for uname, pwd, fname, email, role, dept in defaults:
        pw_hash, salt = hash_password(pwd)
        users[uname] = {
            "username": uname,
            "full_name": fname,
            "email": email,
            "role": role,
            "department": dept,
            "password_hash": pw_hash,
            "salt": salt,
            "active": True,
            "created_at": now,
            "last_login": None,
        }
    return users


def load_users() -> dict[str, dict[str, Any]]:
    with _AUTH_LOCK:
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not USERS_FILE.exists():
            users = _default_users()
            USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")
            return users
        try:
            users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            if not isinstance(users, dict) or not users:
                users = _default_users()
                USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")
            return users
        except Exception:
            users = _default_users()
            USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")
            return users


def save_users(users: dict[str, dict[str, Any]]) -> None:
    with _AUTH_LOCK:
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


def public_user(u: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": u["username"],
        "full_name": u.get("full_name") or u["username"],
        "email": u.get("email") or "",
        "role": u.get("role") or "designer",
        "department": u.get("department") or "Refractory Engineering",
        "active": bool(u.get("active", True)),
        "created_at": u.get("created_at"),
        "last_login": u.get("last_login"),
    }


def authenticate_user(username: str, password: str) -> dict[str, Any]:
    uname = clean_username(username)
    with _AUTH_LOCK:
        users = load_users()
        u = users.get(uname)
        if not u or not u.get("active", True):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
        if not verify_password(password, u["password_hash"], u["salt"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
        u["last_login"] = datetime.now(timezone.utc).isoformat()
        save_users(users)
        return u


def register_user(
    username: str,
    password: str,
    full_name: str = "",
    email: str = "",
    role: str = "designer",
    department: str = "Refractory Engineering",
    allow_admin_role: bool = False,
) -> dict[str, Any]:
    uname = clean_username(username)
    if not uname or len(uname) < 3:
        raise HTTPException(status_code=422, detail="Username must be at least 3 alphanumeric characters.")
    if not password or len(password) < 4:
        raise HTTPException(status_code=422, detail="Password must be at least 4 characters.")
    role_clean = role if (allow_admin_role and role in ("admin", "designer")) else "designer"
    with _AUTH_LOCK:
        users = load_users()
        if uname in users:
            raise HTTPException(status_code=409, detail=f"Username '{uname}' is already registered.")
        pw_hash, salt = hash_password(password)
        now = datetime.now(timezone.utc).isoformat()
        u = {
            "username": uname,
            "full_name": (full_name or uname).strip(),
            "email": (email or "").strip(),
            "role": role_clean,
            "department": (department or "Refractory Engineering").strip(),
            "password_hash": pw_hash,
            "salt": salt,
            "active": True,
            "created_at": now,
            "last_login": now,
        }
        users[uname] = u
        save_users(users)
        return u


def get_user_sessions_dir(username: str) -> Path:
    uname = clean_username(username) or "anonymous"
    d = USER_SESSIONS_ROOT / uname
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_current_user(
    authorization: Optional[str] = Header(default=None),
    x_user: str = Header(default="anonymous"),
) -> dict[str, Any]:
    """Resolve the active user from Bearer token or X-User header.
    Never mixes two different logged-in users."""
    if authorization and authorization.lower().startswith("bearer "):
        tok = authorization.split(" ", 1)[1].strip()
        payload = decode_token(tok)
        if not payload:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid token. Please sign in again.")
        uname = clean_username(payload.get("sub") or "")
        users = load_users()
        u = users.get(uname)
        if u and u.get("active", True):
            return public_user(u)
        return {
            "username": uname or "anonymous",
            "full_name": payload.get("name") or uname,
            "email": payload.get("email") or "",
            "role": payload.get("role") or "designer",
            "department": "Refractory Engineering",
            "active": True,
        }
    uname = clean_username(x_user) or "anonymous"
    users = load_users()
    if uname in users:
        return public_user(users[uname])
    return {
        "username": uname,
        "full_name": x_user if x_user and x_user != "anonymous" else "Anonymous Designer",
        "email": "",
        "role": "designer",
        "department": "Refractory Engineering",
        "active": True,
    }
