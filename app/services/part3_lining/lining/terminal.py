"""
Terminal question-and-answer that builds the VESSEL and DESIGN data.
Nothing is pre-filled: every value comes from the person at the keyboard (or, later,
from colleague 1's vessel.json and colleague 2's rules via main.py --vessel / --design).
Numbers may be formulas, e.g.  D/2  or  0.12*Rmax  (parameters you define can be used).
"""
import math

from .expr import evaluate


# ------------------------------------------------------------------ small prompt helpers
def ask_text(prompt, default=None):
    while True:
        raw = input(f"  {prompt}" + (f" [{default}]" if default is not None else "") + ": ").strip()
        if raw:
            return raw
        if default is not None:
            return default


def ask_num(prompt, variables=None, default=None, positive=False, minimum=None, allow_blank=False):
    while True:
        raw = input(f"  {prompt}" + (f" [{default:g}]" if isinstance(default, (int, float)) else
                                     (f" [{default}]" if default is not None else "")) + ": ").strip()
        if raw == "":
            if default is not None:
                return float(default) if not isinstance(default, str) else evaluate(default, variables)
            if allow_blank:
                return None
            continue
        try:
            v = evaluate(raw, variables)
        except ValueError as e:
            print(f"    {e}")
            continue
        if positive and v <= 0:
            print("    must be greater than 0")
            continue
        if minimum is not None and v < minimum:
            print(f"    must be at least {minimum:g}")
            continue
        return v


def ask_int(prompt, minimum=1, default=None):
    while True:
        v = ask_num(prompt, default=default)
        if abs(v - round(v)) < 1e-9 and v >= minimum:
            return int(round(v))
        print(f"    enter a whole number >= {minimum}")


def ask_yes(prompt, default=True):
    raw = input(f"  {prompt} ({'Y/n' if default else 'y/N'}): ").strip().lower()
    return default if raw == "" else raw.startswith("y")


def ask_choice(prompt, choices, default=None):
    while True:
        raw = ask_text(f"{prompt} ({'/'.join(choices)})", default).lower()
        for c in choices:
            if raw == c.lower() or raw == c.lower()[0]:
                return c
        print(f"    choose one of: {', '.join(choices)}")


def ask_num_list(prompt, variables=None, allow_blank=False):
    while True:
        raw = input(f"  {prompt}: ").strip()
        if raw == "" and allow_blank:
            return []
        try:
            vals = [evaluate(t, variables) for t in raw.replace(",", " ").split()]
            if vals:
                return vals
        except ValueError as e:
            print(f"    {e}")


def banner(title):
    print(f"\n==== {title} " + "=" * max(4, 60 - len(title)))


# ------------------------------------------------------------------ STEP 1: vessel geometry
def collect_vessel():
    banner("STEP 1 - VESSEL GEOMETRY (shell inner profile)")
    print("  Describe the half-section of the steel shell, bottom -> top, as segments (lines/arcs).")
    print("  The profile is revolved around the vessel axis. Numbers may be formulas.\n")
    v = {"job": ask_text("Job / vessel name"), "units": ask_text("Units label", "mm")}
    mode = ask_choice("Do you give widths as radius or diameter", ["diameter", "radius"], "diameter")
    k = 0.5 if mode == "diameter" else 1.0
    v["shell_thickness"] = ask_num("Steel shell plate thickness (0 = draw shell as a line only)", minimum=0)
    r0 = ask_num(f"Start of profile at the bottom centre: {mode} (0 if it starts on the axis)", minimum=0) * k
    z0 = ask_num("Start of profile: height z (usually 0)")
    v["start"] = {"r": r0, "z": z0}
    n = ask_int("How many profile segments (e.g. dish, cone, barrel, cone, mouth)?")
    segs, r, z = [], r0, z0
    for i in range(1, n + 1):
        print(f"\n  -- Segment {i} of {n} --")
        name = ask_text("Name")
        dz = ask_num("Height of this segment (dz, along the axis)", minimum=0)
        er = ask_num(f"{mode.capitalize()} at the TOP end of this segment") * k
        shape = ask_choice("Shape", ["line", "arc"], "line")
        seg = {"name": name, "end_r": er, "end_z": z + dz, "shape": shape}
        if shape == "arc":
            seg["arc_radius"] = ask_num("Arc radius", positive=True)
            seg["convex"] = ask_yes("Does the arc bulge OUTWARD (like a dish bottom or a knuckle)?", True)
        segs.append(seg)
        r, z = er, z + dz
    v["segments"] = segs
    return v


def vessel_table(vessel):
    from .model import build_segments
    segs = build_segments(vessel)
    print(f"\n  {'#':<3}{'segment':<18}{'shape':<6}{'r start':>10}{'r end':>10}{'z start':>10}{'z end':>10}{'angle*':>8}")
    for i, s in enumerate(segs, 1):
        ang = math.degrees(math.atan2(s.p1[0] - s.p0[0], s.p1[1] - s.p0[1]))
        print(f"  {i:<3}{s.name:<18}{s.shape:<6}{s.p0[0]:>10.1f}{s.p1[0]:>10.1f}{s.p0[1]:>10.1f}{s.p1[1]:>10.1f}"
              f"{ang:>8.1f}" + (f"   arc R={s.R:g}" if s.shape == "arc" else ""))
    print("  (*angle of the chord from the vertical axis, degrees)")


def vessel_vars(vessel):
    """Names usable in formulas, computed from the vessel."""
    from .model import build_segments
    from .geometry import bounds, offset_path
    segs = build_segments(vessel)
    xmin, xmax, zmin, zmax = bounds(offset_path(segs, [0] * len(segs)).all())
    return {"H": zmax - zmin, "Rmax": xmax, "Dmax": 2 * xmax}


# ------------------------------------------------------------------ STEP 2/3: lining design
def collect_design(vessel):
    banner("STEP 2 - LINING DESIGN (zones, layers, grades, thickness)")
    print("  This is what the rules from colleague 2 will decide. For now you type it.")
    variables = dict(vessel_vars(vessel))
    print(f"  Vessel values you can use in formulas: H (height) = {variables['H']:g}, "
          f"Rmax = {variables['Rmax']:g}, Dmax = {variables['Dmax']:g}")
    print("  Your own named values (optional) e.g.   T = 230   -   press Enter on an empty line to finish")
    params = {}
    while True:
        raw = input("  name = value: ").strip()
        if not raw:
            break
        if "=" not in raw:
            print("    type like  T = 230")
            continue
        k, e = [s.strip() for s in raw.split("=", 1)]
        try:
            params[k] = variables[k] = evaluate(e, variables)
            print(f"    {k} = {params[k]:g}")
        except ValueError as ex:
            print(f"    {ex}")

    n_layers = ask_int("Number of lining layers (from steel shell towards the hot face)")
    layers = [ask_text(f"Name of layer {i} ({'next to the shell' if i == 1 else 'hot face' if i == n_layers else 'middle'})")
              for i in range(1, n_layers + 1)]
    design = {"layers": layers, "params": params,
              "heat_size": ask_num("Heat size / capacity in tonnes (blank to skip)", allow_blank=True),
              "bands": []}

    names = [s["name"] for s in vessel["segments"]]
    print("\n  Profile segments:  " + ",  ".join(f"{i}={n}" for i, n in enumerate(names, 1)))
    print("  A BAND is a group of neighbouring segments that share the same lining design.")
    nb = ask_int("How many bands?")
    for bi in range(1, nb + 1):
        print(f"\n  -- Band {bi} of {nb} --")
        band = {"name": ask_text("Band name")}
        used = {s for b in design["bands"] for s in b["segments"]}
        free = [n for n in names if n not in used]
        print("  Segments not yet in a band: " + (", ".join(free) or "none"))
        while True:
            raw = ask_text("Segments in this band (numbers or names, comma separated)")
            picked = []
            for tok in [t.strip() for t in raw.split(",") if t.strip()]:
                if tok.isdigit() and 1 <= int(tok) <= len(names):
                    picked.append(names[int(tok) - 1])
                elif tok in names:
                    picked.append(tok)
                else:
                    picked = None
                    print(f"    '{tok}' is not a segment")
                    break
            if picked:
                dup = [p for p in picked if p in used]
                if dup:
                    print(f"    already used: {', '.join(dup)}")
                    continue
                band["segments"] = picked
                break
        if design["bands"] and ask_yes(f"Copy the sectors/layers of '{design['bands'][-1]['name']}'?", False):
            band["sectors"] = [{"from": s["from"], "to": s["to"],
                                "layers": [dict(l) for l in s["layers"]]} for s in design["bands"][-1]["sectors"]]
        else:
            band["sectors"] = collect_sectors(layers, variables)
        design["bands"].append(band)
    return design


def collect_sectors(layers, variables):
    ns = ask_int("Number of angular sectors in this band (1 = same all around, 360 deg)")
    sectors, a = [], 0.0
    for si in range(1, ns + 1):
        if ns == 1:
            frm, to = 0.0, 360.0
        else:
            frm = a
            if si == ns:
                to = 360.0
                print(f"    sector {si}: {frm:g} -> 360 degrees")
            else:
                to = ask_num(f"Sector {si}: from {frm:g} degrees to", minimum=frm + 1e-6)
            a = to
        sec = {"from": frm, "to": to, "layers": []}
        for ln in layers:
            grade = ask_text(f"  [{frm:g}-{to:g} deg] {ln}: grade / material")
            t = ask_num(f"  [{frm:g}-{to:g} deg] {ln}: thickness", variables, positive=True)
            sec["layers"].append({"grade": grade, "thickness": t})
        sectors.append(sec)
    return sectors


# ------------------------------------------------------------------ STEP 3: which drawings
def collect_views(vessel, design):
    banner("STEP 3 - WHICH VIEWS DO YOU WANT")
    v = {}
    v["cuts"] = ask_num_list("Vertical section cut angles in degrees, e.g.  0  or  0 90  (cut plane through the axis)")
    heights = ask_num_list("Horizontal plan sections at these heights z (blank = automatic, one per band)",
                           allow_blank=True)
    v["plan_heights"] = heights or None
    v["top_view"] = ask_yes("Top view looking down (hot face by zone)?", True)
    v["unrolled"] = ask_yes("Unrolled wall map (angle x height)?", True)
    zero = ask_choice("In plan views, where does 0 degrees point", ["up", "right", "left", "down"], "up")
    v["plan_zero_deg"] = {"right": 0, "up": 90, "left": 180, "down": 270}[zero]
    return v
