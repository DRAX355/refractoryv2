"""
Bill of materials from the generated lining (no demo / default quantities).

Every line comes from the engine's own results:
  * wall working lining   <- course schedule (A/B pair counts + cut pieces per course and sector)
  * bottom working lining <- bottom ring layout (SH wedges + KEY)
  * permanent lining      <- permanent-layer volume of each band / library standard brick
  * courses with no library shape <- their geometric volume (so tonnes are still known)
  * stirring elements / taphole set <- nozzles in the input + library marks
Unit volumes are computed from the library dimensions (or the BOM volume quoted in the library note
when the library has no dimensions). Unit weight = unit volume x bulk density of the zone's grade.
"""
import math
import re

VOL_NOTE = re.compile(r"vol\s*([\d.]+)\s*dm3", re.I)
WT_NOTE = re.compile(r"unit\s*wt\s*([\d.]+)\s*kg", re.I)


def _note(b):
    return " ".join(str(x) for x in (b.get("note"), (b.get("dimensions_mm") or {}).get("note")) if x)


def _F(fid, variables):
    """Evaluate a user-editable formula from refractory_core (defaults in refractory_core/defaults/formulas.json)."""
    from refractory_core import get_core
    return get_core().formulas.evaluate(fid, variables)


_VOL_FORMULA = {"standard_trapezoidal": "vol_standard_trapezoidal", "steel_cladded": "vol_standard_trapezoidal",
                "bottom_dish_wedge": "vol_bottom_dish_wedge", "center_block": "vol_center_block",
                "safety_lining_std": "vol_safety_lining_std", "safety_lining_rect": "vol_safety_lining_rect",
                "seating_block": "vol_seating_block",
                "barrel_safety_brick": "vol_barrel_safety_brick",
                "key_wedge_series": "vol_key_wedge_series",
                "beveled_key_ht": "vol_beveled_key_ht",
                "double_tapered": "vol_double_tapered",
                "triple_tapered": "vol_triple_tapered"}


def unit_volume(b):
    """(dm3, how) for one library brick; (None, reason) if it cannot be computed.
    The volume formula per shape family is user-editable (refractory_core formulas 'vol_<family>')."""
    fam, d = b.get("shape_family"), b.get("dimensions_mm") or {}
    fid = _VOL_FORMULA.get(fam)
    if fid and not (fam == "safety_lining_std" and "thickness" not in d):
        try:
            from refractory_core import get_core
            v = _F(fid, {k: float(x) for k, x in d.items() if isinstance(x, (int, float))})
            return v, "formula " + fid + ": " + get_core().formulas.get(fid)["expression"]
        except Exception:                 # missing dimensions / invalid formula -> try wall trapezoidal or quoted volume
            pass
    if all(k in d for k in ("length", "width_top", "width_bottom", "thickness")):
        try:
            from refractory_core import get_core
            v = _F("vol_standard_trapezoidal", {k: float(x) for k, x in d.items() if isinstance(x, (int, float))})
            return v, "formula vol_standard_trapezoidal: " + get_core().formulas.get("vol_standard_trapezoidal")["expression"]
        except Exception:
            pass
    if b.get("catalog_volume_dm3") is not None:
        return float(b["catalog_volume_dm3"]), "catalog_volume_dm3 (TRL Standard Shape Library)"
    m = VOL_NOTE.search(_note(b))
    if m:
        return float(m.group(1)), "volume quoted in library (BOM sheet)"
    return None, "no dimensions or volume in library"


def size_text(b):
    d = b.get("dimensions_mm") or {}
    fam = b.get("shape_family")
    if fam in ("standard_trapezoidal", "steel_cladded", "key_wedge_series", "beveled_key_ht") \
            and all(k in d for k in ("length", "width_top", "width_bottom", "thickness")):
        return f"{d['length']:g} x {d['width_top']:g}/{d['width_bottom']:g} x {d['thickness']:g}"
    if fam == "barrel_safety_brick" and all(k in d for k in ("C", "A", "B", "T")):
        return f"C{d['C']:g} x A{d['A']:g}/B{d['B']:g} x T{d['T']:g}"
    if fam == "key_wedge_series" and all(k in d for k in ("h", "a", "b", "s1", "s2")):
        return f"h{d['h']:g} x {d['a']:g}/{d['b']:g} x S{d['s1']:g}/{d['s2']:g}"
    if fam == "beveled_key_ht" and all(k in d for k in ("h", "a", "b", "s")):
        return f"h{d['h']:g} x {d['a']:g}/{d['b']:g} x s{d['s']:g}"
    if fam == "double_tapered" and all(k in d for k in ("h", "a1", "a2", "b1", "b2", "s")):
        return f"h{d['h']:g} x {d['a1']:g}/{d['a2']:g} x {d['b1']:g}/{d['b2']:g} x s{d['s']:g}"
    if fam == "triple_tapered" and all(k in d for k in ("h", "a1", "a2", "b1", "b2", "s1", "s2")):
        return f"h{d['h']:g} x {d['a1']:g}/{d['a2']:g} x {d['b1']:g}/{d['b2']:g} x S{d['s1']:g}/{d['s2']:g}"
    if fam == "bottom_dish_wedge" and all(k in d for k in ("overall_length", "face_width_top", "face_width_bottom")):
        return f"L{d['overall_length']:g}, faces {d['face_width_top']:g}/{d['face_width_bottom']:g}"
    if fam == "center_block" and all(k in d for k in ("inner_dia", "outer_dia", "height")):
        return f"D{d['inner_dia']:g}/{d['outer_dia']:g} x {d['height']:g}"
    if fam == "safety_lining_std":
        return f"{d.get('length')} x {d.get('width')} x {d.get('thickness', d.get('thickness_range'))}"
    if fam == "purging_plug":
        return f"{d.get('top_section')} x {d.get('overall_height')}"
    if fam == "seating_block" and all(k in d for k in ("outer_dia", "inner_dia", "height")):
        return f"D{d['outer_dia']:g}/{d['inner_dia']:g} x {d['height']:g}"
    return ""


def build_bom(sheet, lib, courses, rings, vols, nozzles, st, densities, cone_gaps=(), monolithic=None,
              extra_items=None, junctions=None):
    """Returns {"lines": [...], "by_grade": {...}, "total_t", "flags": [...]}.
    densities: {grade: g/cm3} and optionally {"band|zone|grade": g/cm3} for a zone-specific density.
    monolithic: {grade: {"density", "fill_factor"}} - layers laid as castable / filling (volume, not bricks).
    extra_items: lines taken as entered in the UI (special assemblies, junction pieces)."""
    by_mark = {b["mark"]: b for b in lib.get("bricks", [])}
    allow = float(st.get("cut_allowance", 0) or 0)
    monolithic = monolithic or {}
    flags = []
    lines = {}

    def dens(grade, band=None, zone=None):
        k = f"{band}|{zone}|{grade}"
        if band is not None and densities.get(k):
            return float(densities[k]), "zone density (UI)"
        if grade in densities and densities[grade]:
            return float(densities[grade]), "UI grade table"
        flags.append(f"No bulk density entered for grade '{grade}' - its tonnes are not calculated (enter the density)")
        return None, "not entered"

    def add(group, zone, grade, mark, qty, allow_pct, note="", where=(None, None)):
        if not qty:
            return
        base = mark.replace(" (cut)", "")
        b = by_mark.get(base, {})
        key = (group, zone, grade, mark)
        if key not in lines:
            v, how = unit_volume(b) if b else (None, "mark not in library")
            rho, rsrc = dens(grade, *where)
            lines[key] = {"group": group, "zone": zone, "grade": grade, "mark": mark,
                          "family": b.get("shape_family", ""), "size": size_text(b) if b else "",
                          "qty": 0, "allow_pct": allow_pct, "unit_vol_dm3": None if v is None else round(v, 3),
                          "vol_method": how, "density": rho, "density_src": rsrc, "note": note}
        lines[key]["qty"] += qty

    # 1. wall working lining (course schedule)
    for r in courses:
        if not r.get("pair"):
            continue
        zone = f"{r['band']} - {r.get('zone') or r['sector']}"
        w = (r["band"], r.get("zone") or "")
        add("Working lining - wall", zone, r["grade"], r["A"], r["nA"], allow, where=w)
        add("Working lining - wall", zone, r["grade"], r["B"], r["nB"], allow, where=w)
        add("Working lining - wall", zone, r["grade"], r["A"] + " (cut)", r["nCut"], 0,
            "cut on site from a full brick to close the ring", where=w)

    # 2. bottom working lining (rings)
    if rings:
        zone = f"{rings['band']} - bottom rings"
        w = (rings["band"], "Bottom")
        if rings.get("key"):
            add("Working lining - bottom", zone, rings["grade"], rings["key"]["mark"], 1, 0, where=w)
        for rg in rings["rings"]:
            for m, q in rg["marks"].items():
                add("Working lining - bottom", zone, rings["grade"], m, q, allow, where=w)
            if rg["nCut"] and rg["marks"]:
                add("Working lining - bottom", zone, rings["grade"], list(rg["marks"])[0] + " (cut)", rg["nCut"], 0,
                    "cut on site to close the ring", where=w)

    # 3. permanent lining (bricks by volume) and monolithic layers (castable / filling / backfill)
    stds = [b for b in lib.get("bricks", []) if b.get("shape_family") == "safety_lining_std" and unit_volume(b)[0]]
    perm_allow = float(st.get("perm_cut_allowance", allow) or 0)
    perm_in_bom = float(st.get("perm_in_bom", 1)) > 0
    mono = {}
    # permanent layers: the first layer + any extra permanent layers ("PERMANENT 2", ...) entered in the UI
    perm_layers = [sheet.layers[0]] + [n for n in sheet.layers[1:-1] if re.fullmatch(r"PERMANENT \d+", str(n))]
    perm_multi = {}                                  # band -> permanent layers with a volume
    for row in vols["rows"] if sheet.L > 1 else []:
        if row["layer"] in perm_layers and row["volume_m3"] > 0 and row["grade"] not in monolithic:
            perm_multi.setdefault(row["band"], set()).add(row["layer"])
    for row in vols["rows"] if sheet.L > 1 else []:
        if row["layer"] == sheet.layers[-1] or row["volume_m3"] <= 0:
            continue
        if row["grade"] in monolithic:
            m = mono.setdefault((row["band"], row["layer"], row["grade"]), 0.0)
            mono[(row["band"], row["layer"], row["grade"])] = m + row["volume_m3"]
            continue
        if row["layer"] not in perm_layers:
            continue
        if not perm_in_bom:
            continue
        band_def = next((b for b in sheet.bands if b["name"] == row["band"]), {})
        perm_cfg = band_def.get("permanent") or {}
        if perm_cfg.get("in_bom") == 0:
            continue
        zone = str(band_def.get("section") or row["band"])          # one line per section (split zones summed)
        layer_idx = perm_layers.index(row["layer"])
        if len(perm_multi.get(row["band"], ())) > 1:                       # several permanent layers: one line each
            zone += f" - layer {layer_idx + 1}"
        # Check if the band carries exact TRL course/layer-calculated permanent lining quantities (BSM-1, LC-1/2, TYP1/2, TM-1/2)
        p_layers = perm_cfg.get("layers") or []
        if layer_idx < len(p_layers) and p_layers[layer_idx].get("qty"):
            pl_item = p_layers[layer_idx]
            mk = pl_item.get("mark") or perm_cfg.get("mark") or "BSM-1"
            add("Permanent lining", zone, pl_item.get("grade") or row["grade"], mk, float(pl_item["qty"]),
                float(pl_item.get("cut_allowance", perm_allow) or 0),
                "TRL spherical-cap safety layer calculation (Q7 radius-ratio & Q8 round-up)",
                where=(row["band"], "PERMANENT" if row["layer"] == sheet.layers[0] else row["layer"]))
            key = ("Permanent lining", zone, pl_item.get("grade") or row["grade"], mk)
            if key in lines and pl_item.get("unit_vol_dm3"):
                lines[key]["unit_vol_dm3"] = round(float(pl_item["unit_vol_dm3"]), 3)
                lines[key]["vol_method"] = "TRL Q7 radius-ratio spherical-cap brick volume"
            continue
        calc_parts = perm_cfg.get("calculated_parts") or []
        if layer_idx == 0 and calc_parts:
            # Share across split sub-bands of the same section if needed, or add once per band
            sec_bands = [b for b in sheet.bands if str(b.get("section") or b["name"]) == str(band_def.get("section") or row["band"])]
            share = 1.0 / max(1, len(sec_bands))
            for pt in calc_parts:
                mk = str(pt.get("mark") or "STD")
                q_pt = float(pt.get("qty") or 0) * share
                if q_pt <= 0:
                    continue
                add("Permanent lining", zone, row["grade"], mk, q_pt, 0,
                    "TRL course-by-course safety lining calculation (Q7 radius-ratio & Q8 per-course round-up)",
                    where=(row["band"], "PERMANENT"))
                key = ("Permanent lining", zone, row["grade"], mk)
                if key in lines and pt.get("unit_vol_dm3"):
                    lines[key]["unit_vol_dm3"] = round(float(pt["unit_vol_dm3"]), 4)
                    lines[key]["vol_method"] = "TRL Q7 radius-ratio course brick volume"
            continue
        t = row.get("thickness")
        std = next((b for b in stds if t is not None and abs(float(b["dimensions_mm"].get("length", -1)) - t) < 0.5), None) \
            or (stds[0] if stds else None)
        if std:
            uv = unit_volume(std)[0]
            n = row["volume_m3"] * 1000 / uv                            # rounded up once per line below
            add("Permanent lining", zone, row["grade"], std["mark"], n, perm_allow,
                "count = drawn permanent-layer volume / brick volume (laying pattern not computed)",
                where=(row["band"], "PERMANENT" if row["layer"] == sheet.layers[0] else row["layer"]))
        else:
            flags.append("No standard permanent-lining brick in library - permanent lining given as volume only")
    if not perm_in_bom and sheet.L > 1:
        flags.append("Permanent lining is reused (reline) - drawn, but left out of the BOM")

    # 4. courses without a library shape -> volume line
    missing = {}
    covered = {}
    for r in courses:
        if r.get("pair") or r.get("r_cold") is None or r.get("r_hot") is None:
            continue
        if "transition" in (r.get("flag") or ""):
            zm = (r["z0"] + r["z1"]) / 2
            j = min((j for j in junctions or [] if j.get("pieces")), key=lambda j: abs(float(j["z"]) - zm), default=None)
            if j is not None and abs(float(j["z"]) - zm) <= float(r["L"]) + float(r["h"]):
                # junction course (knuckle / transition): built with the special pieces chosen in step 12;
                # the quantity is the drawn volume of these courses (the step-12 estimate is not added again)
                a0, a1 = [float(x) for x in r["sector"].split("-")]
                v = _F("sector_course_volume", {"angle_from": a0, "angle_to": a1, "r_cold": r["r_cold"], "r_hot": r["r_hot"], "course_height": r["h"]})
                k = (r["band"], r.get("zone") or r["sector"], r["grade"], "junction:" + str(j.get("mark") or "special"))
                mj = missing.setdefault(k, {"vol": 0.0, "courses": []})
                mj["vol"] += v
                mj["courses"].append(r["course"])
                mj["junction"] = j.get("name", "")
                covered.setdefault(j.get("name", ""), []).append(r["course"])
                continue
        a0, a1 = [float(x) for x in r["sector"].split("-")]
        v = _F("sector_course_volume", {"angle_from": a0, "angle_to": a1, "r_cold": r["r_cold"], "r_hot": r["r_hot"], "course_height": r["h"]})
        k = (r["band"], r.get("zone") or r["sector"], r["grade"],
             "transition" if "transition" in (r.get("flag") or "") else f"L{r['L']:g}")
        m = missing.setdefault(k, {"vol": 0.0, "courses": []})
        m["vol"] += v
        m["courses"].append(r["course"])

    # 4b. cone gaps (horizontal bricks shorter than the drawn lining)
    for g in cone_gaps:
        rho, _ = dens(g["grade"])
        missing_lines = missing.setdefault((g["band"], g["zone"], g["grade"], "conegap"), {"vol": 0.0, "courses": []})
        missing_lines["vol"] += g["volume_m3"]
        missing_lines["gap"] = g["max_gap_mm"]

    # 5. assemblies (MHP, taphole) come only from what is entered in step 12 (extra_items) - nothing assumed
    if not extra_items:
        for n in nozzles:
            if n.get("type") == "stirring" and n.get("count"):
                flags.append("MHP / stirring elements: no assembly entered in step 12 - not in the BOM")
            if n.get("type") == "taphole":
                flags.append("Taphole: no taphole set entered in step 12 - not in the BOM")
    out = []
    for l in lines.values():
        if l["group"] == "Permanent lining":
            l["qty"] = math.ceil(l["qty"] - 1e-9)
        q_order = int(_F("order_qty", {"qty": l["qty"], "allow_pct": l["allow_pct"]}))
        uw = None if l["unit_vol_dm3"] is None or l["density"] is None else _F("unit_weight", {"unit_vol_dm3": l["unit_vol_dm3"], "density": l["density"]})
        out.append({**l, "order_qty": q_order, "unit_wt_kg": None if uw is None else round(uw, 2),
                    "total_t": None if uw is None else round(q_order * uw / 1000, 3)})
    for name, cs in covered.items():
        est = [it for it in extra_items or [] if it.get("group") == "Junction pieces" and name in str(it.get("zone", ""))]
        flags.append(f"Junction {name}: courses {min(cs)}-{max(cs)} need the special pieces of step 12; BOM uses their drawn "
                     f"volume" + (f" (step-12 estimate {sum(float(i.get('tonnes') or 0) for i in est):.2f} t not added)" if est else ""))
    for (band, layer, grade), v in mono.items():
        cfg = monolithic.get(grade) or {}
        rho = float(cfg.get("density") or 0) or dens(grade)[0]
        _tt = (lambda x: None) if rho is None else (lambda x: round(x * rho, 3))
        ff = float(cfg.get("fill_factor") or 1)
        out.append({"group": "Monolithic / filling", "zone": f"{band} - {layer.lower()}", "grade": grade,
                    "mark": grade, "family": "monolithic", "size": "", "qty": None, "allow_pct": 0, "order_qty": None,
                    "unit_vol_dm3": None, "vol_method": "drawn layer volume", "density": rho, "density_src": "UI",
                    "unit_wt_kg": None, "total_t": _tt(v * ff), "volume_m3": round(v * ff, 3),
                    "note": f"drawn volume {v:.3f} m3 x fill factor {ff:g} x density "
                            f"{'NOT ENTERED' if rho is None else f'{rho:g} t/m3'}"})
    for it in extra_items or []:
        if it.get("group") == "Junction pieces" and any(n and n in str(it.get("zone", "")) for n in covered):
            continue                                        # replaced by the drawn junction courses
        q = it.get("qty")
        q = None if q in (None, "") or (isinstance(q, float) and math.isnan(q)) else q
        out.append({"group": it.get("group") or "Special assemblies", "zone": it.get("zone", ""),
                    "grade": it.get("grade", ""), "mark": it.get("mark", ""), "family": "", "size": "",
                    "qty": q, "allow_pct": 0, "order_qty": None if q is None else math.ceil(float(q)),
                    "unit_vol_dm3": None, "vol_method": "", "density": None, "density_src": "",
                    "unit_wt_kg": it.get("unit_kg"), "total_t": None if it.get("tonnes") is None else round(float(it["tonnes"]), 3),
                    "note": it.get("note") or "as entered in the UI"})
    for (band, zone, grade, what), m in missing.items():
        rho, _ = dens(grade, band, zone)
        _tt = (lambda x: None) if rho is None else (lambda x: round(x * rho, 3))
        cs = m["courses"]
        if what.startswith("junction:"):
            out.append({"group": "Junction pieces", "zone": f"{band} - {zone}", "grade": grade,
                        "mark": f"{what[9:]} (junction pieces, step 12)", "family": "", "size": "", "qty": None,
                        "allow_pct": 0, "order_qty": None, "unit_vol_dm3": None, "vol_method": "drawn volume",
                        "density": rho, "density_src": "", "unit_wt_kg": None, "total_t": _tt(m["vol"]),
                        "volume_m3": round(m["vol"], 3),
                        "note": f"junction {m.get('junction', '')}: courses {min(cs)}-{max(cs)} ({len(cs)}), "
                                f"tonnes from the drawn volume - piece count needs the special shape dimensions"})
            continue
        if what == "conegap":
            out.append({"group": "GAP / FILL", "zone": f"{band} - {zone}", "grade": grade,
                        "mark": f"cone gap at hot face (up to {m.get('gap', 0):.0f} mm)", "family": "", "size": "",
                        "qty": None, "allow_pct": 0, "order_qty": None, "unit_vol_dm3": None,
                        "vol_method": "geometric", "density": rho, "density_src": "", "unit_wt_kg": None,
                        "total_t": _tt(m["vol"]), "volume_m3": round(m["vol"], 3),
                        "note": "horizontal bricks are shorter than the drawn lining on the cone: longer / cut-end bricks or fill"})
            continue
        out.append({"group": "NOT IN BRICK LIBRARY", "zone": f"{band} - {zone}", "grade": grade,
                    "mark": "transition shapes (TSC/TPZ type)" if what == "transition" else f"no {what[1:]} mm shape",
                    "family": "", "size": "", "qty": None, "allow_pct": 0, "order_qty": None, "unit_vol_dm3": None,
                    "vol_method": "geometric volume of the courses", "density": rho, "density_src": "",
                    "unit_wt_kg": None, "total_t": _tt(m["vol"]), "volume_m3": round(m["vol"], 3),
                    "note": f"courses {min(cs)}-{max(cs)} ({len(cs)}); shape must be added to the library"})
        flags.append(f"{band} {zone}: {round(m['vol'], 2)} m3 needs shapes not in the brick library")
    # (junction lines were added above)

    order = ["Working lining - bottom", "Working lining - wall", "Permanent lining", "Monolithic / filling",
             "Special - stirring", "Special - taphole", "Junction pieces", "Special assemblies",
             "NOT IN BRICK LIBRARY", "GAP / FILL"]
    out.sort(key=lambda o: (order.index(o["group"]) if o["group"] in order else 99, o["zone"], o["mark"]))
    for i, o in enumerate(out, 1):
        o["item"] = i
    by_grade = {}
    for o in out:
        if o["total_t"]:
            by_grade[o["grade"]] = round(by_grade.get(o["grade"], 0) + o["total_t"], 3)
    return {"lines": out, "by_grade": by_grade, "total_t": round(sum(by_grade.values()), 3),
            "pieces": sum(o["order_qty"] or 0 for o in out), "flags": sorted(set(flags)),
            "cut_allowance_pct": allow}
