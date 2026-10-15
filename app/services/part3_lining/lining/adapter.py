"""
Adapter between the wizard UI (teammate 1's extracted + user-confirmed geometry, step 3 zones)
and the lining engine (vessel.json / design.json contract in model.py).

What it does (no vessel-specific numbers live here):
  * profile_face == "outer": the UI dimensions are the OUTSIDE of the steel shell (as on the
    customer's shell drawing). The engine's profile is the shell's INSIDE face, so the profile is
    offset inward by the plate thickness (exact offset of lines and arcs) and the plate is drawn
    as `shell_thickness`.
  * Bands may cover only part of a segment height ("z_from"/"z_to", from height-split zones in
    step 3). Segments are split at those heights so every band maps to whole segments.
  * Sectors that wrap through 0 deg (e.g. 315 -> 45) are split into 315-360 and 0-45.
"""
import math

from .geometry import Seg, offset_path, left, unit, sub

EPS = 1e-6


# ---------------------------------------------------------------- helpers
def _num(v, name):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: '{v}' is not a number")
    if math.isnan(f) or math.isinf(f):
        raise ValueError(f"{name}: not a valid number")
    return f


def _point_at_z(seg, z):
    """Point of a profile segment at height z (segment must span z)."""
    (r0, z0), (r1, z1) = seg.p0, seg.p1
    if seg.shape == "line":
        if abs(z1 - z0) < EPS:
            raise ValueError(f"cannot split horizontal segment '{seg.name}' by height")
        s = (z - z0) / (z1 - z0)
        return (r0 + s * (r1 - r0), z)
    C, R = seg.C, seg.R
    dz = z - C[1]
    if abs(dz) > R + EPS:
        raise ValueError(f"height {z:g} is outside arc '{seg.name}'")
    dx = math.sqrt(max(R * R - dz * dz, 0.0))
    best = None
    for x in (C[0] + dx, C[0] - dx):
        if min(r0, r1) - 1e-3 <= x <= max(r0, r1) + 1e-3:
            if best is None or abs(x - (r0 + r1) / 2) < abs(best - (r0 + r1) / 2):
                best = x
    if best is None:
        raise ValueError(f"height {z:g} is not on arc '{seg.name}'")
    return (best, z)


def _segs_from_profile(start, segments):
    segs, p = [], (start["r"], start["z"])
    for s in segments:
        q = (_num(s["end_r"], f"{s['name']} end radius"), _num(s["end_z"], f"{s['name']} end height"))
        shape = s.get("shape", "line")
        R = _num(s["arc_radius"], f"{s['name']} arc radius") if shape == "arc" else None
        segs.append(Seg(s["name"], p, q, shape, R, bool(s.get("convex", True))))
        p = q
    return segs


def _seg_to_dict(seg):
    d = {"name": seg.name, "end_r": round(seg.p1[0], 4), "end_z": round(seg.p1[1], 4), "shape": seg.shape}
    if seg.shape == "arc":
        d["arc_radius"] = round(seg.R, 4)
        d["convex"] = bool(seg.ccw)
    return d


# ---------------------------------------------------------------- vessel
def split_segments(vessel, cuts):
    """cuts: {segment name: [z, ...]}. Returns (new vessel, {old name: [(new name, z0, z1), ...]})."""
    segs = _segs_from_profile(vessel["start"], vessel["segments"])
    out, mapping = [], {}
    for s in segs:
        z0, z1 = s.p0[1], s.p1[1]
        zs = sorted({round(z, 6) for z in cuts.get(s.name, []) if z0 + 1.0 < z < z1 - 1.0})
        if not zs:
            out.append(s)
            mapping[s.name] = [(s.name, z0, z1)]
            continue
        pts = [s.p0] + [_point_at_z(s, z) for z in zs] + [s.p1]
        bounds = [z0] + zs + [z1]
        mapping[s.name] = []
        for k in range(len(pts) - 1):
            nm = f"{s.name} [{bounds[k]:.0f}-{bounds[k + 1]:.0f}]"
            out.append(Seg(nm, pts[k], pts[k + 1], s.shape, s.R, s.ccw))
            mapping[s.name].append((nm, bounds[k], bounds[k + 1]))
    nv = dict(vessel)
    nv["segments"] = [_seg_to_dict(s) for s in out]
    return nv, mapping


def outer_to_inner(vessel):
    """Offset an OUTSIDE-of-shell profile inward by the plate thickness."""
    plate = float(vessel.get("shell_thickness", 0) or 0)
    if plate <= 0:
        return dict(vessel)
    segs = _segs_from_profile(vessel["start"], vessel["segments"])
    path = offset_path(segs, [plate] * len(segs))
    J = path.J
    out = []
    for i, s in enumerate(segs):
        d = {"name": s.name, "end_r": round(J[i + 1][0], 4), "end_z": round(J[i + 1][1], 4), "shape": s.shape}
        if s.shape == "arc":
            d["arc_radius"] = round(s.R - plate if s.ccw else s.R + plate, 4)
            d["convex"] = bool(s.ccw)
        out.append(d)
    nv = dict(vessel)
    nv["start"] = {"r": round(J[0][0], 4), "z": round(J[0][1], 4)}
    nv["segments"] = out
    nv["profile_face"] = "inner"
    return nv


# ---------------------------------------------------------------- design
def normalise_sectors(sectors):
    """Split wrap-around sectors, sort, and return engine sectors (0 -> 360)."""
    out = []
    for s in sectors:
        a0 = _num(s["from"], "sector from") % 360.0
        a1 = _num(s["to"], "sector to")
        a1 = 360.0 if abs(a1 - 360.0) < EPS else a1 % 360.0
        full = abs(_num(s["to"], "sector to") - _num(s["from"], "sector from")) >= 360 - EPS
        base = {k: v for k, v in s.items() if k not in ("from", "to")}
        if full:
            out.append({**base, "from": 0.0, "to": 360.0})
        elif a1 == 0:
            out.append({**base, "from": a0, "to": 360.0})
        elif a0 < a1:
            out.append({**base, "from": a0, "to": a1})
        else:
            out.append({**base, "from": a0, "to": 360.0})
            out.append({**base, "from": 0.0, "to": a1})
    out.sort(key=lambda s: s["from"])
    # merge neighbours that are identical (e.g. the two halves of a wrap zone are not neighbours,
    # but 0-45 / 45-90 of the same zone could be)
    merged = []
    for s in out:
        if merged and abs(merged[-1]["to"] - s["from"]) < EPS and merged[-1].get("name") == s.get("name") \
                and merged[-1]["layers"] == s["layers"]:
            merged[-1]["to"] = s["to"]
        else:
            merged.append(dict(s))
    return merged


def build_engine_inputs(payload):
    """UI payload -> (vessel, design, mapping). Raises ValueError with a readable message."""
    vessel_in = payload["vessel"]
    design_in = payload["design"]
    if not vessel_in.get("segments"):
        raise ValueError("Vessel profile has no segments.")

    # 1. height cuts requested by bands
    cuts = {}
    for b in design_in["bands"]:
        for seg_name in b["segments"]:
            for key in ("z_from", "z_to"):
                if b.get(key) not in (None, ""):
                    cuts.setdefault(seg_name, []).append(_num(b[key], f"band '{b['name']}' {key}"))

    vessel, mapping = split_segments(vessel_in, cuts)
    if vessel_in.get("profile_face", "outer") == "outer":
        vessel = outer_to_inner(vessel)

    # 2. bands -> engine bands on the split segments
    bands = []
    for b in design_in["bands"]:
        zf = None if b.get("z_from") in (None, "") else float(b["z_from"])
        zt = None if b.get("z_to") in (None, "") else float(b["z_to"])
        names = []
        for seg_name in b["segments"]:
            if seg_name not in mapping:
                raise ValueError(f"Band '{b['name']}': unknown segment '{seg_name}'.")
            for nm, z0, z1 in mapping[seg_name]:
                zm = (z0 + z1) / 2
                if (zf is None or zm >= zf - EPS) and (zt is None or zm <= zt + EPS):
                    names.append(nm)
        if not names:
            raise ValueError(f"Band '{b['name']}': its height range covers no part of {b['segments']}.")
        sectors = normalise_sectors(b["sectors"])
        for s in sectors:
            for l in s["layers"]:
                l["thickness"] = _num(l["thickness"], f"band '{b['name']}' thickness")
                l["grade"] = str(l.get("grade", "")).strip()
        # a horizontal step segment that sits exactly on a band boundary belongs to the lower band only
        names = [n for n in names if not any(n in ob["segments"] for ob in bands)]
        if not names:
            raise ValueError(f"Band '{b['name']}': its height range covers no part of {b['segments']}.")
        extra = {k: _num(b[k], f"band '{b['name']}' {k}") for k in ("course_height", "brick_height", "joint", "min_cut", "course_origin")
                 if b.get(k) not in (None, "")}
        if b.get("section"):
            extra["section"] = str(b["section"])
        if b.get("expansion"):
            extra["expansion"] = {k: _num(v, f"band '{b['name']}' expansion {k}") for k, v in b["expansion"].items()
                                  if v not in (None, "")}
        bands.append({"name": b["name"], "segments": names, "sectors": sectors,
                      **({"kind": b["kind"]} if b.get("kind") else {}), **extra})

    design = {
        "layers": list(design_in["layers"]),
        "heat_size": design_in.get("heat_size"),
        "bands": bands,
        "views": design_in.get("views") or {"cuts": [0], "plan_heights": None, "top_view": True,
                                            "unrolled": True, "plan_zero_deg": 90},
    }
    if design_in.get("grade_colors"):
        design["grade_colors"] = design_in["grade_colors"]
    return vessel, design, mapping
