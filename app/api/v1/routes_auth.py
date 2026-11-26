"""
app/api/v1/routes_auth.py
=========================
Multi-User Authentication & Account Management API endpoints:
  - POST /api/v1/auth/login          -> Authenticate & return Bearer token + user profile
  - POST /api/v1/auth/register       -> Self-register a new designer account & return Bearer token
  - GET  /api/v1/auth/me             -> Verify token & return current user profile
  - GET  /api/v1/auth/users          -> List users (Admin view)
  - POST /api/v1/auth/users          -> Admin: create user with custom role (admin / designer)
  - DELETE /api/v1/auth/users/{user} -> Admin: delete or deactivate user account
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.auth import (
    authenticate_user,
    clean_username,
    create_token,
    get_user_sessions_dir,
    hash_password,
    load_users,
    public_user,
    register_user,
    resolve_current_user,
    save_users,
)
from refractory_core import get_core

router = APIRouter(prefix="/auth", tags=["Authentication & Multi-User Accounts"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    full_name: str = ""
    email: str = ""
    department: str = "Refractory Engineering"
    role: str = "designer"


class PasswordChangeRequest(BaseModel):
    new_password: str


@router.post("/login", summary="Sign in with username and password")
def login(body: LoginRequest) -> dict:
    u = authenticate_user(body.username, body.password)
    token = create_token(u)
    pub = public_user(u)
    try:
        get_core().audit.record("auth.login", f"user:{u['username']}", actor=u["username"], meta={"role": u.get("role")})
    except Exception:
        pass
    return {"ok": True, "token": token, "user": pub}


@router.post("/register", status_code=status.HTTP_201_CREATED, summary="Create a new isolated designer account")
def register(body: RegisterRequest) -> dict:
    u = register_user(
        username=body.username,
        password=body.password,
        full_name=body.full_name,
        email=body.email,
        department=body.department,
        role="designer",
        allow_admin_role=False,
    )
    token = create_token(u)
    pub = public_user(u)
    try:
        get_core().audit.record("auth.register", f"user:{u['username']}", actor=u["username"], meta={"role": u.get("role")})
    except Exception:
        pass
    return {"ok": True, "token": token, "user": pub}


@router.get("/me", summary="Current authenticated user profile & workspace isolation summary")
def get_me(user: dict = Depends(resolve_current_user)) -> dict:
    sdir = get_user_sessions_dir(user["username"])
    session_count = len(list(sdir.glob("*.json")))
    return {"ok": True, "user": user, "private_sessions_count": session_count}


@router.get("/users", summary="List registered accounts (Admin or workspace directory)")
def list_accounts(user: dict = Depends(resolve_current_user)) -> list[dict]:
    users = load_users()
    out = []
    for uname, u in sorted(users.items()):
        p = public_user(u)
        sdir = get_user_sessions_dir(uname)
        p["session_count"] = len(list(sdir.glob("*.json")))
        out.append(p)
    return out


@router.post("/users", status_code=status.HTTP_201_CREATED, summary="Admin: create a user account with specific role")
def admin_create_user(body: RegisterRequest, user: dict = Depends(resolve_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only Admin users can create accounts with custom roles.")
    u = register_user(
        username=body.username,
        password=body.password,
        full_name=body.full_name,
        email=body.email,
        department=body.department,
        role=body.role,
        allow_admin_role=True,
    )
    try:
        get_core().audit.record("auth.admin_create_user", f"user:{u['username']}", actor=user["username"], meta={"role": u.get("role")})
    except Exception:
        pass
    return {"ok": True, "user": public_user(u)}


@router.put("/users/{target_username}/password", summary="Change password (self or Admin)")
def change_password(target_username: str, body: PasswordChangeRequest, user: dict = Depends(resolve_current_user)) -> dict:
    tname = clean_username(target_username)
    if user.get("role") != "admin" and user.get("username") != tname:
        raise HTTPException(status_code=403, detail="You can only change your own password.")
    if not body.new_password or len(body.new_password) < 4:
        raise HTTPException(status_code=422, detail="Password must be at least 4 characters.")
    users = load_users()
    if tname not in users:
        raise HTTPException(status_code=404, detail=f"User '{tname}' not found.")
    pw_hash, salt = hash_password(body.new_password)
    users[tname]["password_hash"] = pw_hash
    users[tname]["salt"] = salt
    save_users(users)
    return {"ok": True, "username": tname}


@router.delete("/users/{target_username}", summary="Admin: delete user account")
def delete_account(target_username: str, user: dict = Depends(resolve_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only Admin users can remove accounts.")
    tname = clean_username(target_username)
    if tname == "admin":
        raise HTTPException(status_code=400, detail="The primary 'admin' account cannot be deleted.")
    users = load_users()
    if tname not in users:
        raise HTTPException(status_code=404, detail=f"User '{tname}' not found.")
    del users[tname]
    save_users(users)
    return {"ok": True, "deleted": tname}
