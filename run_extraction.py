"""
run_extraction.py
=================
CLI runner to test and verify the drawing ingestion and geometry extraction pipeline.
Supports --debug / -debug mode with millisecond timestamps for each step.

Usage:
  python run_extraction.py inputs/BOF-180-CB_CAD_Diagram_Clean.pdf --debug
"""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
import time

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.part1_ingestion.ingestion_service import ingest_drawing
from app.services.part2_extraction.extraction_service import extract_vessel_geometry


def ts() -> str:
    """Return formatted timestamp with milliseconds."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def log_step(step_name: str, debug: bool, detail: str = ""):
    if debug:
        detail_str = f" - {detail}" if detail else ""
        print(f"[{ts()}] [DEBUG] {step_name}{detail_str}")
    else:
        print(f"[*] {step_name}")


async def process_file(file_path: Path, debug: bool):
    t_start = time.perf_counter()
    print("=" * 70)
    print(f"Refractory Extraction Pipeline Runner")
    print(f"Target File: {file_path}")
    print(f"Debug Mode : {'ON (millisecond timestamps enabled)' if debug else 'OFF'}")
    print("=" * 70)

    if not file_path.exists():
        print(f"Error: File '{file_path}' does not exist.")
        sys.exit(1)

    # Step 1: Read file
    log_step("Step 1: Reading file into memory", debug, f"Size: {file_path.stat().st_size:,} bytes")
    file_bytes = file_path.read_bytes()

    # Step 2: Part 1 Ingestion
    t_ingest_start = time.perf_counter()
    log_step("Step 2: Ingesting drawing (Format detection & rasterization)", debug)
    ingestion_result = await ingest_drawing(file_bytes, file_path.name)
    t_ingest_end = time.perf_counter()
    log_step(
        "Step 2: Ingestion complete",
        debug,
        f"Format: {ingestion_result.format}, Pages: {ingestion_result.page_count}, Time: {t_ingest_end - t_ingest_start:.3f}s"
    )

    # Step 3: Part 2 Extraction
    t_extract_start = time.perf_counter()
    log_step("Step 3: Extracting vessel geometry & OCR text", debug)
    extraction_result = await extract_vessel_geometry(ingestion_result)
    t_extract_end = time.perf_counter()
    log_step(
        "Step 3: Extraction complete",
        debug,
        f"Time: {t_extract_end - t_extract_start:.3f}s"
    )

    g = extraction_result.vessel_geometry
    t_total = time.perf_counter() - t_start

    # Output Summary
    print("\n" + "=" * 70)
    print(f"EXTRACTION SUMMARY - {file_path.name}")
    print("=" * 70)
    print(f"Extraction Confidence : {g.extraction_confidence:.1%}")
    print(f"Vessel Type           : {g.vessel_type}")
    print(f"Drawing Number        : {g.drawing_number or 'N/A'}")
    print(f"Vessel Capacity       : {g.operating_conditions.vessel_capacity_tons or 'N/A'} tons")

    if g.shell.outer_diameter_mm:
        print(f"Outer Diameter (OD)   : {g.shell.outer_diameter_mm.value} {g.shell.outer_diameter_mm.unit.value}")
    if g.shell.inner_diameter_mm:
        print(f"Inner Diameter (ID)   : {g.shell.inner_diameter_mm.value} {g.shell.inner_diameter_mm.unit.value}")
    if g.shell.total_length_mm:
        print(f"Total Height / Length : {g.shell.total_length_mm.value} {g.shell.total_length_mm.unit.value}")

    print(f"Profile Segments      : {len(g.shell.profile.segments)} ({g.shell.profile.extraction_method})")
    print(f"Detected Nozzles      : {len(g.nozzles or [])}")
    print(f"Raw OCR Text Tokens   : {len(g.raw_ocr_texts or [])}")
    print("-" * 70)
    print(f"Total Processing Time : {t_total:.3f} seconds")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Refractory Drawing Extraction CLI")
    parser.add_argument("file", nargs="?", default="inputs/BOF-180-CB_CAD_Diagram_Clean.pdf", help="Path to drawing PDF/DXF/PNG")
    parser.add_argument("--debug", "-debug", action="store_true", help="Enable millisecond timestamped debug logs")
    args = parser.parse_args()

    asyncio.run(process_file(Path(args.file), debug=args.debug))


if __name__ == "__main__":
    main()
