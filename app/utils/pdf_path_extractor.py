"""
app/utils/pdf_path_extractor.py
================================
Extract vector paths (drawn lines/curves) from a vector PDF.

For CAD-generated PDFs, the vessel outline is stored as PDF path objects
(not rasterised pixels). This module reads those paths directly using
pdfplumber's low-level page primitives, giving us the exact geometric
profile of the vessel as drawn — without any heuristic assumptions.

Pipeline:
  1. Enumerate all path objects on each page
  2. Flatten to line segments (curves are approximated)
  3. Find the largest contiguous outline (= vessel profile)
  4. Sample the radius profile at evenly-spaced height intervals
  5. Return (y_mm, r_mm) ProfilePoint list ordered bottom→top

Returns [] if no usable paths are found.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from loguru import logger


class ProfilePoint(NamedTuple):
    y_mm: float   # axial position (0 = bottom)
    r_mm: float   # outer shell radius at this axial position


@dataclass
class PDFPathProfile:
    """Result of vector-path extraction from a PDF."""
    points: list[ProfilePoint] = field(default_factory=list)
    total_height_mm: float = 0.0
    max_radius_mm: float = 0.0
    page_width_pt: float = 0.0
    page_height_pt: float = 0.0
    taphole_inclination_deg: float | None = None
    lower_cone_angle_deg: float | None = None
    top_cone_angle_deg: float | None = None
    barrel_span_pt: float | None = None
    mouth_throat_span_pt: float | None = None
    mouth_flange_span_pt: float | None = None
    mouth_throat_to_barrel_ratio: float | None = None
    mouth_flange_to_barrel_ratio: float | None = None
    mouth_flange_ratio: float | None = None
    success: bool = False


def extract_pdf_path_profile(pdf_path: str | Path, text_polygons: list = None) -> PDFPathProfile:
    """
    Extract the vessel cross-section profile directly from PDF vector paths.

    Uses pdfplumber's rect/curve/line primitives (not text, not raster).
    Returns PDFPathProfile(success=False) if no paths found.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.warning("[PDF-Paths] pdfplumber not installed")
        return PDFPathProfile()

    path = Path(pdf_path)
    if not path.exists():
        return PDFPathProfile()

    result = PDFPathProfile()

    try:
        with pdfplumber.open(str(path)) as pdf:
            if not pdf.pages:
                return result

            page = pdf.pages[0]
            result.page_width_pt  = float(page.width)
            result.page_height_pt = float(page.height)

            # Collect all line segments from PDF paths
            # pdfplumber exposes: page.lines, page.curves, page.rects
            # If the CAD PDF separates structural shell lines (black/gray) from dimension/extension lines (red/blue),
            # filter out the colored annotation layers and tiny stroked text glyphs so only the structural shell is traced.
            def _is_neutral_stroke(col) -> bool:
                if col in (None, 0, 0.0, (0,), (0.0,), (0, 0, 0), (0.0, 0.0, 0.0)):
                    return True
                if isinstance(col, (list, tuple)) and len(col) == 3:
                    return (max(col) - min(col)) < 0.2 and max(col) < 0.65
                return False

            raw_lines = page.lines or []
            neutral_lines = [
                l for l in raw_lines
                if _is_neutral_stroke(l.get("stroking_color"))
                and math.hypot(float(l.get("x1", 0)) - float(l.get("x0", 0)), float(l.get("y1", 0)) - float(l.get("y0", 0))) >= 10.0
            ]
            use_neutral_only = len(neutral_lines) >= 8 and len(neutral_lines) < len(raw_lines)

            segments: list[tuple[float, float, float, float]] = []

            for line in (neutral_lines if use_neutral_only else raw_lines):
                try:
                    x0 = float(line["x0"])
                    y0 = float(line["y0"])
                    x1 = float(line["x1"])
                    y1 = float(line["y1"])
                    if (abs(x1 - x0) >= result.page_width_pt * 0.72) or (abs(y1 - y0) >= result.page_height_pt * 0.72):
                        continue
                    segments.append((x0, y0, x1, y1))
                except (KeyError, TypeError):
                    pass

            for curve in (page.curves or []):
                # Approximate the Bezier curve with straight line segments
                try:
                    if use_neutral_only and not _is_neutral_stroke(curve.get("stroking_color")):
                        continue
                    pts = curve.get("pts", [])
                    if len(pts) >= 2:
                        xs = [float(p[0]) for p in pts]
                        ys = [float(p[1]) for p in pts]
                        # Skip tiny stroked text character glyphs (< 14 pt bounding box) or full-page border curves
                        c_dx, c_dy = max(xs) - min(xs), max(ys) - min(ys)
                        if use_neutral_only and max(c_dx, c_dy) < 14.0:
                            continue
                        if c_dx >= result.page_width_pt * 0.72 or c_dy >= result.page_height_pt * 0.72:
                            continue
                        for i in range(len(pts) - 1):
                            x1, y1 = pts[i]
                            x2, y2 = pts[i + 1]
                            segments.append((x1, y1, x2, y2))
                except (KeyError, TypeError):
                    pass
                    
            # Use CADGraph to filter out segments that intersect with text bounding boxes
            if text_polygons:
                from app.services.part2_extraction.graph_engine.cad_graph import CADGraph
                from shapely.geometry import LineString, Polygon
                from shapely.affinity import scale, translate
                
                # Convert text_polygons from Image pixels to PDF points
                # file_handler.py renders with zoom = 5.5
                ZOOM = 5.5
                scaled_masks = []
                for p in text_polygons:
                    # In pdfplumber, y=0 is top and y increases downwards just like images.
                    p_scaled = scale(p, xfact=1/ZOOM, yfact=1/ZOOM, origin=(0, 0))
                    scaled_masks.append(p_scaled)
                    
                graph = CADGraph(tolerance=1.0) # 1pt = ~0.35mm
                lines = [LineString([(x1, y1), (x2, y2)]) for (x1, y1, x2, y2) in segments]
                graph.add_lines(lines)
                
                graph.filter_by_masks(scaled_masks)
                
                # Retrieve filtered segments
                filtered_segments = []
                for u, v, data in graph.graph.edges(data=True):
                    line = data['linestring']
                    coords = list(line.coords)
                    filtered_segments.append((coords[0][0], coords[0][1], coords[1][0], coords[1][1]))
                segments = filtered_segments
                logger.info(f"[PDF-Paths] Filtered with DocTR: {len(segments)} segments remaining")

            for rect in (page.rects or []):
                try:
                    if use_neutral_only and not _is_neutral_stroke(rect.get("stroking_color")):
                        continue
                    x0 = float(rect["x0"])
                    y0 = float(rect["y0"])
                    x1 = float(rect["x1"])
                    y1 = float(rect["y1"])
                    # Skip full-page border frames (> 75% of page width or height)
                    if (abs(x1 - x0) >= result.page_width_pt * 0.72) or (abs(y1 - y0) >= result.page_height_pt * 0.72):
                        continue
                    # Add 4 edges of the rectangle
                    segments.extend([
                        (x0, y0, x1, y0),
                        (x1, y0, x1, y1),
                        (x1, y1, x0, y1),
                        (x0, y1, x0, y0),
                    ])
                except (KeyError, TypeError):
                    pass

            if not segments:
                logger.info("[PDF-Paths] No path segments found in PDF")
                return result

            logger.info(f"[PDF-Paths] Found {len(segments)} path segments")

            # Cluster segments and pick the dominant vessel view cluster (side view)
            vessel_cluster = _select_vessel_cluster(
                segments,
                page_w=result.page_width_pt,
                page_h=result.page_height_pt,
            )

            # ── Build radius profile ─────────────────────────────────────────
            # In PDF coordinates, y=0 is at the BOTTOM of the page.
            # The vessel is typically the dominant vertical structure.
            profile_pts = _build_profile_from_segments(
                segments,
                page_w=result.page_width_pt,
                page_h=result.page_height_pt,
                preselected_cluster=vessel_cluster,
            )

            if len(profile_pts) < 3:
                logger.info("[PDF-Paths] Insufficient profile points from PDF paths")
                return result

            # Convert from PDF points (pt) to mm (1 pt = 0.352778 mm)
            PT_TO_MM = 0.352778
            profile_mm = [
                ProfilePoint(y_mm=p.y_mm * PT_TO_MM, r_mm=p.r_mm * PT_TO_MM)
                for p in profile_pts
            ]

            # Detect taphole centerline inclination from magenta (1,0,1) or red (1,0,0) centerline segments (CAD convention)
            mag_angles_h: list[float] = []
            mag_angles_v: list[float] = []
            has_horiz_cl = False
            has_vert_cl = False
            for line in (page.lines or []):
                try:
                    col = line.get("stroking_color")
                    is_cl_color = (
                        col in ((1, 0, 1), (1, 0, 0), (1.0, 0.0, 1.0), (1.0, 0.0, 0.0))
                        or (isinstance(col, (list, tuple)) and len(col) == 3 and col[0] > 0.8 and col[1] < 0.2)
                    )
                    if is_cl_color:
                        dx = float(line["x1"]) - float(line["x0"])
                        dy = float(line["y1"]) - float(line["y0"])
                        ln = math.hypot(dx, dy)
                        if ln > 3.0:
                            ang = abs(math.degrees(math.atan2(dy, dx)))
                            if ang > 90.0:
                                ang = 180.0 - ang
                            if ang < 1.0 and ln > 8.0:
                                has_horiz_cl = True
                            elif ang > 89.0 and ln > 8.0:
                                has_vert_cl = True
                            elif 58.0 <= ang <= 85.0:
                                mag_angles_h.append(90.0 - ang)
                            elif 5.0 <= ang <= 35.0:
                                mag_angles_v.append(ang)
                except (KeyError, TypeError):
                    pass
            incls = mag_angles_h if (has_horiz_cl and mag_angles_h) else (mag_angles_v if (has_vert_cl and mag_angles_v) else (mag_angles_h or mag_angles_v))
            if incls:
                incls.sort()
                result.taphole_inclination_deg = round(incls[len(incls) // 2], 1)
                logger.info(f"[PDF-Paths] Extracted taphole inclination from centerline: {result.taphole_inclination_deg}°")

            # Also extract exact lower cone and top cone half-angles directly from the long structural black cone lines (>=65 pt).
            # When the vessel is drawn horizontally (x_span > y_span), cone wall inclination from the vessel axis is tan^-1(|dy|/|dx|);
            # when drawn vertically (y_span >= x_span), cone wall inclination from the vertical axis is tan^-1(|dx|/|dy|).
            # Note: pdfplumber normalizes line coordinates so (y0, y1) = (bottom, top), which means on a sloped line
            # (x1, y0) and (x2, y1) may be paired in reverse order; we recover the true endpoints below.
            v_segs = vessel_cluster if vessel_cluster else segments
            all_xs = [x for x1, y1, x2, y2 in v_segs for x in (x1, x2)]
            all_ys = [y for x1, y1, x2, y2 in v_segs for y in (y1, y2)]
            x_span_v = (max(all_xs) - min(all_xs)) if all_xs else 0.0
            y_span_v = (max(all_ys) - min(all_ys)) if all_ys else 0.0
            # Determine vessel axis orientation directly from the parallel barrel lines (the longest horizontal or vertical lines >= 100 pt)
            horiz_bar_len = sum(math.hypot(x2 - x1, y2 - y1) for x1, y1, x2, y2 in v_segs if math.hypot(x2 - x1, y2 - y1) >= 100.0 and abs(y2 - y1) < 2.0)
            vert_bar_len = sum(math.hypot(x2 - x1, y2 - y1) for x1, y1, x2, y2 in v_segs if math.hypot(x2 - x1, y2 - y1) >= 100.0 and abs(x2 - x1) < 2.0)
            vessel_horiz = (horiz_bar_len >= vert_bar_len) if (horiz_bar_len > 0 or vert_bar_len > 0) else (x_span_v > y_span_v)
            cone_lines: list[tuple[float, float]] = []  # (axis_mid, angle_from_axis_deg)
            for x1, y1, x2, y2 in v_segs:
                ln = math.hypot(x2 - x1, y2 - y1)
                if ln >= 65.0:
                    dx_abs, dy_abs = abs(x2 - x1), abs(y2 - y1)
                    ang = abs(math.degrees(math.atan2(dy_abs, dx_abs) if vessel_horiz else math.atan2(dx_abs, dy_abs)))
                    if 16.0 <= ang <= 42.0:
                        axis_pos = (x1 + x2) / 2.0 if vessel_horiz else (y1 + y2) / 2.0
                        cone_lines.append((axis_pos, round(ang, 2)))
            if cone_lines:
                cone_lines.sort(key=lambda t: t[0])
                mid_split = (min(t[0] for t in cone_lines) + max(t[0] for t in cone_lines)) * 0.5
                low_angs = [a for pos, a in cone_lines if pos < mid_split]
                high_angs = [a for pos, a in cone_lines if pos >= mid_split]
                if low_angs:
                    low_angs.sort()
                    result.lower_cone_angle_deg = round(low_angs[len(low_angs) // 2], 2)
                if high_angs:
                    high_angs.sort()
                    result.top_cone_angle_deg = round(high_angs[len(high_angs) // 2], 2)

            # Extract scale-invariant mouth section ratios (throat opening & mouth flange collar) directly from CAD vector paths:
            # 1. Measure the parallel barrel lines' transverse span (barrel_span_pt)
            # 2. At the top-cone / mouth end, measure the cone-tip throat span (mouth_throat_span_pt) and the outer flange face span (mouth_flange_span_pt)
            axis_min = min(all_xs) if vessel_horiz else min(all_ys)
            axis_max = max(all_xs) if vessel_horiz else max(all_ys)
            axis_span = max(axis_max - axis_min, 1.0)

            barrel_trans_coords: list[float] = []
            barrel_axis_vals: list[float] = []
            for x1, y1, x2, y2 in v_segs:
                ln = math.hypot(x2 - x1, y2 - y1)
                dx_abs, dy_abs = abs(x2 - x1), abs(y2 - y1)
                ang = abs(math.degrees(math.atan2(dy_abs, dx_abs) if vessel_horiz else math.atan2(dx_abs, dy_abs)))
                a1, t1 = (x1, y1) if vessel_horiz else (y1, x1)
                a2, t2 = (x2, y2) if vessel_horiz else (y2, x2)
                a_mid = (a1 + a2) / 2.0
                if ln >= 100.0 and ang < 1.5 and (axis_min + 0.12 * axis_span) <= a_mid <= (axis_min + 0.82 * axis_span):
                    barrel_trans_coords.extend([t1, t2])
                    barrel_axis_vals.extend([a1, a2])

            if len(barrel_trans_coords) >= 2:
                b_min, b_max = min(barrel_trans_coords), max(barrel_trans_coords)
                b_center = (b_min + b_max) / 2.0
                low_wall = sorted([t for t in barrel_trans_coords if t < b_center])
                high_wall = sorted([t for t in barrel_trans_coords if t > b_center])
                if low_wall and high_wall:
                    inner_span = min(high_wall) - max(low_wall)
                    outer_span = max(high_wall) - min(low_wall)
                    result.barrel_span_pt = round(inner_span if inner_span > outer_span * 0.85 else outer_span, 2)

                # For top cone lines (which converge toward b_center as they move away from the barrel),
                # the mouth-end transverse coordinate of each cone line is ALWAYS the endpoint closer to b_center!
                bar_a_mid = (min(barrel_axis_vals) + max(barrel_axis_vals)) / 2.0 if barrel_axis_vals else (axis_min + 0.45 * axis_span)
                top_cone_tip_trans: list[float] = []
                top_cone_tip_axes: list[float] = []
                for x1, y1, x2, y2 in v_segs:
                    ln = math.hypot(x2 - x1, y2 - y1)
                    dx_abs, dy_abs = abs(x2 - x1), abs(y2 - y1)
                    ang = abs(math.degrees(math.atan2(dy_abs, dx_abs) if vessel_horiz else math.atan2(dx_abs, dy_abs)))
                    a1, t1 = (x1, y1) if vessel_horiz else (y1, x1)
                    a2, t2 = (x2, y2) if vessel_horiz else (y2, x2)
                    a_mid = (a1 + a2) / 2.0
                    if ln >= 65.0 and 16.0 <= ang <= 42.0 and a_mid > bar_a_mid:
                        tip_t = t1 if abs(t1 - b_center) < abs(t2 - b_center) else t2
                        tip_a = max(a1, a2)
                        top_cone_tip_trans.append(tip_t)
                        top_cone_tip_axes.append(tip_a)

                if result.barrel_span_pt and result.barrel_span_pt > 20.0 and len(top_cone_tip_trans) >= 2:
                    tip_axis = max(top_cone_tip_axes)
                    low_tips = sorted([t for t in top_cone_tip_trans if t < b_center])
                    high_tips = sorted([t for t in top_cone_tip_trans if t > b_center])
                    if low_tips and high_tips:
                        throat_inner = min(high_tips) - max(low_tips)
                        throat_outer = max(high_tips) - min(low_tips)
                        throat_span = throat_inner if throat_inner > throat_outer * 0.80 else throat_outer
                        if result.barrel_span_pt * 0.35 <= throat_span <= result.barrel_span_pt * 0.88:
                            result.mouth_throat_span_pt = round(throat_span, 2)
                            result.mouth_throat_to_barrel_ratio = round(throat_span / result.barrel_span_pt, 5)

                    # Look for perpendicular mouth flange lines at or beyond the top-cone tip
                    flange_trans: list[float] = []
                    for x1, y1, x2, y2 in v_segs:
                        a1, t1 = (x1, y1) if vessel_horiz else (y1, x1)
                        a2, t2 = (x2, y2) if vessel_horiz else (y2, x2)
                        if min(a1, a2) >= tip_axis - axis_span * 0.015 and max(a1, a2) <= axis_max + axis_span * 0.02:
                            if abs(t1 - b_center) <= result.barrel_span_pt * 0.52 and abs(t2 - b_center) <= result.barrel_span_pt * 0.52:
                                flange_trans.extend([t1, t2])
                    if len(flange_trans) >= 2:
                        fl_span = max(flange_trans) - min(flange_trans)
                        if result.barrel_span_pt * 0.45 <= fl_span <= result.barrel_span_pt * 0.95:
                            result.mouth_flange_span_pt = round(fl_span, 2)
                            result.mouth_flange_to_barrel_ratio = round(fl_span / result.barrel_span_pt, 5)
                            if result.mouth_throat_span_pt and fl_span > result.mouth_throat_span_pt * 1.03:
                                result.mouth_flange_ratio = round(fl_span / result.mouth_throat_span_pt, 5)

            result.points = profile_mm
            result.total_height_mm = profile_mm[-1].y_mm - profile_mm[0].y_mm if profile_mm else 0.0
            result.max_radius_mm   = max(p.r_mm for p in profile_mm) if profile_mm else 0.0
            result.success = True

            logger.info(
                f"[PDF-Paths] Profile extracted: {len(profile_mm)} pts, "
                f"height={result.total_height_mm:.1f}mm, "
                f"max_r={result.max_radius_mm:.1f}mm"
            )

    except Exception as exc:
        logger.warning(f"[PDF-Paths] Extraction failed: {exc}")
        return PDFPathProfile()

    return result


def _select_vessel_cluster(
    segments: list[tuple[float, float, float, float]],
    page_w: float,
    page_h: float,
) -> list[tuple[float, float, float, float]]:
    """Select the primary vessel view cluster (filtering out title blocks, page borders, and small views)."""
    if not segments or page_h < 1:
        return []

    min_seg_len = max(page_w, page_h) * 0.002
    filtered: list[tuple[float, float, float, float]] = []
    for seg in segments:
        x1, y1, x2, y2 = seg
        length = math.hypot(x2 - x1, y2 - y1)
        # Skip page-border frame lines (>75% of page width or height near the edge)
        if length >= max(page_w, page_h) * 0.72:
            continue
        if length >= min_seg_len:
            filtered.append(seg)

    if not filtered:
        return []

    distance_threshold = max(page_w, page_h) * 0.025
    parent = {i: i for i in range(len(filtered))}

    def find(i):
        if parent[i] == i:
            return i
        parent[i] = find(parent[i])
        return parent[i]

    def union(i, j):
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    bboxes = [(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)) for x1, y1, x2, y2 in filtered]
    for i in range(len(filtered)):
        ix_min, iy_min, ix_max, iy_max = bboxes[i]
        for j in range(i + 1, len(filtered)):
            jx_min, jy_min, jx_max, jy_max = bboxes[j]
            dx = max(0, max(ix_min, jx_min) - min(ix_max, jx_max))
            dy = max(0, max(iy_min, jy_min) - min(iy_max, jy_max))
            if dx <= distance_threshold and dy <= distance_threshold:
                union(i, j)

    clusters_map: dict[int, list[tuple[float, float, float, float]]] = {}
    for i in range(len(filtered)):
        root = find(i)
        clusters_map.setdefault(root, []).append(filtered[i])

    best_cluster = filtered
    best_score = -1.0
    for cluster in clusters_map.values():
        ys = [y for seg in cluster for y in (seg[1], seg[3])]
        xs = [x for seg in cluster for x in (seg[0], seg[2])]
        y_span = max(ys) - min(ys)
        x_span = max(xs) - min(xs)
        if y_span < 1 or x_span < 1:
            continue
        area = x_span * y_span
        aspect_ratio = max(x_span, y_span) / min(x_span, y_span)
        is_h = x_span >= y_span
        has_cones = sum(
            1 for x1, y1, x2, y2 in cluster
            if math.hypot(x2 - x1, y2 - y1) >= 55.0
            and 16.0 <= abs(math.degrees(math.atan2(abs(y2 - y1), abs(x2 - x1)) if is_h else math.atan2(abs(x2 - x1), abs(y2 - y1)))) <= 42.0
        )
        score = area * (1.0 + 0.5 * min(has_cones, 6))
        if has_cones < 2:
            score *= 0.05
        if aspect_ratio > 2.2:
            score *= 0.1
        if score > best_score:
            best_score = score
            best_cluster = cluster

    return best_cluster


def _build_profile_from_segments(
    segments: list[tuple[float, float, float, float]],
    page_w: float,
    page_h: float,
    n_samples: int = 200,
    preselected_cluster: list[tuple[float, float, float, float]] | None = None,
) -> list[ProfilePoint]:
    """
    Scan through the vessel vertically (or horizontally if rotated) and at each 
    level find the maximum transverse extent of the drawn paths → the vessel outer radius.
    """
    if not segments or page_h < 1:
        return []

    min_seg_len = max(page_w, page_h) * 0.002
    best_cluster = preselected_cluster or _select_vessel_cluster(segments, page_w, page_h)
    if not best_cluster:
        return []

    # Use the best cluster (the side view) for extraction
    ys = [y for seg in best_cluster for y in (seg[1], seg[3])]
    xs = [x for seg in best_cluster for x in (seg[0], seg[2])]
    y_min, y_max = min(ys), max(ys)
    x_min, x_max = min(xs), max(xs)

    y_span = y_max - y_min
    x_span = x_max - x_min
    if y_span < 1 and x_span < 1:
        return []

    profile: list[ProfilePoint] = []
    is_horizontal = x_span > y_span

    if is_horizontal:
        # Scan along X axis
        for i in range(n_samples):
            x_sample = x_min + x_span * i / (n_samples - 1)
            tol = x_span / (n_samples * 2)

            y_vals: list[float] = []
            for x1, y1, x2, y2 in best_cluster:
                seg_x_min = min(x1, x2)
                seg_x_max = max(x1, x2)
                if seg_x_min - tol <= x_sample <= seg_x_max + tol:
                    dx = seg_x_max - seg_x_min
                    if dx < 1e-9:
                        y_interp = (y1 + y2) / 2.0
                    else:
                        t = (x_sample - seg_x_min) / dx
                        y_interp = min(y1, y2) + t * abs(y2 - y1)
                    y_vals.append(y_interp)

            if not y_vals:
                continue

            y_far_bot = min(y_vals)
            y_far_top = max(y_vals)
            half_width = (y_far_top - y_far_bot) / 2.0

            if half_width < min_seg_len * 0.5:
                continue

            y_norm = x_sample - x_min
            profile.append(ProfilePoint(y_mm=y_norm, r_mm=half_width))
    else:
        # Scan along Y axis
        for i in range(n_samples):
            y_sample = y_min + y_span * i / (n_samples - 1)
            tol = y_span / (n_samples * 2)

            x_vals: list[float] = []
            for x1, y1, x2, y2 in best_cluster:
                seg_y_min = min(y1, y2)
                seg_y_max = max(y1, y2)
                if seg_y_min - tol <= y_sample <= seg_y_max + tol:
                    dy = seg_y_max - seg_y_min
                    if dy < 1e-9:
                        x_interp = (x1 + x2) / 2.0
                    else:
                        t = (y_sample - seg_y_min) / dy
                        x_interp = min(x1, x2) + t * abs(x2 - x1)
                    x_vals.append(x_interp)

            if not x_vals:
                continue

            x_far_left  = min(x_vals)
            x_far_right = max(x_vals)
            half_width = (x_far_right - x_far_left) / 2.0

            if half_width < min_seg_len * 0.5:
                continue

            y_norm = y_sample - y_min
            profile.append(ProfilePoint(y_mm=y_norm, r_mm=half_width))

    profile.sort(key=lambda p: p.y_mm)
    return profile
