"""
app/services/part1_ingestion/ingestion_service.py
==================================================
Part 1 — Orchestrates the full drawing ingestion pipeline:

  1. Save uploaded bytes to disk
  2. Validate the file
  3. Load / convert to numpy image(s)
  4. Return an IngestionResult for downstream use (Part 2)
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
from loguru import logger

from app.core.config import settings
from app.models.vessel_geometry import DrawingFormat, IngestionResponse
from app.services.part1_ingestion.file_handler import load_drawing, detect_format
from app.services.part1_ingestion.drawing_validator import (
    validate_upload,
    validate_images,
    ValidationError,
)


@dataclass
class IngestionResult:
    """
    Internal result object produced by Part 1 (with FR-01.1 source file audit metadata).
    Passed directly into Part 2 (extraction pipeline).
    """
    job_id: str
    file_path: Path
    filename: str
    format: DrawingFormat
    images: list[np.ndarray]          # one image per drawing page
    file_size_bytes: int
    file_hash_sha256: str = ""
    drawing_number: str = ""
    revision: str = ""
    uploaded_by: str = "anonymous"
    user_details: dict[str, Any] = field(default_factory=dict)
    uploaded_at: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.images)

    def to_response(self) -> IngestionResponse:
        return IngestionResponse(
            job_id=self.job_id,
            filename=self.filename,
            format=self.format,
            file_size_bytes=self.file_size_bytes,
            page_count=self.page_count,
            file_hash_sha256=self.file_hash_sha256,
            drawing_number=self.drawing_number,
            revision=self.revision,
            uploaded_by=self.uploaded_by,
            user_details=self.user_details,
            uploaded_at=self.uploaded_at,
            message=(
                "Drawing ingested successfully."
                if not self.warnings
                else f"Ingested with {len(self.warnings)} warning(s)."
            ),
        )


def _infer_drawing_no_and_rev(filename: str) -> tuple[str, str]:
    stem = Path(filename).stem
    rev_match = re.search(r"(?:rev(?:ision)?[\s._-]*)([A-Za-z0-9]+)", stem, re.I)
    rev = rev_match.group(1) if rev_match else "0"
    dwg_no = re.sub(r"[\s]+", "_", stem).upper()
    return dwg_no, rev


async def ingest_drawing(
    file_bytes: bytes,
    filename: str,
    *,
    drawing_number: str = "",
    revision: str = "",
    uploaded_by: str = "anonymous",
    user_details: Optional[dict[str, Any]] = None,
) -> IngestionResult:
    """
    Entry point for Part 1 — accepts raw bytes of an uploaded drawing file
    and records SHA-256 hash, revision, drawing number, and user details (FR-01.1).
    """
    job_id = str(uuid.uuid4())
    file_hash_sha256 = hashlib.sha256(file_bytes).hexdigest()
    inferred_dwg, inferred_rev = _infer_drawing_no_and_rev(filename)
    final_dwg_no = (drawing_number or "").strip() or inferred_dwg
    final_rev = (revision or "").strip() or inferred_rev
    actor = (uploaded_by or "").strip() or "anonymous"
    u_details = dict(user_details or {})
    u_details.setdefault("username", actor)
    uploaded_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    logger.info(
        f"[Part 1] Starting ingestion — job_id={job_id}, file={filename}, "
        f"sha256={file_hash_sha256[:12]}..., dwg={final_dwg_no}, rev={final_rev}, user={actor}"
    )
    from app.utils.step_timer import StepTimer
    timer = StepTimer("Part 1 - Drawing Ingestion", job_id, settings.outputs_dir / job_id)

    # ── Step 1: Save to disk + Write FR-01.1 Source Audit Manifest ────────────
    with timer.step("1.1 Save uploaded file & source audit manifest to disk"):
        save_dir = settings.outputs_dir / job_id
        save_dir.mkdir(parents=True, exist_ok=True)
        file_path = save_dir / filename

        with open(file_path, "wb") as f:
            f.write(file_bytes)

        file_size = len(file_bytes)
        source_manifest = {
            "job_id": job_id,
            "filename": filename,
            "file_size_bytes": file_size,
            "sha256": file_hash_sha256,
            "drawing_number": final_dwg_no,
            "revision": final_rev,
            "uploaded_by": actor,
            "user_details": u_details,
            "uploaded_at": uploaded_at,
        }
        (save_dir / "source_manifest.json").write_text(
            json.dumps(source_manifest, indent=2), encoding="utf-8"
        )
        try:
            from refractory_core import get_core
            get_core().audit.record(
                action="ingestion.upload",
                entity=f"job:{job_id}",
                actor=actor,
                after=source_manifest,
                reason=f"Source file '{filename}' (Dwg: {final_dwg_no}, Rev: {final_rev}) uploaded by {actor}",
            )
        except Exception as audit_exc:
            logger.warning(f"[Part 1] Could not write ingestion audit log: {audit_exc}")

        logger.debug(f"[Part 1] Saved to {file_path} ({file_size} bytes)")

    # ── Step 2: Validate ──────────────────────────────────────────────────────
    with timer.step("1.2 Validate file format & size"):
        validate_upload(file_path, file_size)

    # ── Step 3: Load / Convert ────────────────────────────────────────────────
    with timer.step("1.3 Rasterize / convert drawing to image(s)"):
        images, fmt = load_drawing(file_path)

    # ── Step 4: Image quality warnings ───────────────────────────────────────
    with timer.step("1.4 Validate image quality"):
        warnings = validate_images(images)
        if warnings:
            for w in warnings:
                logger.warning(f"[Part 1] {w}")

    timer.finalize()

    result = IngestionResult(
        job_id=job_id,
        file_path=file_path,
        filename=filename,
        format=fmt,
        images=images,
        file_size_bytes=file_size,
        file_hash_sha256=file_hash_sha256,
        drawing_number=final_dwg_no,
        revision=final_rev,
        uploaded_by=actor,
        user_details=u_details,
        uploaded_at=uploaded_at,
        warnings=warnings,
    )

    logger.info(
        f"[Part 1] Ingestion complete — job_id={job_id}, "
        f"format={fmt}, pages={result.page_count}"
    )
    return result
