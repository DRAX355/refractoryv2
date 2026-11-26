"""
app/services/part3_lining/service.py
====================================
Part 3 - Lining Designer service.

Input  : the values the user confirmed in the wizard (vessel profile from teammate 1's extraction,
         design basis, lining concept zones, nozzles) + the brick library.
Output : DXF drawings (lining GA with nozzles, brick shapes, layers/brick layout, zone drawings,
         bottom brick layout, detail brick shapes, MHP drawings, taphole drawings) with PNG/PDF
         previews, the course schedule, bottom ring table, volumes and checks.
"""
from __future__ import annotations

import csv
import datetime
import json
import math
import re
from pathlib import Path

from .lining.adapter import build_engine_inputs
from .lining.bom import build_bom
from .lining.checks import run_checks
from .lining.bricks import band_kind, bottom_rings, course_schedule, design_mhp_and_taphole_assemblies, quantities
from .lining.assemblies import MhpSheet, TapholeSheet, mhp_inputs, taphole_inputs
from .lining.drawings import (BottomLayoutSheet, BrickSheet, LayerSheet, LiningSheet, ShapeSheet, ZoneSheet,
                              aci_hex, assign_mark_colors, mark_info, save_previews)
from .lining import pages as _pages
from .lining.geometry import region_area_moment, reverse_run, sample
from .lining.model import angle_breaks, validate
from .lining.report import build_report

LIB_PATH = Path(__file__).parent / "data" / "trl_brick_library.json"
LEGACY_MOCK_LIB_PATH = Path(__file__).parent / "data" / "brick_library_mock.json"
CLIENT_ASSEMBLIES_PATH = Path(__file__).parent / "data" / "client_assemblies.json"


class LiningInputError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def load_library(payload_lib=None):
    if payload_lib and payload_lib.get("bricks"):
        return payload_lib
    try:
        from refractory_core import get_core
        return get_core().shapes.get_library()
    except Exception:
        p = LIB_PATH if LIB_PATH.exists() else LEGACY_MOCK_LIB_PATH
        return json.loads(p.read_text(encoding="utf-8"))


def _load_default_client_assemblies():
    if CLIENT_ASSEMBLIES_PATH.exists():
        try:
            return json.loads(CLIENT_ASSEMBLIES_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def add_entered_bricks(lib, payload, st):
    """Brick shapes entered in the UI steps (zone brick pairs, mouth pairs, bottom ring bands, permanent
    brick) are added to the library so drawings, BOM and checks use exactly those dimensions.
    An entered mark that also exists in the library with other dimensions replaces it (UI wins)."""
    bricks = {b["mark"]: b for b in lib.get("bricks", [])}
    notes, added = [], {}

    def put(b):
        old = bricks.get(b["mark"])
        if old and old.get("dimensions_mm") != b["dimensions_mm"] and b["mark"] not in added:
            notes.append(f"{b['mark']}: dimensions entered in the UI used instead of the brick library")
        if b["mark"] in added and added[b["mark"]]["dimensions_mm"] != b["dimensions_mm"]:
            notes.append(f"{b['mark']}: entered twice with different dimensions - check the brick pairs")
            return
        bricks[b["mark"]] = b
        added[b["mark"]] = b

    design = payload.get("design") or {}
    for band in design.get("bands", []):
        kind = band.get("kind") or "wall"
        ch = float(band.get("course_height") or (st.get("mouth_course_height") if kind == "mouth" else st.get("course_height")) or 100)
        brick_h = float(band.get("brick_height") or ch)
        for sec in band.get("sectors", []):
            grade = (sec.get("layers") or [{}])[-1].get("grade", "")
            for q in sec.get("pairs") or []:
                try:
                    L = float(q["L"])
                    dims = [(str(q["A"]), float(q["aCold"]), float(q["aHot"])), (str(q["B"]), float(q["bCold"]), float(q["bHot"]))]
                except (KeyError, TypeError, ValueError):
                    continue
                fam = q.get("family") or "standard_trapezoidal"
                for mark, cold, hot in dims:
                    if min(L, cold, hot) <= 0:
                        continue
                    put({"mark": mark, "shape_family": fam, "zone": f"{band['name']} / {sec.get('name', '')}",
                         "quality_grade_internal": grade, "source": "entered in UI",
                         "dimensions_mm": {"length": L, "width_top": cold, "width_bottom": hot, "thickness": brick_h}})
    bot = payload.get("bottom") or {}
    rbs = bot.get("ring_bands") or []
    if rbs and all(all(str(rb.get(k, "")).strip() not in ("", "0") for k in ("aHot", "aCold", "bHot", "bCold")) for rb in rbs):
        w, L = float(bot.get("ring_width") or 0), float(bot.get("brick_length") or 0)
        for rb in rbs:
            for suf, inner, outer in (("A", rb["aHot"], rb["aCold"]), ("B", rb["bHot"], rb["bCold"])):
                put({"mark": f"{rb['mark']}{suf}", "shape_family": "bottom_dish_wedge", "zone": "Bottom",
                     "quality_grade_internal": bot.get("grade", ""), "source": "entered in UI",
                     "dimensions_mm": {"overall_length": L, "face_width_top": w, "face_width_bottom": w,
                                       "A": float(outer), "B": float(inner), "C": float(outer), "D": float(inner)}})
    pb = payload.get("permanent_brick") or {}
    if pb.get("width") and pb.get("height"):
        ts = set()
        mono = payload.get("monolithic") or {}
        names = design.get("layers") or []
        pidx = [0] + [i for i, n in enumerate(names[:-1]) if i and re.fullmatch(r"PERMANENT \d+", str(n))]
        for band in design.get("bands", []):
            for sec in band.get("sectors", []):
                lay = sec.get("layers") or []
                for i in pidx:                                          # every permanent layer (bottom may have several)
                    if len(lay) > 1 and i < len(lay) - 1 and lay[i].get("grade") not in mono:   # castable / filling is not bricks
                        ts.add(float(lay[i]["thickness"]))
        for t in sorted(ts):
            if t > 0:
                put({"mark": f"PL-{float(pb['width']):g}x{float(pb['height']):g}x{t:g}", "shape_family": "safety_lining_std",
                     "zone": "Permanent lining", "quality_grade_internal": "", "source": "entered in UI",
                     "dimensions_mm": {"length": t, "width": float(pb["width"]), "thickness": float(pb["height"])}})
        # the library's own permanent bricks are not used when the permanent brick is entered
        for m in [m for m, b in bricks.items() if b.get("shape_family") == "safety_lining_std" and m not in added]:
            del bricks[m]
    out = dict(lib)
    out["bricks"] = list(bricks.values())
    return out, notes


def _num_or_none(v):
    if v in (None, ""):
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _nozzles(raw):
    out = []
    for n in raw or []:
        t = (n.get("type") or "radial").lower()
        d = {"label": str(n.get("label") or t), "type": t}
        for k in ("z", "angle", "inclination", "diameter", "count", "pcd", "start_angle", "size_a", "size_b", "height"):
            d[k] = _num_or_none(n.get(k))
        if t == "stirring":
            if not d["count"] or not d["pcd"]:
                continue
            d["count"] = int(d["count"])
        elif d["z"] is None or d["angle"] is None:
            continue
        out.append(d)
    return out


def _aci_colors(colors):
    """UI colours (#rrggbb) -> nearest AutoCAD colour index, so the drawing matches the wizard."""
    from ezdxf.colors import DXF_DEFAULT_COLORS, int2rgb
    out = {}
    for g, c in colors.items():
        if isinstance(c, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", c.strip()):
            h = c.strip().lstrip("#")
            rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
            best = min(range(1, 256), key=lambda i: sum((a - b) ** 2 for a, b in zip(int2rgb(DXF_DEFAULT_COLORS[i]), rgb)))
            out[g] = best
        elif isinstance(c, (int, float)) or (isinstance(c, str) and c.strip()):
            out[g] = c
    return out


def volumes(sheet):
    """Structured version of report.build_report (same maths)."""
    from refractory_core import get_core
    f = get_core().formulas
    k = 1e-9 if sheet.vessel.get("units", "mm") == "mm" else 1.0
    L = sheet.L
    br = angle_breaks(sheet.design)
    cavity = 0.0
    for a, b in zip(br, br[1:]):
        paths, _ = sheet.boundary_paths((a + b) / 2)
        hot = paths[L].all()
        pts = sample(hot) + [(0.0, hot[-1][1]), (0.0, hot[0][1])]
        cavity += f.evaluate("revolved_volume", {
            "area_moment": region_area_moment(pts)[1],
            "angle_from": a, "angle_to": b, "unit_factor": 1.0,
        })
    rows, by_grade, by_layer = [], {}, {}
    for bi, band in enumerate(sheet.bands):
        i0, i1 = sheet.band_idx[bi]
        for sec in band["sectors"]:
            paths, _ = sheet.boundary_paths((sec["from"] + sec["to"]) / 2)
            for j in range(1, L + 1):
                pts = sample(paths[j - 1].run(i0, i1) + reverse_run(paths[j].run(i0, i1)))
                vol = f.evaluate("revolved_volume", {
                    "area_moment": region_area_moment(pts)[1],
                    "angle_from": sec["from"], "angle_to": sec["to"], "unit_factor": k,
                })
                l = sec["layers"][j - 1]
                rows.append({"band": band["name"], "sector": f"{sec['from']:g}-{sec['to']:g}", "zone": sec.get("name", ""),
                             "layer": sheet.layers[j - 1], "grade": l["grade"], "thickness": l["thickness"],
                             "volume_m3": round(vol, 3)})
                by_grade[l["grade"]] = by_grade.get(l["grade"], 0.0) + vol
                by_layer[sheet.layers[j - 1]] = by_layer.get(sheet.layers[j - 1], 0.0) + vol
    hs = sheet.design.get("heat_size")
    cav_m3 = cavity * k
    return {"rows": rows, "by_grade": {g: round(v, 3) for g, v in by_grade.items()},
            "by_layer": {g: round(v, 3) for g, v in by_layer.items()},
            "total_lining_m3": round(sum(by_layer.values()), 3), "inside_volume_m3": round(cav_m3, 3),
            "specific_volume_m3_per_t": round(f.evaluate("specific_volume", {"cavity_m3": cav_m3, "heat_size": float(hs)}), 4) if hs else None}


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _write_xlsx(path, job, head, rows, bom):
    """BOM workbook (sheet BOM + sheet By grade). Returns False if openpyxl is missing."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    ws.append([f"Bill of materials - {job}  (generated from the lining design; cut allowance {bom['cut_allowance_pct']:g} %)"])
    ws["A1"].font = Font(bold=True, size=12)
    ws.append([])
    ws.append(head)
    for c in ws[3]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E6F4EC")
    for r in rows:
        ws.append(r)
    ws.append([])
    ws.append(["TOTAL", "", "", "", "", "", "", "", "", bom["pieces"], "", "", "", bom["total_t"], ""])
    ws[ws.max_row][0].font = Font(bold=True)
    for col, w in zip("ABCDEFGHIJKLMNO", (5, 24, 30, 18, 22, 20, 26, 8, 10, 10, 11, 10, 10, 10, 60)):
        ws.column_dimensions[col].width = w
    g = wb.create_sheet("By grade")
    g.append(["Grade", "Tonnes"])
    for k, v in bom["by_grade"].items():
        g.append([k, v])
    g.append(["TOTAL", bom["total_t"]])
    wb.save(path)
    return True


def generate(payload: dict, out_dir: Path, url_prefix: str = "", progress=None) -> dict:
    """progress(stage_text, percent) is called as the work advances (optional)."""
    step = progress or (lambda *_, **__: None)

    def ready(title, png, pct):                               # tell the UI a drawing is finished (live preview)
        if not png:
            return
        info = {"title": title, "url": f"{url_prefix}/{png}"}
        try:
            step(f"{title} ready", pct, preview=info)
        except TypeError:                                     # progress callback without previews
            step(f"{title} ready", pct)

    step("Checking the input values", 2)
    out_dir.mkdir(parents=True, exist_ok=True)
    from app.utils.step_timer import StepTimer
    job = str(payload.get("job") or "job")
    timer = StepTimer("Part 3 - Lining Drawings & BOM Generation", job, out_dir)

    with timer.step("3.1 Validate inputs & prepare brick library"):
        lib = load_library(payload.get("brick_library"))
        from refractory_core import get_core
        f = get_core().formulas
        st = {"course_height": f.evaluate("d_course_height", {}),
              "mouth_course_height": f.evaluate("d_mouth_course_height", {}),
              "joint": f.evaluate("d_joint", {}),
              "bottom_joint": f.evaluate("d_bottom_joint", {}),
              "min_cut": f.evaluate("d_min_cut", {}),
              "cut_allowance": f.evaluate("d_cut_allowance", {}),
              **{k: v for k, v in (payload.get("settings") or {}).items() if v not in (None, "")}}
        for k in list(st):
            try:
                st[k] = float(st[k])
            except (TypeError, ValueError):
                raise LiningInputError([f"setting '{k}': '{st[k]}' is not a number"])
        lib, lib_notes = add_entered_bricks(lib, payload, st)
        nozzles = _nozzles(payload.get("nozzles"))

        # Load MHP and taphole set from payload, or default to TRL client_assemblies.json when nozzles are present
        _client_asm = _load_default_client_assemblies()
        raw_mhp = payload.get("mhp")
        if not raw_mhp and any(n["type"] == "stirring" for n in nozzles):
            mhp_sets = _client_asm.get("mhp_sets") or []
            if mhp_sets:
                raw_mhp = dict(mhp_sets[0].get("values") or {})
        raw_th_set = payload.get("taphole_set")
        if not raw_th_set and any(n["type"] == "taphole" for n in nozzles):
            th_sets = _client_asm.get("taphole_sets") or []
            if th_sets:
                raw_th_set = dict(th_sets[0].get("values") or {})
                tap_noz = next((n for n in nozzles if n["type"] == "taphole"), {})
                if tap_noz.get("diameter") and not raw_th_set.get("opening"):
                    raw_th_set["opening"] = tap_noz["diameter"]
                if tap_noz.get("inclination") and not raw_th_set.get("incl"):
                    raw_th_set["incl"] = tap_noz["inclination"]

        _mhp = mhp_inputs(raw_mhp)
        _th_set = taphole_inputs(raw_th_set)
        for n in nozzles:                                      # the stirring elements are the MHPs (TRL name)
            if n["type"] == "stirring" and _mhp:
                n["label"] = _mhp["mark"]
                n["mhp"] = _mhp
                if not n.get("size_a"):
                    n["size_a"] = _mhp["coldW"]
                if not n.get("size_b"):
                    n["size_b"] = _mhp["hotW"]
                if not n.get("height"):
                    n["height"] = _mhp["L"]

        payload = dict(payload)
        if (payload.get("design") or {}).get("grade_colors"):
            payload["design"] = {**payload["design"], "grade_colors": _aci_colors(payload["design"]["grade_colors"])}
        try:
            vessel, design, _ = build_engine_inputs(payload)
        except (KeyError, ValueError) as e:
            raise LiningInputError([f"{e}"])
        vessel["job"] = job
        vessel.setdefault("units", "mm")
        design["course_height"] = st["course_height"]
        err, warn = validate(vessel, design)
        if err:
            raise LiningInputError(err)

        inputs = [r for r in (payload.get("input_values") or []) if isinstance(r, dict)]
        n_edited = sum(1 for r in inputs if r.get("edited"))
        set_info = {"project": f"{job}" + (f"  -  {payload.get('customer')}" if payload.get("customer") else ""),
                    "company": "REFRACTORY LINING DESIGN  -  AI LINING COPILOT",
                    "edited": n_edited,
                    # TRL Krosaki title block
                    "client": " - ".join(str(x) for x in (payload.get("customer"), payload.get("plant")) if x),
                    "vessel": str(payload.get("vessel_name") or ""),
                    "prepared": str(payload.get("prepared_by") or ""), "checked": "", "approved": "",
                    "rev": 0, "rev_kind": "FIRST ISSUE", "issue_status": "FOR APPROVAL"}

    def output(doc, views, name, set_title, code, pct0, pct1, paper="A1"):        # A1 = TRL's drawing size
        """One page per view (PDF + AutoCAD layouts in the DXF), DXF saved, DWG when the converter exists."""
        pre = str(payload.get("file_prefix") or "")
        info = {**set_info, "set_title": set_title, "drawing_no": f"{safe_name(job)}-{code}",
                "cad_file": f"{pre + '_' if pre else ''}{name}.dxf", "paper": paper}
        n = len(views)

        def page_done(i, n_, title):
            step(f"{set_title}: page {i}/{n_} - {title}", int(pct0 + (pct1 - pct0) * i / max(n_, 1)))
        made = []
        try:
            made, extents = _pages.render_pages(doc, views, out_dir, name, info, progress=page_done)
            _pages.add_layouts(doc, views, extents, info)
        except Exception as e:                                 # pages are a convenience - the DXF is the deliverable
            step(f"{set_title}: page output failed ({type(e).__name__}: {e}) - single-sheet preview used", pct1)
            made = save_previews(doc.modelspace(), out_dir, name)
        dxf = out_dir / f"{name}.dxf"
        doc.saveas(dxf)
        res = {dxf.name: dxf}
        for p_ in made:
            res[p_.name] = p_
        dwg, why = _pages.export_dwg(dxf)
        if dwg:
            res[dwg.name] = dwg
        elif why:
            step(f"{set_title}: DWG not made - {why}", pct1)
        return res

    files = {}
    with timer.step("3.2 Build Lining GA DXF (sections, plans, nozzles)"):
        try:
            sheet = LiningSheet(vessel, design, nozzles)
            sheet.inputs = inputs
            sheet.steel_density = _num_or_none(payload.get("steel_density"))
            sheet.slag_volume = _num_or_none(payload.get("slag_volume"))
            views = design["views"]
            if not views.get("plan_heights"):
                hs = [round(sum(sheet.band_z(b)) / 2, 1) for b in range(len(sheet.bands))]
                hs += [n["z"] for n in nozzles if n["type"] != "stirring" and sheet.zmin < n["z"] < sheet.zmax]
                views["plan_heights"] = sorted(set(hs))
            step("Drawing the lining GA (sections, plans, top view, unrolled map, nozzles)", 8)
            sheet.build()
        except ValueError as e:
            raise LiningInputError([f"Could not build the lining drawing: {e}"])

    with timer.step("3.3 Compute volumes, course schedule, bottom rings & BOM"):
        report = build_report(sheet)
        vols = volumes(sheet)
        step("Computing course schedule and bottom rings from the brick library", 10)
        courses, course_flags, cone_gaps = course_schedule(sheet, lib, st)
        rings = bottom_rings(sheet, lib, st, nozzles, payload.get("bottom") or {})
        mhp_taphole = design_mhp_and_taphole_assemblies(lib, nozzles, payload.get("bottom") or {}, payload.get("grade_density") or {})
        step("Drawing every brick joint on the lining GA", 12)
        try:
            sheet.draw_brick_joints(courses, rings)
        except Exception:
            pass
        step("Adding TRL detail to the lining GA (permanent bricks, course tags, marks, rings, MHP)", 12)
        try:
            sheet.draw_trl_detail(courses, rings, _mhp,
                                  {b.get("name"): b.get("permanent") for b in (payload.get("design") or {}).get("bands", [])},
                                  _th_set)
        except Exception as e:                                 # the GA without the extra detail is still delivered
            step(f"Lining GA detail not added ({type(e).__name__}: {e})", 12)
        qty = quantities(courses, rings)
        bom = build_bom(sheet, lib, courses, rings, vols, nozzles, st, payload.get("grade_density") or {}, cone_gaps,
                        payload.get("monolithic") or {}, payload.get("extra_items") or [], payload.get("junctions") or [])
        bom["flags"] = sorted(set(bom["flags"]) | set(lib_notes))

    with timer.step("3.4 Render Lining GA pages & previews (DXF / PNG / PDF)"):
        step(f"Lining GA: {len(sheet.views)} drawings, one page each (PDF + AutoCAD layouts)", 13)
        files.update(output(sheet.doc, sheet.views, "lining_ga", "LINING GA", "GA", 13, 30, "A1"))
        ready("Lining GA", "lining_ga.png" if "lining_ga.png" in files else None, 30)

    with timer.step("3.5 Run accuracy self-checks"):
        step("Checking the drawings against the input values", 38)
        checks = run_checks({**payload, "brick_library": lib}, vessel, sheet, courses, rings, vols, bom, st, nozzles)
        mcol = assign_mark_colors(courses, rings)
        minfo = mark_info(lib, courses, rings, qty, mcol)
        qty_sheet = dict(qty)                                   # + permanent-lining bricks counted in the BOM
        for o in bom["lines"]:
            if o["group"] == "Permanent lining" and o.get("qty"):
                qty_sheet[o["mark"]] = qty_sheet.get(o["mark"], 0) + o["qty"]

    with timer.step("3.6 Build & render Brick Shapes sheet (DXF / PNG / PDF)"):
        bs = BrickSheet(lib, qty_sheet, job, mcol, minfo)
        step("Drawing the brick shapes sheet", 40)
        bs.build()
        files.update(output(bs.doc, bs.views, "brick_shapes", "BRICK SHAPES", "BS", 41, 55))
        ready("Brick shapes", "brick_shapes.png" if "brick_shapes.png" in files else None, 55)

    with timer.step("3.7 Build & render Layers & Brick Layout sheet (DXF / PNG / PDF)"):
        ls = LayerSheet(sheet, courses, rings, nozzles, job, mcol, minfo)
        step("Drawing the layers & brick layout sheet", 60)
        ls.build()
        files.update(output(ls.doc, ls.views, "lining_layers", "LINING LAYERS & BRICK LAYOUT", "LL", 62, 86))
        ready("Layers & brick layout", "lining_layers.png" if "lining_layers.png" in files else None, 86)

    with timer.step("3.7b Build & render Zone drawings - one page per zone (DXF / PNG / PDF)"):
        try:
            zs = ZoneSheet(sheet, courses, rings, nozzles, job, mcol, minfo, bom)
            zs.detail = {"mhp": _mhp, "th_set": _th_set,
                         "perm": {b.get("name"): b.get("permanent") for b in (payload.get("design") or {}).get("bands", [])}}
            step("Drawing one zone drawing per zone (section, dimensions, brick layout, zone BOM)", 87)
            zs.build()
            files.update(output(zs.doc, zs.views, "zone_drawings", "ZONE DRAWINGS", "ZD", 87, 94))
            ready("Zone drawings", "zone_drawings.png" if "zone_drawings.png" in files else None, 94)
        except Exception as e:                                 # the other drawings and the BOM are still delivered
            step(f"Zone drawings not made ({type(e).__name__}: {e})", 94)

    with timer.step("3.7b2 Build & render Circular bottom brick layout (section + plan + ring BOQ)"):
        if rings and rings.get("rings"):
            try:
                bl = BottomLayoutSheet(sheet, courses, rings, nozzles, job, mcol, minfo, bom)
                bl.detail = zs.detail if "zs" in locals() else None
                step("Drawing the circular bottom brick layout (TRL sheet: section, plan, ring BOQ)", 94)
                bl.build()
                files.update(output(bl.doc, bl.views, "bottom_layout", "BOTTOM BRICK LAYOUT", "BL", 94, 94, "A1"))
                ready("Bottom brick layout", "bottom_layout.png" if "bottom_layout.png" in files else None, 94)
            except Exception as e:                             # the other drawings and the BOM are still delivered
                step(f"Bottom brick layout not made ({type(e).__name__}: {e})", 94)

    with timer.step("3.7c Build & render Detail brick shapes - one drawing per mark (DXF / PNG / PDF)"):
        try:
            ss = ShapeSheet(bom, lib, job)
            step(f"Drawing the detail brick shapes ({len(ss.marks)} marks of this design)", 94)
            ss.build()
            files.update(output(ss.doc, ss.views, "shape_drawings", "DETAIL BRICK SHAPES", "DS", 94, 95))
            ready("Detail brick shapes", "shape_drawings.png" if "shape_drawings.png" in files else None, 95)
        except Exception as e:                                 # the other drawings and the BOM are still delivered
            step(f"Detail brick shapes not made ({type(e).__name__}: {e})", 95)

    with timer.step("3.7d Build & render MHP drawings - MHP & seating block, dummy brick, position plan"):
        mhp = _mhp
        stir = next((n for n in nozzles if n["type"] == "stirring"), None)
        if mhp and stir:
            try:
                ms = MhpSheet(mhp, stir, rings, job, str(payload.get("vessel_name") or ""))
                rc = float((rings or {}).get("cold_sphere_radius") or 0)
                rb_ = next((bi for bi, b in enumerate(sheet.bands) if b["name"] == (rings or {}).get("band")), None)
                if rc and rb_ is not None:              # dish radii of this job (foundation sheet)
                    from .lining.model import sector_at as _sa
                    lay_ = _sa(sheet.bands[rb_], 0.0)["layers"]
                    t_w = float(lay_[-1]["thickness"] or 0)
                    t_p = sum(float(l["thickness"] or 0) for l in lay_[:-1])
                    ms.radii = {"hot": rc - t_w, "cold": rc, "shell": rc + t_p,
                                "shell_out": rc + t_p + sheet.shell_t if sheet.shell_t else None,
                                "edge": float(rings["rings"][-1]["r_out"])}
                step(f"Drawing the {mhp['mark']} sheets ({stir['count']} on PCD {stir['pcd']:g})", 95)
                ms.build()
                files.update(output(ms.doc, ms.views, "mhp_drawings", "MHP DRAWINGS", "MH", 95, 95))
                ready("MHP drawings", "mhp_drawings.png" if "mhp_drawings.png" in files else None, 95)
            except Exception as e:                             # the other drawings and the BOM are still delivered
                step(f"MHP drawings not made ({type(e).__name__}: {e})", 95)
        elif stir:
            step("MHP drawings not made - MHP dimensions not entered", 95)

    with timer.step("3.7e Build & render Taphole drawings - assembly & detail of blocks and sleeves"):
        tap = next((n for n in nozzles if n["type"] == "taphole"), None)
        if _th_set:
            try:
                lin_len = None
                if tap:
                    p_s, p_o, p_e = sheet._radial_axis(tap, float(tap["angle"]))
                    lin_len = math.dist(p_o, p_e) - sheet.shell_t
                b_len = sum(q["L"] for q in _th_set["pieces"] if q["kind"] == "block")
                if lin_len and abs(b_len - lin_len) > 0.05 * lin_len:
                    step(f"WARNING taphole: block set {b_len:g} mm long, lining along the taphole axis {lin_len:.0f} mm "
                         f"- adjust the taphole set or the top cone lining", 95)
                ts = TapholeSheet(_th_set, tap, job, lin_len)
                step("Drawing the taphole assembly & detail sheets (blocks, sleeves)", 95)
                ts.build()
                files.update(output(ts.doc, ts.views, "taphole_drawings", "TAPHOLE DRAWINGS", "TH", 95, 95))
                ready("Taphole drawings", "taphole_drawings.png" if "taphole_drawings.png" in files else None, 95)
            except Exception as e:                             # the other drawings and the BOM are still delivered
                step(f"Taphole drawings not made ({type(e).__name__}: {e})", 95)
        elif tap:
            step("Taphole drawings not made - taphole set not entered", 95)

    # tables
    with timer.step("3.8 Write CSV / XLSX tables and summary"):
        step("Writing tables (CSV) and summary", 95)
        _write_csv(out_dir / "course_schedule.csv",
                   ["course", "z0", "z1", "height", "band", "sector", "zone", "grade", "brick_length", "r_cold", "r_hot",
                    "pair", "mark_A", "nA", "mark_B", "nB", "cut_pieces", "cut_mm", "gap_mm", "flag"],
                   [[r["course"], r["z0"], r["z1"], r["h"], r["band"], r["sector"], r.get("zone", ""), r["grade"], r["L"],
                     r.get("r_cold"), r.get("r_hot"), r.get("pair") or "", r.get("A") or "", r["nA"], r.get("B") or "",
                     r["nB"], r["nCut"], r["cut"], r.get("gap"), r.get("flag") or ""] for r in courses])
        files["course_schedule.csv"] = out_dir / "course_schedule.csv"
        if rings:
            _write_csv(out_dir / "bottom_rings.csv", ["ring", "r_in", "r_out", "marks", "cut_pieces", "cut_mm", "note", "flag"],
                       ([[0, 0, rings["key"]["r"], f"{rings['key']['mark']} x 1", 0, 0, "", ""]] if rings.get("key") else []) +
                       [[r["ring"], r["r_in"], r["r_out"], " + ".join(f"{m} x {q}" for m, q in r["marks"].items()), r["nCut"],
                         r["cut"], r.get("note") or "", r.get("flag") or ""] for r in rings["rings"]])
            files["bottom_rings.csv"] = out_dir / "bottom_rings.csv"
        bom_head = ["item", "group", "zone", "grade", "mark", "shape", "size_mm", "qty", "allowance_pct", "order_qty",
                    "unit_vol_dm3", "density_g_cm3", "unit_wt_kg", "total_t", "note"]
        bom_rows = [[o["item"], o["group"], o["zone"], o["grade"], o["mark"], o["family"], o["size"], o["qty"],
                     o["allow_pct"], o["order_qty"], o["unit_vol_dm3"], o["density"], o["unit_wt_kg"], o["total_t"],
                     (o.get("note") or "") + (f" [volume {o['volume_m3']} m3]" if o.get("volume_m3") else "")]
                    for o in bom["lines"]]
        _write_csv(out_dir / "bom.csv", bom_head, bom_rows + [[], ["TOTAL", "", "", "", "", "", "", "", "",
                   bom["pieces"], "", "", "", bom["total_t"], ""]])
        files["bom.csv"] = out_dir / "bom.csv"
        if _write_xlsx(out_dir / "bom.xlsx", job, bom_head, bom_rows, bom):
            files["bom.xlsx"] = out_dir / "bom.xlsx"
        _write_csv(out_dir / "brick_quantities.csv", ["mark", "pieces"], sorted(qty.items()))
        files["brick_quantities.csv"] = out_dir / "brick_quantities.csv"

        flags = list(course_flags) + list((rings or {}).get("flags", []))
        lib_gaps = [f"{g.get('issue')}: {g.get('status')}" for g in lib.get("_known_gaps", [])]
        summary = [report, "", "BRICK QUANTITIES (pieces, before allowances)"]
        summary += [f"  {m:<24}{n:>8}" for m, n in sorted(qty.items())]
        summary += ["", "ACCURACY CHECKS (drawing measured again and compared with the inputs)"]
        summary += [f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['check']}: {c['value']} (expected {c['expected']}) {c['detail']}"
                    for c in checks]
        summary += ["", "FLAGS"] + [f"  - {f}" for f in flags] + ["", "BRICK LIBRARY GAPS"] + [f"  - {g}" for g in lib_gaps]
        (out_dir / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
        files["summary.txt"] = out_dir / "summary.txt"
        (out_dir / "vessel.json").write_text(json.dumps(vessel, indent=2), encoding="utf-8")
        (out_dir / "design.json").write_text(json.dumps(design, indent=2), encoding="utf-8")
        (out_dir / "input_payload.json").write_text(json.dumps({k: v for k, v in payload.items() if k != "brick_library"},
                                                               indent=2, default=str), encoding="utf-8")
        files["vessel.json"] = out_dir / "vessel.json"
        files["design.json"] = out_dir / "design.json"

        # Link every output package to its source document versions and Knowledge Base version
        from refractory_core import get_core
        _core = get_core()
        _src_docs = list(payload.get("source_documents") or [])
        _ing_job_id = str(payload.get("ingestion_job_id") or payload.get("source_job_id") or "").strip()
        if _ing_job_id:
            from app.core.config import settings as _settings
            _mf = _settings.outputs_dir / _ing_job_id / "source_manifest.json"
            if _mf.exists():
                try:
                    _src_docs.append(json.loads(_mf.read_text(encoding="utf-8")))
                except Exception:
                    pass
        if not _src_docs and isinstance(payload.get("vessel"), dict):
            _vsrc = payload["vessel"]
            if _vsrc.get("source_file") or _vsrc.get("drawing_number"):
                _src_docs.append({
                    "filename": _vsrc.get("source_file", "shell_drawing"),
                    "drawing_number": _vsrc.get("drawing_number", ""),
                    "revision": _vsrc.get("revision", "0"),
                    "sha256": _vsrc.get("file_hash_sha256", ""),
                })
        _used_marks = sorted({m.replace(" (cut)", "") for m in qty if qty[m]})
        _by_mark = {b["mark"]: b for b in lib.get("bricks", [])}
        _mark_sources = {}
        for _m in _used_marks:
            _b = _by_mark.get(_m, {})
            _mark_sources[_m] = {
                "source_ref": _b.get("source_ref") or {"document_no": "TRLK/SS/BOF/06/08.10.2016", "revision": "08.10.2016"},
                "registered_drawings": _b.get("registered_drawings", []),
            }
        _kb_stamp = _core.kb_versions.stamp_design(
            job_id=str(payload.get("_job_run_id") or job),
            job_ref=job,
            output_dir=out_dir,
            actor=str(payload.get("_actor") or "designer"),
            source_documents=_src_docs,
        )
        package_lineage = {
            "job": job,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "generated_by": str(payload.get("_actor") or "designer"),
            "kb_version": _kb_stamp["kb_version"],
            "kb_sha256": _kb_stamp["kb_sha256"],
            "kb_snapshot_json": _kb_stamp["kb_snapshot_json"],
            "source_documents": _src_docs,
            "shape_library_document": (lib.get("document_metadata") or {}).get("primary_catalog", {
                "document_no": "TRLK/SS/BOF/06/08.10.2016",
                "revision": "08.10.2016",
            }),
            "safety_brick_document": (lib.get("document_metadata") or {}).get("safety_brick_catalog", {
                "document_no": "TRLK/SAFETY-BRICK-LIB/BOF-BARREL",
                "revision": "1.0",
            }),
            "used_brick_marks_lineage": _mark_sources,
        }
        (out_dir / "package_lineage.json").write_text(json.dumps(package_lineage, indent=2), encoding="utf-8")
        files["package_lineage.json"] = out_dir / "package_lineage.json"
        files["kb_stamp.json"] = out_dir / "kb_stamp.json"

    txt_path, json_path = timer.finalize()
    files["timings.txt"] = txt_path
    files["timings.json"] = json_path

    # one ZIP with every output, each file named "<project>_<file>"
    import zipfile
    prefix = safe_name(str(payload.get("file_prefix") or job))
    pkg = out_dir / "package.zip"
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as z:
        for name, path in files.items():
            z.write(path, arcname=f"{prefix}_{name}")
    files["package.zip"] = pkg

    n_wall = len({r["course"] for r in courses})
    return {
        "job": job,
        "file_prefix": safe_name(str(payload.get("file_prefix") or job)),
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "files": {name: f"{url_prefix}/{name}" for name in files},
        "report": report,
        "volumes": vols,
        "courses": courses,
        "n_courses": n_wall,
        "bottom_rings": rings,
        "mhp_and_taphole_assemblies": mhp_taphole,
        "quantities": qty,
        "bom": bom,
        "checks": checks,
        "mark_colors": {m: aci_hex(c) for m, c in mcol.items()},
        "mark_info": minfo,
        "flags": flags,
        "library_gaps": lib_gaps,
        "warnings": warn,
        "engine_vessel": vessel,
        "kb_stamp": _kb_stamp,
        "package_lineage": package_lineage,
        "bands": [{"name": b["name"], "kind": band_kind(sheet, i), "z": [round(x, 1) for x in sheet.band_z(i)]}
                  for i, b in enumerate(sheet.bands)],
    }


def safe_name(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", s or "job").strip("_") or "job"
