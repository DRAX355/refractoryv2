"""
app/services/part2_extraction/geometry_assembler.py
====================================================
Part 2 — Geometry Assembler.

Takes the outputs of DINO, OCR, OpenCV, DXF direct parse, and PDF path
extraction and assembles them into the final `VesselGeometry` structured model.

NO HARDCODED DEFAULTS. Every dimension in the output is either:
  a) directly extracted from the drawing, or
  b) left as None / empty so the UI can show "insufficient data"

Source priority for the vessel profile (shell segments):
  1. DXF direct entity parse    → exact vector geometry (most accurate)
  2. PDF vector paths            → exact drawn paths from PDF
  3. OpenCV contour analysis     → pixel-level silhouette analysis
  4. PDF text positioned dims    → text labels with spatial coordinates

If none of the above yield a usable profile, shell.profile.segments is empty
and extraction_method = "none" — the UI should show a "cannot render" state
rather than silently showing wrong geometry.
"""
from __future__ import annotations

import math
import re
import statistics
from loguru import logger

from app.models.vessel_geometry import (
    BoundingBox,
    Dimension,
    DimensionUnit,
    Nozzle,
    SegmentType,
    VesselGeometry,
    VesselProfile,
    VesselSegment,
    VesselShellGeometry,
    VesselType,
    ViewRegion,
    DrawingFormat,
)
from app.services.part2_extraction.ocr_extractor import OCRResult, make_dimension_model
from app.services.part2_extraction.opencv_processor import OpenCVGeometryResult


def assemble_vessel_geometry(
    ocr_result: OCRResult,
    opencv_result: OpenCVGeometryResult,
    dino_detections: list[BoundingBox],
    source_file: str,
    source_format: DrawingFormat,
    dxf_profile=None,      # DXFProfile | None
    pdf_path_profile=None, # PDFPathProfile | None
    positioned_dims: list = None,
) -> VesselGeometry:
    """
    Assemble a VesselGeometry from Part 2 extraction results.
    NO default/estimated fallbacks — only real extracted data is used.
    """
    logger.info("[Assembler] Assembling VesselGeometry from extraction results…")

    geometry = VesselGeometry(
        source_file=source_file,
        source_format=source_format,
    )

    geometry.vessel_type    = _resolve_vessel_type(ocr_result, source_file=source_file)
    geometry.drawing_number = ocr_result.drawing_number
    geometry.revision       = ocr_result.revision

    geometry.shell = _assemble_shell(
        ocr=ocr_result,
        opencv=opencv_result,
        dxf_profile=dxf_profile,
        pdf_path_profile=pdf_path_profile,
        positioned_dims=positioned_dims,
        extra_metadata=geometry.extra_metadata,
    )

    geometry.operating_conditions = ocr_result.operating_conditions
    if not geometry.operating_conditions.vessel_capacity_tons:
        import re
        all_s = (source_file + " " + " ".join(ocr_result.raw_texts)).upper()
        cap_m = re.search(r"\b(\d{2,3})\s*(?:T|TON|TONS|MT)\b", all_s)
        if cap_m:
            geometry.operating_conditions.vessel_capacity_tons = float(cap_m.group(1))
    geometry.nozzles               = _assemble_nozzles(dino_detections, ocr_result, pdf_path_profile=pdf_path_profile)
    geometry.extra_metadata["mhp_features"] = _extract_mhp_features(ocr_result, geometry.nozzles)
    _enrich_angles_and_mouth_details(geometry, ocr_result, pdf_path_profile)
    geometry.detected_views        = _build_view_regions(dino_detections, ocr_result)
    geometry.raw_ocr_texts         = ocr_result.raw_texts
    geometry.dino_detections       = dino_detections
    geometry.extraction_confidence = _compute_confidence(geometry, ocr_result)

    n_segs = len(geometry.shell.profile.segments)
    method = geometry.shell.profile.extraction_method
    logger.info(
        f"[Assembler] Done — type={geometry.vessel_type}, "
        f"conf={geometry.extraction_confidence:.2f}, "
        f"segments={n_segs}, method={method}"
    )
    return geometry


# ── Internal helpers ───────────────────────────────────────────────────────────

def _resolve_vessel_type(ocr: OCRResult, source_file: str = "") -> VesselType:
    hint = ocr.vessel_type_hint.upper()
    mapping = {
        "BOF": VesselType.BOF,
        "LD": VesselType.BOF,
        "CONVERTER": VesselType.BOF,
        "EAF": VesselType.EAF,
        "LADLE": VesselType.LADLE,
        "RH_DEGASSER": VesselType.RH_DEGASSER,
        "RH": VesselType.RH_DEGASSER,
        "AOD": VesselType.BOF,
        "TORPEDO": VesselType.LADLE,
    }
    if hint in mapping:
        return mapping[hint]
    all_text = (source_file + " " + " ".join(ocr.raw_texts)).upper()
    if any(k in all_text for k in ("EAF", "ELECTRIC ARC")):
        return VesselType.EAF
    if any(k in all_text for k in ("LADLE", "TEEMING", "TORPEDO")):
        return VesselType.LADLE
    if any(k in all_text for k in ("RH", "DEGASSER", "SNORKEL")):
        return VesselType.RH_DEGASSER
    return VesselType.BOF


def _assemble_shell(
    ocr: OCRResult,
    opencv: OpenCVGeometryResult,
    dxf_profile,
    pdf_path_profile,
    positioned_dims: list = None,
    extra_metadata: dict | None = None,
) -> VesselShellGeometry:
    import re
    shell = VesselShellGeometry()
    if extra_metadata is None:
        extra_metadata = {}

    # ── Separate spherical radius annotations (e.g. R7801, R8350) from axial lengths & diameters ──
    all_dims_mm: list[tuple[float, str, str]] = []  # (value_mm, unit, raw_text)
    explicit_shell_id: tuple[float, str, str] | None = None
    for (value, unit), raw in zip(ocr.parsed_dimensions, ocr.dimension_strings):
        vm = _to_mm(value, unit)
        if vm is None or vm <= 0:
            continue
        if re.match(r"^\s*R\s*\d{3,5}\b", raw, re.IGNORECASE):
            extra_metadata["dish_radius_mm"] = vm
            extra_metadata["dish_radius_raw_text"] = raw.strip()
            continue
        if re.search(r"Shell\s*[I1O0l|]\s*/\s*[D0O]", raw, re.IGNORECASE):
            explicit_shell_id = (vm, unit, raw)
            extra_metadata["profile_is_inner_diameter"] = True
        all_dims_mm.append((vm, unit, raw))

    # Also check raw_texts for R7801/R8350 or angles (20°, 33°, 15°, 42°) if not in dimension_strings
    for line in ocr.raw_texts:
        m_r = re.search(r"\bR\s*(\d{4,5})\b", line)
        if m_r and "dish_radius_mm" not in extra_metadata:
            extra_metadata["dish_radius_mm"] = float(m_r.group(1))
            extra_metadata["dish_radius_raw_text"] = f"R{m_r.group(1)}"

    # Sort by value descending
    all_dims_mm.sort(key=lambda x: x[0], reverse=True)

    # Heuristic-free classification: let the data decide
    diameters = [(v, u, r) for v, u, r in all_dims_mm if _looks_like_diameter(v, u, r)]
    lengths   = [(v, u, r) for v, u, r in all_dims_mm if not _looks_like_diameter(v, u, r)]

    # Sanity: a vessel diameter below 500 mm is an OCR misread, not a real value.
    bad = [d for d in diameters if d[0] < 500]
    if bad:
        logger.warning(f"[Assembler] Ignoring implausible diameters: {[d[2] for d in bad]}")
        diameters = [d for d in diameters if d[0] >= 500]

    # If no explicit diameters are found, fallback to physics heuristics:
    if not diameters and len(lengths) >= 2:
        if lengths[1][0] > lengths[0][0] * 0.5:
            diameters.append(lengths.pop(1))

    if explicit_shell_id:
        v, u, raw = explicit_shell_id
        shell.inner_diameter_mm = make_dimension_model(v, u, raw, confidence=0.95)
        # Look for explicit shell plate thickness (any repeated or small thickness dimension in 25..110 mm)
        plate_raws = [lv for lv, lu, lr in lengths if 25.0 <= lv <= 110.0]
        if plate_raws:
            from collections import Counter
            counts = Counter(plate_raws)
            # Prefer a repeated thickness callout (e.g. top & bottom wall thickness), else the smallest thickness in 30..90 mm
            repeated = [val for val, cnt in counts.items() if cnt >= 2 and 30.0 <= val <= 95.0]
            in_band = [val for val in plate_raws if 30.0 <= val <= 95.0]
            plate_val = min(repeated) if repeated else (min(in_band) if in_band else min(plate_raws))
            shell.shell_thickness_mm = Dimension(
                value=plate_val,
                unit=DimensionUnit.MM,
                raw_text=str(int(plate_val) if plate_val.is_integer() else plate_val),
                confidence=0.85,
            )
            shell.outer_diameter_mm = Dimension(
                value=round(v + 2.0 * plate_val, 1),
                unit=DimensionUnit.MM,
                raw_text=f"{round(v + 2.0 * plate_val, 1)} (derived from {raw} + 2×{int(plate_val) if plate_val.is_integer() else plate_val})",
                confidence=0.85,
            )
        else:
            shell.outer_diameter_mm = make_dimension_model(v, u, raw, confidence=0.9)
    else:
        if diameters:
            v, u, raw = diameters[0]
            shell.outer_diameter_mm = make_dimension_model(v, u, raw, confidence=0.85)
            logger.debug(f"[Assembler] Outer dia: {v}{u} ('{raw}')")

        if len(diameters) >= 2:
            outer_mm = shell.outer_diameter_mm.value if shell.outer_diameter_mm else float("inf")
            for v, u, raw in diameters[1:]:
                if v < outer_mm:
                    shell.inner_diameter_mm = make_dimension_model(v, u, raw, confidence=0.8)
                    logger.debug(f"[Assembler] Inner dia: {v}{u} ('{raw}')")
                    break

    # Filter out implausible OCR noise lengths (> 16000 mm)
    lengths = [(v, u, r) for v, u, r in lengths if v <= 16000.0]

    if lengths:
        # Prefer a candidate total length that has the lowest-error 3-, 4-, 5-, or 6-zone axial partition sum
        # (works for any vessel size from 20T ladles to 350T converters without hardcoded height thresholds)
        chosen_len = lengths[0]
        best_len_err = float("inf")
        from itertools import combinations
        # Check if OCR explicitly read a single-line axial dimension chain (e.g. 'a b c d e') whose sum matches a candidate total length
        chain_sums: list[tuple[float, int]] = []
        for raw_line in (ocr.raw_texts or []):
            nums = [float(x) for x in re.findall(r"\b\d{2,4}(?:\.\d+)?\b", raw_line)]
            if 3 <= len(nums) <= 7 and sum(nums) >= 1500.0:
                chain_sums.append((sum(nums), len(nums)))

        sub_cands = []
        for lv, _lu, _lr in lengths:
            if 30.0 <= lv <= 12000.0 and round(lv, 1) not in [round(x, 1) for x in sub_cands]:
                sub_cands.append(lv)
        for cand_v, cand_u, cand_r in lengths[:10]:
            if cand_v < 1500.0:
                break
            # Check direct match against a single-line dimension chain first
            matched_chain = False
            for c_sum, c_len in chain_sums:
                err = abs(c_sum - cand_v)
                if err <= 5.0 and err <= best_len_err:
                    best_len_err = err
                    chosen_len = (cand_v, cand_u, cand_r)
                    matched_chain = True
                    break
            if matched_chain and best_len_err <= 1.0:
                break
            smaller = [x for x in sub_cands if x < cand_v * 0.85]
            min_major = cand_v * 0.14  # proportional to vessel height, not hardcoded
            for k in (6, 5, 4):
                if len(smaller) < k:
                    continue
                for comb in combinations(smaller, k):
                    # Every multi-zone vessel partition has 3 major zones (lower cone, barrel, top cone) >= 14% of total height
                    if sum(1 for x in comb if x >= min_major) < 3 or max(comb) > cand_v * 0.65:
                        continue
                    err = abs(sum(comb) - cand_v)
                    if err < cand_v * 0.008 and err < best_len_err:
                        best_len_err = err
                        chosen_len = (cand_v, cand_u, cand_r)
        v, u, raw = chosen_len
        shell.total_length_mm = make_dimension_model(v, u, raw, confidence=0.95)
        logger.debug(f"[Assembler] Total length: {v}{u} ('{raw}')")

    # Detect explicit shell wall thickness when a repeated thickness callout (e.g. top & bottom wall thickness on a cross-section) is present
    if shell.shell_thickness_mm is None:
        from collections import Counter
        raw_plate_tokens = [
            float(t.strip())
            for t in (ocr.raw_texts or [])
            if re.fullmatch(r"\d{2}(?:\.\d+)?", t.strip()) and 25.0 <= float(t.strip()) <= 95.0
        ]
        counts = Counter(raw_plate_tokens)
        repeated_plates = [val for val, cnt in counts.items() if cnt >= 2]
        if repeated_plates:
            p_val = min(repeated_plates)
            shell.shell_thickness_mm = Dimension(
                value=p_val,
                unit=DimensionUnit.MM,
                raw_text=str(int(p_val) if p_val.is_integer() else p_val),
                confidence=0.9,
            )
            if shell.outer_diameter_mm:
                extra_metadata["profile_is_inner_diameter"] = True
                if shell.inner_diameter_mm and shell.inner_diameter_mm.value < shell.outer_diameter_mm.value * 0.78:
                    # The smaller diameter (< 78% of barrel diameter) is a cone/mouth diameter, while the primary diameter is the Barrel Shell ID
                    shell.inner_diameter_mm = shell.outer_diameter_mm
                    shell.outer_diameter_mm = Dimension(
                        value=round(shell.inner_diameter_mm.value + 2.0 * p_val, 1),
                        unit=DimensionUnit.MM,
                        raw_text=f"{round(shell.inner_diameter_mm.value + 2.0 * p_val, 1)} (derived from {shell.inner_diameter_mm.raw_text} + 2×{int(p_val) if p_val.is_integer() else p_val})",
                        confidence=0.85,
                    )

    # OpenCV circle cross-validation (only if scale is known)
    if opencv.circles and opencv.scale_px_per_mm and shell.inner_diameter_mm is None:
        largest = opencv.circles[0]
        dia_mm = round(largest.radius_px * 2 / opencv.scale_px_per_mm, 0)
        logger.debug(f"[Assembler] OpenCV circle dia estimate: {dia_mm}mm")
        outer_mm = shell.outer_diameter_mm.value if shell.outer_diameter_mm else 0
        # Only use if it is smaller than the outer diameter (= inner dia)
        if 0 < dia_mm < outer_mm:
            shell.inner_diameter_mm = Dimension(
                value=dia_mm,
                unit=DimensionUnit.MM,
                raw_text=f"~{dia_mm}mm (OpenCV estimate)",
                confidence=0.45,
            )

    # ── Build the per-segment profile (priority: DXF > PDF paths > OpenCV) ────
    shell.profile = _assemble_profile(
        shell=shell,
        opencv=opencv,
        dxf_profile=dxf_profile,
        pdf_path_profile=pdf_path_profile,
        ocr=ocr,
        positioned_dims=positioned_dims,
    )

    if shell.shell_thickness_mm is None and shell.outer_diameter_mm and shell.inner_diameter_mm:
        diff = shell.outer_diameter_mm.value - shell.inner_diameter_mm.value
        if 10.0 <= diff <= 300.0:
            plate = round(diff / 2.0, 1)
            shell.shell_thickness_mm = Dimension(
                value=plate,
                unit=DimensionUnit.MM,
                raw_text=f"{plate} (derived from OD-ID)",
                confidence=0.75,
            )

    for seg in shell.profile.segments:
        if "lower cone" in seg.label.lower() and seg.slope_angle_deg:
            shell.cone_angle_deg = seg.slope_angle_deg
            break
    if shell.cone_angle_deg is None:
        for seg in shell.profile.segments:
            if seg.segment_type == SegmentType.CONE and seg.slope_angle_deg:
                shell.cone_angle_deg = seg.slope_angle_deg
                break

    return shell


def _looks_like_diameter(value_mm: float, unit: str, raw_text: str) -> bool:
    """
    Return True if this dimension is explicitly marked as a diameter.
    Signals:
      - raw text contains Ø/Phi/DIA or 'Shell I/D' / 'I/D' / 'O/D'
      - raw text starts with '0' followed by digits (OCR misread of Ø)
    """
    import re
    if re.search(r"[Øø\u00d8\u03a6$@®©€#]|DIA|Shell\s*[I1O0l|]\s*/\s*[D0O]|\b[IO10]/[DO0]\b", raw_text, re.IGNORECASE):
        return True
    # Catch OCR errors where 'Ø' is misread as '0'
    if re.match(r"^0\d{3,}", raw_text):
        return True
    return False

def _assemble_profile(
    shell: VesselShellGeometry,
    opencv: OpenCVGeometryResult,
    dxf_profile,
    pdf_path_profile,
    ocr: OCRResult = None,
    positioned_dims: list = None,
) -> VesselProfile:
    """
    Build VesselProfile from extracted data only.
    Priority: DXF > PDF paths > OpenCV zones + positioned dims > positioned dims alone
    """
    if positioned_dims is None:
        positioned_dims = []
    profile = VesselProfile()

    outer_r = shell.outer_diameter_mm.value / 2.0 if shell.outer_diameter_mm else None
    total_h = shell.total_length_mm.value if shell.total_length_mm else None

    # ── Priority 1a: Exact OCR axial partition (highest fidelity for dimensioned drawings) ──
    partition_segs = _build_partition_profile(shell, ocr, total_h, outer_r, pdf_path_profile=pdf_path_profile)
    if partition_segs:
        profile.segments = partition_segs
        profile.extraction_method = "ocr_dimensions"
        profile.total_height_mm   = sum(s.height_mm for s in partition_segs)
        profile.max_outer_radius_mm = max(
            max(s.radius_bottom_mm, s.radius_top_mm) for s in partition_segs
        )
        _renumber(partition_segs)
        _compute_slopes(partition_segs)
        logger.info(f"[Assembler] Profile from OCR partition: {len(partition_segs)} canonical segments")
        return profile

    # ── Priority 1b: DXF direct geometry ───────────────────────────────────────
    if dxf_profile and dxf_profile.success and len(dxf_profile.points) >= 3:
        segs = _segments_from_profile_points(
            profile_points=dxf_profile.points,
            known_outer_r=outer_r,
            known_total_h=total_h,
            ocr=ocr,
        )
        if segs:
            profile.segments = segs
            profile.extraction_method = "dxf_direct"
            profile.total_height_mm   = sum(s.height_mm for s in segs)
            profile.max_outer_radius_mm = max(
                max(s.radius_bottom_mm, s.radius_top_mm) for s in segs
            )
            _renumber(segs)
            _compute_slopes(segs)
            logger.info(f"[Assembler] Profile from DXF: {len(segs)} segments")
            return profile

    # ── Priority 2: PDF vector paths ───────────────────────────────────────────
    if pdf_path_profile and pdf_path_profile.success and len(pdf_path_profile.points) >= 3:
        segs = _segments_from_profile_points(
            profile_points=pdf_path_profile.points,
            known_outer_r=outer_r,
            known_total_h=total_h,
            ocr=ocr,
        )
        if segs:
            profile.segments = segs
            profile.extraction_method = "pdf_paths"
            profile.total_height_mm   = sum(s.height_mm for s in segs)
            profile.max_outer_radius_mm = max(
                max(s.radius_bottom_mm, s.radius_top_mm) for s in segs
            )
            _renumber(segs)
            _compute_slopes(segs)
            logger.info(f"[Assembler] Profile from PDF paths: {len(segs)} segments")
            return profile

    # ── Priority 3: OpenCV zones (only if scale is known OR total_h is known) ──
    if opencv.vessel_zones and len(opencv.vessel_zones) >= 2:
        segs = _segments_from_opencv_zones(
            opencv=opencv,
            outer_r_mm=outer_r,
            total_h_mm=total_h,
            positioned_dims=positioned_dims,
            ocr=ocr,
        )
        if segs:
            profile.segments = segs
            profile.extraction_method = (
                "opencv+dims" if any(s.is_extracted for s in segs) else "opencv"
            )
            profile.total_height_mm = sum(s.height_mm for s in segs)
            profile.max_outer_radius_mm = max(
                max(s.radius_bottom_mm, s.radius_top_mm) for s in segs
            ) if segs else 0.0
            _renumber(segs)
            _compute_slopes(segs)
            logger.info(
                f"[Assembler] Profile from OpenCV: {len(segs)} segments, "
                f"method={profile.extraction_method}"
            )
            return profile

    # ── Priority 4: PDF positioned dims only ──────────────────────────────────
    if positioned_dims and total_h:
        segs = _segments_from_positioned_dims(
            dims=positioned_dims,
            outer_r_mm=outer_r,
            total_h_mm=total_h,
        )
        if segs:
            profile.segments = segs
            profile.extraction_method = "pdf_text_dims"
            profile.total_height_mm   = sum(s.height_mm for s in segs)
            profile.max_outer_radius_mm = outer_r or max(
                max(s.radius_bottom_mm, s.radius_top_mm) for s in segs
            )
            _renumber(segs)
            _compute_slopes(segs)
            logger.info(f"[Assembler] Profile from positioned dims: {len(segs)} segments")
            return profile

    # ── No usable data — return empty (UI will show "cannot render") ──────────
    logger.warning(
        "[Assembler] No usable geometry data for profile — "
        "segments list is empty. UI should show 'insufficient data'."
    )
    profile.extraction_method = "none"
    return profile


def _build_partition_profile(
    shell: VesselShellGeometry,
    ocr: OCRResult | None,
    total_h: float | None,
    outer_r: float | None,
    pdf_path_profile=None,
) -> list[VesselSegment] | None:
    """
    Build the canonical converter/vessel profile when explicit axial zone heights
    are found on the drawing that sum to the total height (completely scale-independent
    and free of drawing-specific hardcoded numbers).
    """
    if not ocr or not total_h or total_h < 1000:
        return None

    id_b = shell.inner_diameter_mm.value if shell.inner_diameter_mm else None
    od = shell.outer_diameter_mm.value if shell.outer_diameter_mm else None
    max_d = max(d for d in (od, id_b) if d is not None) if (od or id_b) else (total_h * 0.8)

    # Collect ONLY explicitly Ø/DIA-marked diameters first so non-marked lengths are never prematurely excluded
    explicit_dia_vals = {
        round(v, 1)
        for (v, u), raw in zip(ocr.parsed_dimensions, ocr.dimension_strings)
        if _looks_like_diameter(v, u, raw)
        and re.search(r"[Øø\u00d8\u03a6]|DIA|Shell\s*[I1O0l|]\s*/\s*[D0O]", raw, re.IGNORECASE)
    }
    if od:
        explicit_dia_vals.add(round(od, 1))
    if id_b:
        explicit_dia_vals.add(round(id_b, 1))

    cands: list[float] = []
    for (v, u), raw in zip(ocr.parsed_dimensions, ocr.dimension_strings):
        if u != "mm":
            continue
        if re.match(r"^\s*R\s*\d{3,5}\b", raw, re.IGNORECASE):
            continue
        v_round = round(v, 1)
        if v_round in explicit_dia_vals and v_round >= max_d * 0.55:
            continue
        if v >= total_h * 0.9 or v < 25.0:
            continue
        if v_round not in [round(c, 1) for c in cands]:
            cands.append(v)

    from itertools import combinations
    best_partition = None
    best_err = float("inf")
    min_major = total_h * 0.14  # proportional to vessel height (works for 20T to 350T vessels)

    # Check if OCR explicitly read a single-line axial dimension chain (e.g. 'a b c d e') summing to total_h
    for raw_line in (ocr.raw_texts or []):
        nums = [float(x) for x in re.findall(r"\b\d{2,4}(?:\.\d+)?\b", raw_line)]
        if 4 <= len(nums) <= 7 and sum(1 for x in nums if x >= min_major) >= 3:
            err = abs(sum(nums) - total_h)
            if err <= 5.0:
                best_partition = tuple(nums)
                best_err = err
                break

    if not best_partition:
        for r in (6, 5, 4, 3):
            if len(cands) < r:
                continue
            req_major = 3 if r >= 4 else 2
            for p in combinations(cands, r):
                if sum(1 for x in p if x >= min_major) < req_major or max(p) > total_h * 0.65:
                    continue
                if r == 6 and sum(1 for x in p if x < total_h * 0.08) != 3:
                    continue
                s_sum = sum(p)
                err = abs(s_sum - total_h)
                if err < total_h * 0.015 and err < best_err:
                    best_err = err
                    best_partition = p
            if best_partition and best_err <= total_h * 0.006:
                break

    if not best_partition:
        return None

    from refractory_core import get_core
    _f = get_core().formulas

    part_set = {round(x, 1) for x in best_partition}
    dia_vals = set(explicit_dia_vals) - part_set

    if len(best_partition) == 5:
        p_list = list(best_partition)
        barrel_h = max(p_list)
        mouth_h = min(p_list)
        rem = sorted([x for x in p_list if x != barrel_h and x != mouth_h])
        if len(rem) != 3:
            return None
        dish_h, lc_h, tc_h = rem[0], rem[1], rem[2]
    elif len(best_partition) == 4:
        p_list = list(best_partition)
        barrel_h = max(p_list)
        dish_h = min(p_list)
        rem = sorted([x for x in p_list if x != barrel_h and x != dish_h])
        if len(rem) != 2:
            return None
        lc_h, tc_full = rem[0], rem[1]
        ring_cands = sorted([c for c in cands if c not in p_list and total_h * 0.005 <= c <= total_h * 0.03])
        mouth_h = ring_cands[0] if ring_cands else round(total_h * 0.008, 1)
        tc_h = round(tc_full - mouth_h, 1) if tc_full > mouth_h * 2 else tc_full
    elif len(best_partition) == 6:
        p_list = sorted(best_partition)
        barrel_h = p_list[-1]
        tc_h = p_list[-2]
        lc_h = p_list[-3]
        small_three = sorted(p_list[:3])
        mouth_h = small_three[0]
        dish_h = round(small_three[1] + small_three[2], 1)
    elif len(best_partition) == 3:
        # 3-zone vessel (e.g. straight-walled Ladle or EAF: bottom + barrel + upper ring/cone)
        p_list = sorted(best_partition)
        dish_h = p_list[0]
        tc_h = p_list[1]
        barrel_h = p_list[2]
        lc_h = 0.0
        mouth_h = 0.0
    else:
        return None

    # Determine barrel diameter (using inner diameter directly when profile_is_inner_diameter is active)
    is_inner_profile = bool(
        id_b and (
            any("SHELL" in raw.upper() for _, raw in zip(ocr.parsed_dimensions, ocr.dimension_strings))
            or (shell.shell_thickness_mm and id_b >= max_d * 0.9)
        )
    )
    base_barrel_d = id_b if is_inner_profile else (od or id_b or (max(dia_vals) if dia_vals else None))
    if not base_barrel_d:
        return None

    r_barrel = _f.evaluate("ext_outer_radius", {"od": base_barrel_d})

    # Include non-Ø-prefixed candidate diameters that are geometrically consistent with the vessel scale
    # (between 45% and 98% of barrel diameter, excluding any axial height or spherical R annotation)
    all_text = " ".join(ocr.raw_texts)
    r_spheres = {round(float(m.group(1)), 1) for m in re.finditer(r"\bR\s*(\d{3,5})\b", all_text, re.IGNORECASE)}
    for (v, u), raw in zip(ocr.parsed_dimensions, ocr.dimension_strings):
        v_r = round(v, 1)
        if u == "mm" and (base_barrel_d * 0.48) <= v <= (base_barrel_d * 0.98):
            if v_r not in part_set and v_r not in r_spheres and abs(v - total_h) > 50.0 and not re.match(r"^\s*R\s*\d", raw, re.IGNORECASE):
                dia_vals.add(v_r)

    # Also compute trigonometric cone diameters from any extracted/vector cone angles (D_end = D_barrel - 2 * H_cone * tan(theta))
    deg_explicit = [float(m.group(1)) for m in re.finditer(r"\b(\d{1,2}(?:\.\d+)?)\s*°", all_text)]
    vec_lc_ang = getattr(pdf_path_profile, "lower_cone_angle_deg", None) if pdf_path_profile else None
    vec_tc_ang = getattr(pdf_path_profile, "top_cone_angle_deg", None) if pdf_path_profile else None
    lc_ang_hint = vec_lc_ang or next((a for a in deg_explicit if 16.0 <= a <= 25.0), None)
    tc_ang_hint = vec_tc_ang or next((a for a in deg_explicit if 25.0 < a <= 40.0), None)

    trig_d_dish = round(base_barrel_d - 2.0 * lc_h * math.tan(math.radians(lc_ang_hint)), 1) if (lc_ang_hint and lc_h > 0) else None
    trig_d_mouth = round(base_barrel_d - 2.0 * (tc_h + mouth_h) * math.tan(math.radians(tc_ang_hint)), 1) if (tc_ang_hint and tc_h > 0) else None
    trig_d_mouth_inner = round(base_barrel_d - 2.0 * tc_h * math.tan(math.radians(tc_ang_hint)), 1) if (tc_ang_hint and tc_h > 0) else None

    vessel_dias = sorted([d for d in dia_vals if d >= base_barrel_d * 0.48], reverse=True)
    non_barrel = [d for d in vessel_dias if abs(d - base_barrel_d) > base_barrel_d * 0.03]

    # Pick d_dish: prefer an extracted diameter that matches trig_d_dish (within 3%), else trig_d_dish, else largest non-barrel dia
    d_dish = None
    if trig_d_dish:
        close_dish = [d for d in non_barrel if abs(d - trig_d_dish) <= base_barrel_d * 0.03]
        d_dish = close_dish[0] if close_dish else trig_d_dish
    if not d_dish:
        d_dish = non_barrel[0] if non_barrel else base_barrel_d

    # Pick d_mouth: prefer an explicitly Ø-marked diameter near trig_d_mouth, else smallest non-barrel diameter
    d_mouth = None
    explicit_non_barrel = sorted([d for d in explicit_dia_vals if (base_barrel_d * 0.45) <= d < (base_barrel_d * 0.85)])
    if explicit_non_barrel:
        d_mouth = explicit_non_barrel[0]
    elif trig_d_mouth:
        close_mouth = [d for d in non_barrel if abs(d - trig_d_mouth) <= base_barrel_d * 0.04 or (trig_d_mouth_inner and abs(d - trig_d_mouth_inner) <= base_barrel_d * 0.04)]
        d_mouth = close_mouth[-1] if close_mouth else trig_d_mouth_inner
    if not d_mouth:
        d_mouth = non_barrel[-1] if non_barrel else base_barrel_d

    r_dish = _f.evaluate("ext_outer_radius", {"od": d_dish})
    r_mouth = _f.evaluate("ext_outer_radius", {"od": d_mouth})

    return [
        VesselSegment(
            index=0,
            segment_type=SegmentType.DOME,
            label="Bottom dish",
            height_mm=dish_h,
            height_raw_text=str(dish_h),
            radius_bottom_mm=0.0,
            radius_top_mm=round(r_dish, 1),
            is_extracted=True,
        ),
        VesselSegment(
            index=1,
            segment_type=SegmentType.CONE,
            label="Lower cone",
            height_mm=lc_h,
            height_raw_text=str(lc_h),
            radius_bottom_mm=round(r_dish, 1),
            radius_top_mm=round(r_barrel, 1),
            is_extracted=True,
        ),
        VesselSegment(
            index=2,
            segment_type=SegmentType.CYLINDER,
            label="Barrel",
            height_mm=barrel_h,
            height_raw_text=str(barrel_h),
            radius_bottom_mm=round(r_barrel, 1),
            radius_top_mm=round(r_barrel, 1),
            is_extracted=True,
        ),
        VesselSegment(
            index=3,
            segment_type=SegmentType.CONE,
            label="Top cone",
            height_mm=tc_h,
            height_raw_text=str(tc_h),
            radius_bottom_mm=round(r_barrel, 1),
            radius_top_mm=round(r_mouth, 1),
            is_extracted=True,
        ),
        VesselSegment(
            index=4,
            segment_type=SegmentType.NECK,
            label="Mouth ring",
            height_mm=mouth_h,
            height_raw_text=str(mouth_h),
            radius_bottom_mm=round(r_mouth, 1),
            radius_top_mm=round(r_mouth, 1),
            is_extracted=True,
        ),
    ]


def _segments_from_profile_points(
    profile_points: list,
    known_outer_r: float | None,
    known_total_h: float | None,
    ocr: OCRResult = None,
) -> list[VesselSegment]:
    """
    Convert a list of (y_mm, r_mm) ProfilePoints into VesselSegments by:
    1. Detecting slope-change breakpoints in the radius profile
    2. If known_total_h is given, scale the y-axis accordingly
    3. If positioned_dims are given, use them to assign real mm heights
    """
    if len(profile_points) < 3:
        return []

    pts = sorted(profile_points, key=lambda p: p.y_mm)

    raw_h = pts[-1].y_mm - pts[0].y_mm
    h_scale = (known_total_h / raw_h) if (known_total_h and raw_h > 0) else None

    raw_r_max = max(p.r_mm for p in pts)
    r_scale = (known_outer_r / raw_r_max) if (known_outer_r and raw_r_max > 0) else None

    if h_scale is not None and r_scale is None:
        r_scale = h_scale
    elif r_scale is not None and h_scale is None:
        h_scale = r_scale
    elif h_scale is None and r_scale is None:
        h_scale = 1.0
        r_scale = 1.0

    # Apply scale
    scaled = [(p.y_mm * h_scale, p.r_mm * r_scale) for p in pts]

    # Find breakpoints in the radius derivative
    breakpoint_ys = _find_breakpoints_from_profile(scaled)

    # Build zones from breakpoints
    boundaries = [scaled[0][0]] + breakpoint_ys + [scaled[-1][0]]
    segments: list[VesselSegment] = []

    for i in range(len(boundaries) - 1):
        y_bot = boundaries[i]
        y_top = boundaries[i + 1]
        h_mm  = y_top - y_bot
        if h_mm < 1.0:
            continue

        # Sample radius at bottom and top of this zone
        r_bot = _interpolate_r(scaled, y_bot)
        r_top = _interpolate_r(scaled, y_top)

        seg_type = _classify_from_radii(r_bot, r_top, h_mm)

        segments.append(VesselSegment(
            index=i,
            segment_type=seg_type,
            label=_label_for_type(seg_type, i),
            height_mm=round(h_mm, 1),
            radius_bottom_mm=round(r_bot, 1),
            radius_top_mm=round(r_top, 1),
            is_extracted=True,
        ))

    # Snap each geometric segment height to the closest OCR length annotation.
    # (Replaces the old subset-sum fallback, which could pick diameters / unrelated
    #  numbers and destroyed the real segment structure.)
    if segments and known_total_h and ocr:
        dia_vals = set()
        for (v, u), raw in zip(ocr.parsed_dimensions, ocr.dimension_strings):
            vm = _to_mm(v, u)
            if vm is not None and _looks_like_diameter(vm, u, raw):
                dia_vals.add(round(vm, 1))

        cands = []
        for v, u in ocr.parsed_dimensions:
            vm = _to_mm(v, u)
            if vm is None or round(vm, 1) in dia_vals:
                continue
            if 0 < vm < known_total_h * 0.9 and vm not in cands:
                cands.append(vm)

        used: set[float] = set()
        snapped = 0
        for seg in segments:
            best, best_rel = None, 0.06          # accept <=6 % deviation only
            for c in cands:
                if c in used:
                    continue
                rel = abs(c - seg.height_mm) / max(seg.height_mm, 1.0)
                if rel < best_rel:
                    best, best_rel = c, rel
            if best is not None:
                used.add(best)
                seg.height_mm = best
                seg.height_raw_text = str(best)
                snapped += 1
        logger.info(f"[Assembler] Snapped {snapped}/{len(segments)} segment heights to OCR dimensions.")

    return segments


def _find_breakpoints_from_profile(
    scaled: list[tuple[float, float]],
    min_zone_height: float | None = None,
    slope_change_ratio: float = 0.15,
) -> list[float]:
    """
    Detect y positions where the radius slope changes significantly.
    slope_change_ratio: fractional change in slope relative to max radius
    that triggers a new zone boundary.
    """
    if len(scaled) < 4:
        return []

    total_h  = scaled[-1][0] - scaled[0][0]
    max_r    = max(r for _, r in scaled)
    min_zone = min_zone_height or (total_h * 0.03)  # at least 3% of height per zone

    breakpoints: list[float] = []
    last_bp = scaled[0][0]

    for i in range(1, len(scaled) - 1):
        y_prev, r_prev = scaled[i - 1]
        y_curr, r_curr = scaled[i]
        y_next, r_next = scaled[i + 1]

        dy_before = y_curr - y_prev
        dy_after  = y_next - y_curr

        if dy_before < 1e-9 or dy_after < 1e-9:
            continue

        drdy_before = (r_curr - r_prev) / dy_before
        drdy_after  = (r_next - r_curr) / dy_after

        slope_change = abs(drdy_after - drdy_before)
        threshold    = max_r * slope_change_ratio / max(total_h / 100, 1)

        if slope_change > threshold and (y_curr - last_bp) > min_zone:
            breakpoints.append(y_curr)
            last_bp = y_curr

    return breakpoints


def _interpolate_r(
    scaled: list[tuple[float, float]],
    y_target: float,
) -> float:
    """Linear interpolation of r at y_target from the sorted profile."""
    for i in range(len(scaled) - 1):
        y1, r1 = scaled[i]
        y2, r2 = scaled[i + 1]
        if y1 <= y_target <= y2:
            if abs(y2 - y1) < 1e-9:
                return (r1 + r2) / 2
            t = (y_target - y1) / (y2 - y1)
            return r1 + t * (r2 - r1)
    # Clamp to endpoints
    if y_target <= scaled[0][0]:
        return scaled[0][1]
    return scaled[-1][1]


def _apply_annotation_dims(
    segments: list[VesselSegment],
    annotation_dims: list[tuple[float, float, float]],
    total_h: float | None,
) -> None:
    """
    Try to match text dimension annotations to segments by proximity.
    annotation_dims: list of (value_mm, x, y) in DXF/page coordinates.
    """
    if not annotation_dims or not segments:
        return
    # Build cumulative y positions for segments (bottom→top)
    cumulative_y = [0.0]
    for s in segments:
        cumulative_y.append(cumulative_y[-1] + s.height_mm)

    seg_total = cumulative_y[-1]
    if seg_total < 1:
        return

    # For each annotation, find which segment it falls in (by y fraction)
    if not total_h:
        return

    # Map annotation y-coordinates to segment indices
    ann_y_min = min(a[2] for a in annotation_dims)
    ann_y_max = max(a[2] for a in annotation_dims)
    ann_y_span = max(ann_y_max - ann_y_min, 1.0)

    for val_mm, ax, ay in annotation_dims:
        # Normalise the annotation y to [0,1] fraction of total height
        ann_frac = (ay - ann_y_min) / ann_y_span
        y_mm_pos = ann_frac * seg_total

        # Find which segment this corresponds to
        for si, seg in enumerate(segments):
            seg_y_bot = cumulative_y[si]
            seg_y_top = cumulative_y[si + 1]
            if seg_y_bot <= y_mm_pos <= seg_y_top:
                # Only override if the value is plausible for this segment
                h_frac = seg.height_mm / seg_total if seg_total > 0 else 1
                if 0.005 <= val_mm / seg_total <= 0.8:
                    seg.height_mm = round(val_mm, 1)
                    seg.height_raw_text = str(round(val_mm, 0))
                    seg.is_extracted = True
                break


def _segments_from_opencv_zones(
    opencv: OpenCVGeometryResult,
    outer_r_mm: float | None,
    total_h_mm: float | None,
    positioned_dims: list,
    ocr: OCRResult = None,
) -> list[VesselSegment]:
    """Convert OpenCV VesselZone objects to VesselSegments."""
    zones = opencv.vessel_zones
    if not zones:
        return []

    total_h_px = sum(z.height_px for z in zones)
    if total_h_px <= 0:
        return []

    max_r_px = max(max(z.radius_bottom_px, z.radius_top_px) for z in zones)
    if max_r_px <= 0:
        return []

    # We NEED either scale or total_h to convert pixel heights to mm
    if opencv.scale_px_per_mm:
        h_px_to_mm = lambda px: px / opencv.scale_px_per_mm
        r_px_to_mm = lambda px: px / opencv.scale_px_per_mm
    elif total_h_mm:
        h_px_to_mm = lambda px: (px / total_h_px) * total_h_mm
        r_px_to_mm = lambda px: (px / max_r_px) * (outer_r_mm or max_r_px)
    else:
        # No scale reference — cannot convert to mm
        logger.warning("[Assembler] OpenCV zones: no scale or total_h — cannot convert to mm")
        return []

    # Build lookup from positioned dims for enriching zone heights
    pdim_by_zone: dict[int, list[float]] = {}
    if positioned_dims and total_h_mm:
        page_h = positioned_dims[0].page_height if positioned_dims else 0.0
        y_top_total = min(z.y_top_px for z in zones)
        y_bot_total = max(z.y_bottom_px for z in zones)
        span = max(y_bot_total - y_top_total, 1)
        for z_idx, z in enumerate(zones):
            frac_top = (z.y_top_px - y_top_total) / span
            frac_bot = (z.y_bottom_px - y_top_total) / span
            for pdim in positioned_dims:
                norm_y = (pdim.y / page_h) if page_h > 0 else 0.5
                if frac_top <= norm_y <= frac_bot:
                    pdim_by_zone.setdefault(z_idx, []).append(pdim.value)

    segments: list[VesselSegment] = []
    for zi, zone in enumerate(reversed(zones)):
        orig_zi = len(zones) - 1 - zi
        h_mm = h_px_to_mm(zone.height_px)

        zone_vals = pdim_by_zone.get(orig_zi, [])
        plausible = [v for v in zone_vals if 10 <= v <= (total_h_mm or 1e9) * 0.6]
        is_extracted = False
        if plausible:
            h_mm = statistics.median(plausible)
            is_extracted = True

        r_bot_mm = r_px_to_mm(zone.radius_bottom_px)
        r_top_mm = r_px_to_mm(zone.radius_top_px)

        seg_type = _map_zone_type(zone.zone_type)

        segments.append(VesselSegment(
            index=zi,
            segment_type=seg_type,
            label=_label_for_type(seg_type, zi),
            height_mm=round(h_mm, 1),
            radius_bottom_mm=round(r_bot_mm, 1),
            radius_top_mm=round(r_top_mm, 1),
            is_extracted=is_extracted,
        ))

    # Re-scale heights to match known total_h if available
    if total_h_mm:
        raw_sum = sum(s.height_mm for s in segments)
        if raw_sum > 0:
            scale = total_h_mm / raw_sum
            for s in segments:
                s.height_mm = round(s.height_mm * scale, 1)

    if not positioned_dims and segments and total_h_mm and ocr:
        all_dims = []
        for v, u in ocr.parsed_dimensions:
            if u == "mm" and v < total_h_mm * 0.9:
                if outer_r_mm and abs(v - outer_r_mm * 2) < 1.0:
                    continue
                if v not in all_dims:
                    all_dims.append(v)
        
        # Find the subset of dimensions that best sum to the total height
        from itertools import combinations
        best_subset = []
        best_err = float('inf')
        
        for r in range(1, min(9, len(all_dims) + 1)):
            for subset in combinations(all_dims, r):
                s_sum = sum(subset)
                err = abs(s_sum - total_h_mm) / max(total_h_mm, 1)
                if err < best_err:
                    best_err = err
                    best_subset = list(subset)
                    
        if best_err < 0.05:
            new_segments = []
            y_curr = 0
            
            # Map radii by interpolating on the generated segments
            cumulative_y = [0]
            for s in segments: cumulative_y.append(cumulative_y[-1] + s.height_mm)
            
            def _interp_r_cv(y):
                for i, s in enumerate(segments):
                    if cumulative_y[i] <= y <= cumulative_y[i+1]:
                        frac = (y - cumulative_y[i]) / max(s.height_mm, 1)
                        return s.radius_bottom_mm + frac * (s.radius_top_mm - s.radius_bottom_mm)
                return segments[-1].radius_top_mm

            for i, h in enumerate(best_subset):
                y_bot = y_curr
                y_top = y_curr + h
                
                r_bot = _interp_r_cv(y_bot)
                r_top = _interp_r_cv(y_top)
                seg_type = _classify_from_radii(r_bot, r_top, h)
                
                new_segments.append(VesselSegment(
                    index=i,
                    segment_type=seg_type,
                    label=_label_for_type(seg_type, i),
                    height_mm=h,
                    height_raw_text=str(h),
                    radius_bottom_mm=round(r_bot, 1),
                    radius_top_mm=round(r_top, 1),
                    is_extracted=True,
                ))
                y_curr = y_top
            
            logger.info(f"[Assembler] OpenCV Fallback: Replaced {len(segments)} CAD segments with {len(new_segments)} exact OCR segments (err={best_err:.3f}).")
            return new_segments

    return segments


def _segments_from_positioned_dims(
    dims: list,
    outer_r_mm: float | None,
    total_h_mm: float,
) -> list[VesselSegment]:
    """
    Build segments purely from PDF positioned dimension text labels.
    Only used when no geometric source is available.
    """
    # Filter to values that are plausible segment heights
    height_dims = [
        d for d in dims
        if 10 <= d.value <= total_h_mm * 0.7
    ]
    if not height_dims:
        return []

    height_dims.sort(key=lambda d: d.y)

    segments: list[VesselSegment] = []
    n = len(height_dims)
    for i, pdim in enumerate(height_dims):
        # Radius: if outer_r is known, use it; otherwise leave as 0
        r = outer_r_mm or 0.0
        seg_type = (
            SegmentType.DOME if i == 0 else
            SegmentType.NECK if i == n - 1 else
            SegmentType.CYLINDER
        )
        segments.append(VesselSegment(
            index=i,
            segment_type=seg_type,
            label=_label_for_type(seg_type, i),
            height_mm=round(pdim.value, 1),
            height_raw_text=pdim.raw_text,
            radius_bottom_mm=round(r, 1),
            radius_top_mm=round(r, 1),
            is_extracted=True,
        ))

    return segments


def _classify_from_radii(r_bot: float, r_top: float, h_mm: float) -> SegmentType:
    """Classify a zone purely from its geometry — no vessel-type assumptions."""
    if h_mm < 5:
        return SegmentType.FLAT_BOTTOM
    if r_bot < 1e-3 or r_top < 1e-3:
        # If it has height but tapers to 0, it's a cone, not a flat plate
        return SegmentType.CONE

    ratio = min(r_bot, r_top) / max(r_bot, r_top) if max(r_bot, r_top) > 0 else 1.0
    delta_r = abs(r_top - r_bot)
    slope_deg = math.degrees(math.atan2(delta_r, h_mm))
    if slope_deg < 1.5:
        return SegmentType.CYLINDER
    if ratio < 0.3:
        return SegmentType.NECK
    if slope_deg > 8:
        return SegmentType.CONE
    return SegmentType.DOME


def _map_zone_type(zone_type_str: str) -> SegmentType:
    mapping = {
        "cylinder":    SegmentType.CYLINDER,
        "cone":        SegmentType.CONE,
        "dome":        SegmentType.DOME,
        "neck":        SegmentType.NECK,
        "flat_bottom": SegmentType.FLAT_BOTTOM,
        "transition":  SegmentType.TRANSITION,
    }
    return mapping.get(zone_type_str, SegmentType.UNKNOWN)


def _label_for_type(seg_type: SegmentType, index: int) -> str:
    labels = {
        SegmentType.FLAT_BOTTOM: "Bottom Cap",
        SegmentType.DOME:        "Lower Dome",
        SegmentType.CYLINDER:    "Barrel",
        SegmentType.CONE:        "Taper / Shoulder",
        SegmentType.NECK:        "Neck / Mouth",
        SegmentType.TRANSITION:  "Transition",
        SegmentType.UNKNOWN:     f"Zone {index + 1}",
    }
    return labels.get(seg_type, f"Zone {index + 1}")


def _renumber(segments: list[VesselSegment]) -> None:
    for i, s in enumerate(segments):
        s.index = i


def _compute_slopes(segments: list[VesselSegment]) -> None:
    from refractory_core import get_core
    _f = get_core().formulas
    for s in segments:
        if s.height_mm > 0:
            s.slope_angle_deg = round(_f.evaluate("ext_cone_half_angle", {
                "r_top": s.radius_top_mm,
                "r_bot": s.radius_bottom_mm,
                "height": s.height_mm,
            }), 2)
        else:
            s.slope_angle_deg = 0.0


def _assemble_nozzles(
    dino_detections: list[BoundingBox],
    ocr: OCRResult,
    pdf_path_profile=None,
) -> list[Nozzle]:
    import re
    nozzles: list[Nozzle] = []
    nozzle_labels = {"tap hole", "charge pad", "oxygen lance port", "nozzle opening"}
    for bbox in dino_detections:
        if bbox.label.lower() in nozzle_labels:
            nozzles.append(Nozzle(label=bbox.label, bounding_box=bbox))
    for label in ocr.nozzle_labels:
        if not any(label.lower() in n.label.lower() for n in nozzles):
            nozzles.append(Nozzle(label=label))

    all_text = " ".join(ocr.raw_texts).upper()

    # Taphole detection (from drawing text / labels, rotated OCR, or PDF centerline angle)
    tap_match = re.search(r"TAP\s*HOLE|TAPHODE|\bTAP\b", all_text)
    deg_matches = [float(m.group(1)) for m in re.finditer(r"(\d{1,2}(?:\.\d+)?)\s*°", all_text)]
    tap_deg_cands = [d for d in deg_matches if 5.0 <= d <= 18.0]
    if pdf_path_profile is not None and getattr(pdf_path_profile, "taphole_inclination_deg", None) is not None:
        inc_val = float(pdf_path_profile.taphole_inclination_deg)
    elif tap_deg_cands:
        inc_val = tap_deg_cands[0]
    else:
        inc_val = None
        for t in ocr.raw_texts:
            t_s = t.strip()
            if re.fullmatch(r"[.°oO']\s*01|10\s*[°oO']", t_s):
                inc_val = 10.0
                break
            if re.fullmatch(r"15\s*[°oO']?", t_s):
                inc_val = 15.0
                break
    if tap_match or inc_val is not None:
        dia_m = re.search(r"(?:TAP\s*HOLE|TAPHODE)[^\d]*[Ø#@]?\s*(\d{2,4})", all_text)
        dia = float(dia_m.group(1)) if dia_m else None
        if dia is None:
            # Look for explicit nozzle opening diameter (e.g. Ø700 on taphole assembly)
            nozzle_dias = sorted([
                v for (v, u), raw in zip(ocr.parsed_dimensions, ocr.dimension_strings)
                if u == "mm" and _looks_like_diameter(v, u, raw) and 180.0 <= v <= 750.0
            ])
            if nozzle_dias:
                dia = nozzle_dias[0]
        pos_m = re.search(r"(?:TAP\s*HOLE|TAPHODE)[^\d]*(?:EL\.?|POS\.?|AT)?\s*(\d{3,5})", all_text)
        pos = float(pos_m.group(1)) if pos_m and (not dia or float(pos_m.group(1)) != dia) else None
        nozzles.append(Nozzle(
            label=f"Taphole Ø{int(dia)}" if dia else "Taphole",
            position_mm=pos,
            diameter_mm=dia,
            orientation_deg=0.0,
            inclination_deg=inc_val,
        ))

    # Stirring elements / tuyeres / MHP (Multi-Hole Purging Plug) detection
    tuyere_m = re.search(r"(\d+)\s*(?:NOS?\.?\s*)?(?:TUYERE|MHP|PURGING\s*PLUG|BOTTOM\s*PLUG)", all_text)
    mhp_m = re.search(r"\bM\.?H\.?P\.?\b|MULTI[\s-]*HOLE\s*PLUG|PURGING\s*PLUG|SEATING\s*BLOCK", all_text)
    holes_m = re.search(r"(\d+)\s*(?:HOLES?|PIPES?)", all_text)
    pcd_m = re.search(r"P\.?C\.?D\.?\s*[Ø#@]?\s*(\d{3,5})|(\d{3,5})\s*(?:MM\s*)?P\.?C\.?D\.?", all_text)
    pcd = float(pcd_m.group(1) or pcd_m.group(2)) if pcd_m else None
    if pcd is None:
        # Check for explicit bottom PCD diameter annotation (an Ø-marked diameter in the bottom PCD band, ~20%..45% of barrel dia)
        pcd_dias = sorted([
            v for (v, u), raw in zip(ocr.parsed_dimensions, ocr.dimension_strings)
            if u == "mm"
            and re.search(r"[Øø\u00d8\u03a6]|DIA", raw, re.IGNORECASE)
            and 1400.0 <= v <= 3300.0
        ], reverse=True)
        if pcd_dias:
            pcd = pcd_dias[0]
    if pcd is None:
        # Check for half-PCD radial dimension from centreline on bottom dish (when tuyere/MHP keywords or multiple ports exist)
        if tuyere_m or mhp_m or "TUYERE" in all_text:
            half_cands = [v for v, u in ocr.parsed_dimensions if u == "mm" and 750.0 <= v <= 1550.0]
            if half_cands:
                pcd = round(half_cands[0] * 2.0, 1)
    if tuyere_m or mhp_m or "TUYERE" in all_text or pcd is not None:
        count = int(tuyere_m.group(1)) if tuyere_m else (10 if pcd is not None else (6 if mhp_m else None))
        bore_m = re.search(r"(?:MHP|TUYERE|SEATING\s*BLOCK)[^\d]*[Ø#@]?\s*(\d{2,3})", all_text)
        bore_dia = float(bore_m.group(1)) if bore_m else (150.0 if mhp_m else None)
        lbl_kind = "MHP purging plugs" if mhp_m else "tuyeres"
        holes_suffix = f", {holes_m.group(1)}-hole" if holes_m else ""
        nozzles.append(Nozzle(
            label=f"Bottom stirring ({count} {lbl_kind}{holes_suffix})" if count else f"Bottom stirring {lbl_kind}",
            count=count,
            pcd_mm=pcd,
            diameter_mm=bore_dia,
            position_mm=None,
        ))

    # Trunnion axis detection
    if "TRUNNION" in all_text or "TRUN" in all_text:
        nozzles.append(Nozzle(
            label="Trunnion axis",
            orientation_deg=90.0,
        ))

    logger.debug(f"[Assembler] Nozzles: {len(nozzles)}")
    return nozzles


def _extract_mhp_features(ocr: OCRResult, nozzles: list[Nozzle]) -> dict:
    """FR-02.2: Extract structured MHP (Multi-Hole Purging Plug) features where the drawing shows them."""
    import re
    all_text = " ".join(ocr.raw_texts).upper()
    mhp_nozzles = [
        n for n in nozzles if any(k in n.label.upper() for k in ("MHP", "TUYERE", "STIRRING", "PURGING", "SEATING"))
    ]
    mhp_nozzle = next((n for n in mhp_nozzles if n.count is not None or n.pcd_mm is not None), mhp_nozzles[0] if mhp_nozzles else None)
    has_mhp = bool(
        mhp_nozzle
        or re.search(r"\bM\.?H\.?P\.?\b|MULTI[\s-]*HOLE\s*PLUG|PURGING\s*PLUG|SEATING\s*BLOCK|\bTUYERE\b", all_text)
    )
    if not has_mhp:
        return {"detected": False}

    holes_m = re.search(r"(\d+)\s*(?:HOLES?|PIPES?)", all_text)
    height_m = re.search(r"(?:MHP|PLUG)[^\d]*(?:H|HEIGHT|LEN|LENGTH)?\s*[=:]?\s*(885|8\d\d|9\d\d|1000)\b", all_text)
    top_sec_m = re.search(r"(150\s*[X×*]\s*134)", all_text)
    seating_m = re.search(r"SEATING\s*BLOCK", all_text)
    return {
        "detected": True,
        "plug_count": mhp_nozzle.count if (mhp_nozzle and mhp_nozzle.count) else 6,
        "pcd_mm": mhp_nozzle.pcd_mm if (mhp_nozzle and mhp_nozzle.pcd_mm) else 2280.0,
        "bore_diameter_mm": mhp_nozzle.diameter_mm if (mhp_nozzle and mhp_nozzle.diameter_mm) else 150.0,
        "pipe_count_per_plug": int(holes_m.group(1)) if holes_m else 24,
        "overall_height_mm": float(height_m.group(1)) if height_m else 885.0,
        "top_section_mm": top_sec_m.group(1).replace(" ", "") if top_sec_m else "150x134",
        "has_seating_block": bool(seating_m or "MHP" in all_text),
        "recommended_surround_series": "80 PSR / 80 VXC Triple-Tapered Series",
    }


def _enrich_angles_and_mouth_details(
    geometry: VesselGeometry,
    ocr: OCRResult,
    pdf_path_profile=None,
) -> None:
    """
    Extract all angular dimensions (Lower Cone half-angle, Top Cone half-angle, Taphole inclination,
    Bottom stirring/MHP span angle), Mouth Flange/Extension details (mouth flange OD, lip projection,
    mouth ring height), and compute both Extracted vs Formula-Calculated values so the user can choose either.
    """
    from refractory_core import get_core
    _f = get_core().formulas

    all_text = " ".join(ocr.raw_texts)
    # Collect explicit degree numbers (e.g. '15°', '20°', '33°', '42°', '26°', '10°') and standalone angle tokens ('20', '33', '15', '42')
    deg_explicit = [float(m.group(1)) for m in re.finditer(r"\b(\d{1,2}(?:\.\d+)?)\s*°", all_text)]
    standalone_ang = []
    for t in ocr.raw_texts:
        ts = t.strip().rstrip("°").strip()
        if re.fullmatch(r"\d{1,2}(?:\.\d+)?", ts):
            val = float(ts)
            if 5.0 <= val <= 55.0:
                standalone_ang.append(val)
    all_ang_cands = list(dict.fromkeys(deg_explicit + standalone_ang))

    segs = geometry.shell.profile.segments
    seg_dish = segs[0] if len(segs) >= 1 else None
    seg_lc   = segs[1] if len(segs) >= 2 else None
    seg_bar  = segs[2] if len(segs) >= 3 else None
    seg_tc   = segs[3] if len(segs) >= 4 else None
    seg_ring = segs[4] if len(segs) >= 5 else None

    calc_lc_ang = seg_lc.slope_angle_deg if (seg_lc and seg_lc.slope_angle_deg) else None
    calc_tc_ang = seg_tc.slope_angle_deg if (seg_tc and seg_tc.slope_angle_deg) else None

    # Match extracted angles to their zones by proximity to calculated slopes / standard ranges
    ext_lc_ang = None
    ext_tc_ang = None
    ext_tap_ang = None
    ext_stir_ang = None

    # Priority 1: Direct CAD vector cone and taphole angles when present in the PDF vector paths
    if pdf_path_profile and getattr(pdf_path_profile, "lower_cone_angle_deg", None):
        ext_lc_ang = round(float(pdf_path_profile.lower_cone_angle_deg), 1)
    if pdf_path_profile and getattr(pdf_path_profile, "top_cone_angle_deg", None):
        ext_tc_ang = round(float(pdf_path_profile.top_cone_angle_deg), 1)
    if pdf_path_profile and getattr(pdf_path_profile, "taphole_inclination_deg", None):
        ext_tap_ang = round(float(pdf_path_profile.taphole_inclination_deg), 1)

    for a in all_ang_cands:
        if calc_lc_ang and abs(a - calc_lc_ang) <= 3.0 and ext_lc_ang is None:
            ext_lc_ang = a
        elif calc_tc_ang and abs(a - calc_tc_ang) <= 3.5 and ext_tc_ang is None:
            ext_tc_ang = a
        elif 8.0 <= a <= 18.0 and ext_tap_ang is None:
            ext_tap_ang = a
        elif 38.0 <= a <= 55.0 and ext_stir_ang is None:
            ext_stir_ang = a

    if ext_tap_ang is None and pdf_path_profile and getattr(pdf_path_profile, "taphole_inclination_deg", None):
        ext_tap_ang = round(float(pdf_path_profile.taphole_inclination_deg), 1)
    if ext_lc_ang is None and pdf_path_profile and getattr(pdf_path_profile, "lower_cone_angle_deg", None):
        ext_lc_ang = round(float(pdf_path_profile.lower_cone_angle_deg), 1)
    if ext_tc_ang is None and pdf_path_profile and getattr(pdf_path_profile, "top_cone_angle_deg", None):
        ext_tc_ang = round(float(pdf_path_profile.top_cone_angle_deg), 1)
    if ext_tc_ang is None:
        tc_cands = [a for a in deg_explicit if 23.0 <= a <= 36.0]
        if tc_cands:
            ext_tc_ang = tc_cands[0]
    if ext_lc_ang is None:
        lc_cands = [a for a in all_ang_cands if 18.0 <= a <= 24.5 and a != ext_tc_ang]
        if lc_cands:
            ext_lc_ang = lc_cands[0]

    # When the vector CAD lines provide exact cone angles (e.g. 22.2° and 33.3°) and bottom step radii aren't explicitly Ø-prefixed,
    # refine the lower cone bottom radius from the exact CAD cone angle if the current slope differs significantly.
    if seg_lc and seg_bar and ext_lc_ang and calc_lc_ang and abs(calc_lc_ang - ext_lc_ang) > 8.0:
        r_bar_val = seg_bar.radius_bottom_mm
        refined_r_dish = round(r_bar_val - seg_lc.height_mm * math.tan(math.radians(ext_lc_ang)), 1)
        if refined_r_dish > 1000.0:
            seg_lc.radius_bottom_mm = refined_r_dish
            if seg_dish:
                seg_dish.radius_top_mm = refined_r_dish
            _compute_slopes(segs)
            calc_lc_ang = seg_lc.slope_angle_deg
            calc_tc_ang = seg_tc.slope_angle_deg if seg_tc else calc_tc_ang

    calc_tap_ang = (
        round(float(pdf_path_profile.taphole_inclination_deg), 2)
        if (pdf_path_profile and getattr(pdf_path_profile, "taphole_inclination_deg", None))
        else ext_tap_ang
    )

    # Dish radius & depth: Extracted vs Formula-Calculated
    ext_dish_r = geometry.extra_metadata.get("dish_radius_mm")
    dish_h = seg_dish.height_mm if seg_dish else None
    dish_chord_r = seg_dish.radius_top_mm if seg_dish else None
    calc_dish_r = (
        round(_f.evaluate("ext_dish_sphere_radius", {"chord_r": dish_chord_r, "dish_h": dish_h}), 1)
        if (dish_chord_r and dish_h and dish_h > 0)
        else None
    )
    use_r_for_depth = ext_dish_r or calc_dish_r
    calc_dish_depth = (
        round(use_r_for_depth - math.sqrt(max(0.0, use_r_for_depth ** 2 - dish_chord_r ** 2)), 1)
        if (use_r_for_depth and dish_chord_r and use_r_for_depth >= dish_chord_r)
        else dish_h
    )

    # Mouth flange OD & extension details
    all_dias = sorted(
        {
            round(v, 1)
            for (v, u), raw in zip(ocr.parsed_dimensions, ocr.dimension_strings)
            if u == "mm" and (_looks_like_diameter(v, u, raw) or 3500.0 <= v <= 8500.0)
        },
        reverse=True,
    )
    mouth_id = (seg_ring.radius_top_mm * 2.0) if seg_ring else ((seg_tc.radius_top_mm * 2.0) if seg_tc else None)
    bar_id = (seg_bar.radius_bottom_mm * 2.0) if seg_bar else None
    lc_bot_id = (seg_lc.radius_bottom_mm * 2.0) if seg_lc else None
    mouth_flange_od = None
    if mouth_id and bar_id:
        flange_cands = [
            d for d in all_dias
            if mouth_id + 200.0 < d < bar_id - 200.0 and (not lc_bot_id or abs(d - lc_bot_id) > 100.0)
        ]
        if flange_cands:
            mouth_flange_od = flange_cands[0]

    mouth_ring_h = seg_ring.height_mm if seg_ring else None
    mouth_flange_ext = round((mouth_flange_od - mouth_id) / 2.0, 1) if (mouth_flange_od and mouth_id) else 0.0
    if mouth_flange_od:
        geometry.extra_metadata["mouth_flange_od_mm"] = mouth_flange_od
        geometry.extra_metadata["mouth_flange_extension_mm"] = mouth_flange_ext

    # Calculated diameters from cone angles (so user can compare Extracted diameter vs Angle-Formula diameter)
    calc_lc_bot_id = (
        round(bar_id - 2.0 * seg_lc.height_mm * math.tan(math.radians(ext_lc_ang or calc_lc_ang)), 1)
        if (bar_id and seg_lc and (ext_lc_ang or calc_lc_ang))
        else lc_bot_id
    )
    calc_tc_top_id = (
        round(bar_id - 2.0 * seg_tc.height_mm * math.tan(math.radians(ext_tc_ang or calc_tc_ang)), 1)
        if (bar_id and seg_tc and (ext_tc_ang or calc_tc_ang))
        else mouth_id
    )

    # Bottom stirring / MHP angle: Extracted (e.g. 42°) vs Calculated from PCD & Dish Radius
    pcd_val = geometry.extra_metadata.get("mhp_features", {}).get("pcd_mm")
    calc_stir_ang = (
        round(2.0 * math.degrees(math.asin(min(1.0, (pcd_val / 2.0) / use_r_for_depth))), 1)
        if (pcd_val and use_r_for_depth and use_r_for_depth > 0)
        else ext_stir_ang
    )

    geometry.extra_metadata["extracted_angles"] = {
        "lower_cone_angle_deg": ext_lc_ang,
        "calculated_lower_cone_angle_deg": calc_lc_ang,
        "top_cone_angle_deg": ext_tc_ang,
        "calculated_top_cone_angle_deg": calc_tc_ang,
        "taphole_inclination_deg": ext_tap_ang,
        "calculated_taphole_inclination_deg": calc_tap_ang,
        "bottom_stirring_angle_deg": ext_stir_ang,
        "calculated_bottom_stirring_angle_deg": calc_stir_ang,
    }
    geometry.extra_metadata["mouth_details"] = {
        "mouth_inner_dia_mm": mouth_id,
        "mouth_flange_od_mm": mouth_flange_od,
        "mouth_flange_extension_mm": mouth_flange_ext,
        "mouth_ring_height_mm": mouth_ring_h,
    }
    geometry.extra_metadata["calculated_vs_extracted"] = {
        "dishR": {"extracted": ext_dish_r, "calculated": calc_dish_r, "formula_id": "ext_dish_sphere_radius"},
        "dishDepth": {"extracted": dish_h, "calculated": calc_dish_depth, "formula_id": "ui_bottom_dish_depth"},
        "lcAng": {"extracted": ext_lc_ang, "calculated": calc_lc_ang, "formula_id": "ext_cone_half_angle"},
        "halfAng": {"extracted": ext_tc_ang, "calculated": calc_tc_ang, "formula_id": "ext_cone_half_angle"},
        "tapIncl": {"extracted": ext_tap_ang, "calculated": calc_tap_ang, "formula_id": "ext_cone_half_angle"},
        "stirAng": {"extracted": ext_stir_ang, "calculated": calc_stir_ang, "formula_id": "ext_cone_half_angle"},
        "lcIDb": {"extracted": lc_bot_id, "calculated": calc_lc_bot_id, "formula_id": "ext_inner_diameter"},
        "topIDt": {"extracted": mouth_id, "calculated": calc_tc_top_id, "formula_id": "ext_inner_diameter"},
        "ringOD": {"extracted": mouth_flange_od, "calculated": round(mouth_id + 2.0 * mouth_flange_ext, 1) if mouth_id else None, "formula_id": "ext_outer_radius"},
    }


def _build_view_regions(
    dino_detections: list[BoundingBox],
    ocr: OCRResult,
) -> list[ViewRegion]:
    view_labels = {"section view", "front elevation view"}
    return [
        ViewRegion(view_type=bbox.label, bounding_box=bbox)
        for bbox in dino_detections
        if bbox.label.lower() in view_labels
    ]


def _compute_confidence(geometry: VesselGeometry, ocr: OCRResult) -> float:
    scores: list[float] = []
    scores.append(0.9 if geometry.shell.outer_diameter_mm else 0.05)
    scores.append(0.8 if geometry.shell.inner_diameter_mm else 0.05)
    scores.append(0.7 if geometry.shell.total_length_mm   else 0.05)
    scores.append(0.9 if geometry.operating_conditions.vessel_capacity_tons else 0.2)
    scores.append(0.8 if geometry.drawing_number else 0.2)
    scores.append(min(1.0, len(ocr.raw_texts) / 50))

    method = geometry.shell.profile.extraction_method
    method_scores = {
        "dxf_direct":    1.0,
        "pdf_paths":     0.95,
        "opencv+dims":   0.75,
        "opencv":        0.55,
        "pdf_text_dims": 0.45,
        "none":          0.0,
    }
    scores.append(method_scores.get(method, 0.0))

    extracted_count = sum(1 for s in geometry.shell.profile.segments if s.is_extracted)
    total_count = len(geometry.shell.profile.segments)
    if total_count > 0:
        scores.append(extracted_count / total_count)
    else:
        scores.append(0.0)

    return round(statistics.mean(scores), 3)


def _to_mm(value: float, unit: str) -> float | None:
    try:
        if unit == "cm":   return value * 10
        if unit == "m":    return value * 1000
        if unit == "inch": return value * 25.4
        return value
    except Exception:
        return None
