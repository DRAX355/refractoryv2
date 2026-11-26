"""
app/api/v1/routes_ingestion.py
================================
Part 1 — REST API routes for drawing upload and ingestion.

Endpoint:
  POST /api/v1/ingestion/upload
    - Accepts multipart/form-data with a drawing file
    - Returns: IngestionResponse (job_id, format, page_count, etc.)
    - The job_id is used to chain into Part 2 extraction

  GET /api/v1/ingestion/{job_id}/status
    - Check the status of an ingestion job
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.config import settings
from app.models.vessel_geometry import DrawingFormat, IngestionResponse
from app.services.part1_ingestion.drawing_validator import ValidationError
from app.services.part1_ingestion.file_handler import get_dwg_converter_status
from app.services.part1_ingestion.ingestion_service import ingest_drawing

router = APIRouter(
    prefix="/ingestion",
    tags=["Part 1 — Drawing Ingestion"],
)


@router.post(
    "/upload",
    response_model=IngestionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an engineering drawing (with FR-01.1 SHA-256 hash & audit metadata)",
    description=(
        "Upload a naked-shell engineering drawing in PDF, DXF, DWG, PNG, JPG, or TIFF format. "
        "The file is hashed (SHA-256), recorded in the server audit trail with revision, drawing number, "
        "and user details, validated, and converted to images ready for Part 2 extraction."
    ),
)
async def upload_drawing(
    file: UploadFile = File(
        ...,
        description="Engineering drawing file (PDF, DXF, DWG, PNG, JPG, TIFF)",
    ),
    vessel_type_hint: str = Form(
        default="BOF",
        description="Expected vessel type — used as a hint for downstream processing",
    ),
    drawing_number: str = Form(
        default="",
        description="Source drawing number (optional; inferred from filename if blank)",
    ),
    revision: str = Form(
        default="",
        description="Drawing revision (optional; inferred from filename or '0' if blank)",
    ),
    user_name: str = Form(
        default="",
        description="Name of the user uploading the source file",
    ),
    user_email: str = Form(
        default="",
        description="Email of the user uploading the source file",
    ),
    user_role: str = Form(
        default="designer",
        description="Role of the user uploading the source file",
    ),
    x_user: str = Header(default="anonymous"),
) -> IngestionResponse:
    """
    Upload a drawing file and run Part 1 ingestion.

    Returns job_id and SHA-256 source metadata which can be used to trigger Part 2 extraction via
    `POST /api/v1/extraction/process/{job_id}`.
    """
    actor = (user_name or x_user or "anonymous").strip()
    logger.info(f"Upload request — filename={file.filename}, content_type={file.content_type}, user={actor}")

    # Read uploaded bytes
    file_bytes = await file.read()

    if len(file_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    try:
        result = await ingest_drawing(
            file_bytes=file_bytes,
            filename=file.filename or "drawing_upload",
            drawing_number=drawing_number,
            revision=revision,
            uploaded_by=actor,
            user_details={
                "username": actor,
                "email": user_email,
                "role": user_role,
                "vessel_type_hint": vessel_type_hint,
            },
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except RuntimeError as exc:
        logger.exception(f"Ingestion failed for '{file.filename}'")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Drawing ingestion failed: {exc}",
        )

    return result.to_response()


@router.post(
    "/shape-drawing",
    status_code=status.HTTP_201_CREATED,
    summary="Upload and register an individual shape drawing against brick mark(s)",
)
async def upload_and_register_shape_drawing(
    file: UploadFile = File(..., description="Individual shape drawing file (PDF, DXF, DWG, PNG, JPG)"),
    marks: str = Form(..., description="Comma-separated brick marks this drawing defines (e.g. 'SH-1,SH-2' or 'TYP1')"),
    drawing_number: str = Form(default="", description="Shape drawing number"),
    revision: str = Form(default="0", description="Shape drawing revision"),
    notes: str = Form(default="", description="Optional notes"),
    x_user: str = Header(default="anonymous"),
) -> dict:
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded shape drawing file is empty.")
    mark_list = [m.strip() for m in marks.split(",") if m.strip()]
    if not mark_list:
        raise HTTPException(status_code=422, detail="At least one brick mark must be provided in 'marks'.")

    sha256_hash = hashlib.sha256(file_bytes).hexdigest()
    fname = file.filename or "shape_drawing"
    shape_dir = settings.outputs_dir / "_shape_drawings" / sha256_hash[:12]
    shape_dir.mkdir(parents=True, exist_ok=True)
    saved_path = shape_dir / fname
    saved_path.write_bytes(file_bytes)

    from refractory_core import get_core
    reg = get_core().shapes.register_shape_drawing(
        mark_list,
        filename=fname,
        file_hash_sha256=sha256_hash,
        drawing_number=drawing_number or Path(fname).stem,
        revision=revision or "0",
        file_path=str(saved_path),
        actor=x_user,
        notes=notes,
    )
    return reg


@router.get(
    "/dwg-support",
    summary="Check ODA / LibreDWG converter status on the server",
)
async def check_dwg_converter() -> dict:
    return get_dwg_converter_status()


@router.get(
    "/{job_id}/status",
    summary="Check ingestion job status & source manifest",
    description="Returns whether the job output exists on disk along with its source file SHA-256 audit manifest.",
)
async def get_job_status(job_id: str) -> JSONResponse:
    """Check whether a given job_id has been processed."""
    job_dir = settings.outputs_dir / job_id
    if not job_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    geometry_file = job_dir / "vessel_geometry.json"
    manifest_file = job_dir / "source_manifest.json"
    source_manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else None
    return JSONResponse(content={
        "job_id": job_id,
        "ingestion_complete": True,
        "extraction_complete": geometry_file.exists(),
        "output_path": str(geometry_file) if geometry_file.exists() else None,
        "source_manifest": source_manifest,
    })
