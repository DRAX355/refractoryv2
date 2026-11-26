"""
app/services/part2_extraction/ocr_extractor.py
===============================================
Part 2 — Tesseract OCR: Dimension & Text Extraction.

Extracts:
  - Vessel dimensions (diameters, lengths) with unit parsing
  - Operating conditions (vessel capacity, e.g. "180T BOF")
  - Drawing metadata (drawing number, revision, title)
  - Nozzle labels and annotations
  - All raw text strings (for downstream NLP / RAG in Part 3)

Pipeline per image:
  1. Preprocess image (grayscale → denoise → threshold)
  2. Run Tesseract with engineering drawing config
  3. Parse dimension values using unit_parser
  4. Classify text into categories
"""
from __future__ import annotations

import re

import numpy as np
import pytesseract
from loguru import logger

from app.core.config import settings
from app.models.vessel_geometry import (
    Dimension,
    DimensionUnit,
    OperatingConditions,
    VesselType,
)
from app.utils.image_utils import preprocess_for_ocr, scale_image, crop_region
from app.utils.unit_parser import parse_dimension, parse_capacity_tons
from app.models.vessel_geometry import BoundingBox


# ── Tesseract configuration ────────────────────────────────────────────────────

def _configure_tesseract() -> None:
    """Set Tesseract binary path from settings."""
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


# PSM 6  = uniform block of text
# PSM 11 = sparse text (scattered dimension labels on drawings)
# PSM 7  = single text line (good for individual dimension annotations)
# PSM 13 = raw line — no OSD, treat as single line
TESSERACT_CONFIG_SPARSE  = r'--psm 11 -l eng --oem 3'
TESSERACT_CONFIG_BLOCK   = r'--psm 6  -l eng --oem 3'
# Numeric-focused config: whitelist digits, dot, comma, slash, degree, Ø symbol
TESSERACT_CONFIG_NUMERIC = (
    r'--psm 11 -l eng --oem 3 '
    r'-c tessedit_char_whitelist=0123456789.,/\xd8\xf8\u00d8\u00f8\u03a6%-: '
)


# ── Regex patterns for drawing-specific text ────────────────────────────────────

_RE_DRAWING_NUMBER = re.compile(
    r"\b([A-Z]{2,}-[A-Z0-9]+-\d{3,}(?:-[A-Z0-9]+)?)\b"
)
_RE_REVISION = re.compile(r"\bRev(?:ision)?\s*([A-Z0-9]+)\b", re.IGNORECASE)
_RE_VESSEL_TYPE = re.compile(r"\b(BOF|EAF|LADLE|RH\s*DEGASSER)\b", re.IGNORECASE)

NOZZLE_KEYWORDS = {
    "tap hole", "tap pad", "taphole",
    "charge pad", "charge door",
    "oxygen lance", "o2 lance",
    "running pad", "rest area",
    "trunnion",
    "mhp", "multi hole plug", "purging plug", "seating block", "tuyere", "bottom stirring",
}


# ── Public API ─────────────────────────────────────────────────────────────────

class OCRResult:
    """Container for all OCR-extracted data from one drawing image."""

    def __init__(self) -> None:
        self.raw_texts: list[str] = []
        self.dimension_strings: list[str] = []
        self.parsed_dimensions: list[tuple[float, str]] = []  # (value, unit)
        self.drawing_number: str = ""
        self.revision: str = ""
        self.vessel_type_hint: str = ""
        self.operating_conditions: OperatingConditions = OperatingConditions()
        self.nozzle_labels: list[str] = []
        
        # New for DocTR-based spatial masking
        from shapely.geometry import Polygon
        self.text_polygons: list[Polygon] = []


_RE_DIA_SYMBOL_MISREAD = re.compile(r"^[-=~<>\"'“”]*[$@®©€#%OQGq]\s*([1-9]\d{2,4})[-=~<>.,]*$")
_RE_DIA_DIGIT_MISREAD  = re.compile(r"^[-=~<>\"'“”]*[024689]\s*([1-9]\d{2,3})[-=~<>.,]*$")


def _normalise_ocr_line(line: str) -> str | None:
    """
    Clean one Tesseract line using generic typographical and CAD symbol rules
    (zero drawing-specific hardcoded numbers):
      - 'Ø' is often read as a leading symbol ($, @, ®, ©, €, #, %, O, Q, G) or separated digit.
      - 'R<num>}' or 'R <num>' is normalized to 'R<num>'.
      - '<num> Shell 1/0' or '<num> Shell 1/D' is normalized to '<num> Shell I/D'.
      - Tokens with leading zeros ('00001' = mirrored '10000' from a wrong rotation) are dropped.
    """
    line = line.strip()
    if not line:
        return None
    # Normalise '<num> Shell 1/0-' / '<num> Shell 1/D' -> '<num> Shell I/D'
    m_sh = re.search(r"\b(\d{3,5})\s*Shell\s*[I1O0l|]\s*/\s*[D0O]", line, re.IGNORECASE)
    if m_sh:
        return f"{m_sh.group(1)} Shell I/D"
    # Normalise spherical radius callout where trailing '1' is misread as '}' or ']' or 'l' (e.g. 'R780}' -> 'R7801')
    m_rad = re.search(r"\bR\s*(\d{3,5}[}\])l|]?)\b", line)
    if m_rad:
        val = re.sub(r"[}\])l|]$", "1", m_rad.group(1))
        return f"R{val}"
    # Normalise 'Ø' misread as a leading symbol (%, $, @, #, O, Q, G, etc.) on any 3- or 4-digit diameter
    # where a trailing comma/brace is a misread of '1' (e.g. '%119,' -> 'Ø1191')
    m_sym_comma = re.fullmatch(r"[-=~<>\"'“”]*[$@®©€#%OQGq]\s*([1-9]\d{2,3})[,}\])]", line)
    if m_sym_comma:
        return f"Ø{m_sym_comma.group(1)}1"
    m_sym = _RE_DIA_SYMBOL_MISREAD.match(line)
    if m_sym:
        return f"Ø{m_sym.group(1)}"
    # Normalise 'Ø' misread as a leading digit separated by space or on a 4-digit diameter (e.g. '97570' -> 'Ø7570', '2800' when preceded by Ø glyph)
    m_dig_4 = re.fullmatch(r"[-=~<>\"'“”]*[04689]\s*([1-9]\d{3})[-=~<>.,]*", line)
    if m_dig_4:
        return f"Ø{m_dig_4.group(1)}"
    if re.fullmatch(r"0\d{3,}", line):
        return None
    # Strip leading/trailing dimension line dashes/equals around pure numbers ('=598><=' -> '598')
    m_num = re.fullmatch(r"[-=~<>|]+\s*(\d{2,5}(?:\.\d+)?)\s*[-=~<>|)]*", line)
    if m_num:
        return m_num.group(1)
    m_num2 = re.fullmatch(r"(\d{2,5}(?:\.\d+)?)\s*[-=~<>|)]+", line)
    if m_num2:
        return m_num2.group(1)
    return line


def _strip_colored_annotation_lines(image_bgr: np.ndarray) -> np.ndarray:
    """
    Remove colored dimension / extension / centerline strokes (e.g. red and blue CAD layers)
    so they do not merge with black dimension digits when converted to grayscale.
    """
    if image_bgr.ndim != 3 or image_bgr.shape[2] < 3:
        return image_bgr
    b = image_bgr[:, :, 0].astype(np.int16)
    g = image_bgr[:, :, 1].astype(np.int16)
    r = image_bgr[:, :, 2].astype(np.int16)
    sat = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    colored_mask = sat > 35
    if not np.any(colored_mask):
        return image_bgr
    cleaned = image_bgr.copy()
    cleaned[colored_mask] = 255
    return cleaned


def run_tesseract_ocr(image_bgr: np.ndarray) -> OCRResult:
    """
    Run multi-angle Tesseract OCR on a drawing image.
    Includes a color-stripped pass so red/blue CAD dimension lines touching black text
    are removed before grayscale conversion, and avoids 180° upside-down rotations
    that produce mirrored false numbers (e.g. '2186' read upside-down as '9912').
    """
    _configure_tesseract()
    import cv2
    result = OCRResult()

    clean_bgr = _strip_colored_annotation_lines(image_bgr)
    h, w = clean_bgr.shape[:2]
    M_tilt = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), 15.0, 1.0)
    tilted_15 = cv2.warpAffine(clean_bgr, M_tilt, (w, h), borderValue=(255, 255, 255))

    # Color-stripped passes (0 deg horizontal, 270 deg vertical CAD text, +15 deg tilted nozzle/radius text)
    passes = [
        (0, clean_bgr, TESSERACT_CONFIG_SPARSE),
        (270, cv2.rotate(clean_bgr, cv2.ROTATE_90_COUNTERCLOCKWISE), TESSERACT_CONFIG_SPARSE),
        (0, clean_bgr, TESSERACT_CONFIG_BLOCK),
        (270, cv2.rotate(clean_bgr, cv2.ROTATE_90_COUNTERCLOCKWISE), TESSERACT_CONFIG_BLOCK),
        (15, tilted_15, TESSERACT_CONFIG_SPARSE),
    ]

    seen = set()
    for _angle, rimg, cfg in passes:
        gray = cv2.cvtColor(rimg, cv2.COLOR_BGR2GRAY)
        txt = pytesseract.image_to_string(gray, config=cfg)
        for line in txt.splitlines():
            cleaned = _normalise_ocr_line(line)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.raw_texts.append(cleaned)

    _parse_all_texts(result)
    logger.info(
        f"[OCR] Tesseract complete — raw_lines={len(result.raw_texts)}, "
        f"dimensions={len(result.parsed_dimensions)}, "
        f"vessel_type={result.vessel_type_hint or 'unknown'}"
    )
    return result


def run_ocr(
    image_bgr: np.ndarray,
    dino_regions: list[BoundingBox] | None = None,
) -> OCRResult:
    """
    Run full OCR pipeline on a drawing image.
    Uses lightweight multi-angle Tesseract on CPU, and DocTR on CUDA/GPU.
    """
    if getattr(settings, "device", "cpu") != "cuda":
        logger.info("[OCR] Running lightweight multi-angle Tesseract OCR...")
        return run_tesseract_ocr(image_bgr)

    try:
        from app.services.part2_extraction.doctr_extractor import run_doctr_on_image
        import cv2

        result = OCRResult()
        logger.info("[OCR] Running DocTR on drawing image...")
        
        # Downscale image if too large (DocTR is slow on huge images)
        h, w = image_bgr.shape[:2]
        img_for_doctr = image_bgr
        scale = 1.0
        if max(h, w) > 2500:
            scale = 2500 / max(h, w)
            img_for_doctr = cv2.resize(image_bgr, (int(w * scale), int(h * scale)))
            
        doctr_result = run_doctr_on_image(img_for_doctr)
        logger.info(f"[OCR] Pass 1 (horizontal) extracted {len(doctr_result.words)} words. Running Pass 2 (vertical text)...")
        
        from shapely.affinity import scale as shapely_scale
        for word in doctr_result.words:
            poly = word.polygon
            if scale != 1.0:
                poly = shapely_scale(poly, xfact=1/scale, yfact=1/scale, origin=(0,0))
                
            result.text_polygons.append(poly)
            result.raw_texts.append(word.value)
            
        img_rot = cv2.rotate(img_for_doctr, cv2.ROTATE_90_COUNTERCLOCKWISE)
        doctr_rot_result = run_doctr_on_image(img_rot)
        
        orig_w, orig_h = img_for_doctr.shape[1], img_for_doctr.shape[0]
        for word in doctr_rot_result.words:
            mapped_coords = []
            for (x_rot, y_rot) in word.polygon.exterior.coords:
                orig_x = orig_w - y_rot
                orig_y = x_rot
                mapped_coords.append((orig_x, orig_y))
                
            from shapely.geometry import Polygon
            poly = Polygon(mapped_coords)
            if scale != 1.0:
                poly = shapely_scale(poly, xfact=1/scale, yfact=1/scale, origin=(0,0))
                
            result.text_polygons.append(poly)
            result.raw_texts.append(word.value)
            
        _parse_all_texts(result)
        logger.info(
            f"[OCR] DocTR complete — raw_lines={len(result.raw_texts)}, "
            f"dimensions={len(result.parsed_dimensions)}, "
            f"vessel_type={result.vessel_type_hint or 'unknown'}"
        )
        return result
    except Exception as exc:
        logger.warning(f"[OCR] DocTR error ({exc}). Falling back to multi-angle Tesseract...")
        return run_tesseract_ocr(image_bgr)


def _classify_text(line: str, result: "OCRResult") -> None:
    """Classify a single text line into OCRResult fields (drawing num, revision, vessel type, etc.)."""
    if not result.drawing_number:
        m = _RE_DRAWING_NUMBER.search(line)
        if m: result.drawing_number = m.group(1)
    if not result.revision:
        m = _RE_REVISION.search(line)
        if m: result.revision = m.group(0)
    if not result.vessel_type_hint:
        m = _RE_VESSEL_TYPE.search(line)
        if m: result.vessel_type_hint = m.group(0).upper().replace(" ", "_")
    line_lower = line.lower()
    for kw in NOZZLE_KEYWORDS:
        if kw in line_lower and line not in result.nozzle_labels:
            result.nozzle_labels.append(line); break


def _parse_all_texts(result: OCRResult) -> None:
    """Classify and parse all raw text lines."""
    for line in result.raw_texts:

        # Drawing number
        if not result.drawing_number:
            m = _RE_DRAWING_NUMBER.search(line)
            if m:
                result.drawing_number = m.group(1)

        # Revision
        if not result.revision:
            m = _RE_REVISION.search(line)
            if m:
                result.revision = m.group(0)

        # Vessel type
        if not result.vessel_type_hint:
            m = _RE_VESSEL_TYPE.search(line)
            if m:
                result.vessel_type_hint = m.group(0).upper().replace(" ", "_")

        # Capacity (e.g. "180T BOF")
        capacity = parse_capacity_tons(line)
        if capacity is not None and result.operating_conditions.vessel_capacity_tons is None:
            result.operating_conditions.vessel_capacity_tons = capacity
            result.operating_conditions.additional_notes.append(line)

        # Dimensions
        parsed = parse_dimension(line)
        if parsed:
            result.dimension_strings.append(line)
            result.parsed_dimensions.append(parsed)
        else:
            # Handle Tesseract merging multiple vertical dimensions into one horizontal line
            parts = [p for p in line.split() if p]
            if len(parts) > 1:
                for part in parts:
                    norm_part = _normalise_ocr_line(part) or part
                    parsed_part = parse_dimension(norm_part)
                    if parsed_part:
                        result.dimension_strings.append(norm_part)
                        result.parsed_dimensions.append(parsed_part)

        # Nozzle labels
        line_lower = line.lower()
        for kw in NOZZLE_KEYWORDS:
            if kw in line_lower and line not in result.nozzle_labels:
                result.nozzle_labels.append(line)
                break


def make_dimension_model(
    value: float,
    unit_str: str,
    raw_text: str = "",
    confidence: float = 0.8,
) -> Dimension:
    """Helper: build a Dimension model from parsed values."""
    try:
        unit = DimensionUnit(unit_str)
    except ValueError:
        unit = DimensionUnit.UNKNOWN
    return Dimension(value=value, unit=unit, raw_text=raw_text, confidence=confidence)
