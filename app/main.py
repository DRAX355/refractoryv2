"""
app/main.py
============
RefractoryAI — FastAPI Application Entry Point.

Wires together:
  - Logging
  - CORS
  - API routers (Part 1: ingestion, Part 2: extraction)
  - Health check endpoint
  - Global exception handlers

Run with:
  uvicorn app.main:app --reload --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.api.v1.routes_ingestion import router as ingestion_router
from app.api.v1.routes_extraction import router as extraction_router
from app.api.v1.routes_lining import router as lining_router


# ── Application Lifecycle ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    setup_logging()
    logger.info("=" * 60)
    logger.info(f"  RefractoryAI API  v{settings.app_version}")
    logger.info(f"  Environment: {settings.app_env}")
    logger.info(f"  Device: {settings.device}")
    import threading
    def _warmup():
        try:
            logger.info("Pre-warming Grounding DINO model in background...")
            from app.services.part2_extraction.dino_detector import _get_model_and_processor
            _get_model_and_processor()
            logger.info("Grounding DINO pre-warmed and ready in memory.")
        except Exception as e:
            logger.warning(f"Grounding DINO warmup failed: {e}")
    threading.Thread(target=_warmup, daemon=True).start()

    yield
    logger.info("RefractoryAI API shutting down.")


# ── FastAPI App ────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    description=(
        "AI-Driven Refractory Lining Design — Drawing Intelligence API.\n\n"
        "**Part 1**: Upload engineering drawings (PDF, DXF, DWG, Image).\n\n"
        "**Part 2**: Automatically extract structured vessel geometry using "
        "Grounding DINO, Tesseract OCR, and OpenCV."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ── Middleware ─────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global Exception Handlers ──────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled exception on {request.method} {request.url}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "path": str(request.url),
        },
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

app.include_router(ingestion_router, prefix="/api/v1")
app.include_router(extraction_router, prefix="/api/v1")
app.include_router(lining_router, prefix="/api/v1")
app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")
import os
os.makedirs("data/outputs", exist_ok=True)
app.mount("/outputs", StaticFiles(directory="data/outputs"), name="outputs")



# ── Health Check ──────────────────────────────────────────────────────────────

@app.get(
    "/health",
    tags=["System"],
    summary="Health check",
    description="Returns 200 OK when the API is running.",
)
async def health_check() -> dict:
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.app_env,
        "device": settings.device,
    }


@app.get("/", tags=["System"], summary="API root")
async def root() -> dict:
    return {
        "message": "RefractoryAI Drawing Intelligence API",
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health",
        "parts": {
            "part1_ingestion": "/api/v1/ingestion/upload",
            "part2_extraction": "/api/v1/extraction/process",
        },
    }
