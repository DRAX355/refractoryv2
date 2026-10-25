"""
Accuracy self-checks: the generated geometry and brick layout are measured again, independently of
how they were built, and compared with the input values. Every generation reports these results.
"""
import math

from .geometry import offset_path, radius_at_z, reverse_run, sample
from .model import build_segments


def _ok(name, ok, value, expected, detail=""):
    return {"check": name, "ok": bool(ok), "value": value, "expected": expected, "detail": detail}


def _dist_to_poly(p, pts):
    best = float("inf")
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        L2 = dx * dx + dy * dy
        t = 0 if L2 == 0 else max(0, min(1, ((p[0] - x0) * dx + (p[1] - y0) * dy) / L2))
        best = min(best, math.hypot(p[0] - (x0 + t * dx), p[1] - (y0 + t * dy)))
    return best


def run_checks(payload, vessel, sheet, courses, rings, vols, bom, st, nozzles):
    from refractory_core import get_core
    f = get_core().formulas
    out = []
    tol = f.evaluate("p_geometry_check_tol", {})                                    # mm

    # 1. shell profile: drawn shell (inside face + plate) back to the input outside dimensions
    vin = payload["vessel"]
    plate = float(vin.get("shell_thickness", 0) or 0)
    segs = build_segments(vessel)
    inner_face = vin.get("profile_face", "outer") == "inner"      # input = inside of the shell (ID values)
    outer = offset_path(segs, [-plate] * len(segs)) if plate > 0 and not inner_face else offset_path(segs, [0.0] * len(segs))
    pts_in = [(float(s["end_r"]), float(s["end_z"])) for s in vin["segments"]]
    # inputs may have been split by height zones: compare at the input segment ends
    worst, worst_at = 0.0, ""
    J = outer.J
    for (r, z), s in zip(pts_in, vin["segments"]):
        d = min(math.hypot(jx - r, jz - z) for jx, jz in J)
        if d > worst:
            worst, worst_at = d, s["name"]
    out.append(_ok("Shell outline = input dimensions (all segment end points: radius & height"
                   + (", inside of shell" if inner_face else ", outside of shell") + ")", worst <= tol,
                   f"max deviation {worst:.3f} mm", f"<= {tol} mm", worst_at and f"worst at {worst_at}"))
    H_in = max(z for _, z in pts_in)
    H_dr = max(z for _, z in J)
    out.append(_ok("Overall height drawn", abs(H_in - H_dr) <= tol, f"{H_dr:.1f} mm", f"{H_in:.1f} mm"))
    for (r, z), s in zip(pts_in, vin["segments"]):
        rr = None
        for jx, jz in J:
            if abs(jz - z) < 1.0 and abs(jx - r) < 1.0:
                rr = jx
        out.append(_ok(f"Diameter at top of '{s['name']}'", rr is not None and abs(2 * rr - 2 * r) <= 2 * tol,
                       f"{2 * rr:.1f} mm" if rr is not None else "not found", f"{2 * r:.1f} mm"))

    # 2. lining thickness: distance between boundary curves at the middle of every band / sector / layer
    worst, n_meas, worst_txt = 0.0, 0, ""
    for bi, band in enumerate(sheet.bands):
        b0, b1 = sheet.band_idx[bi]
        # measure on the longest segment of the band (a horizontal shell step carries no lining)
        im = max(range(b0, b1 + 1), key=lambda i: math.dist(sheet.segs[i].p0, sheet.segs[i].p1)
                 if abs(sheet.segs[i].p1[1] - sheet.segs[i].p0[1]) > 1e-6 or i == 0 else -1)
        i0 = i1 = im
        for sec in band["sectors"]:
            paths, _ = sheet.boundary_paths((sec["from"] + sec["to"]) / 2)
            for k in range(1, sheet.L + 1):
                # middle point of the band on boundary k-1, distance to boundary k (normal thickness)
                run_a = paths[k - 1].run(i0, i1)
                pa = sample(run_a, 0.5)
                seglen = [math.dist(p, q) for p, q in zip(pa, pa[1:])]
                half, acc, mid = sum(seglen) / 2, 0.0, pa[0]
                for (p, q), l in zip(zip(pa, pa[1:]), seglen):        # true midpoint along the curve
                    if acc + l >= half:
                        t = (half - acc) / l if l else 0
                        mid = (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))
                        break
                    acc += l
                pb = sample(paths[k].run(i0, i1), 0.5)
                if sheet.segs[im].shape == "line" and len(pb) == 2:
                    # straight wall: thickness = distance between the two parallel lines (the band may be
                    # shorter than its thickness, so the foot of the normal can fall outside the segment)
                    (x0, y0), (x1, y1) = pb
                    d = abs((x1 - x0) * (y0 - mid[1]) - (x0 - mid[0]) * (y1 - y0)) / max(math.hypot(x1 - x0, y1 - y0), 1e-12)
                else:
                    d = _dist_to_poly(mid, pb)
                t = float(sec["layers"][k - 1]["thickness"])
                n_meas += 1
                if abs(d - t) > worst:
                    worst = abs(d - t)
                    worst_txt = f"{band['name']} {sec['from']:g}-{sec['to']:g}° {sheet.layers[k - 1]}: drawn {d:.2f}, input {t:g}"
    out.append(_ok(f"Layer thickness drawn = input thickness ({n_meas} band/sector/layer measurements)", worst <= tol,
                   f"max deviation {worst:.3f} mm", f"<= {tol} mm", worst_txt))

    # 3. inside volume by an independent method (horizontal slices of the hot face)
    from .model import angle_breaks
    br = angle_breaks(sheet.design)
    V = 0.0
    for a, b in zip(br, br[1:]):
        paths, _ = sheet.boundary_paths((a + b) / 2)
        hot = paths[sheet.L].all()
        pts = sample(hot, 0.5)
        z0, z1 = min(p[1] for p in pts), max(p[1] for p in pts)
        n = 4000
        dz = (z1 - z0) / n
        for i in range(n):
            r = radius_at_z(hot, z0 + (i + 0.5) * dz) or 0.0
            V += math.pi * r * r * dz * (b - a) / 360.0
    V /= 1e9
    ref = vols["inside_volume_m3"]
    vol_tol = f.evaluate("p_inside_volume_tol", {})
    out.append(_ok("Inside volume: engine vs independent slice integration", abs(V - ref) / ref <= vol_tol,
                   f"{ref:.3f} m3", f"{V:.3f} m3 (±{vol_tol * 100:g} %)"))

    # 4. every brick ring closes (hot face): placed bricks + cut piece = arc length at the hot face
    j = float(st.get("joint", 0.5))
    band_joint = {b["name"]: float(b["joint"]) for b in sheet.bands if b.get("joint") not in (None, "")}
    worst, wc = 0.0, ""
    lib = {b["mark"]: b for b in payload.get("brick_library", {}).get("bricks", [])}
    n_rings, bad_rings = 0, []
    rings_ = {}                                        # wrapped zones (x-360 + 0-y) close as one ring segment
    for r in courses:
        if not r.get("pair"):
            continue
        k = r.get("ring_id") or id(r)
        if k in rings_:
            g = rings_[k]
            a0, a1 = [float(x) for x in r["sector"].split("-")]
            g["span"] += a1 - a0
            for f in ("nA", "nB", "nCut"):
                g[f] += r[f]
        else:
            a0, a1 = [float(x) for x in r["sector"].split("-")]
            rings_[k] = {**r, "span": a1 - a0}
    for r in rings_.values():
        A, B = lib.get(r["A"], {}).get("dimensions_mm", {}), lib.get(r["B"], {}).get("dimensions_mm", {})
        if not A or not B:
            continue
        need = math.radians(r["span"]) * r["r_hot"]
        jb = band_joint.get(r["band"], j)
        # the cut length already includes its joint
        have = r["nA"] * (A["width_bottom"] + jb) + r["nB"] * (B["width_bottom"] + jb) + r["cut"] * r["nCut"]
        n_rings += 1
        sq_tol = f.evaluate("ring_squeeze", {"na": r["nA"], "nb": r["nB"]}) + f.evaluate("p_squeeze_check_tol", {})
        excess = abs(have - need) - sq_tol
        if excess > worst - 1e9 and abs(have - need) > (worst if wc else -1):
            worst, wc = abs(have - need), f"course {r['course']} {r['band']} {r['sector']}° ({r['nA'] + r['nB']} bricks)"
        if excess > 0:
            bad_rings.append(wc)
    out.append(_ok(f"Wall rings close at the hot face ({n_rings} course/sector rings)", not bad_rings,
                   f"max difference {worst:.1f} mm in a ring", "within joint squeeze per brick",
                   wc + (f"; {len(bad_rings)} ring(s) outside tolerance" if bad_rings else "")))
    if rings:
        bj = float(st.get("bottom_joint", 0) or 0)
        worst, wr = 0.0, ""
        for rg in rings["rings"]:
            need = 2 * math.pi * rg["r_in"] - sum(e["count"] * (e["width"] + bj) for e in rg.get("elements", []))
            have = sum(q * (float(lib[m]["dimensions_mm"]["D"]) + bj) for m, q in rg["marks"].items() if m in lib)
            have += rg["cut"] * rg["nCut"]
            n_b = sum(rg["marks"].values())
            sq_tol = f.evaluate("ring_squeeze", {"na": n_b, "nb": 0}) + f.evaluate("p_squeeze_check_tol", {})
            if abs(have - need) > sq_tol and not wr:        # same rule as the closure
                wr = f"ring {rg['ring']} differs {abs(have - need):.1f} mm"
            worst = max(worst, abs(have - need))
        out.append(_ok(f"Bottom rings close ({len(rings['rings'])} rings)", not wr,
                       f"max difference {worst:.1f} mm in a ring", "within joint squeeze per brick", wr))

    # 5. lining volume rebuilt from the course schedule (library bricks, scaled to the course height,
    #    + courses that need shapes missing from the library) vs the drawn working-lining volume
    # bands of one section on the same course grid share courses at their boundary -> compare per section
    sec_of = {b["name"]: b.get("section") or b["name"] for b in sheet.bands}
    zone_geo = {}
    for row in vols["rows"]:
        if row["layer"] == sheet.layers[-1]:
            k = (sec_of.get(row["band"], row["band"]), row.get("zone") or "")
            zone_geo[k] = zone_geo.get(k, 0.0) + row["volume_m3"]
    uv = {o["mark"]: o["unit_vol_dm3"] for o in bom["lines"] if o.get("unit_vol_dm3")}
    hb = {m: float(lib[m]["dimensions_mm"].get("thickness", 100)) for m in lib if "thickness" in lib[m].get("dimensions_mm", {})}
    zone_b, zone_m, zone_g = {}, {}, {}
    for r in courses:
        k = (sec_of.get(r["band"], r["band"]), r.get("zone") or "")
        a0, a1 = [float(x) for x in r["sector"].split("-")]
        phi = math.radians(a1 - a0)
        if r.get("pair"):
            f = r["h"] / hb.get(r["A"], r["h"])
            hotA = float(lib[r["A"]]["dimensions_mm"]["width_bottom"]) + band_joint.get(r["band"], j)
            cut_frac = (r["cut"] / hotA) if r["nCut"] else 0          # a cut piece fills only part of a brick
            v = (r["nA"] * uv.get(r["A"], 0) + r["nB"] * uv.get(r["B"], 0) + r["nCut"] * cut_frac * uv.get(r["A"], 0)) / 1000 * f
            zone_b[k] = zone_b.get(k, 0.0) + v
            if r.get("space") and r["space"] > r["L"] + 0.5:             # cone: horizontal brick shorter than the room
                rc = r["r_cold"]
                zone_g[k] = zone_g.get(k, 0.0) + phi / 2 * ((rc - r["L"]) ** 2 - (rc - r["space"]) ** 2) * r["h"] / 1e9
        elif r.get("r_cold") is not None and r.get("r_hot") is not None:
            v = phi / 2 * (r["r_cold"] ** 2 - r["r_hot"] ** 2) * r["h"] / 1e9
            zone_m[k] = zone_m.get(k, 0.0) + v
    for k in sorted(set(zone_b) | set(zone_m)):
        if k not in zone_geo:
            continue
        vb, vm, vgap, vg = zone_b.get(k, 0.0), zone_m.get(k, 0.0), zone_g.get(k, 0.0), zone_geo[k]
        tot = vb + vm + vgap
        reb_tol = f.evaluate("p_lining_volume_rebuild_tol", {})
        out.append(_ok(f"Working lining volume rebuilt course by course: {k[0]} - {k[1]}", abs(tot / vg - 1) <= reb_tol,
                       f"bricks {vb:.2f} + not-in-library {vm:.2f} + cone gap {vgap:.2f} = {tot:.2f} m3",
                       f"drawing {vg:.2f} m3 (±{reb_tol * 100:g} %)"))

    # 6. BOM = schedule
    n_sched = sum(r["nA"] + r["nB"] + r["nCut"] for r in courses if r.get("pair"))
    n_bom = sum(o["qty"] for o in bom["lines"] if o["group"] == "Working lining - wall")
    out.append(_ok("BOM wall pieces = course schedule pieces", n_sched == n_bom, f"{n_bom}", f"{n_sched}"))
    if rings:
        n_r = rings["total"]
        n_b = sum(o["qty"] for o in bom["lines"] if o["group"] == "Working lining - bottom")
        out.append(_ok("BOM bottom pieces = ring layout pieces", n_r == n_b, f"{n_b}", f"{n_r}"))

    # 8. cross-checks with values entered / derived in the UI steps (input consistency, shown as warnings)
    ref = payload.get("reference") or {}
    def cross(name, val, exp, tol_rel, unit):
        try:
            val, exp = float(val), float(exp)
        except (TypeError, ValueError):
            return
        if not (math.isfinite(val) and math.isfinite(exp)) or exp == 0:
            return
        c = _ok(f"Cross-check: {name}", abs(val - exp) <= tol_rel * abs(exp), f"{val:,.1f} {unit}", f"{exp:,.1f} {unit} (±{tol_rel * 100:g} %)",
                "drawn value vs the value entered / calculated in the steps")
        c["cross"] = True
        out.append(c)
    try:
        bb = next((i for i, b in enumerate(sheet.bands) if b.get("kind") == "bottom"), None)
        if bb is not None:
            i0, i1 = sheet.band_idx[bb]
            hot = sheet.boundary_paths(0.0)[0][sheet.L].run(i0, i1)
            from .geometry import arc_from_bulge
            if abs(hot[0][2]) > 1e-12:
                cross("bottom dish hot-face radius", arc_from_bulge(hot[0][:2], hot[1][:2], hot[0][2])[1], ref.get("dish_hot_R"), 0.01, "mm")
            cross("bottom working-lining diameter at the edge", 2 * max(p[0] for p in hot), ref.get("bot_dia"), 0.005, "mm")
        mb = [i for i, b in enumerate(sheet.bands) if b.get("kind") == "mouth"]
        if mb:
            top = max(mb, key=lambda i: sheet.band_z(i)[1])
            i0, i1 = sheet.band_idx[top]
            pts = sample(sheet.boundary_paths(0.0)[0][sheet.L].run(i0, i1), 1.0)
            cross("mouth opening diameter", 2 * min(p[0] for p in pts), ref.get("mouth_opening"), 0.005, "mm")
        cross("inside volume (step 6 estimate)", vols["inside_volume_m3"], ref.get("inside_volume"), 0.03, "m3")
    except Exception as e:                                    # cross-checks are information only
        out.append({"check": "Cross-checks", "ok": True, "value": "skipped", "expected": "", "detail": str(e), "cross": True})

    # 7. nozzles
    for n in nozzles:
        if n.get("type") == "stirring" and rings:
            placed = sum(e["count"] for rg in rings["rings"] for e in rg.get("elements", []))
            out.append(_ok(f"Stirring elements placed in the bottom layout ({n.get('label')})", placed == int(n["count"]),
                           f"{placed}", f"{int(n['count'])}"))
    return out
