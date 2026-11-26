"""
app/services/part1_ingestion/file_handler.py
=============================================
Part 1 — Input Engineering Drawing: multi-format file loading.

Supported input formats:
  - PDF   → rasterised pages (via pdf2image / poppler)
  - DXF   → parsed geometry + rasterised preview (via ezdxf + matplotlib)
  - DWG   → converted to DXF first (requires LibreCAD / ODA Converter)
  - Image → PNG, JPG, TIFF loaded directly via PIL / OpenCV

Returns a list of numpy images (one per drawing page/view) ready for
downstream processing.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from loguru import logger
from PIL import Image

from app.core.config import settings
from app.models.vessel_geometry import DrawingFormat


# ── Format Detection ───────────────────────────────────────────────────────────

EXTENSION_MAP: dict[str, DrawingFormat] = {
    ".pdf":  DrawingFormat.PDF,
    ".dxf":  DrawingFormat.DXF,
    ".dwg":  DrawingFormat.DWG,
    ".png":  DrawingFormat.PNG,
    ".jpg":  DrawingFormat.JPG,
    ".jpeg": DrawingFormat.JPG,
    ".tif":  DrawingFormat.TIFF,
    ".tiff": DrawingFormat.TIFF,
}


def detect_format(path: Path) -> DrawingFormat:
    """Detect drawing format from file extension."""
    ext = path.suffix.lower()
    fmt = EXTENSION_MAP.get(ext, DrawingFormat.UNKNOWN)
    logger.info(f"Detected format: {fmt} for file: {path.name}")
    return fmt


# ── Public API ─────────────────────────────────────────────────────────────────

def load_drawing(path: Path) -> tuple[list[np.ndarray], DrawingFormat]:
    """
    Load a drawing file and return a list of numpy BGR images (one per page).

    Args:
        path: Path to the uploaded drawing file.

    Returns:
        Tuple of (list_of_images, DrawingFormat)

    Raises:
        ValueError: for unsupported formats.
        RuntimeError: if loading/conversion fails.
    """
    path = Path(path)
    fmt = detect_format(path)

    match fmt:
        case DrawingFormat.PDF:
            images = _load_pdf(path)
        case DrawingFormat.DXF:
            images = _load_dxf(path)
        case DrawingFormat.DWG:
            images = _load_dwg(path)
        case DrawingFormat.PNG | DrawingFormat.JPG | DrawingFormat.TIFF:
            images = _load_image(path)
        case _:
            raise ValueError(
                f"Unsupported drawing format: '{path.suffix}'. "
                f"Supported: PDF, DXF, DWG, PNG, JPG, TIFF"
            )

    logger.info(
        f"Loaded drawing '{path.name}' — format={fmt}, pages={len(images)}"
    )
    return images, fmt


# ── Loaders ────────────────────────────────────────────────────────────────────

def _load_pdf(path: Path) -> list[np.ndarray]:
    """Rasterise PDF pages to images using PyMuPDF (fitz)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install PyMuPDF")

    logger.debug(f"Loading PDF via PyMuPDF: {path}")
    
    images = []
    # Open document
    doc = fitz.open(str(path))
    
    # 250-260 DPI equivalent matrix (1 scale = 72 DPI, 260/72 = 3.6) so stroked CAD text is crisp for OCR
    zoom = 3.6
    mat = fitz.Matrix(zoom, zoom)
    
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat, alpha=False)
        # Convert pixmap to numpy array (RGB)
        img_rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        
        # PyMuPDF might output RGBA or RGB depending on alpha=False
        if pix.n == 4:
            img_rgb = __import__('cv2').cvtColor(img_rgb, __import__('cv2').COLOR_RGBA2RGB)
            
        img_bgr = __import__('cv2').cvtColor(img_rgb, __import__('cv2').COLOR_RGB2BGR)
        images.append(img_bgr)
        logger.debug(f"  Page {i + 1}: shape={img_bgr.shape}")

    doc.close()
    return images


def _load_dxf(path: Path) -> list[np.ndarray]:
    """
    Load a DXF file using ezdxf and render it to a raster image.

    Uses matplotlib backend — produces a high-res PNG of the DXF layout.
    """
    try:
        import ezdxf
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        import matplotlib.pyplot as plt
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend
    except ImportError:
        raise RuntimeError(
            "ezdxf or matplotlib is not installed. "
            "Run: pip install ezdxf matplotlib"
        )

    logger.debug(f"Loading DXF: {path}")
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()

    fig = plt.figure(figsize=(20, 16), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax)
    Frontend(ctx, out).draw_layout(msp)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    fig.savefig(str(tmp_path), format="png", bbox_inches="tight", dpi=150)
    plt.close(fig)

    img_bgr = cv2.imread(str(tmp_path))
    tmp_path.unlink(missing_ok=True)

    if img_bgr is None:
        raise RuntimeError("Failed to render DXF to image.")

    return [img_bgr]


def get_dwg_converter_status() -> dict[str, str | bool | None]:
    """
    Detect installed DWG -> DXF converters on the host / target server (FR-01.5).
    Checks:
      1. ODA File Converter (`ODAFileConverter` / `odafc`)
      2. LibreDWG CLI (`dwg2dxf`) - open-source headless Linux/Windows converter
      3. LibreCAD CLI (`librecad`)
    """
    oda = shutil.which("ODAFileConverter") or shutil.which("odafc")
    for win_cand in (
        r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
        r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
        "/usr/bin/ODAFileConverter",
        "/usr/local/bin/ODAFileConverter",
    ):
        if not oda and Path(win_cand).exists():
            oda = win_cand
    dwg2dxf = shutil.which("dwg2dxf")
    librecad = shutil.which("librecad")
    active = "oda" if oda else ("libredwg" if dwg2dxf else ("librecad" if librecad else None))
    return {
        "dwg_supported": active is not None,
        "active_converter": active,
        "oda_path": oda,
        "libredwg_dwg2dxf_path": dwg2dxf,
        "librecad_path": librecad,
        "install_hint": (
            "Install LibreDWG on Ubuntu/AWS ('sudo apt-get install -y libredwg-tools') "
            "or ODA File Converter ('deploy/install_oda_converter.sh')."
        ),
    }


def _load_dwg(path: Path) -> list[np.ndarray]:
    """
    Convert DWG → DXF using ODA File Converter or LibreDWG (`dwg2dxf` fallback, FR-01.5),
    then load the DXF.
    """
    st = get_dwg_converter_status()
    oda_path = st["oda_path"]
    dwg2dxf_path = st["libredwg_dwg2dxf_path"]

    if not oda_path and not dwg2dxf_path:
        logger.warning(
            "Neither ODA File Converter nor LibreDWG (dwg2dxf) found. DWG → DXF conversion skipped."
        )
        raise RuntimeError(
            "DWG format requires ODA File Converter or LibreDWG (dwg2dxf). "
            "Run deploy/install_oda_converter.sh on the target server."
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_out = Path(tmp_dir)
        if oda_path:
            cmd = [
                str(oda_path),
                str(path.parent),   # input folder
                str(tmp_out),        # output folder
                "ACAD2018",          # output DXF version
                "DXF",               # output type
                "0",                 # recurse
                "1",                 # audit
                str(path.name),      # specific file
            ]
            logger.debug(f"Running ODA converter: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise RuntimeError(f"ODA DWG→DXF conversion failed: {result.stderr}")
        else:
            out_dxf = tmp_out / f"{path.stem}.dxf"
            cmd = [str(dwg2dxf_path), "-o", str(out_dxf), str(path)]
            logger.debug(f"Running LibreDWG converter: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0 and not out_dxf.exists():
                raise RuntimeError(f"LibreDWG DWG→DXF conversion failed: {result.stderr}")

        dxf_files = list(tmp_out.glob("*.dxf"))
        if not dxf_files:
            raise RuntimeError("DWG Converter produced no DXF output.")

        return _load_dxf(dxf_files[0])


def _load_image(path: Path) -> list[np.ndarray]:
    """Load a raster image (PNG/JPG/TIFF) directly via OpenCV."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        # fallback to PIL (handles TIFF multipage, etc.)
        pil_img = Image.open(str(path)).convert("RGB")
        img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    if img is None:
        raise RuntimeError(f"Failed to load image: {path}")

    logger.debug(f"Loaded image: {path.name} — shape={img.shape}")
    return [img]
