"""
Brick layout from the brick library (brick_library_mock.json until the client's real library arrives).

  * Wall courses (every band except the bottom): horizontal courses of `course_height`; each course and
    angular sector is closed with a tapered A/B pair of the brick length = working thickness.
        nA*(hotA+j) + nB*(hotB+j)   = phi * R_hot
        nA*(coldA+j) + nB*(coldB+j) = phi * (R_hot + L)
    rounded to whole bricks, remainder closed with one cut piece >= min_cut (else two cut pieces).
  * Bottom (dish) working lining: concentric rings of upright spherical wedge bricks (SH marks) around
    the centre KEY block, laid on the cold face sphere. Each ring is closed with the two adjacent SH
    marks whose taper brackets the ring's taper (outer/inner circumference).

Library conventions used (from the shape sheets; verified: SH-1 alone closes the first ring around KEY
with 20 pieces, ring 2 gives SH-1 x16 + SH-2 x14, exactly the reference BOQ):
  standard_trapezoidal / steel_cladded : length = radial brick length, width_top = cold face width,
                                         width_bottom = hot face width, thickness = course height
  bottom_dish_wedge : face_width_bottom = radial width on the cold face, C/D = outer/inner tangential
                      width on the cold face, A/B = the same on the hot face
  center_block      : outer_dia = cold face diameter
"""
import math

WALL_FAMILIES = ("standard_trapezoidal", "steel_cladded")


# ======================================================================== library
def wall_pairs(lib):
    """Group wall bricks into A/B pairs: same family + length (+ course height)."""
    groups = {}
    for b in lib.get("bricks", []):
        fam = b.get("shape_family")
        d = b.get("dimensions_mm", {})
        if fam not in WALL_FAMILIES or not all(k in d for k in ("length", "width_top", "width_bottom", "thickness")):
            continue
        key = (fam, float(d["length"]), float(d["thickness"]), _series(b["mark"]))
        groups.setdefault(key, []).append(b)
    pairs = []
    for (fam, L, H, _), bs in groups.items():
        shapes = sorted(({"mark": b["mark"], "cold": float(b["dimensions_mm"]["width_top"]),
                          "hot": float(b["dimensions_mm"]["width_bottom"]), "zone": b.get("zone", ""),
                          "grade": b.get("quality_grade_internal", "")} for b in bs),
                        key=lambda s: s["cold"] / s["hot"])
        if len(shapes) < 2:
            continue
        A, B = shapes[0], shapes[-1]
        if abs(A["cold"] / A["hot"] - B["cold"] / B["hot"]) < 1e-6:
            continue
        pairs.append({"id": f"{A['mark']}+{B['mark']}", "family": fam, "L": L, "H": H, "A": A, "B": B,
                      "zone": A["zone"] + " / " + B["zone"]})
    return pairs


def sector_pairs(sec, kind="wall", course_h=100.0):
    """Brick pairs entered in the UI for one zone (sector): [{A, B, L, aHot, aCold, bHot, bCold}].
    hot = hot-face (inner) width, cold = cold-face (outer) width, L = radial length."""
    out = []
    for q in sec.get("pairs") or []:
        try:
            L = float(q["L"])
            A = {"mark": str(q["A"]), "hot": float(q["aHot"]), "cold": float(q["aCold"]), "zone": sec.get("name", ""),
                 "grade": ""}
            B = {"mark": str(q["B"]), "hot": float(q["bHot"]), "cold": float(q["bCold"]), "zone": sec.get("name", ""),
                 "grade": ""}
        except (KeyError, TypeError, ValueError):
            continue
        if L <= 0 or min(A["hot"], A["cold"], B["hot"], B["cold"]) <= 0:
            continue
        fam = q.get("family") or ("steel_cladded" if kind == "mouth" and str(q["A"]).upper().startswith("SCB")
                                  else "standard_trapezoidal")
        out.append({"id": f"{A['mark']}+{B['mark']}", "family": fam, "L": L, "H": float(q.get("H") or course_h),
                    "A": A, "B": B, "zone": sec.get("name", ""), "src": "entered"})
    return out


def _series(mark):
    """SCB-1A/SCB-1B -> 'SCB-1'; '100/20','100/80' -> '100'."""
    if "/" in mark:
        return mark.split("/")[0]
    return mark[:-1] if mark[-1:] in "AB" else mark


def pair_range(p, joint=0.0):
    """Hot-face radius range in which the pair closes a full ring with nA, nB >= 0."""
    def r_alone(s):
        return p["L"] * (s["hot"] + joint) / (s["cold"] - s["hot"]) if s["cold"] > s["hot"] else float("inf")
    a, b = r_alone(p["A"]), r_alone(p["B"])
    return min(a, b), max(a, b)


def choose_pair(pairs, L, band_name, kind, course_h, r_hot, joint):
    """Best library pair for a brick length, or (None, reason)."""
    cands = [p for p in pairs if abs(p["L"] - L) < 0.5]
    if not cands:
        lengths = sorted({p["L"] for p in pairs})
        return None, f"no library brick of length {L:g} mm (library lengths: {', '.join(f'{x:g}' for x in lengths)})"
    want_fam = "steel_cladded" if kind == "mouth" else "standard_trapezoidal"
    bn = band_name.lower()

    def score(p):
        s = 0
        if p["family"] == want_fam:
            s += 100
        if abs(p["H"] - course_h) < 0.5:
            s += 50
        zone = p["zone"].lower()
        for word in ("barrel", "top cone", "lower cone", "mouth"):
            if word in bn and word in zone:
                s += 20
        r0, r1 = pair_range(p, joint)
        if r0 <= r_hot <= r1:
            s += 200
            s += 10 * (1 - abs((r_hot - r0) / max(r1 - r0, 1e-9) - 0.5))
        return s
    best = max(cands, key=score)
    return best, None


# ======================================================================== ring closure
def close_ring(inner_len, outer_len, A, B, joint, min_cut):
    """A, B: (inner width, outer width) of the two shapes. Lengths are arc lengths to fill.
    Returns dict nA, nB, nCut, cut, exact_nA, exact_nB, ok, note."""
    ia, oa = A[0] + joint, A[1] + joint
    ib, ob = B[0] + joint, B[1] + joint
    det = ia * ob - ib * oa
    if abs(det) < 1e-12:                          # identical taper -> one shape only
        n = inner_len / ia
        return _finish(int(math.floor(n)), 0, inner_len, ia, ib, min_cut, n, 0.0, True, "")
    nA = (inner_len * ob - ib * outer_len) / det
    nB = (ia * outer_len - inner_len * oa) / det
    ok = nA >= -0.05 and nB >= -0.05                 # radius inside the pair's closing range
    note = "" if ok else "ring taper outside the pair's closing range"
    best = None
    for nb in {max(0, math.floor(nB)), max(0, math.ceil(nB)), max(0, round(nB))}:
        na = max(0, int(math.floor((inner_len - nb * ib) / ia + 1e-9)))
        r = _finish(na, nb, inner_len, ia, ib, min_cut, nA, nB, ok, note)
        key = (r["nCut"] == 0 and r["ok_cut"], r["ok_cut"], -r["nCut"], -abs(nb - nB))
        if best is None or key > best[0]:
            best = (key, r)
    return best[1]


def _finish(na, nb, inner_len, ia, ib, min_cut, exA, exB, ok, note):
    rem = inner_len - na * ia - nb * ib
    n_cut, cut = 0, 0.0
    squeeze = max(1.0, 0.1 * (na + nb + 1))       # joints may close up 0.1 mm per brick (incl. the added one)
    if rem > ia - squeeze:                        # whole extra brick(s) fit
        na += int(math.ceil((rem - (ia - squeeze)) / ia))
        rem = inner_len - na * ia - nb * ib
    while rem < -squeeze and (na > 0 or nb > 0):  # never overlap: take bricks out, a cut piece closes the ring
        if na > 0:
            na -= 1
        else:
            nb -= 1
        rem = inner_len - na * ia - nb * ib
    if rem >= 1.0:
        if rem >= min_cut or na == 0:
            n_cut, cut = 1, rem
        else:                                     # spread a too-small remainder over two cut pieces
            na -= 1
            n_cut, cut = 2, (rem + ia) / 2
    return {"nA": na, "nB": nb, "nCut": n_cut, "cut": round(cut, 1), "exact_nA": round(exA, 2),
            "exact_nB": round(exB, 2), "ok": ok, "ok_cut": n_cut == 0 or cut >= min_cut, "note": note}


# ======================================================================== wall courses
def course_schedule(sheet, lib, st):
    """Course-by-course brick schedule for every non-bottom band."""
    from .geometry import reverse_run, sample
    pairs = wall_pairs(lib)
    joint, min_cut = float(st.get("joint", 0.5)), float(st.get("min_cut", 40))
    L = sheet.L
    rows, flags = [], []
    no = 0
    bands = sorted(range(len(sheet.bands)), key=lambda b: sheet.band_z(b)[0])
    for bi in bands:
        band = sheet.bands[bi]
        kind = band_kind(sheet, bi)
        if kind == "bottom":
            continue
        ch = float(band.get("course_height") or (st.get("mouth_course_height", 200) if kind == "mouth"
                                                  else st.get("course_height", 100)))
        bj = float(band["joint"]) if band.get("joint") not in (None, "") else joint      # tangential joint per brick
        bmin = float(band["min_cut"]) if band.get("min_cut") not in (None, "") else min_cut
        exp = band.get("expansion") or {}
        za, zb = sheet.band_z(bi)
        i0, i1 = sheet.band_idx[bi]
        # the band's working lining can reach above / below its shell joints at the corners
        # (the zone divider runs from the cold-face corner to the hot-face corner), so courses
        # cover the whole drawn working-lining area of the band, on a grid anchored at its shell joint
        zr0, zr1 = za, zb
        for sec in band["sectors"]:
            pths, _ = sheet.boundary_paths((sec["from"] + sec["to"]) / 2)
            pz = [p[1] for p in sample(pths[L - 1].run(i0, i1) + reverse_run(pths[L].run(i0, i1)), 1.0)]
            zr0, zr1 = min(zr0, min(pz)), max(zr1, max(pz))
        # course grid: anchored at the start of the section (UI: course k at section start + (k-1) x pitch)
        zo = float(band["course_origin"]) if band.get("course_origin") not in (None, "") else za
        k0 = int(math.floor((zr0 - zo) / ch + 1e-6))
        k1 = int(math.ceil((zr1 - zo) / ch - 1e-6))
        # a boundary with a neighbouring band on the same course grid (zone split in step 3, transition /
        # steel-clad, mouth ring step) is not a course joint: a course is never cut there - it belongs
        # whole to the band that holds its middle (as in the step calculations)
        def same_grid(other):
            return other is not band and other.get("course_origin") not in (None, "") \
                and abs(float(other["course_origin"]) - zo) < 0.01 \
                and abs(float(other.get("course_height") or ch) - ch) < 0.01
        soft_lo = any(same_grid(o) and abs(sheet.band_z(j)[1] - za) < 0.01 for j, o in enumerate(sheet.bands))
        soft_hi = any(same_grid(o) and abs(sheet.band_z(j)[0] - zb) < 0.01 for j, o in enumerate(sheet.bands))
        heights = []
        for k in range(k0, k1):
            f0, f1 = zo + k * ch, zo + (k + 1) * ch
            fm = (f0 + f1) / 2
            if soft_lo and f0 < za - 0.01 < f1:
                if fm < za:
                    continue                             # this course belongs to the band below
                zr0 = min(zr0, f0)
            if soft_hi and f0 < zb - 0.01 and f1 > zb + 0.01:
                if fm >= zb:
                    continue                             # this course belongs to the band above
                zr1 = max(zr1, f1)
            h0, h1 = max(f0, zr0), min(f1, zr1)
            if h1 - h0 > 1.0:
                heights.append((k + 1, h0, h1))
        # a zone that runs through 0 deg is split in two sectors (x-360 and 0-y): it is ONE ring segment,
        # closed once (one cut piece), the bricks are shared between the two pieces by angle
        secs = band["sectors"]
        wrap = len(secs) > 1 and secs[0].get("name") == secs[-1].get("name") and secs[0]["layers"] == secs[-1]["layers"] \
            and abs(secs[0]["from"]) < 1e-9 and abs(secs[-1]["to"] - 360) < 1e-9
        span_of = lambda sc: (secs[0]["to"] - secs[0]["from"]) + (secs[-1]["to"] - secs[-1]["from"]) \
            if wrap and (sc is secs[0] or sc is secs[-1]) else sc["to"] - sc["from"]
        for kc, h0, h1 in heights:
            no += 1
            zm = (h0 + h1) / 2
            for sec in band["sectors"]:
                paths, _ = sheet.boundary_paths((sec["from"] + sec["to"]) / 2)
                span = _course_span(paths[L - 1].run(i0, i1), paths[L].run(i0, i1), zm)
                work = sec["layers"][L - 1]
                # brick length: horizontal bricks on a cone are longer than the drawn (normal) thickness
                Lb = float(sec.get("brick_length") or work["thickness"])
                row = {"course": no, "band": band["name"], "sector": f"{sec['from']:g}-{sec['to']:g}",
                       "zone": sec.get("name", ""), "grade": work["grade"], "z0": round(h0, 1), "z1": round(h1, 1),
                       "h": round(h1 - h0, 1), "partial": (h1 - h0) < ch - 1.0, "L": Lb,
                       "r_cold": None, "r_hot": None, "space": None, "pair": None, "A": None, "B": None,
                       "nA": 0, "nB": 0, "nCut": 0, "cut": 0, "gap": None, "flag": None}
                if span is None:
                    row["flag"] = "no working lining of this band at this height (neighbouring band occupies it)"
                    rows.append(row)
                    continue
                r_cold, r_in = span
                space = r_cold - r_in                    # horizontal room for the brick in this course
                row.update({"r_cold": round(r_cold, 1), "space": round(space, 1)})
                if space < Lb - 1.0:                     # knuckle / cone-end course: shorter brick needed
                    row["L"] = round(space, 1)
                    row["flag"] = (f"transition course: only {space:.0f} mm room for a {Lb:g} mm brick - "
                                   f"needs a shorter / cut-end shape (TSC/TPZ type, not in library)")
                    row["r_hot"] = round(r_in, 1)
                    rows.append(row)
                    continue
                r_hot = r_cold - Lb                      # brick laid horizontally against the permanent lining
                row["r_hot"] = round(r_hot, 1)
                row["gap"] = round(space - Lb, 1)        # >0 on cones: drawn lining is wider than the brick
                phi = math.radians(span_of(sec))
                in_wrap = wrap and (sec is secs[0] or sec is secs[-1])
                if in_wrap:
                    row["ring_id"] = f"{no}|{band['name']}|{sec.get('name', '')}|wrap"
                # expansion allowance (UI rule): floor(cold arc / avg cold width / interval) x allowance,
                # taken off the cold-face arc, for courses inside the expansion range of the band
                gaps = 0.0
                p, why, res = None, None, None
                own = [q for q in sector_pairs(sec, kind, ch) if abs(q["L"] - Lb) < 0.5]
                if sec.get("pairs") and not own:
                    why = (f"none of the brick pairs entered for '{sec.get('name', '')}' is {Lb:g} mm long "
                           f"(pairs: {', '.join(q['id'] + ' L' + format(q['L'], 'g') for q in sector_pairs(sec, kind, ch))})")
                cands = own or []
                if not cands:
                    lp, lwhy = choose_pair(pairs, Lb, band["name"], kind, ch, r_hot, bj)
                    cands = [lp] if lp else []
                    why = why or lwhy
                    if lp and sec.get("pairs"):
                        why = None
                        row["pair_src"] = "brick library (no entered pair of this length)"
                best = None
                for q in cands:
                    g = 0.0
                    if exp.get("a") and exp.get("n") and exp.get("from", 1) <= kc <= exp.get("to", 1e9):
                        avg_c = (q["A"]["cold"] + q["B"]["cold"]) / 2
                        g = math.floor(phi * (r_hot + Lb) / avg_c / float(exp["n"])) * float(exp["a"])
                    rq = close_ring(phi * r_hot, phi * (r_hot + Lb) - g, (q["A"]["hot"], q["A"]["cold"]),
                                    (q["B"]["hot"], q["B"]["cold"]), bj, bmin)
                    key = (rq["ok"], rq["ok_cut"], -rq["nCut"], -rq["cut"])
                    if best is None or key > best[0]:
                        best = (key, q, rq, g)
                if best is None:
                    row["flag"] = why or f"no brick pair of length {Lb:g} mm"
                    rows.append(row)
                    continue
                _, p, res, gaps = best
                row["exp_gap"] = round(gaps, 1)
                nA_, nB_, nC_ = res["nA"], res["nB"], res["nCut"]
                if in_wrap:                              # share of this piece of the wrapped zone
                    first = sec is secs[0]
                    fr = (sec["to"] - sec["from"]) / span_of(sec)
                    other = 1 - fr
                    if first:
                        nA_, nB_ = int(round(res["nA"] * fr)), int(round(res["nB"] * fr))
                    else:
                        nA_ = res["nA"] - int(round(res["nA"] * other))
                        nB_ = res["nB"] - int(round(res["nB"] * other))
                    big = fr > 0.5 + 1e-9 or (abs(fr - 0.5) <= 1e-9 and first)    # cut piece in the larger piece
                    nC_ = res["nCut"] if big else 0
                row.update({"pair": p["id"], "A": p["A"]["mark"], "B": p["B"]["mark"], "nA": nA_,
                            "nB": nB_, "nCut": nC_, "cut": res["cut"],
                            "exact": [res["exact_nA"], res["exact_nB"]], "course_h_brick": p["H"]})
                if not res["ok"]:
                    r0, r1 = pair_range(p, bj)
                    row["flag"] = f"hot-face radius {r_hot:.0f} outside closing range {r0:.0f}-{r1:.0f} of {p['id']}"
                elif not res["ok_cut"]:
                    row["flag"] = f"cut piece {res['cut']:.0f} mm < minimum {bmin:g} mm"
                elif abs(p["H"] - (h1 - h0)) > 1.0 and not row["partial"]:
                    row["flag"] = f"brick height {p['H']:g} differs from course height {h1 - h0:g}"
                rows.append(row)
    # cones: bricks lie horizontally, so a brick of length L spans less than the drawn lining
    # (thickness L measured square to the sloping wall) -> report the gap per band / zone
    gap = {}
    for r in rows:
        if r.get("pair") and r.get("space") and r["space"] > r["L"] + 0.5:
            a0, a1 = [float(x) for x in r["sector"].split("-")]
            v = math.radians(a1 - a0) / 2 * ((r["r_cold"] - r["L"]) ** 2 - (r["r_cold"] - r["space"]) ** 2) * r["h"] / 1e9
            g = gap.setdefault((r["band"], r.get("zone") or r["sector"], r["grade"]), [0.0, 0.0, 0])
            g[0] += v
            g[1] = max(g[1], r["space"] - r["L"])
            g[2] += 1
    for (band, zone, grade), (v, mx, n) in gap.items():
        flags.append(f"{band} {zone}: cone - horizontal bricks leave up to {mx:.0f} mm gap at the hot face "
                     f"({v:.2f} m3 over {n} course rings): use longer / cut-end bricks or fill")
    # course numbers = physical layers counted from the bottom (rows at the same level share a number)
    levels = sorted({round(r["z0"], 0) for r in rows})
    num = {z: i + 1 for i, z in enumerate(levels)}
    for r in rows:
        r["course"] = num[round(r["z0"], 0)]
    rows.sort(key=lambda r: (r["course"], r["band"], r["sector"]))
    for r in rows:
        if r["flag"]:
            flags.append(f"Course {r['course']} ({r['band']} {r['sector']}°): {r['flag']}")
    return rows, flags, [{"band": k[0], "zone": k[1], "grade": k[2], "volume_m3": round(v[0], 3),
                          "max_gap_mm": round(v[1], 1), "rings": v[2]} for k, v in gap.items()]


def _course_span(cold_run, hot_run, z):
    """(outer x, inner x) where the horizontal line z crosses the working-layer region on the
    cold-face side, or None."""
    from .geometry import reverse_run, sample
    pts = sample(cold_run + reverse_run(hot_run), 1.0)
    xs = []
    n = len(pts)
    for k in range(n):
        (x0, y0), (x1, y1) = pts[k], pts[(k + 1) % n]
        if (y0 <= z < y1) or (y1 <= z < y0):
            xs.append(x0 + (z - y0) * (x1 - x0) / (y1 - y0))
    xs.sort()
    if len(xs) < 2:
        return None
    return xs[-1], xs[-2]


def band_kind(sheet, bi):
    band = sheet.bands[bi]
    if band.get("kind"):
        return band["kind"]
    i0, _ = sheet.band_idx[bi]
    if i0 == 0 and abs(sheet.segs[0].p0[0]) < 1e-6:
        return "bottom"
    if bi == max(range(len(sheet.bands)), key=lambda b: sheet.band_z(b)[1]) and \
            abs(sheet.segs[-1].p1[0] - sheet.segs[-1].p0[0]) < 1e-6:
        return "mouth"
    return "wall"


# ======================================================================== bottom rings
def bottom_rings(sheet, lib, st, nozzles=(), bottom=None):
    """Ring-by-ring layout of the bottom working lining (dish band). The bottom is dry-laid:
    `bottom_joint` (default 0) is used instead of the wall expansion joint."""
    from .geometry import arc_from_bulge, dist
    joint, min_cut = float(st.get("bottom_joint", 0) or 0), float(st.get("min_cut", 40))
    bis = [bi for bi in range(len(sheet.bands)) if band_kind(sheet, bi) == "bottom"]
    if not bis:
        return None
    bi = bis[0]
    band = sheet.bands[bi]
    i0, i1 = sheet.band_idx[bi]
    L = sheet.L
    sec = band["sectors"][0]
    paths, _ = sheet.boundary_paths((sec["from"] + sec["to"]) / 2)
    cold = paths[L - 1].run(i0, i1)               # cold face of the working layer
    work = sec["layers"][L - 1]

    bottom = bottom or {}
    sh = [b for b in lib.get("bricks", []) if b.get("shape_family") == "bottom_dish_wedge"]
    key = next((b for b in lib.get("bricks", []) if b.get("shape_family") == "center_block"), None)
    out = {"band": band["name"], "grade": work["grade"], "rings": [], "flags": [], "key": None}
    # ring bands entered in the UI (mark per ring range, optional A/B widths for exact closure)
    rbands = []
    for rb in bottom.get("ring_bands") or []:
        try:
            d = {"mark": str(rb["mark"]), "from": int(float(rb["from"])), "to": int(float(rb["to"]))}
        except (KeyError, TypeError, ValueError):
            continue
        try:
            dims = [float(rb[k]) for k in ("aHot", "aCold", "bHot", "bCold")]
            if min(dims) > 0:
                d["dims"] = dims
        except (KeyError, TypeError, ValueError):
            pass
        rbands.append(d)
    use_entered = bool(rbands) and all("dims" in b for b in rbands)
    if not sh and not use_entered:
        out["flags"].append("brick library has no bottom_dish_wedge shapes and no ring-band brick widths were entered")
        return out
    out["source"] = "entered ring bands (Bottom step)" if use_entered else "brick library bottom wedges"

    # cold face as a polyline from the axis outward -> arc length s, radius r(s)
    from .geometry import sample
    pts = sample(cold, 0.25)
    if pts[0][0] > pts[-1][0]:
        pts = pts[::-1]
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + dist(a, b))
    s_end = cum[-1]

    def r_at(s):
        for k in range(1, len(cum)):
            if cum[k] >= s:
                t = (s - cum[k - 1]) / max(cum[k] - cum[k - 1], 1e-12)
                return pts[k - 1][0] + t * (pts[k][0] - pts[k - 1][0])
        return pts[-1][0]

    R_cold = None
    if abs(cold[0][2]) > 1e-12:
        R_cold = arc_from_bulge(cold[0][:2], cold[1][:2], cold[0][2])[1]
    out["cold_sphere_radius"] = None if R_cold is None else round(R_cold, 1)
    lib_R = None
    d0 = sh[0]["dimensions_mm"] if sh else {}
    if not use_entered and d0.get("face_width_bottom") and d0.get("face_width_top") and d0.get("overall_length"):
        k = d0["face_width_bottom"] / d0["face_width_top"]
        lib_R = k * d0["overall_length"] / (k - 1)          # cold radius for which the wedge was made
        out["library_cold_radius"] = round(lib_R, 1)
        if R_cold and abs(lib_R - R_cold) / lib_R > 0.03:
            out["flags"].append(f"dish cold-face radius {R_cold:.0f} mm differs from the SH-brick design radius "
                                f"{lib_R:.0f} mm by more than 3 % - ring counts are approximate")
    if not use_entered and work["thickness"] and abs(float(d0.get("overall_length", work["thickness"])) - work["thickness"]) > 0.5:
        out["flags"].append(f"bottom working thickness {work['thickness']:g} mm but SH bricks are "
                            f"{d0.get('overall_length'):g} mm long")

    marks = sorted(({"mark": b["mark"], "in": float(b["dimensions_mm"]["D"]), "out": float(b["dimensions_mm"]["C"]),
                     "w": float(b["dimensions_mm"]["face_width_bottom"])} for b in sh),
                   key=lambda m: -m["out"] / m["in"]) if sh else []
    ring_w = float(bottom.get("ring_width") or 0)
    if use_entered:
        if ring_w <= 0:
            out["flags"].append("ring radial width not entered")
            return out
        pitch = ring_w + joint
    else:
        pitch = max(m["w"] for m in marks) + joint
        if ring_w > 0 and abs(ring_w - max(m["w"] for m in marks)) > 0.5:
            out["flags"].append(f"ring radial width entered {ring_w:g} mm, library wedge bricks are "
                                f"{max(m['w'] for m in marks):g} mm wide on the cold face - library width used")

    # stirring elements in the bottom
    stir = [n for n in nozzles if n.get("type") == "stirring" and n.get("count")]
    s0 = 0.0
    key_r_in = float(bottom.get("key_r") or 0)
    if key or key_r_in > 0:
        r_key = float(key["dimensions_mm"].get("outer_dia", 0)) / 2 if key else 0.0
        if key_r_in > 0:
            if key and abs(key_r_in - r_key) > 0.5:
                out["flags"].append(f"key brick radius entered {key_r_in:g} mm; library {key['mark']} is "
                                    f"Ø{2 * r_key:g} (r {r_key:g}) - entered radius used")
            r_key = key_r_in
        out["key"] = {"mark": key["mark"] if key else str(bottom.get("key_mark") or "KEY"), "r": r_key, "qty": 1}
        # arc length from axis to the key's outer radius
        lo, hi = 0.0, s_end
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if r_at(mid) < r_key else (lo, mid)
        s0 = (lo + hi) / 2
    ring = 0
    s = s0
    while s < s_end - 1.0:
        ring += 1
        s1 = min(s + pitch, s_end)
        r_in, r_out = r_at(s), r_at(s1)
        width = s1 - s
        cin, cout = 2 * math.pi * r_in, 2 * math.pi * r_out
        elems = []
        for n in stir:
            rp = float(n.get("pcd", 0)) / 2
            if r_in <= rp < r_out:
                cnt = int(n["count"])
                w = float(n.get("size_b") or n.get("size_a") or 0) + joint
                cin -= cnt * w
                cout -= cnt * w
                elems.append({"label": n.get("label", "stirring"), "count": cnt, "width": w - joint})
        row = {"ring": ring, "r_in": round(r_in, 1), "r_out": round(r_out, 1), "width": round(width, 1),
               "partial": width < pitch - 1.0, "marks": {}, "nCut": 0, "cut": 0, "flag": None, "elements": elems}
        rb = next((b for b in rbands if b["from"] <= ring <= b["to"]), rbands[-1] if rbands else None)
        if use_entered:
            aH, aC, bH, bC = rb["dims"]
            res = close_ring(cin, cout, (aH, aC), (bH, bC), joint, min_cut)
            row["marks"] = {m: q for m, q in ((rb["mark"] + "A", res["nA"]), (rb["mark"] + "B", res["nB"])) if q}
            row["nCut"], row["cut"] = res["nCut"], res["cut"]
            if not res["ok"]:
                row["flag"] = f"ring taper outside the closing range of {rb['mark']}A/{rb['mark']}B"
            if not res["ok_cut"]:
                row["flag"] = (row["flag"] + "; " if row["flag"] else "") + f"cut piece {res['cut']:.0f} < {min_cut:g} mm"
            if row["partial"]:
                row["flag"] = (row["flag"] + "; " if row["flag"] else "") + \
                    f"last ring only {width:.0f} mm wide (brick {pitch - joint:g}) - cut to fit"
            if elems:
                row["note"] = ", ".join(f"{e['count']} x {e['label']} on this ring" for e in elems)
            out["rings"].append(row)
            s = s1
            continue
        if rb:
            row["ui_mark"] = rb["mark"]
        ratio = cout / cin if cin > 0 else 1
        pair = None
        for a, b in zip(marks, marks[1:]):
            ra, rb = a["out"] / a["in"], b["out"] / b["in"]
            if rb - 1e-9 <= ratio <= ra + 1e-9:
                pair = (a, b)
                break
        if pair is None:
            pair = (marks[0], marks[0]) if ratio > marks[0]["out"] / marks[0]["in"] else (marks[-1], marks[-1])
            if pair[0] is marks[-1] and ratio < marks[-1]["out"] / marks[-1]["in"] - 0.005:
                row["flag"] = (f"ring taper {ratio:.3f} flatter than the flattest library wedge "
                               f"{marks[-1]['mark']} ({marks[-1]['out'] / marks[-1]['in']:.3f}) - straight shape needed")
        a, b = pair
        if a is b:
            res = close_ring(cin, cout, (a["in"], a["out"]), (a["in"], a["out"]), joint, min_cut)
            row["marks"] = {a["mark"]: res["nA"]}
        else:
            res = close_ring(cin, cout, (a["in"], a["out"]), (b["in"], b["out"]), joint, min_cut)
            row["marks"] = {m: q for m, q in ((a["mark"], res["nA"]), (b["mark"], res["nB"])) if q}
        row["nCut"], row["cut"] = res["nCut"], res["cut"]
        if row["partial"]:
            row["flag"] = (row["flag"] + "; " if row["flag"] else "") + \
                f"last ring only {width:.0f} mm wide (brick {pitch - joint:g}) - cut to fit"
        if not res["ok_cut"]:
            row["flag"] = (row["flag"] + "; " if row["flag"] else "") + f"cut piece {res['cut']:.0f} < {min_cut:g} mm"
        if elems:
            row["note"] = ", ".join(f"{e['count']} x {e['label']} on this ring (pieces beside them not in library)"
                                    for e in elems)
        out["rings"].append(row)
        s = s1
    for r in out["rings"]:
        if r["flag"]:
            out["flags"].append(f"Ring {r['ring']}: {r['flag']}")
    out["total"] = (1 if out.get("key") else 0) + sum(sum(r["marks"].values()) + r["nCut"] for r in out["rings"])
    return out


def quantities(courses, rings):
    q = {}
    for r in courses:
        for m, n in ((r.get("A"), r["nA"]), (r.get("B"), r["nB"])):
            if m and n:
                q[m] = q.get(m, 0) + n
        if r.get("A") and r["nCut"]:
            q[r["A"] + " (cut)"] = q.get(r["A"] + " (cut)", 0) + r["nCut"]
    if rings:
        if rings.get("key"):
            q[rings["key"]["mark"]] = q.get(rings["key"]["mark"], 0) + 1
        for r in rings["rings"]:
            for m, n in r["marks"].items():
                q[m] = q.get(m, 0) + n
            if r["nCut"]:
                k = list(r["marks"])[0] + " (cut)" if r["marks"] else "cut"
                q[k] = q.get(k, 0) + r["nCut"]
    return q
