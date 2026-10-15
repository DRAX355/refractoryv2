"""
app/api/v1/routes_lining.py
===========================
Part 3 - Lining Designer (lining GA, brick shapes, layers / brick layout, nozzles).

  POST /api/v1/lining/generate
    Body: the values the user confirmed in the wizard - vessel profile (from Part 2 extraction,
          confirmed at G1), design (layers / bands / sectors from the concept, G3), nozzles,
          brick settings and (optionally) the brick library.
    Starts the generation in the background and returns at once: {"job_id", "status": "running"}.
    Input errors are returned straight away (422).

  GET /api/v1/lining/jobs/{job_id}
    Progress of a generation: {"status": running|done|error, "stage", "percent", "elapsed_s",
    "result" (when done), "errors" (when failed)}. The UI polls this every 2 s.

  GET /api/v1/lining/library
    The brick library used when the request does not carry one.
"""
from __future__ import annotations

import datetime
import threading
import time
import traceback
import uuid
from typing import Any

import ezdxf
from fastapi import APIRouter, Body, HTTPException, status
from loguru import logger

from app.core.config import settings
from app.services.part3_lining.lining.adapter import build_engine_inputs
from app.services.part3_lining.service import LiningInputError, generate, load_library, safe_name

router = APIRouter(prefix="/lining", tags=["Part 3 — Lining Designer"])

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()

if tuple(int(x) for x in ezdxf.__version__.split(".")[:2]) < (1, 4):
    logger.warning(f"[Part 3] ezdxf {ezdxf.__version__} is installed - drawing previews will be VERY slow "
                   f"(minutes). Run: pip install -U \"ezdxf>=1.4\"")


def _update(job_id: str, **kw) -> None:
    with _LOCK:
        _JOBS[job_id].update(kw)


def _run(job_id: str, payload: dict, out_dir, url_prefix: str) -> None:
    t0 = time.time()

    def progress(stage: str, pct: int) -> None:
        logger.info(f"[Part 3] {pct:>3}% {stage}")
        _update(job_id, stage=stage, percent=pct, elapsed_s=round(time.time() - t0, 1))

    try:
        result = generate(payload, out_dir, url_prefix, progress)
        _update(job_id, status="done", stage="Done", percent=100, elapsed_s=round(time.time() - t0, 1), result=result)
        logger.info(f"[Part 3] Done — {len(result['files'])} files, {len(result['flags'])} flags, "
                    f"{time.time() - t0:.0f} s — {out_dir}")
    except LiningInputError as exc:
        logger.warning(f"[Part 3] Input rejected: {exc.errors}")
        with open("failed_payload.json", "w") as _f: import json; json.dump(payload, _f)
        _update(job_id, status="error", errors=exc.errors, elapsed_s=round(time.time() - t0, 1))
    except Exception as exc:                                   # never leave the UI waiting
        logger.exception("[Part 3] Lining generation failed")
        _update(job_id, status="error", errors=[f"{type(exc).__name__}: {exc}"],
                trace=traceback.format_exc()[-2000:], elapsed_s=round(time.time() - t0, 1))


@router.post("/generate", summary="Start lining, brick and layer drawing generation (background job)")
async def generate_lining(payload: dict[str, Any] = Body(...)) -> dict:
    if not isinstance(payload.get("vessel"), dict) or not isinstance(payload.get("design"), dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"errors": ["Request needs 'vessel' and 'design' objects."]})
    try:                                                       # fast input check -> immediate answer
        build_engine_inputs(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"errors": [str(exc)]})

    job = safe_name(str(payload.get("job_id") or payload.get("job") or "lining"))
    run = "lining_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    out_dir = settings.outputs_dir / job / run
    url_prefix = f"/outputs/{job}/{run}"
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {"job_id": job_id, "status": "running", "stage": "Queued", "percent": 0, "elapsed_s": 0,
                         "out_dir": str(out_dir), "started": datetime.datetime.now().isoformat(timespec="seconds")}
    logger.info(f"[Part 3] Lining generation started — job={job}, id={job_id}, out={out_dir} (ezdxf {ezdxf.__version__})")
    threading.Thread(target=_run, args=(job_id, payload, out_dir, url_prefix), daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@router.get("/jobs/{job_id}", summary="Progress / result of a lining generation")
async def get_job(job_id: str) -> dict:
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            raise HTTPException(status_code=404, detail={"errors": [
                "Generation job not found (the backend was restarted). Click Generate again."]})
        return dict(j)


@router.get("/library", summary="Brick library used by the lining designer")
async def get_library() -> dict:
    return load_library()
