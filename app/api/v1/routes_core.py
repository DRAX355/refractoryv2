"""
app/api/v1/routes_core.py
=========================
HTTP face of the `refractory_core` library: editable formulas, locked properties, audit log, presets.
The actor for the audit trail is taken from the `X-User` header (put real auth in front of this).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query
from pydantic import BaseModel

from refractory_core import FormulaError, ImmutablePropertyError, get_core

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
def list_server_sessions():
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(SESSIONS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            st = data.get("state") or {}
            job = st.get("job") or {}
            items.append({
                "id": p.stem,
                "filename": p.name,
                "job_ref": job.get("ref") or p.stem,
                "customer": job.get("customer") or "",
                "vessel": job.get("vessel") or "BOF converter",
                "heat": job.get("heat"),
                "step": data.get("step", 0),
                "step_name": data.get("stepName") or "",
                "saved_at": data.get("savedAt") or datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
                "saved_by": data.get("savedBy") or "designer",
                "confirmed_steps": len(data.get("confirmedSteps") or []),
            })
        except Exception:
            continue
    return items


@router.get("/sessions/{session_id}")
def get_server_session(session_id: str):
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    sid = _safe_session_id(session_id)
    path = SESSIONS_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, f"Session '{sid}' not found on server")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"Failed to read session '{sid}': {e}")


@router.post("/sessions")
def save_server_session(payload: dict[str, Any] = Body(...), x_user: str = Header("designer")):
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    st = payload.get("state") or {}
    job = st.get("job") or {}
    raw_id = payload.get("sessionId") or job.get("ref") or "session"
    sid = _safe_session_id(str(raw_id))
    payload["savedAt"] = datetime.now(timezone.utc).isoformat()
    payload["savedBy"] = x_user
    path = SESSIONS_DIR / f"{sid}.json"
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    try:
        get_core().audit.record(
            actor=x_user,
            action="session.save",
            entity=f"session:{sid}",
            before=None,
            after={"job_ref": job.get("ref"), "customer": job.get("customer"), "step": payload.get("step")},
            reason=f"Saved session at step {payload.get('stepName', payload.get('step'))}",
        )
    except Exception:
        pass
    return {"ok": True, "id": sid, "filename": f"{sid}.json", "saved_at": payload["savedAt"]}


@router.delete("/sessions/{session_id}")
def delete_server_session(session_id: str, x_user: str = Header("designer")):
    sid = _safe_session_id(session_id)
    path = SESSIONS_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, f"Session '{sid}' not found")
    path.unlink()
    return {"ok": True, "id": sid}


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

