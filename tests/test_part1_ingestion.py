"""
tests/test_part1_ingestion.py
==============================
Unit tests for Part 1 — Drawing Ingestion.

Tests:
  - File format detection
  - Validation rules (size, extension, empty file)
  - Image loading from PNG
  - Ingestion service integration
  - API endpoint via TestClient

Run with:
  pytest tests/test_part1_ingestion.py -v
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.models.vessel_geometry import DrawingFormat
from app.services.part1_ingestion.file_handler import detect_format
from app.services.part1_ingestion.drawing_validator import (
    validate_upload,
    ValidationError,
)
from app.services.part1_ingestion.ingestion_service import ingest_drawing

client = TestClient(app)


# ── Fixtures ────────────────────────────────────────────────────────────────────

def make_png_bytes(width: int = 1200, height: int = 900) -> bytes:
    """Generate a synthetic white PNG image as bytes."""
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def sample_png_bytes() -> bytes:
    return make_png_bytes()


@pytest.fixture
def tiny_png_bytes() -> bytes:
    """Low-resolution image — should produce a validation warning."""
    return make_png_bytes(400, 300)


# ── Format Detection Tests ────────────────────────────────────────────────────

class TestFormatDetection:
    def test_pdf_detected(self, tmp_path):
        p = tmp_path / "drawing.pdf"
        p.touch()
        assert detect_format(p) == DrawingFormat.PDF

    def test_dxf_detected(self, tmp_path):
        p = tmp_path / "vessel.dxf"
        p.touch()
        assert detect_format(p) == DrawingFormat.DXF

    def test_png_detected(self, tmp_path):
        p = tmp_path / "sketch.png"
        p.touch()
        assert detect_format(p) == DrawingFormat.PNG

    def test_jpg_detected(self, tmp_path):
        p = tmp_path / "photo.JPG"
        p.touch()
        assert detect_format(p) == DrawingFormat.JPG

    def test_unknown_returns_unknown(self, tmp_path):
        p = tmp_path / "data.xyz"
        p.touch()
        assert detect_format(p) == DrawingFormat.UNKNOWN


# ── Validation Tests ──────────────────────────────────────────────────────────

class TestValidation:
    def test_empty_file_rejected(self, tmp_path):
        p = tmp_path / "empty.png"
        p.write_bytes(b"")
        with pytest.raises(ValidationError, match="empty"):
            validate_upload(p, file_size_bytes=0)

    def test_unsupported_extension_rejected(self, tmp_path):
        p = tmp_path / "model.stl"
        p.write_bytes(b"solid ...")
        with pytest.raises(ValidationError, match="Unsupported"):
            validate_upload(p, file_size_bytes=100)

    def test_oversized_file_rejected(self, tmp_path):
        p = tmp_path / "large.png"
        p.write_bytes(b"x" * 100)
        with pytest.raises(ValidationError, match="exceeds"):
            validate_upload(p, file_size_bytes=200 * 1024 * 1024)  # 200 MB

    def test_valid_png_passes(self, tmp_path, sample_png_bytes):
        p = tmp_path / "drawing.png"
        p.write_bytes(sample_png_bytes)
        validate_upload(p, file_size_bytes=len(sample_png_bytes))  # should not raise


# ── Ingestion Service Tests ───────────────────────────────────────────────────

class TestIngestionService:
    @pytest.mark.asyncio
    async def test_ingest_png(self, sample_png_bytes):
        result = await ingest_drawing(
            file_bytes=sample_png_bytes,
            filename="test_drawing.png",
        )
        assert result.job_id
        assert result.format == DrawingFormat.PNG
        assert result.page_count == 1
        assert len(result.images) == 1
        assert isinstance(result.images[0], np.ndarray)

    @pytest.mark.asyncio
    async def test_ingest_low_res_has_warning(self, tiny_png_bytes):
        result = await ingest_drawing(
            file_bytes=tiny_png_bytes,
            filename="low_res.png",
        )
        assert any("resolution" in w.lower() for w in result.warnings)


# ── API Endpoint Tests ────────────────────────────────────────────────────────

class TestIngestionAPI:
    def test_upload_png_returns_201(self, sample_png_bytes):
        response = client.post(
            "/api/v1/ingestion/upload",
            files={"file": ("test.png", sample_png_bytes, "image/png")},
        )
        assert response.status_code == 201
        data = response.json()
        assert "job_id" in data
        assert data["format"] == "png"
        assert data["page_count"] == 1

    def test_upload_empty_file_returns_400(self):
        response = client.post(
            "/api/v1/ingestion/upload",
            files={"file": ("empty.png", b"", "image/png")},
        )
        assert response.status_code == 400

    def test_upload_invalid_extension_returns_422(self):
        response = client.post(
            "/api/v1/ingestion/upload",
            files={"file": ("model.stl", b"solid ...", "application/octet-stream")},
        )
        assert response.status_code == 422

    def test_health_check(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_job_status_not_found(self):
        response = client.get("/api/v1/ingestion/nonexistent-job-id/status")
        assert response.status_code == 404

    def test_fr01_1_upload_records_sha256_revision_drawing_no_and_user_details(self, sample_png_bytes):
        import hashlib
        expected_hash = hashlib.sha256(sample_png_bytes).hexdigest()
        response = client.post(
            "/api/v1/ingestion/upload",
            files={"file": ("180MT_BOF_Shell_RevB.png", sample_png_bytes, "image/png")},
            data={
                "drawing_number": "TRLK-180MT-SHELL-01",
                "revision": "Rev B",
                "user_name": "Darshan C M",
                "user_email": "darshan@trlkrosaki.com",
                "user_role": "Lead Engineer",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["file_hash_sha256"] == expected_hash
        assert data["drawing_number"] == "TRLK-180MT-SHELL-01"
        assert data["revision"] == "Rev B"
        assert data["uploaded_by"] == "Darshan C M"
        assert data["user_details"]["email"] == "darshan@trlkrosaki.com"

        # Check job status includes source_manifest
        st_resp = client.get(f"/api/v1/ingestion/{data['job_id']}/status")
        assert st_resp.status_code == 200
        manifest = st_resp.json()["source_manifest"]
        assert manifest["sha256"] == expected_hash
        assert manifest["drawing_number"] == "TRLK-180MT-SHELL-01"

    def test_fr01_3_register_shape_drawing_against_brick_marks(self, sample_png_bytes):
        import hashlib
        expected_hash = hashlib.sha256(sample_png_bytes).hexdigest()
        resp = client.post(
            "/api/v1/ingestion/shape-drawing",
            files={"file": ("SH1_SH2_Shape_Sheet.png", sample_png_bytes, "image/png")},
            data={
                "marks": "SH-1, SH-2, TYP1",
                "drawing_number": "TRLK-BOF-SH-01",
                "revision": "08.10.2016",
                "notes": "Bottom working wedge & barrel safety shape sheet",
            },
            headers={"X-User": "Darshan"},
        )
        assert resp.status_code == 201
        rjson = resp.json()
        assert rjson["registered_marks"] == ["SH-1", "SH-2", "TYP1"]
        assert rjson["drawing"]["sha256"] == expected_hash
        assert rjson["drawing"]["drawing_number"] == "TRLK-BOF-SH-01"

    def test_fr01_5_dwg_support_endpoint(self):
        resp = client.get("/api/v1/ingestion/dwg-support")
        assert resp.status_code == 200
        data = resp.json()
        assert "dwg_supported" in data
        assert "install_hint" in data

