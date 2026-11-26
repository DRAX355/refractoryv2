"""
app/api/v1/routes_core.py
=========================
HTTP face of the `refractory_core` library: editable formulas, locked properties, audit log, presets.
The actor for the audit trail is taken from the `X-User` header (put real auth in front of this).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from app.core.auth import get_user_sessions_dir, resolve_current_user
from refractory_core import FormulaError, ImmutablePropertyError, ShapeLibraryValidationError, get_core

router = APIRouter(prefix="/core", tags=["Core - formulas, properties, audit, presets"])


class ExprBody(BaseModel):
    expression: str
    reason: str = ""
    sample: Optional[dict[str, float]] = None


class ValueBody(BaseModel):
    value: float
    reason: str


class GradeBody(BaseModel):
    code: str
    generic_class: str
    chemistry: dict[str, float] = {}
    bulk_density: float
    notes: str = ""
    reason: str = ""


class PresetUse(BaseModel):
    kind: str
    context: dict = {}
    settings: dict


class PresetDecision(BaseModel):
    edits: Optional[dict] = None
    context: Optional[dict] = None
    reason: str = ""


def _bad(e: Exception, code: int = 422):
    raise HTTPException(code, str(e))


# ---------------- formulas ----------------
@router.get("/formulas")
def list_formulas(advanced: bool = True):
    return get_core().formulas.list(include_advanced=advanced)


@router.get("/formulas/{fid}")
def get_formula(fid: str):
    try:
        return get_core().formulas.get(fid)
    except KeyError as e:
        _bad(e, 404)


@router.post("/formulas/{fid}/validate")
def validate_formula(fid: str, body: ExprBody):
    return get_core().formulas.validate(fid, body.expression, body.sample)


@router.post("/formulas/{fid}/evaluate")
def evaluate_formula(fid: str, variables: dict[str, float] = Body(...)):
    try:
        return {"result": get_core().formulas.evaluate(fid, variables)}
    except KeyError as e:
        _bad(e, 404)
    except FormulaError as e:
        _bad(e)


@router.put("/formulas/{fid}")
def update_formula(fid: str, body: ExprBody, x_user: str = Header("anonymous")):
    try:
        return get_core().formulas.set_expression(fid, body.expression, actor=x_user, reason=body.reason)
    except KeyError as e:
        _bad(e, 404)
    except FormulaError as e:
        _bad(e)


@router.post("/formulas/{fid}/reset")
def reset_formula(fid: str, body: dict = Body(default={}), x_user: str = Header("anonymous")):
    try:
        return get_core().formulas.reset(fid, actor=x_user, reason=body.get("reason", ""))
    except KeyError as e:
        _bad(e, 404)


# ---------------- properties / grades ----------------
@router.get("/properties")
def list_properties(category: Optional[str] = None):
    return get_core().properties.list(category)


@router.put("/properties/{pid}")
def update_property(pid: str, body: ValueBody, x_user: str = Header("anonymous")):
    try:
        return get_core().properties.set(pid, body.value, actor=x_user, reason=body.reason)
    except KeyError as e:
        _bad(e, 404)
    except ImmutablePropertyError as e:
        _bad(e, 403)
    except ValueError as e:
        _bad(e)


@router.get("/grades")
def list_grades():
    return list(get_core().properties.grades.values())


@router.post("/grades")
def register_grade(body: GradeBody, x_user: str = Header("anonymous")):
    try:
        return get_core().properties.register_grade(body.code, generic_class=body.generic_class, chemistry=body.chemistry,
                                                    bulk_density=body.bulk_density, notes=body.notes,
                                                    actor=x_user, reason=body.reason)
    except ImmutablePropertyError as e:
        _bad(e, 409)
    except ValueError as e:
        _bad(e)


@router.patch("/grades/{code}")
def update_grade(code: str, fields: dict = Body(...), x_user: str = Header("anonymous")):
    reason = str(fields.pop("reason", ""))
    try:
        return get_core().properties.update_grade(code, actor=x_user, reason=reason, **fields)
    except KeyError as e:
        _bad(e, 404)
    except ImmutablePropertyError as e:
        _bad(e, 403)


# ---------------- audit ----------------
@router.get("/audit")
def audit_entries(entity: Optional[str] = None, action: Optional[str] = None, actor: Optional[str] = None,
                  limit: int = Query(200, le=5000)):
    return get_core().audit.entries(entity=entity, action=action, actor=actor, limit=limit)


@router.get("/audit/verify")
def audit_verify():
    return get_core().audit.verify()


# ---------------- presets (pre-populate) ----------------
@router.get("/presets/suggest")
def suggest(kind: str, context: str = "{}", top_k: int = 3):
    try:
        ctx = json.loads(context)
    except json.JSONDecodeError:
        _bad(ValueError("context must be JSON"))
    return get_core().presets.suggest(kind, ctx, top_k)


@router.post("/presets/use")
def record_use(body: PresetUse, x_user: str = Header("anonymous")):
    return get_core().presets.record_use(body.kind, body.context, body.settings, actor=x_user)


@router.post("/presets/{pid}/confirm")
def confirm(pid: str, body: PresetDecision, x_user: str = Header("anonymous")):
    try:
        return get_core().presets.confirm(pid, actor=x_user, edits=body.edits, context=body.context)
    except KeyError as e:
        _bad(e, 404)


@router.post("/presets/{pid}/reject")
def reject(pid: str, body: PresetDecision, x_user: str = Header("anonymous")):
    get_core().presets.reject(pid, actor=x_user, reason=body.reason)
    return {"ok": True}


# ---------------- server-side job sessions (client-server sync) ----------------
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SESSIONS_DIR = Path("data/core/sessions")


def _safe_session_id(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (raw or "").strip()).strip("_.")
    if not cleaned:
        raise HTTPException(422, "Invalid session ID")
    return cleaned[:120]


@router.get("/sessions")
def list_server_sessions(all_users: bool = False, user: dict = Depends(resolve_current_user)):
    """Return ONLY the current user's saved sessions (strict multi-user isolation).
    Admins may optionally pass ?all_users=true to audit across users."""
    uname = user["username"]
    dirs = [(uname, get_user_sessions_dir(uname))]
    if all_users and user.get("role") == "admin":
        root = get_user_sessions_dir(uname).parent
        if root.exists():
            dirs = [(d.name, d) for d in sorted(root.iterdir()) if d.is_dir()]
    items = []
    for owner_name, sdir in dirs:
        for p in sorted(sdir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                pay = data.get("payload") if isinstance(data.get("payload"), dict) else data
                st = pay.get("state") if isinstance(pay.get("state"), dict) else {}
                raw_job = st.get("job")
                job = raw_job if isinstance(raw_job, dict) else {"ref": str(raw_job)} if raw_job else {}
                sid = data.get("session_id") or data.get("id") or p.stem
                items.append({
                    "id": sid,
                    "session_id": sid,
                    "filename": p.name,
                    "owner": owner_name,
                    "job_ref": data.get("job_ref") or job.get("ref") or sid,
                    "customer": data.get("customer") or job.get("customer") or "",
                    "vessel": data.get("vessel") or job.get("vessel") or "BOF converter",
                    "heat": job.get("heat"),
                    "step": data.get("step", pay.get("step", 0)),
                    "step_name": data.get("step_name") or pay.get("stepName") or "",
                    "saved_at": data.get("savedAt") or pay.get("savedAt") or datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
                    "saved_by": data.get("savedBy") or owner_name,
                    "confirmed_steps": len(pay.get("confirmedSteps") or []),
                })
            except Exception:
                continue
    return items


@router.get("/sessions/{session_id}")
def get_server_session(session_id: str, user: dict = Depends(resolve_current_user)):
    sdir = get_user_sessions_dir(user["username"])
    sid = _safe_session_id(session_id)
    path = sdir / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, f"Session '{sid}' not found in your private workspace ({user['username']})")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"Failed to read session '{sid}': {e}")


@router.post("/sessions")
def save_server_session(payload: dict[str, Any] = Body(...), user: dict = Depends(resolve_current_user)):
    uname = user["username"]
    sdir = get_user_sessions_dir(uname)
    pay = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    st = pay.get("state") if isinstance(pay.get("state"), dict) else {}
    raw_job = st.get("job")
    job = raw_job if isinstance(raw_job, dict) else {"ref": str(raw_job)} if raw_job else {}
    raw_id = payload.get("session_id") or payload.get("sessionId") or payload.get("id") or job.get("ref") or "session"
    sid = _safe_session_id(str(raw_id))
    now_iso = datetime.now(timezone.utc).isoformat()
    payload["session_id"] = sid
    payload["savedAt"] = now_iso
    payload["savedBy"] = uname
    payload["owner"] = uname
    path = sdir / f"{sid}.json"
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    try:
        get_core().audit.record(
            actor=uname,
            action="session.save",
            entity=f"session:{uname}:{sid}",
            before=None,
            after={"owner": uname, "job_ref": payload.get("job_ref") or job.get("ref"), "customer": payload.get("customer") or job.get("customer"), "step": payload.get("step")},
            reason=f"Saved isolated session for user '{uname}' at step {payload.get('step_name') or payload.get('stepName', payload.get('step'))}",
        )
    except Exception:
        pass
    return {"ok": True, "id": sid, "session_id": sid, "owner": uname, "filename": f"{sid}.json", "saved_at": now_iso}


@router.delete("/sessions/{session_id}")
def delete_server_session(session_id: str, user: dict = Depends(resolve_current_user)):
    sdir = get_user_sessions_dir(user["username"])
    sid = _safe_session_id(session_id)
    path = sdir / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, f"Session '{sid}' not found in your private workspace")
    path.unlink()
    return {"ok": True, "id": sid, "owner": user["username"]}


@router.get("/timings")
def get_process_timings():
    """Return all recorded step-by-step backend process timings."""
    from app.core.config import settings
    p = Path(settings.outputs_dir) / "process_timings.json"
    if not p.exists():
        return {"runs": [], "text_file": "/outputs/process_timings.txt"}
    try:
        return {"runs": json.loads(p.read_text(encoding="utf-8")), "text_file": "/outputs/process_timings.txt"}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ---------------- TRL shape library & drawing registry (FR-04.1, FR-04.2, FR-01.3) ----------------
@router.get("/shapes")
def list_shapes(series: Optional[str] = None, shape_family: Optional[str] = None, zone: Optional[str] = None):
    return get_core().shapes.get_library(series=series, shape_family=shape_family, zone=zone)


@router.get("/shapes/{mark:path}")
def get_shape_mark(mark: str):
    try:
        return get_core().shapes.get_mark(mark)
    except KeyError as e:
        _bad(e, 404)


@router.post("/shapes/validate")
def validate_shapes_import(payload: dict[str, Any] = Body(...)):
    return get_core().shapes.validate_import_payload(payload)


@router.post("/shapes/import")
def import_shapes_library(payload: dict[str, Any] = Body(...), x_user: str = Header("anonymous")):
    reason = str(payload.get("reason") or "TRL shape library import")
    try:
        return get_core().shapes.import_shapes(payload, actor=x_user, reason=reason)
    except ShapeLibraryValidationError as e:
        raise HTTPException(422, {"errors": e.errors})


@router.get("/shape-drawings")
def list_registered_shape_drawings(mark: Optional[str] = None):
    return get_core().shapes.list_shape_drawings(mark=mark)


# ---------------- Knowledge Base Versioning & Design Stamping (FR-04.3) ----------------
@router.get("/kb/version")
def get_current_kb_version():
    return get_core().kb_versions.current_info()


@router.post("/kb/snapshot")
def save_kb_snapshot(body: dict[str, Any] = Body(default={}), x_user: str = Header("anonymous")):
    reason = str(body.get("reason") or "Manual Knowledge Base snapshot")
    return get_core().kb_versions.save_snapshot(actor=x_user, reason=reason)


@router.get("/kb/versions")
def list_kb_versions():
    return get_core().kb_versions.list_versions()


@router.get("/kb/versions/{kb_version}")
def get_kb_version_snapshot(kb_version: str):
    try:
        return get_core().kb_versions.get_version(kb_version)
    except KeyError as e:
        _bad(e, 404)


@router.get("/kb/stamps")
def list_design_kb_stamps(mine_only: bool = False, user: dict = Depends(resolve_current_user)):
    stamps = get_core().kb_versions.list_stamps()
    if mine_only and user.get("username") and user.get("username") != "anonymous":
        uname = user["username"]
        return [s for s in stamps if str(s.get("actor") or "").lower() == uname]
    return stamps


@router.post("/kb/stamp")
def stamp_design_kb_version(body: dict[str, Any] = Body(...), x_user: str = Header("anonymous")):
    job_id = str(body.get("job_id") or "").strip()
    if not job_id:
        raise HTTPException(422, "job_id is required to stamp a design")
    return get_core().kb_versions.stamp_design(
        job_id=job_id,
        job_ref=str(body.get("job_ref") or job_id),
        output_dir=body.get("output_dir"),
        actor=x_user,
        source_documents=body.get("source_documents") or [],
    )



