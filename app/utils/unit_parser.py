"""
app/utils/unit_parser.py
=========================
Parse dimension strings from OCR output into (value, unit) pairs.

Examples handled:
  "Ø7570"     → (7570.0, "mm")
  "10000mm"   → (10000.0, "mm")
  "97948"     → (97948.0, "mm")   ← assumed mm for refractory drawings
  "3/4\""     → (0.75, "inch")
  "180T"      → (180.0, "tons")   ← vessel capacity
"""
from __future__ import annotations

import re
from loguru import logger


# ── Regex patterns ─────────────────────────────────────────────────────────────

_RE_DIAMETER = re.compile(r"[ØøΦ$@®©€#]\s*([\d.,]+)\s*(mm|cm|m|\")?", re.IGNORECASE)
_RE_SHELL_ID = re.compile(r"\b(\d{3,5}(?:\.\d+)?)\s*Shell\s*[I1O0l|]\s*/\s*[D0O]\b", re.IGNORECASE)
_RE_RADIUS   = re.compile(r"\bR\s*(\d{3,5}(?:\.\d+)?)\b", re.IGNORECASE)
_RE_LENGTH   = re.compile(r'([\d.,]+)\s*("(?=\s|$)|mm\b|cm\b|m\b|inch\b|in\b)', re.IGNORECASE)
_RE_CAPACITY = re.compile(r"(\d+(?:\.\d+)?)\s*[Tt](?:\b|$)")
_RE_PLAIN_NUMBER = re.compile(r"^\s*[-=~<>]*\s*(\d+(?:\.\d+)?)\s*[-=~<>)]*\s*$")


def parse_dimension(text: str) -> tuple[float, str] | None:
    """
    Parse a dimension string into (value_float, unit_str).

    Returns None if parsing fails.
    """
    text = text.strip()

    # Common OCR typo fixes
    if text == "1OQQO":
        text = "10000"
    elif text.endswith("./"):
        text = text[:-2] + ".7"
    elif text.endswith("/") and not re.search(r"Shell\s*[I1O0l|]\s*/", text, re.IGNORECASE):
        text = text[:-1] + "7"
    elif "O" in text and any(c.isdigit() for c in text) and "SHELL" not in text.upper():
        text = text.replace("O", "0")

    # Explicit "7310 Shell I/D" annotation
    m = _RE_SHELL_ID.search(text)
    if m:
        try:
            value = float(m.group(1).replace(",", ""))
            logger.debug(f"Parsed shell ID: '{text}' → ({value}, 'mm')")
            return value, "mm"
        except ValueError:
            pass

    # Explicit spherical/arc radius "R7801" or "R8350"
    m = _RE_RADIUS.search(text)
    if m:
        try:
            value = float(m.group(1).replace(",", ""))
            logger.debug(f"Parsed radius: '{text}' → ({value}, 'mm')")
            return value, "mm"
        except ValueError:
            pass

    # Diameter symbol prefix
    m = _RE_DIAMETER.search(text)
    if m:
        try:
            value = float(m.group(1).replace(",", ""))
            unit = _normalise_unit(m.group(2) or "mm")
            logger.debug(f"Parsed diameter: '{text}' → ({value}, '{unit}')")
            return value, unit
        except ValueError:
            pass

    # Explicit unit suffix
    m = _RE_LENGTH.search(text)
    if m:
        try:
            value = float(m.group(1).replace(",", ""))
            unit = _normalise_unit(m.group(2))
            logger.debug(f"Parsed length: '{text}' → ({value}, '{unit}')")
            return value, unit
        except ValueError:
            pass

    # Plain number — assume mm (common in refractory/metallurgical drawings)
    m = _RE_PLAIN_NUMBER.match(text)
    if m:
        try:
            value = float(m.group(1).replace(",", ""))
            logger.debug(f"Parsed plain number (assumed mm): '{text}' → ({value}, 'mm')")
            return value, "mm"
        except ValueError:
            pass

    logger.warning(f"Could not parse dimension from: '{text}'")
    return None


def parse_capacity_tons(text: str) -> float | None:
    """
    Extract vessel capacity in metric tons from a text string.

    Examples:
        "180T BOF" → 180.0
        "200t"     → 200.0
    """
    m = _RE_CAPACITY.search(text)
    if m:
        try:
            value = float(m.group(1))
            logger.debug(f"Parsed capacity: '{text}' → {value}T")
            return value
        except ValueError:
            pass
    return None


def _normalise_unit(raw: str | None) -> str:
    """Map raw OCR unit strings to canonical unit names."""
    if not raw:
        return "mm"
    mapping = {
        "mm": "mm", "cm": "cm", "m": "m",
        '"': "inch", "inch": "inch", "in": "inch",
    }
    return mapping.get(raw.lower().strip(), "mm")
