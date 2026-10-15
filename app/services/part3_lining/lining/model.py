"""
Data contract between the three parts of the project.

VESSEL  (colleague 1 - extracted from the customer drawing)  -> vessel.json
    {"job": str, "units": "mm", "shell_thickness": number,
     "start": {"r": number, "z": number},                      # profile start (bottom centre)
     "segments": [ {"name": str, "end_r": number, "end_z": number,
                    "shape": "line" | "arc", "arc_radius": number, "convex": bool}, ... ]}   # bottom -> top

DESIGN  (colleague 2's rules applied / or typed by hand)        -> design.json
    {"layers": ["PERMANENT", "WORKING"],                        # shell side -> hot face
     "heat_size": number | null,
     "bands": [ {"name": str, "segments": [segment names],
                 "sectors": [ {"from": deg, "to": deg,
                               "layers": [ {"grade": str, "thickness": number}, ... ]} ]} ],
     "views": {"cuts": [angles], "plan_heights": [z...] | null, "top_view": bool,
               "unrolled": bool, "plan_zero_deg": number},
     "grade_colors": {"GRADE": "red|1..255"}  (optional)}
"""
import json
from pathlib import Path

from .geometry import Seg

PALETTE = [3, 5, 1, 2, 4, 6, 30, 40, 52, 94, 150, 190, 210, 230, 12, 34, 70, 110]
COLOR_NAMES = {"red": 1, "yellow": 2, "green": 3, "cyan": 4, "blue": 5, "magenta": 6,
               "white": 7, "gray": 8, "grey": 8, "orange": 30}


def load_json(path):
    return json.loads(Path(path).read_text())


def save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2))


def build_segments(vessel):
    segs, p = [], (vessel["start"]["r"], vessel["start"]["z"])
    for s in vessel["segments"]:
        q = (s["end_r"], s["end_z"])
        segs.append(Seg(s["name"], p, q, s.get("shape", "line"), s.get("arc_radius"), s.get("convex", True)))
        p = q
    return segs


def sector_at(band, angle):
    a = angle % 360.0
    for sec in band["sectors"]:
        if sec["from"] - 1e-9 <= a < sec["to"] - 1e-9:
            return sec
    return band["sectors"][-1]


def angle_breaks(design):
    """All sector boundary angles of all bands (sorted, 0..360)."""
    br = {0.0, 360.0}
    for b in design["bands"]:
        for s in b["sectors"]:
            br.add(float(s["from"]))
            br.add(float(s["to"]))
    return sorted(br)


def grade_colors(design):
    """grade -> ACI colour (user choice first, then palette in order of appearance)."""
    out, n = {}, 0
    user = design.get("grade_colors", {})
    for b in design["bands"]:
        for s in b["sectors"]:
            for l in s["layers"]:
                g = l["grade"]
                if g in out:
                    continue
                c = user.get(g)
                if isinstance(c, str):
                    c = COLOR_NAMES.get(c.lower(), int(c) if c.isdigit() else None)
                if c is None:
                    c = PALETTE[n % len(PALETTE)]
                    n += 1
                out[g] = int(c)
    return out


def validate(vessel, design):
    """-> (errors, warnings) as lists of readable strings."""
    err, warn = [], []
    segs = vessel.get("segments", [])
    if not segs:
        err.append("Vessel has no segments.")
        return err, warn
    names = [s["name"] for s in segs]
    if len(set(names)) != len(names):
        err.append("Segment names must be unique.")
    prev_z = vessel["start"]["z"]
    for s in segs:
        if s["end_z"] < prev_z - 1e-9:
            err.append(f"Segment '{s['name']}' goes downward (end z {s['end_z']:g} < {prev_z:g}); profile must go bottom -> top.")
        prev_z = s["end_z"]
    try:
        build_segments(vessel)
    except ValueError as e:
        err.append(str(e))

    layers = design.get("layers", [])
    if not layers:
        err.append("Define at least one lining layer.")
    bands = design.get("bands", [])
    assigned = {}
    for bi, b in enumerate(bands):
        idx = []
        for sn in b["segments"]:
            if sn not in names:
                err.append(f"Band '{b['name']}': unknown segment '{sn}'.")
                continue
            if sn in assigned:
                err.append(f"Segment '{sn}' is in both '{assigned[sn]}' and '{b['name']}'.")
            assigned[sn] = b["name"]
            idx.append(names.index(sn))
        idx.sort()
        if idx and idx != list(range(idx[0], idx[-1] + 1)):
            err.append(f"Band '{b['name']}': its segments must be next to each other.")
        secs = b["sectors"]
        if not secs:
            err.append(f"Band '{b['name']}' has no sectors.")
            continue
        if abs(secs[0]["from"]) > 1e-9 or abs(secs[-1]["to"] - 360) > 1e-9:
            err.append(f"Band '{b['name']}': sectors must start at 0 and end at 360 degrees.")
        for a, c in zip(secs, secs[1:]):
            if abs(a["to"] - c["from"]) > 1e-9:
                err.append(f"Band '{b['name']}': gap/overlap between {a['to']:g} and {c['from']:g} degrees.")
        for s in secs:
            if s["to"] <= s["from"]:
                err.append(f"Band '{b['name']}': sector {s['from']:g}-{s['to']:g} is empty or reversed.")
            if len(s["layers"]) != len(layers):
                err.append(f"Band '{b['name']}' sector {s['from']:g}-{s['to']:g}: needs {len(layers)} layers.")
            for li, l in enumerate(s["layers"]):
                # the working (last) layer must exist; an outer layer (e.g. backfill) may be 0 in a band
                if l["thickness"] < 0 or (li == len(s["layers"]) - 1 and l["thickness"] <= 0):
                    err.append(f"Band '{b['name']}': {layers[li] if li < len(layers) else 'layer'} thickness must be > 0.")
                if not l["grade"].strip():
                    err.append(f"Band '{b['name']}': empty grade name.")
    for n in names:
        if n not in assigned:
            err.append(f"Segment '{n}' is not in any lining band.")
    return err, warn
