"""
app/services/part2_extraction/dino_detector.py
===============================================
Part 2 — Grounding DINO Element Detection.

Uses the HuggingFace `transformers` implementation of Grounding DINO to
detect drawing elements: title blocks, dimension lines, views, nozzles,
section indicators, and annotations.

Model: IDEA-Research/grounding-dino-base (via HuggingFace Hub)
Hardware: CPU (default) or CUDA GPU — configured in .env (DEVICE=cpu|cuda)

Key concepts:
  - Grounding DINO performs zero-shot open-vocabulary object detection.
  - We supply text prompts describing what to detect in the drawing.
  - Returns bounding boxes + confidence scores for each detected element.

NOTE: torch and transformers are imported lazily inside _get_model_and_processor()
so the rest of the app can start even if PyTorch is not yet installed.
Run: pip install torch torchvision transformers
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np
from loguru import logger

from app.core.config import settings
from app.models.vessel_geometry import BoundingBox
from app.utils.image_utils import numpy_to_pil


# ── Prompt definitions ─────────────────────────────────────────────────────────
# These text prompts guide DINO to detect specific drawing elements.
# Separating categories with ". " is required by Grounding DINO.

DRAWING_PROMPTS = (
    "title block. "
    "dimension line. "
    "section view. "
    "front elevation view. "
    "nozzle opening. "
    "tap hole. "
    "charge pad. "
    "oxygen lance port. "
    "trunnion ring. "
    "vessel shell. "
    "arrow annotation. "
    "scale indicator. "
    "north arrow"
)

CONFIDENCE_THRESHOLD = 0.25   # minimum detection confidence to keep


import threading
import time

# ── Model loading (thread-safe cached singleton) ──────────────────────────────

_model_lock = threading.Lock()
_cached_model_and_proc: tuple[Any, Any] | None = None


def _get_model_and_processor() -> tuple[Any, Any]:
    """
    Load Grounding DINO model and processor once and cache them.
    Thread-safe to prevent race conditions during warmup and early requests.
    """
    global _cached_model_and_proc
    if _cached_model_and_proc is not None:
        return _cached_model_and_proc

    with _model_lock:
        if _cached_model_and_proc is not None:
            return _cached_model_and_proc

        logger.info(
            f"[DINO] Loading model: {settings.grounding_dino_model} "
            f"on device={settings.device}"
        )
        try:
            import torch  # lazy import
            from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        except ImportError:
            raise RuntimeError(
                "torch/transformers not installed. "
                "Run: pip install torch torchvision transformers"
            )

        processor = AutoProcessor.from_pretrained(settings.grounding_dino_model)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(
            settings.grounding_dino_model
        )
        model = model.to(settings.device)
        model.eval()

        logger.info("[DINO] Model loaded and ready.")
        _cached_model_and_proc = (model, processor)
        return _cached_model_and_proc


# ── Public API ─────────────────────────────────────────────────────────────────

def detect_drawing_elements(
    image_bgr: np.ndarray,
    prompts: str = DRAWING_PROMPTS,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> list[BoundingBox]:
    """
    Run Grounding DINO on an engineering drawing image.

    Args:
        image_bgr:  OpenCV BGR numpy array.
        prompts:    Space-separated text prompt for element types to detect.
        threshold:  Minimum confidence score to include a detection.

    Returns:
        List of BoundingBox objects with labels and pixel coordinates.
    """
    import cv2
    model, processor = _get_model_and_processor()

    h, w = image_bgr.shape[:2]
    # Downscale for DINO on CPU: 800px provides optimal speed and accuracy
    max_dino_dim = 800
    if max(h, w) > max_dino_dim:
        scale = max_dino_dim / max(h, w)
        dw, dh = int(w * scale), int(h * scale)
        dino_bgr = cv2.resize(image_bgr, (dw, dh), interpolation=cv2.INTER_AREA)
    else:
        scale = 1.0
        dw, dh = w, h
        dino_bgr = image_bgr

    pil_image = numpy_to_pil(dino_bgr)
    logger.info(f"[DINO] Running detection on {dw}x{dh} (original {w}x{h})...")
    t0 = time.time()

    inputs = processor(
        images=pil_image,
        text=prompts,
        return_tensors="pt",
    )
    inputs = {k: v.to(settings.device) for k, v in inputs.items()}

    import torch  # lazy import — only needed when actually running DINO
    with torch.no_grad():
        outputs = model(**inputs)

    logger.info(f"[DINO] PyTorch inference complete in {time.time() - t0:.1f}s. Post-processing...")

    # Post-process: convert to (boxes, scores, labels)
    results = processor.post_process_grounded_object_detection(
        outputs,
        inputs["input_ids"],
        target_sizes=[(dh, dw)],
    )[0]

    detections: list[BoundingBox] = []
    for box, score, label in zip(
        results["boxes"], results["scores"], results["labels"]
    ):
        score_val = float(score)
        if score_val < threshold:
            continue
            
        raw_box = box.tolist()
        x_min = raw_box[0] / scale
        y_min = raw_box[1] / scale
        x_max = raw_box[2] / scale
        y_max = raw_box[3] / scale
        bbox = BoundingBox(
            x_min=round(x_min, 1),
            y_min=round(y_min, 1),
            x_max=round(x_max, 1),
            y_max=round(y_max, 1),
            label=label,
            confidence=round(score_val, 3),
        )
        detections.append(bbox)
        logger.debug(
            f"[DINO] Detected '{label}' — conf={score:.3f} "
            f"at [{x_min:.0f},{y_min:.0f},{x_max:.0f},{y_max:.0f}]"
        )

    logger.info(
        f"[DINO] Detection complete in {time.time() - t0:.1f}s — {len(detections)} elements found "
        f"(threshold={threshold})"
    )
    return detections


def get_view_regions(detections: list[BoundingBox]) -> list[BoundingBox]:
    """Filter detections to only major view regions (elevation/section)."""
    view_labels = {"section view", "front elevation view"}
    return [d for d in detections if d.label.lower() in view_labels]


def get_nozzle_detections(detections: list[BoundingBox]) -> list[BoundingBox]:
    """Filter detections to nozzle/opening elements."""
    nozzle_labels = {"nozzle opening", "tap hole", "charge pad", "oxygen lance port"}
    return [d for d in detections if d.label.lower() in nozzle_labels]
