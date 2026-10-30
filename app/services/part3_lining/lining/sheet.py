"""
Builds the lining drawing sheet (DXF) from VESSEL + DESIGN data. No vessel- or job-specific value
lives here: everything comes from the data it is given.

Views: vertical sections (any cut angle), horizontal plan sections (any height),
       top view (hot-face projection), unrolled wall map, quality legend.
"""
import datetime
import math

import ezdxf
from ezdxf.enums import TextEntityAlignment as TA

from .geometry import (bounds, offset_path, radius_at_z, reverse_run, sample)
from .model import angle_breaks, build_segments, grade_colors, sector_at

ALIGN = {"left": TA.LEFT, "center": TA.MIDDLE_CENTER, "right": TA.RIGHT,
         "mleft": TA.MIDDLE_LEFT, "mright": TA.MIDDLE_RIGHT, "bcenter": TA.BOTTOM_CENTER}


def clean(name):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip())


def decimals(v):
    for d in (0, 1, 2):
        if abs(v * 10 ** d - round(v * 10 ** d)) < 1e-6:
            return d
    return 2


def _clip_to_region(p, q, poly):
    """Shortens segment p->q (p on the region boundary) at the first place it leaves the region."""
    best = 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]):
        ex, ey = bx - ax, by - ay
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = ((ax - p[0]) * ey - (ay - p[1]) * ex) / den
        u = ((ax - p[0]) * dy - (ay - p[1]) * dx) / den
        if 1e-3 < t < best and -1e-9 <= u <= 1 + 1e-9:
            best = t
    return p[0] + dx * best, p[1] + dy * best


class Sheet:
    def __init__(self, vessel, design):
        self.vessel, self.design = vessel, design
        self.segs = build_segments(vessel)
        self.n = len(self.segs)
        self.layers = design["layers"]
        self.L = len(self.layers)
        self.shell_t = float(vessel.get("shell_thickness", 0) or 0)
        self.bands = design["bands"]
        self.seg_names = [s.name for s in self.segs]
        self.seg_band = {sn: bi for bi, b in enumerate(self.bands) for sn in b["segments"]}
        self.band_idx = []                       # (first, last) segment index per band
        for b in self.bands:
            ix = sorted(self.seg_names.index(sn) for sn in b["segments"])
            self.band_idx.append((ix[0], ix[-1]))
        shell = offset_path(self.segs, [0.0] * self.n)
        self.rmin, self.rmax, self.zmin, self.zmax = bounds(shell.all())
        self.rout = self.rmax + self.shell_t
        self.th = max(2 * self.rout, self.zmax - self.zmin) / 70.0
        self.colors = grade_colors(design)
        self.zero = float(design.get("views", {}).get("plan_zero_deg", 90))
        self.doc = ezdxf.new("R2018", setup=True)
        self.msp = self.doc.modelspace()
        self._layers, self._cache = set(), {}
        # drawing-set output: every view drawn is recorded (title + its entity handles) so it can be put on
        # its own PDF page / AutoCAD layout; see pages.py
        self.views, self._frames, self._cur_view = [], [], None
        self.plan_letters = {}                   # plan height -> section letter (A, B, ...) shown on the sections
        self.inputs = []                         # step-2 values + source (from the UI), drawn as an input table
        self.steel_density = None                # t/m3, for the metal level line
        self.slag_volume = None                  # m3, for the slag level line

    # ------------------------------------------------------------ layers / primitives
    def layer(self, name, color=7):
        if name not in self._layers:
            self.doc.layers.add(name, color=color)
            self._layers.add(name)
        return name

    def grade_layer(self, grade):
        return self.layer("GRADE_" + clean(grade), self.colors[grade])

    def text(self, x, y, s, h=None, align="left", layer="TEXT"):
        self.layer("TEXT", 7)
        self.msp.add_text(s, height=h or self.th, dxfattribs={"layer": self.layer(layer, 7)}
                          ).set_placement((x, y), align=ALIGN[align])

    def line(self, p, q, layer):
        self.msp.add_line(p, q, dxfattribs={"layer": layer})

    def poly(self, verts, layer, close=False):
        self.msp.add_lwpolyline(verts, format="xyb", close=close, dxfattribs={"layer": layer})

    def fill(self, verts, aci, layer):
        h = self.msp.add_hatch(color=aci, dxfattribs={"layer": layer})
        h.set_solid_fill(color=aci)
        h.paths.add_polyline_path(verts, is_closed=True)

    def dim_override(self, v):
        th = self.th
        return {"dimtxt": th * 0.9, "dimasz": th * 0.7, "dimexe": th * 0.4, "dimexo": th * 0.3,
                "dimgap": th * 0.25, "dimdec": decimals(v), "dimlfac": 1}

    def dim_v(self, x_ref, z1, z2, x_base, Y):
        self.layer("DIMS", 8)
        self.msp.add_linear_dim(base=(x_base, Y(z1)), p1=(x_ref, Y(z1)), p2=(x_ref, Y(z2)), angle=90,
                                override=self.dim_override(abs(z2 - z1)), dxfattribs={"layer": "DIMS"}).render()

    def dim_h(self, x1, x2, y, y_base, v):
        self.layer("DIMS", 8)
        self.msp.add_linear_dim(base=(x1, y_base), p1=(x1, y), p2=(x2, y), angle=0,
                                override=self.dim_override(v), dxfattribs={"layer": "DIMS"}).render()

    # ------------------------------------------------------------ lining geometry at an angle
    def boundary_paths(self, angle):
        """Boundary curves (shell face, layer boundaries ..., hot face) at a given angle."""
        key = round(angle % 360.0, 6)
        if key in self._cache:
            return self._cache[key]
        th_by_seg = []
        for i, s in enumerate(self.segs):
            sec = sector_at(self.bands[self.seg_band[s.name]], key)
            if i > 0 and abs(s.p1[1] - s.p0[1]) < 1e-6 and s.shape == "line":
                # horizontal step in the shell (e.g. mouth ring narrower than the cone top): bricks are laid
                # horizontally from the steel, so the lining just steps with the shell - no thickness on the step
                th_by_seg.append([0.0] * len(sec["layers"]))
            else:
                th_by_seg.append([l["thickness"] for l in sec["layers"]])
        try:
            paths = [offset_path(self.segs, [sum(t[:k]) for t in th_by_seg]) for k in range(self.L + 1)]
            outer = offset_path(self.segs, [-self.shell_t] * self.n) if self.shell_t > 0 else None
        except ValueError as e:
            raise ValueError(f"At {key:g} degrees: {e}")
        self._cache[key] = (paths, outer)
        return self._cache[key]

    def _rad(self, path, z):
        v = path.all()
        r = radius_at_z(v, z)
        if r is None:
            _, _, z0, z1 = bounds(v)
            r = 0.0 if z < z0 else v[-1][0]
        return r

    def band_z(self, bi):
        i0, i1 = self.band_idx[bi]
        return min(s.p0[1] for s in self.segs[i0:i1 + 1]), max(s.p1[1] for s in self.segs[i0:i1 + 1])

    # ------------------------------------------------------------ polar helpers (plan views)
    def polar(self, cx, cy, r, a):
        t = math.radians(a + self.zero)
        return (cx + r * math.cos(t), cy + r * math.sin(t))

    def ring_fill(self, cx, cy, r_out, r_in, a0, a1, aci, layer):
        n = max(1, int(math.ceil((a1 - a0) / 180 - 1e-9)))
        for k in range(n):
            s, e = a0 + (a1 - a0) * k / n, a0 + (a1 - a0) * (k + 1) / n
            b = math.tan(math.radians(e - s) / 4)
            P1, P2 = self.polar(cx, cy, r_out, s), self.polar(cx, cy, r_out, e)
            if r_in > 1e-6:
                Q2, Q1 = self.polar(cx, cy, r_in, e), self.polar(cx, cy, r_in, s)
                v = [(*P1, b), (*P2, 0), (*Q2, -b), (*Q1, 0)]
            else:
                v = [(*P1, b), (*P2, 0), (cx, cy, 0)]
            self.fill(v, aci, layer)

    def arc_line(self, cx, cy, r, a0, a1, layer):
        if r < 1e-6:
            return
        if a1 - a0 >= 359.999:
            self.msp.add_circle((cx, cy), r, dxfattribs={"layer": layer})
        else:
            self.msp.add_arc((cx, cy), r, (a0 + self.zero) % 360, (a1 + self.zero) % 360, dxfattribs={"layer": layer})

    def center_mark(self, cx, cy):
        self.layer("CENTER", 1)
        s = self.th * 2
        self.line((cx - s, cy), (cx + s, cy), "CENTER")
        self.line((cx, cy - s), (cx, cy + s), "CENTER")

    # ================================================================ VERTICAL SECTION
    def vertical_size(self):
        th = self.th
        return 80 * th + 2 * self.rout, (self.zmax - self.zmin) + 17 * th

    def draw_vertical(self, ox, oy, cut):
        self._frames.append(("vertical", ox, oy, cut, self._cur_view))
        th, L = self.th, self.L
        axis_x = ox + 44 * th + self.rout
        Y = lambda z: oy + 6 * th + (z - self.zmin)
        shell_layer = self.layer("SHELL", 8)
        for side, angle in ((1, cut), (-1, cut + 180)):
            paths, outer = self.boundary_paths(angle)
            T = lambda run: [(axis_x + side * x, Y(z), side * b) for x, z, b in run]
            if outer:
                self.fill(T(outer.all() + reverse_run(paths[0].all())), 8, shell_layer)
                self.poly(T(outer.all()), shell_layer)
            for k in range(L + 1):
                self.poly(T(paths[k].all()), shell_layer if k == 0 else self.layer("LINING_" + clean(self.layers[k - 1]), 7))
            cuts = set()
            for bi, band in enumerate(self.bands):
                i0, i1 = self.band_idx[bi]
                sec = sector_at(band, angle)
                cuts.update((i0, i1 + 1))
                for j in range(1, L + 1):
                    reg = paths[j - 1].run(i0, i1) + reverse_run(paths[j].run(i0, i1))
                    g = sec["layers"][j - 1]["grade"]
                    self.fill(T(reg), self.colors[g], self.grade_layer(g))
            for idx in cuts:                                   # zone dividers across each layer
                for j in range(1, L + 1):
                    a, b = paths[j - 1].J[idx], paths[j].J[idx]
                    if abs(a[0]) < 0.01 * th and abs(b[0]) < 0.01 * th:
                        continue
                    self.line((axis_x + side * a[0], Y(a[1])), (axis_x + side * b[0], Y(b[1])),
                              self.layer("LINING_" + clean(self.layers[j - 1]), 7))
            self._labels(axis_x, Y, side, angle, paths)
        # dimensions: level chain + overall height + mouth diameter
        zs = sorted({round(s.p0[1], 6) for s in self.segs} | {round(s.p1[1], 6) for s in self.segs})
        x_ref = ox + 10 * th
        for a, b in zip(zs, zs[1:]):
            self.dim_v(x_ref, a, b, ox + 6 * th, Y)
        self.dim_v(x_ref, zs[0], zs[-1], ox + 2.5 * th, Y)
        rt = abs(self.segs[-1].p1[0])
        self.dim_h(axis_x - rt, axis_x + rt, Y(self.zmax), Y(self.zmax) + 3.5 * th, 2 * rt)
        self.text(axis_x, oy + 1.2 * th, f"VERTICAL SECTION  {cut % 360:g}\u00b0 - {(cut + 180) % 360:g}\u00b0", 1.4 * th, "center")
        self.text(axis_x, oy - 0.6 * th + 0.0, f"right side = {cut % 360:g}\u00b0   left side = {(cut + 180) % 360:g}\u00b0",
                  0.8 * th, "center")
        self.draw_section_marks(axis_x, Y)
        self.draw_levels(axis_x, Y)
        edited = [r for r in self.inputs if r.get("edited")]
        if edited:                                           # values changed by the designer, on the section itself
            self.layer("INPUT_EDITED", 1)
            yy = oy - 2.4 * th
            self.text(axis_x, yy, "EDITED INPUT VALUES USED IN THIS DRAWING (not the values read from the uploaded drawing):",
                      0.8 * th, "center", "INPUT_EDITED")
            for r in edited[:8]:
                yy -= 1.3 * th
                v = r.get("value")
                vt = f"{v:g}" if isinstance(v, (int, float)) else str(v)
                self.text(axis_x, yy, f"{r.get('label', '')} = {vt} {r.get('unit', '')}   (drawing: {r.get('drawing') or '-'})",
                          0.75 * th, "center", "INPUT_EDITED")
            if len(edited) > 8:
                self.text(axis_x, yy - 1.3 * th, f"... and {len(edited) - 8} more - see DESIGN INPUT VALUES", 0.75 * th,
                          "center", "INPUT_EDITED")

    def _labels(self, axis_x, Y, side, angle, paths):
        th = self.th
        items = []
        for bi, band in enumerate(self.bands):
            i0, i1 = self.band_idx[bi]
            za, zb = self.band_z(bi)
            zm = (za + zb) / 2
            sec = sector_at(band, angle)
            for j in range(1, self.L + 1):
                ra = radius_at_z(paths[j - 1].run(i0, i1), zm)
                rb = radius_at_z(paths[j].run(i0, i1), zm)
                if ra is None:
                    continue
                rb = rb or 0.0
                l = sec["layers"][j - 1]
                items.append((Y(zm), f"{self.layers[j - 1]}: {l['grade']} ({l['thickness']:g})",
                              (axis_x + side * (ra + rb) / 2, Y(zm)), band["name"] if j == 1 else None))
        items.sort(key=lambda t: -t[0])
        prev = None
        x_text = axis_x + side * (self.rout + 3 * th)
        for y, txt, tgt, band_name in items:
            ly = y if prev is None else min(y, prev - 1.5 * th)
            prev = ly
            self.line(tgt, (x_text - side * 0.4 * th, ly), "LABEL_LEADERS" if self.layer("LABEL_LEADERS", 8) else "")
            self.text(x_text, ly, txt, 0.9 * th, "mleft" if side == 1 else "mright")

    # ================================================================ PLAN SECTION
    def plan_size(self):
        th = self.th
        return 2 * self.rout + 18 * th, 2 * self.rout + 24 * th

    def _plan_frame(self, ox, oy):
        th = self.th
        W, H = self.plan_size()
        return ox + W / 2, oy + 15 * th + self.rout

    def draw_plan(self, ox, oy, z):
        self._frames.append(("plan", ox, oy, z, self._cur_view))
        th, L = self.th, self.L
        cx, cy = self._plan_frame(ox, oy)
        seg = next((s for s in self.segs if s.p0[1] - 1e-9 <= z <= s.p1[1] + 1e-9), None)
        if seg is None:
            raise ValueError(f"plan height z={z:g} is outside the vessel ({self.zmin:g} .. {self.zmax:g})")
        bi = self.seg_band[seg.name]
        band = self.bands[bi]
        first = None
        for sec in band["sectors"]:
            mid = (sec["from"] + sec["to"]) / 2
            paths, outer = self.boundary_paths(mid)
            rs = [self._rad(paths[k], z) for k in range(L + 1)]
            if first is None:
                first = (rs, outer and self._rad(outer, z))
            if outer:
                ro = self._rad(outer, z)
                self.ring_fill(cx, cy, ro, rs[0], sec["from"], sec["to"], 8, self.layer("SHELL", 8))
                self.arc_line(cx, cy, ro, sec["from"], sec["to"], "SHELL")
            for j in range(1, L + 1):
                g = sec["layers"][j - 1]["grade"]
                self.ring_fill(cx, cy, rs[j - 1], rs[j], sec["from"], sec["to"], self.colors[g], self.grade_layer(g))
            for k in range(L + 1):
                self.arc_line(cx, cy, rs[k], sec["from"], sec["to"],
                              self.layer("SHELL" if k == 0 else "LINING_" + clean(self.layers[k - 1]), 7))
            if len(band["sectors"]) > 1:
                r_in = rs[L]
                r_ou = (self._rad(outer, z) if outer else rs[0])
                self.line(self.polar(cx, cy, r_in, sec["from"]), self.polar(cx, cy, r_ou, sec["from"]), self.layer("SECTOR_LINES", 1))
                t = self.polar(cx, cy, self.rout + 2.2 * th, sec["from"])
                self.text(t[0], t[1], f"{sec['from'] % 360:g}\u00b0", 0.9 * th, "center")
            # zone name, span and hot-face grade written inside the ring (as on the customer's sections)
            span = (sec["to"] - sec["from"]) % 360 or 360
            tm = self.polar(cx, cy, max(rs[L] * 0.55, 0.3 * self.rout), mid)
            g_hot = sec["layers"][L - 1]["grade"]
            self.text(tm[0], tm[1] + 0.55 * th, f"{str(sec.get('name') or band['name']).upper()} ({span:g}\u00b0)",
                      0.75 * th, "center", "ZONE_LABELS")
            self.text(tm[0], tm[1] - 0.75 * th, g_hot, 0.75 * th, "center", "ZONE_LABELS")
        self.center_mark(cx, cy)
        rs, ro = first
        r_dim = ro if ro else rs[0]
        self.dim_h(cx - r_dim, cx + r_dim, cy, cy - self.rout - 4 * th, 2 * r_dim)
        lt = self.plan_letters.get(z)
        self.text(cx, oy + 4.5 * th, f"SECTION {lt}-{lt}   (Z = {z:g})" if lt else f"PLAN SECTION  Z = {z:g}", 1.4 * th, "center")
        self.text(cx, oy + 2.2 * th, f"band: {band['name']}   shell ID {2 * rs[0]:g}   hot face {2 * rs[L]:g}", 0.8 * th, "center")

    # ================================================================ TOP VIEW
    def draw_top(self, ox, oy):
        th, L = self.th, self.L
        cx, cy = self._plan_frame(ox, oy)
        order = sorted(range(len(self.bands)), key=lambda b: self.band_z(b)[0])
        for bi in order:                                       # low -> high, higher parts drawn over lower
            band = self.bands[bi]
            i0, i1 = self.band_idx[bi]
            for sec in band["sectors"]:
                paths, _ = self.boundary_paths((sec["from"] + sec["to"]) / 2)
                xs = [p[0] for p in sample(paths[L].run(i0, i1))]
                r_lo, r_hi = min(xs), max(xs)
                if r_hi - r_lo < 1e-6:
                    continue
                g = sec["layers"][L - 1]["grade"]
                self.ring_fill(cx, cy, r_hi, r_lo, sec["from"], sec["to"], self.colors[g], self.grade_layer(g))
                for r in (r_lo, r_hi):
                    self.arc_line(cx, cy, r, sec["from"], sec["to"], self.layer("LINING_" + clean(self.layers[-1]), 7))
        self.arc_line(cx, cy, self.rout, 0, 360, self.layer("SHELL", 8))
        self.center_mark(cx, cy)
        brk = angle_breaks(self.design)[:-1]
        for a in brk[1:] if len(brk) > 1 else []:
            t = self.polar(cx, cy, self.rout + 2.2 * th, a)
            self.line(self.polar(cx, cy, self.rout, a), self.polar(cx, cy, self.rout + 1.2 * th, a), self.layer("SECTOR_LINES", 1))
            self.text(t[0], t[1], f"{a:g}\u00b0", 0.9 * th, "center")
        self.text(cx, oy + 4.5 * th, "TOP VIEW (looking down, hot face)", 1.4 * th, "center")
        self.text(cx, oy + 2.2 * th, f"0\u00b0 direction = {self._zero_name()}", 0.8 * th, "center")

    def _zero_name(self):
        return {0: "right", 90: "up", 180: "left", 270: "down"}.get(int(self.zero) % 360, f"{self.zero:g}\u00b0")

    # ================================================================ UNROLLED MAP
    def unrolled_size(self):
        th = self.th
        self.sx = 2 * math.pi * self.rmax / 360.0
        return 360 * self.sx + 22 * th, (self.zmax - self.zmin) + 14 * th

    def draw_unrolled(self, ox, oy):
        self._frames.append(("unrolled", ox, oy, None, self._cur_view))
        th = self.th
        self.unrolled_size()
        x0, y0 = ox + 12 * th, oy + 8 * th
        for bi, band in enumerate(self.bands):
            za, zb = self.band_z(bi)
            for sec in band["sectors"]:
                xa, xb = x0 + sec["from"] * self.sx, x0 + sec["to"] * self.sx
                ya, yb = y0 + za - self.zmin, y0 + zb - self.zmin
                g = sec["layers"][-1]["grade"]
                self.fill([(xa, ya, 0), (xb, ya, 0), (xb, yb, 0), (xa, yb, 0)], self.colors[g], self.grade_layer(g))
                self.poly([(xa, ya, 0), (xb, ya, 0), (xb, yb, 0), (xa, yb, 0)], self.layer("UNROLLED_FRAME", 7), close=True)
                lines = [f"{self.layers[j]}: {l['grade']} ({l['thickness']:g})" for j, l in enumerate(sec["layers"])]
                h = 0.8 * th
                if (xb - xa) > 14 * th and (yb - ya) > (len(lines) + 1) * h * 1.3:
                    for k, t in enumerate(lines):
                        self.text((xa + xb) / 2, (ya + yb) / 2 + (len(lines) / 2 - k - 0.5) * h * 1.4, t, h, "center")
            self.text(x0 - 1.5 * th, y0 + (za + zb) / 2 - self.zmin, band["name"], th * 0.9, "right")
        for a in angle_breaks(self.design):
            self.text(x0 + a * self.sx, y0 - 2 * th, f"{a:g}\u00b0", 0.9 * th, "center")
        self.text(x0 + 180 * self.sx, oy + 1.5 * th,
                  "UNROLLED WALL MAP  (angle across, height up; colour = hot-face grade)", 1.3 * th, "center")

    # ================================================================ LEGEND
    def legend_size(self):
        th = self.th
        return 46 * th, (len(self.colors) + 9) * 2.4 * th

    def draw_legend(self, ox, oy):
        th = self.th
        w, h = self.legend_size()
        y = oy + h - 2 * th
        self.text(ox, y, "QUALITY LEGEND", 1.4 * th)
        for g, c in self.colors.items():
            y -= 2.4 * th
            self.fill([(ox, y - 0.4 * th, 0), (ox + 2.4 * th, y - 0.4 * th, 0), (ox + 2.4 * th, y + 1.4 * th, 0),
                       (ox, y + 1.4 * th, 0)], c, self.grade_layer(g))
            self.text(ox + 3.4 * th, y, g, th)
        y -= 3.6 * th
        for line in (f"JOB: {self.vessel.get('job', '')}",
                     f"UNITS: {self.vessel.get('units', '')}",
                     "LAYERS (shell -> hot face): " + " | ".join(self.layers),
                     f"SHELL PLATE: {self.shell_t:g}" if self.shell_t else "SHELL PLATE: line only",
                     f"GENERATED: {datetime.date.today().isoformat()}"):
            self.text(ox, y, line, 0.85 * th)
            y -= 2 * th

    # ================================================================ BRICK JOINTS (individual bricks)
    def draw_brick_joints(self, courses, rings=None):
        """Draws every brick boundary of the working lining on the GA views already drawn, from the course
        schedule and the bottom ring layout: course joints (vertical sections, unrolled map), radial joints
        between the bricks of a course (plan sections) and the bottom rings. Black joint lines on the
        coloured fill, so each brick can be identified. Adds lines only; nothing else is changed."""
        lay = self.layer("BRICK_JOINTS", 7)
        attrs = {"layer": lay, "color": 7, "lineweight": 13}            # black on the colour fill (as on the customer GA)
        L, eps = self.L, 1e-6
        bandix = {b["name"]: i for i, b in enumerate(self.bands)}

        def seg(p, q):
            self.msp.add_line(p, q, dxfattribs=attrs)

        def sec_of(r):
            try:
                a0, a1 = [float(x) for x in str(r["sector"]).split("-")]
                return a0, a1
            except Exception:
                return None

        def in_sec(a, ab):
            a = a % 360.0
            return ab[0] - eps <= a < ab[1] - eps or (ab[1] >= 360 - eps and abs(a) < eps and ab[0] <= eps)

        def band_range(r):
            bi = bandix.get(r.get("band"))
            return self.band_z(bi) if bi is not None else (self.zmin, self.zmax)

        rows = [r for r in (courses or []) if sec_of(r) and r.get("z1") is not None]
        rr = (rings or {}).get("rings") or []
        rb = bandix.get((rings or {}).get("band"))
        ring_r = ([float(rings["key"]["r"])] if (rings or {}).get("key") else []) + [float(x["r_out"]) for x in rr[:-1]]

        def z_at_r(run, r):                           # height of a run at plan radius r (None if outside)
            pts = sample(run)
            for (x0, z0), (x1, z1) in zip(pts, pts[1:]):
                if (x0 - r) * (x1 - r) <= 0 and abs(x1 - x0) > 1e-9:
                    return z0 + (r - x0) * (z1 - z0) / (x1 - x0)
            return None

        for view, ox, oy, arg, vix in self._frames:
            n0 = len(self.msp)
            try:
                if view == "vertical":
                    th = self.th
                    axis_x = ox + 44 * th + self.rout
                    Y = lambda z: oy + 6 * th + (z - self.zmin)
                    for side, ang in ((1, arg), (-1, arg + 180)):
                        paths, _ = self.boundary_paths(ang)
                        cold, hot = paths[L - 1].all(), paths[L].all()
                        for r in rows:                # horizontal course joints
                            za, zb = band_range(r)
                            z = float(r["z1"])
                            if not (za + eps < z < zb - eps) or not in_sec(ang, sec_of(r)):
                                continue
                            rc, rh = radius_at_z(cold, z), radius_at_z(hot, z)
                            if rc is None or rh is None or rc - rh < 1.0:
                                continue
                            seg((axis_x + side * rc, Y(z)), (axis_x + side * rh, Y(z)))
                        if rb is not None and ring_r:  # bottom rings: joint square to the bottom
                            i0, i1 = self.band_idx[rb]
                            crun, hrun = paths[L - 1].run(i0, i1), paths[L].run(i0, i1)
                            s0 = self.segs[i0]
                            region = sample(crun + reverse_run(hrun))
                            for rad in ring_r:
                                zc = z_at_r(crun, rad)
                                if zc is None:
                                    continue
                                if s0.shape == "arc" and s0.C is not None:
                                    dx, dz = s0.C[0] - rad, s0.C[1] - zc
                                    n = math.hypot(dx, dz) or 1.0
                                    dx, dz = dx / n, dz / n
                                else:
                                    dx, dz = 0.0, 1.0
                                t = sector_at(self.bands[rb], ang % 360)["layers"][L - 1]["thickness"]
                                xh, zh = rad + dx * t, zc + dz * t
                                xh, zh = _clip_to_region((rad, zc), (xh, zh), region)
                                seg((axis_x + side * rad, Y(zc)), (axis_x + side * xh, Y(zh)))
                elif view == "plan":
                    z = arg
                    cx, cy = self._plan_frame(ox, oy)
                    sg = next((s_ for s_ in self.segs if s_.p0[1] - 1e-9 <= z <= s_.p1[1] + 1e-9), None)
                    if sg is None:
                        continue
                    bi = self.seg_band[sg.name]
                    band = self.bands[bi]
                    if bi == rb:                      # bottom: ring boundaries + bricks of each ring
                        paths, _ = self.boundary_paths(0)
                        r_lim = self._rad(paths[L - 1], z)
                        for x in rr:
                            ri, ro = float(x["r_in"]), min(float(x["r_out"]), r_lim)
                            if ro - ri < 1.0:
                                continue
                            if float(x["r_out"]) <= r_lim:
                                self.msp.add_circle((cx, cy), float(x["r_out"]), dxfattribs=attrs)
                            n = sum((x.get("marks") or {}).values()) + int(x.get("nCut") or 0)
                            for k in range(n):
                                a = 360.0 * k / max(n, 1)
                                seg(self.polar(cx, cy, ri, a), self.polar(cx, cy, ro, a))
                        if (rings or {}).get("key") and float(rings["key"]["r"]) <= r_lim:
                            self.msp.add_circle((cx, cy), float(rings["key"]["r"]), dxfattribs=attrs)
                        continue
                    for sec in band["sectors"]:
                        ab = (float(sec["from"]), float(sec["to"]))
                        row = next((r for r in rows if r.get("band") == band["name"] and sec_of(r) == ab
                                    and float(r["z0"]) - eps <= z < float(r["z1"]) - eps), None)
                        if not row:
                            continue
                        n = int(row.get("nA") or 0) + int(row.get("nB") or 0) + int(row.get("nCut") or 0)
                        if n < 2:
                            continue
                        paths, _ = self.boundary_paths((ab[0] + ab[1]) / 2)
                        rc, rh = self._rad(paths[L - 1], z), self._rad(paths[L], z)
                        if rc - rh < 1.0:
                            continue
                        for k in range(1, n):
                            a = ab[0] + (ab[1] - ab[0]) * k / n
                            seg(self.polar(cx, cy, rc, a), self.polar(cx, cy, rh, a))
                elif view == "unrolled":
                    th = self.th
                    self.unrolled_size()
                    x0, y0 = ox + 12 * th, oy + 8 * th
                    for r in rows:
                        za, zb = band_range(r)
                        z = float(r["z1"])
                        if not (za + eps < z < zb - eps):
                            continue
                        a0, a1 = sec_of(r)
                        seg((x0 + a0 * self.sx, y0 + z - self.zmin), (x0 + a1 * self.sx, y0 + z - self.zmin))
            except Exception:                         # joints are a visual aid - never stop the drawing
                pass
            if vix is not None and vix < len(self.views):
                self.views[vix]["handles"].update(e.dxf.handle for e in list(self.msp)[n0:])

    # ================================================================ VIEWS (one page each)
    def _view(self, title, fn, *args):
        """Draws one view and records its entities, so it can be put on its own page / layout."""
        n0 = len(self.msp)
        self._cur_view = len(self.views)
        self.views.append({"title": title, "handles": set()})
        fn(*args)
        self.views[-1]["handles"] = {e.dxf.handle for e in list(self.msp)[n0:]}
        self._cur_view = None

    # ================================================================ INSIDE VOLUME / LEVELS
    def hot_volume_at(self, angle=0.0, step=10.0):
        """[(z, inside volume below z in m3)] along the hot face (axisymmetric at `angle`)."""
        paths, _ = self.boundary_paths(angle)
        hot = paths[self.L].all()
        _, _, z0, z1 = bounds(hot)
        out, v, z, r_prev = [(z0, 0.0)], 0.0, z0, radius_at_z(hot, z0) or 0.0
        while z < z1 - 1e-9:
            zn = min(z + step, z1)
            r = radius_at_z(hot, zn) or 0.0
            v += math.pi * (r * r + r * r_prev + r_prev * r_prev) / 3 * (zn - z) / 1e9
            out.append((zn, v))
            z, r_prev = zn, r
        return out

    def level_for_volume(self, vol_m3, table=None):
        table = table or self.hot_volume_at()
        for (za, va), (zb, vb) in zip(table, table[1:]):
            if va <= vol_m3 <= vb and vb > va:
                return za + (vol_m3 - va) / (vb - va) * (zb - za)
        return None

    def draw_levels(self, axis_x, Y):
        """Metal (and slag) level lines + total inside / specific volume, as on the customer's GA."""
        th = self.th
        try:
            table = self.hot_volume_at()
        except Exception:
            return
        v_tot = table[-1][1]
        heat = float(self.design.get("heat_size") or 0)
        lay = self.layer("LEVELS", 1)
        paths, _ = self.boundary_paths(0)
        hot = paths[self.L].all()
        lines = [f"TOTAL INSIDE VOL. = {v_tot:.2f} m³"]
        if heat > 0:
            lines.append(f"SPECIFIC VOL. = {v_tot / heat:.3f} m³/t  ({heat:g} t heat)")
        zc = (self.zmin + self.zmax) / 2
        for k, t in enumerate(lines):
            self.text(axis_x, Y(zc) - k * 1.6 * th, t, 1.0 * th, "center", "LEVELS")
        rho = float(self.steel_density or 0)
        if heat > 0 and rho > 0:
            vm = heat / rho
            zm = self.level_for_volume(vm, table)
            if zm is not None:
                r = radius_at_z(hot, zm) or 0
                self.msp.add_line((axis_x - r, Y(zm)), (axis_x + r, Y(zm)), dxfattribs={"layer": lay, "lineweight": 50})
                self.text(axis_x, Y(zm) + 0.6 * th, f"{heat:g} T METAL LEVEL  (EL {zm:.0f}, ρ {rho:g} t/m³)",
                          0.9 * th, "bcenter", "LEVELS")
                sv = float(self.slag_volume or 0)
                zs = self.level_for_volume(vm + sv, table) if sv > 0 else None
                if zs is not None:
                    r = radius_at_z(hot, zs) or 0
                    self.msp.add_line((axis_x - r, Y(zs)), (axis_x + r, Y(zs)), dxfattribs={"layer": lay, "linetype": "DASHED"})
                    self.text(axis_x, Y(zs) + 0.6 * th, f"SLAG LEVEL  (EL {zs:.0f}, {sv:g} m³ slag)", 0.8 * th,
                              "bcenter", "LEVELS")

    def draw_section_marks(self, axis_x, Y):
        """Section cut marks (A-A, B-B ...) at the plan-section heights, both sides of the vertical section."""
        th = self.th
        lay = self.layer("SECTION_MARKS", 7)
        prev_z, k_off = None, 0
        for z, letter in sorted(self.plan_letters.items()):
            if not (self.zmin < z < self.zmax):
                continue
            # marks closer than 2.5 text heights are stepped outwards so the letters stay readable
            k_off = k_off + 1 if (prev_z is not None and Y(z) - Y(prev_z) < 2.5 * th) else 0
            prev_z = z
            off = -k_off * 2.6 * th
            for side in (1, -1):
                x0, x1 = axis_x + side * (self.rout + 0.6 * th + off), axis_x + side * (self.rout + 2.4 * th + off)
                y = Y(z)
                self.msp.add_lwpolyline([(x0, y), (x1, y)], dxfattribs={"layer": lay, "const_width": 0.22 * th})
                xm = (x0 + x1) / 2
                self.msp.add_lwpolyline([(xm, y), (xm, y - 1.4 * th)], dxfattribs={"layer": lay, "const_width": 0.12 * th})
                self.msp.add_solid([(xm - 0.45 * th, y - 1.0 * th), (xm + 0.45 * th, y - 1.0 * th), (xm, y - 2.0 * th)],
                                   dxfattribs={"layer": lay})
                self.text(xm, y + 0.5 * th, letter, 1.5 * th, "bcenter", "SECTION_MARKS")

    # ================================================================ DESIGN INPUT TABLE
    SRC_TEXT = {"drw": "From the drawing", "sample": "Sample drawing", "miss": "NOT on the drawing",
                "user": "Checked by designer"}

    def inputs_size(self):
        th = self.th
        return 120 * th, (len(self.inputs) + 6) * 1.7 * th

    def draw_inputs(self, ox, oy):
        """Every step-2 value used for this drawing and where it came from. Values the designer changed
        (not the value read from the uploaded drawing) are marked EDITED in red."""
        th = self.th
        w, h = self.inputs_size()
        y = oy + h - 2 * th
        self.text(ox, y, "DESIGN INPUT VALUES (step 2 - drawing extraction, as confirmed)", 1.4 * th)
        y -= 1.8 * th
        self.layer("INPUT_EDITED", 1)                       # red layer, created before its first use
        self.text(ox, y, "Red = EDITED by the designer: the drawing was generated with this value, not with the value "
                         "read from the uploaded drawing.", 0.8 * th, layer="INPUT_EDITED")
        cols = [("Group", 14), ("Value", 40), ("Used", 12), ("Unit", 5), ("On drawing", 22), ("Source", 24)]
        y -= 2.2 * th
        x = ox
        for name, cw in cols:
            self.text(x, y, name, 0.9 * th, layer="INPUT_TABLE")
            x += cw * th
        self.line((ox, y - 0.7 * th), (ox + sum(c for _, c in cols) * th, y - 0.7 * th), self.layer("INPUT_TABLE", 7))
        for r in self.inputs:
            y -= 1.7 * th
            ed = bool(r.get("edited"))
            src = "EDITED by designer" if ed else self.SRC_TEXT.get(r.get("source"), str(r.get("source_text") or ""))
            val = r.get("value")
            vals = [r.get("group", ""), r.get("label", ""), "" if val in (None, "") else (f"{val:g}" if isinstance(val, (int, float)) else str(val)),
                    r.get("unit", ""), r.get("drawing", ""), src]
            x = ox
            for (name, cw), v in zip(cols, vals):
                self.text(x, y, str(v)[:int(cw * 1.5)], 0.8 * th, layer="INPUT_EDITED" if ed else "INPUT_TABLE")
                x += cw * th

    # ================================================================ SHEET LAYOUT
    def build(self):
        v = self.design.get("views", {})
        cuts = v.get("cuts") or [0]
        gap = 6 * self.th
        y_top = 0.0
        # row 1: vertical sections
        x, row_h = 0.0, 0.0
        heights0 = v.get("plan_heights") or [sum(self.band_z(b)) / 2 for b in range(len(self.bands))]
        letters = [ch for ch in "ABCDEFGHJKLMNPQRSTUVWXYZ"]
        self.plan_letters = {z: letters[k % len(letters)] for k, z in enumerate(sorted(heights0))}
        for c in cuts:
            w, h = self.vertical_size()
            self._view(f"VERTICAL SECTION {c % 360:g}-{(c + 180) % 360:g} DEG", self.draw_vertical, x, y_top - h, c)
            x += w + gap
            row_h = max(row_h, h)
        y_top -= row_h + gap
        # row 2: plan sections + top view
        heights = v.get("plan_heights")
        if not heights:
            heights = [sum(self.band_z(b)) / 2 for b in range(len(self.bands))]
        w, h = self.plan_size()
        x = 0.0
        items = [("plan", z) for z in heights] + ([("top", None)] if v.get("top_view", True) else [])
        per_row = 4
        for k, (kind, z) in enumerate(items):
            if k and k % per_row == 0:
                y_top -= h + gap
                x = 0.0
            if kind == "plan":
                lt = self.plan_letters.get(z, "")
                self._view(f"SECTION {lt}-{lt} (PLAN AT Z {z:g})" if lt else f"PLAN SECTION Z {z:g}",
                           self.draw_plan, x, y_top - h, z)
            else:
                self._view("TOP VIEW (HOT FACE)", self.draw_top, x, y_top - h)
            x += w + gap
        y_top -= h + gap
        # row 3: unrolled map + legend
        x = 0.0
        if v.get("unrolled", True):
            w, h = self.unrolled_size()
            self._view("UNROLLED WALL MAP", self.draw_unrolled, x, y_top - h)
            x += w + gap
            lh = self.legend_size()[1]
            self._view("QUALITY LEGEND AND NOTES", self.draw_legend, x, y_top - lh)
            y_top -= max(h, lh) + gap
        else:
            lh = self.legend_size()[1]
            self._view("QUALITY LEGEND AND NOTES", self.draw_legend, x, y_top - lh)
            y_top -= lh + gap
        if self.inputs:
            iw, ih = self.inputs_size()
            self._view("DESIGN INPUT VALUES", self.draw_inputs, 0.0, y_top - ih)
        return self.doc

    def save(self, folder, name="lining_ga"):
        self.build()
        dxf = folder / f"{name}.dxf"
        self.doc.saveas(dxf)
        outs = [dxf]
        try:
            from ezdxf.addons.drawing import matplotlib as mpl
            ext = self.msp  # noqa
            for suffix in ("png", "pdf"):
                p = folder / f"{name}.{suffix}"
                mpl.qsave(self.msp, str(p), bg="#FFFFFF", dpi=170, size_inches=(36, 24))
                outs.append(p)
        except Exception as e:
            print(f"  (preview skipped: {e})")
        return outs