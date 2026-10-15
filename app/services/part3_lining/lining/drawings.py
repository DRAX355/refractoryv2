"""
Extra drawing sheets on top of the lining GA (sheet.py):

  LiningSheet  - the lining GA (all views of sheet.Sheet) + nozzles: taphole / radial nozzles and
                 bottom stirring elements in the vertical sections, plan sections, top view and
                 unrolled map.
  BrickSheet   - every shape in the brick library, dimensioned, with the quantity used in this design.
  LayerSheet   - bottom ring layout (every brick drawn), course (layer) section with course numbers
                 and brick pairs, one ring plan per wall band, and the full course schedule table.

All values come from the data (vessel / design / nozzles / brick library / computed schedule).
"""
import datetime
import math

import ezdxf
from ezdxf.enums import TextEntityAlignment as TA

from .geometry import reverse_run, sample, radius_at_z
from .sheet import Sheet, clean, decimals
from .bricks import band_kind

ALIGN = {"left": TA.LEFT, "center": TA.MIDDLE_CENTER, "right": TA.RIGHT,
         "mleft": TA.MIDDLE_LEFT, "mright": TA.MIDDLE_RIGHT, "bcenter": TA.BOTTOM_CENTER}
# distinct colours; red (1, cut pieces) and magenta (6, nozzles) are reserved
MARK_COLORS = [3, 5, 30, 4, 2, 40, 150, 94, 52, 190, 34, 70, 110, 172, 132, 62, 142, 22]
FAMILY_TEXT = {"standard_trapezoidal": "wall brick", "steel_cladded": "steel-clad mouth brick",
               "bottom_dish_wedge": "bottom wedge brick", "center_block": "centre key block",
               "safety_lining_std": "permanent lining brick", "safety_lining_rect": "safety lining brick",
               "purging_plug": "stirring element", "seating_block": "seating block"}


def assign_mark_colors(courses, rings):
    """One fixed AutoCAD colour per brick mark, shared by every sheet and the UI."""
    marks = []
    if rings and rings.get("key"):
        marks.append(rings["key"]["mark"])
    for r in (rings or {}).get("rings", []):
        for m in r["marks"]:
            if m not in marks:
                marks.append(m)
    for c in courses:
        for m in (c.get("A"), c.get("B")):
            if m and m not in marks:
                marks.append(m)
    return {m: MARK_COLORS[i % len(MARK_COLORS)] for i, m in enumerate(marks)}


def aci_hex(aci):
    from ezdxf.colors import DXF_DEFAULT_COLORS, int2rgb
    r, g, b = int2rgb(DXF_DEFAULT_COLORS[int(aci)])
    return f"#{r:02x}{g:02x}{b:02x}"


def mark_info(lib, courses, rings, qty, mcol=None):
    """mark -> description used in the colour keys: item number (the number in the circle on every
    brick), name (what the brick is and where it is laid), family, size, grades, qty."""
    from .bom import size_text
    by = {b["mark"]: b for b in lib.get("bricks", [])}
    role, grades, where, ring_no = {}, {}, {}, {}
    for c in courses:
        if c.get("A"):
            def ratio(m):
                d = by.get(m, {}).get("dimensions_mm", {})
                try:
                    return float(d["width_top"]) / float(d["width_bottom"])
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    return None
            ra, rb = ratio(c["A"]), ratio(c["B"])
            a_gentle = ra is None or rb is None or ra <= rb
            role[c["A"]] = "A brick - " + ("gentle taper" if a_gentle else "steep taper")
            role[c["B"]] = "B brick - " + ("steep taper" if a_gentle else "gentle taper")
            for m in (c["A"], c["B"]):
                grades.setdefault(m, set()).add(c["grade"])
                w = f"{c['band']}" + (f" / {c['zone']}" if c.get("zone") else "")
                if w not in where.setdefault(m, []):
                    where[m].append(w)
    if rings:
        for r in rings.get("rings", []):
            for m in r["marks"]:
                grades.setdefault(m, set()).add(rings["grade"])
                ring_no.setdefault(m, []).append(r["ring"])
        if rings.get("key"):
            grades.setdefault(rings["key"]["mark"], set()).add(rings["grade"])
            where[rings["key"]["mark"]] = [f"{rings['band']} / centre"]
    for m, rs in ring_no.items():
        where[m] = [f"{rings['band']} / ring {min(rs)}" + (f"-{max(rs)}" if max(rs) != min(rs) else "")]
    order = list(mcol or {}) + sorted(set(list(role) + list(grades)) - set(mcol or {}))
    out = {}
    for i, m in enumerate(order, 1):
        b = by.get(m, {})
        fam = FAMILY_TEXT.get(b.get("shape_family"), b.get("shape_family", ""))
        what = f"{fam}" + (f", {role[m]}" if m in role else "")
        wh = ", ".join(where.get(m, []))
        out[m] = {"no": i, "name": f"{m} - {what}" + (f" - {wh}" if wh else ""), "where": wh,
                  "what": what, "size": size_text(b) if b else "",
                  "grades": ", ".join(sorted(grades.get(m, []))), "qty": qty.get(m, 0), "cut": qty.get(m + " (cut)", 0)}
    return out
NOZZLE_ACI = 6
CUT_ACI = 1


def z_at_x(run, x):
    """Height where a run (going outward from the axis) reaches radius x (first crossing)."""
    pts = sample(run, 0.5)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if (x0 - x) * (x1 - x) <= 0 and abs(x1 - x0) > 1e-9:
            return y0 + (x - x0) * (y1 - y0) / (x1 - x0)
    return None


def save_previews(msp, folder, name, size=(36, 24), dpi=150, skip_prefix=("TABLE",)):
    """PNG + PDF previews of a modelspace. Fast vector path via PyMuPDF; matplotlib as fallback.
    Layers starting with `skip_prefix` (the long schedule table) stay in the DXF but are left out of
    the previews - the same table is delivered as CSV and shown in the UI."""
    outs = []
    keep = lambda e: not str(e.dxf.layer).upper().startswith(tuple(skip_prefix))
    try:
        from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, pymupdf
        backend = pymupdf.PyMuPdfBackend()
        cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE,
                                   color_policy=config.ColorPolicy.COLOR)
        Frontend(RenderContext(msp.doc), backend, config=cfg).draw_layout(msp, filter_func=keep)
        page = layout.Page(size[0] * 25.4, size[1] * 25.4, layout.Units.mm, margins=layout.Margins.all(8))
        settings = layout.Settings(fit_page=True)
        pdf = folder / f"{name}.pdf"
        pdf.write_bytes(backend.get_pdf_bytes(page, settings=settings))
        png = folder / f"{name}.png"
        png.write_bytes(backend.get_pixmap_bytes(page, fmt="png", dpi=dpi, settings=settings))
        return [png, pdf]
    except Exception as e:                      # older ezdxf / no PyMuPDF -> matplotlib
        print(f"  (PyMuPDF preview unavailable for {name}: {e}; using matplotlib)")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        fig = plt.figure(figsize=size)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_facecolor("#FFFFFF")
        Frontend(RenderContext(msp.doc), MatplotlibBackend(ax)).draw_layout(msp, filter_func=keep, finalize=True)
        for suffix in ("png", "pdf"):
            p = folder / f"{name}.{suffix}"
            fig.savefig(str(p), dpi=dpi, facecolor="#FFFFFF")
            outs.append(p)
        plt.close(fig)
    except Exception as e:                      # previews are a convenience, the DXF is the deliverable
        print(f"  (preview skipped for {name}: {e})")
    return outs


# ============================================================================ GA + NOZZLES
class LiningSheet(Sheet):
    def __init__(self, vessel, design, nozzles=()):
        super().__init__(vessel, design)
        self.nozzles = [n for n in (nozzles or []) if n]

    # ------------------------------------------------------------ helpers
    def _outer_r(self, z):
        paths, outer = self.boundary_paths(0)
        return self._rad(outer, z) if outer else self._rad(paths[0], z)

    def _radial_axis(self, n, angle):
        """Points along a radial nozzle axis from outside the shell to the hot face (r, z) at `angle`."""
        z0 = float(n["z"])
        inc = math.radians(float(n.get("inclination") or 0))
        paths, _ = self.boundary_paths(angle)
        r_out = self._outer_r(z0)
        start = (r_out + 2.5 * self.th, z0 + 2.5 * self.th * math.tan(inc))
        d, end = 0.0, None
        step = 5.0
        while d < r_out:
            r, z = r_out - d, z0 - d * math.tan(inc)
            rh = radius_at_z(paths[self.L].all(), z)
            if rh is not None and r <= rh:
                end = (r, z)
                break
            d += step
        if end is None:
            end = (max(r_out - d, 0), z0 - d * math.tan(inc))
        return start, (r_out, z0), end

    def _radial_nozzles(self):
        return [n for n in self.nozzles if n.get("type") in ("taphole", "radial", "other")
                and n.get("z") not in (None, "") and n.get("angle") not in (None, "")]

    def _stirring(self):
        return [n for n in self.nozzles if n.get("type") == "stirring" and n.get("count") and n.get("pcd")]

    def _bottom_band(self):
        for bi in range(len(self.bands)):
            if band_kind(self, bi) == "bottom":
                return bi
        return None

    # ------------------------------------------------------------ vertical section
    def draw_vertical(self, ox, oy, cut):
        super().draw_vertical(ox, oy, cut)
        th = self.th
        axis_x = ox + 44 * th + self.rout
        Y = lambda z: oy + 6 * th + (z - self.zmin)
        lay = self.layer("NOZZLES", NOZZLE_ACI)
        cen = self.layer("CENTER", 1)
        for side, angle in ((1, cut), (-1, cut + 180)):
            a = angle % 360
            for n in self._radial_nozzles():
                if abs(((float(n["angle"]) - a + 180) % 360) - 180) > 1.0:
                    continue
                p_s, p_o, p_e = self._radial_axis(n, a)
                P = lambda p: (axis_x + side * p[0], Y(p[1]))
                self.line(P(p_s), P(p_e), cen)
                dia = float(n.get("diameter") or 0)
                if dia > 0:
                    inc = math.radians(float(n.get("inclination") or 0))
                    nx, nz = math.sin(inc), math.cos(inc)        # normal to the axis in (r, z)
                    for s in (-1, 1):
                        q1 = (p_o[0] + s * nx * dia / 2, p_o[1] + s * nz * dia / 2)
                        q2 = (p_e[0] + s * nx * dia / 2, p_e[1] + s * nz * dia / 2)
                        self.line(P(q1), P(q2), lay)
                    self.line(P((p_e[0] - nx * dia / 2, p_e[1] - nz * dia / 2)),
                              P((p_e[0] + nx * dia / 2, p_e[1] + nz * dia / 2)), lay)
                t = P((p_s[0] + 0.5 * th, p_s[1] + 2.4 * th))
                txt = f"{n.get('label') or n['type'].upper()}  " + \
                      (f"Ø{dia:g}  " if dia else "") + f"{float(n.get('inclination') or 0):g}°  Z{float(n['z']):g}"
                self.text(t[0], t[1], txt, 0.8 * th, "mleft" if side == 1 else "mright", "NOZZLES")
            bi = self._bottom_band()
            for n in self._stirring() if bi is not None else []:
                cnt, rp = int(n["count"]), float(n["pcd"]) / 2
                pitch = 360.0 / cnt
                angs = [(float(n.get("start_angle") or 0) + k * pitch) % 360 for k in range(cnt)]
                dmin = min(abs(((e - a + 180) % 360) - 180) for e in angs)
                if dmin > pitch / 2 + 1e-6:
                    continue
                in_plane = dmin <= 1.0
                w = float(n.get("size_a") or 150)
                paths, _ = self.boundary_paths(a)
                i0, i1 = self.band_idx[bi]
                cold, hot = paths[self.L - 1].run(i0, i1), paths[self.L].run(i0, i1)
                pts = []
                for x, run in ((rp - w / 2, cold), (rp + w / 2, cold), (rp + w / 2, hot), (rp - w / 2, hot)):
                    z = z_at_x(run, x)
                    if z is None:
                        pts = []
                        break
                    pts.append((axis_x + side * x, Y(z), 0))
                if pts:
                    if in_plane:
                        self.fill(pts, NOZZLE_ACI, lay)
                    self.poly(pts, lay, close=True)
                    tx = axis_x + side * rp
                    self.text(tx, pts[2][1] + 0.8 * th, "STIR" + ("" if in_plane else " (proj.)"),
                              0.6 * th, "bcenter", "NOZZLES")

    # ------------------------------------------------------------ plan section
    def draw_plan(self, ox, oy, z):
        super().draw_plan(ox, oy, z)
        th = self.th
        cx, cy = self._plan_frame(ox, oy)
        lay = self.layer("NOZZLES", NOZZLE_ACI)
        for n in self._radial_nozzles():
            dia = float(n.get("diameter") or 0)
            if abs(z - float(n["z"])) > max(dia / 2, 0.5 * float(self.design.get("course_height", 100))):
                continue
            a = float(n["angle"])
            p_s, p_o, p_e = self._radial_axis(n, a)
            self.line(self.polar(cx, cy, p_s[0], a), self.polar(cx, cy, p_e[0], a), self.layer("CENTER", 1))
            if dia > 0:
                for s in (-1, 1):
                    da = math.degrees(s * dia / 2 / max(p_e[0], 1))
                    db = math.degrees(s * dia / 2 / max(p_o[0], 1))
                    self.line(self.polar(cx, cy, p_o[0], a + db), self.polar(cx, cy, p_e[0], a + da), lay)
            t = self.polar(cx, cy, self.rout + 4.5 * th, a)
            self.text(t[0], t[1], n.get("label") or n["type"].upper(), 0.8 * th, "center", "NOZZLES")
        bi = self._bottom_band()
        if bi is not None:
            za, zb = self.band_z(bi)
            if za - 1e-6 <= z <= zb + 1e-6:
                self._stirring_marks(cx, cy)

    def _stirring_marks(self, cx, cy):
        lay = self.layer("NOZZLES", NOZZLE_ACI)
        for n in self._stirring():
            cnt, rp = int(n["count"]), float(n["pcd"]) / 2
            a_s = float(n.get("size_a") or 150) / 2
            b_s = float(n.get("size_b") or n.get("size_a") or 150) / 2
            for k in range(cnt):
                ang = float(n.get("start_angle") or 0) + k * 360.0 / cnt
                c = self.polar(cx, cy, rp, ang)
                t = math.radians(ang + self.zero)
                u, v = (math.cos(t), math.sin(t)), (-math.sin(t), math.cos(t))
                pts = [(c[0] + su * a_s * u[0] + sv * b_s * v[0], c[1] + su * a_s * u[1] + sv * b_s * v[1], 0)
                       for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
                self.fill(pts, NOZZLE_ACI, lay)
                self.poly(pts, lay, close=True)
            self.arc_line(cx, cy, rp, 0, 360, self.layer("CENTER", 1))

    # ------------------------------------------------------------ top view / unrolled
    def draw_top(self, ox, oy):
        super().draw_top(ox, oy)
        cx, cy = self._plan_frame(ox, oy)
        self._stirring_marks(cx, cy)
        for n in self._radial_nozzles():
            a = float(n["angle"])
            _, _, p_e = self._radial_axis(n, a)
            c = self.polar(cx, cy, p_e[0], a)
            self.msp.add_circle(c, max(float(n.get("diameter") or 0) / 2, 0.6 * self.th),
                                dxfattribs={"layer": self.layer("NOZZLES", NOZZLE_ACI)})
            t = self.polar(cx, cy, p_e[0] - 2.5 * self.th, a)
            self.text(t[0], t[1], n.get("label") or n["type"].upper(), 0.7 * self.th, "center", "NOZZLES")

    def draw_unrolled(self, ox, oy):
        super().draw_unrolled(ox, oy)
        th = self.th
        x0, y0 = ox + 12 * th, oy + 8 * th
        for n in self._radial_nozzles():
            x, y = x0 + (float(n["angle"]) % 360) * self.sx, y0 + float(n["z"]) - self.zmin
            r = max(float(n.get("diameter") or 0) / 2, 0.6 * th)
            self.msp.add_circle((x, y), r, dxfattribs={"layer": self.layer("NOZZLES", NOZZLE_ACI)})
            self.text(x, y + r + 0.6 * th, n.get("label") or n["type"].upper(), 0.7 * th, "bcenter", "NOZZLES")

    def draw_legend(self, ox, oy):
        super().draw_legend(ox, oy)
        if not self.nozzles:
            return
        th = self.th
        y = oy - 2 * th
        self.text(ox, y, "NOZZLES", 1.2 * th, layer="NOZZLES")
        for n in self.nozzles:
            y -= 1.8 * th
            if n.get("type") == "stirring":
                s = (f"{n.get('label') or 'Stirring'}: {n.get('count')} x {n.get('size_a') or ''}x{n.get('size_b') or ''} "
                     f"on PCD {n.get('pcd')} from {n.get('start_angle') or 0}°, height {n.get('height') or ''}")
            else:
                s = (f"{n.get('label') or n.get('type')}: Z {n.get('z')}  at {n.get('angle')}°  "
                     f"incl {n.get('inclination') or 0}°  Ø {n.get('diameter') or 'n/a'}")
            self.text(ox, y, s, 0.8 * th, layer="NOZZLES")


# ============================================================================ common doc helper
class Doc:
    def __init__(self, th):
        self.th = th
        self.doc = ezdxf.new("R2018", setup=True)
        self.msp = self.doc.modelspace()
        self._layers = set()

    def layer(self, name, color=7):
        if name not in self._layers:
            self.doc.layers.add(name, color=color)
            self._layers.add(name)
        return name

    def text(self, x, y, s, h=None, align="left", layer="TEXT"):
        self.msp.add_text(str(s), height=h or self.th, dxfattribs={"layer": self.layer(layer, 7)}
                          ).set_placement((x, y), align=ALIGN[align])

    def rtext(self, x, y, s, h, rot, layer="LABELS"):
        self.msp.add_text(str(s), height=h, rotation=rot, dxfattribs={"layer": self.layer(layer, 7)}
                          ).set_placement((x, y), align=TA.MIDDLE_CENTER)

    def line(self, p, q, layer="OUTLINE"):
        self.msp.add_line(p, q, dxfattribs={"layer": self.layer(layer, 7)})

    def poly(self, pts, layer="OUTLINE", close=True):
        self.msp.add_lwpolyline([(p[0], p[1]) for p in pts], close=close, dxfattribs={"layer": self.layer(layer, 7)})

    def fill(self, pts, aci, layer):
        h = self.msp.add_hatch(color=aci, dxfattribs={"layer": self.layer(layer, aci)})
        h.set_solid_fill(color=aci)
        h.paths.add_polyline_path([(p[0], p[1]) for p in pts], is_closed=True)

    def balloon(self, x, y, r, no, aci, layer="BALLOONS"):
        """Item circle: coloured ring (the brick's colour), white centre, item number."""
        if r <= 0:
            return
        ring = [(x + r * math.cos(a), y + r * math.sin(a)) for a in [k * math.pi / 18 for k in range(36)]]
        core = [(x + 0.74 * r * math.cos(a), y + 0.74 * r * math.sin(a)) for a in [k * math.pi / 18 for k in range(36)]]
        self.fill(ring, aci, layer + "_RING")
        self.fill(core, 255, layer + "_CORE")
        self.msp.add_circle((x, y), r, dxfattribs={"layer": self.layer(layer, 7)})
        t = str(no)
        self.text(x, y, t, min(0.95 * r, 1.5 * r / max(len(t), 1)), "center", layer)

    def dim(self, p1, p2, base, angle, v=None, h=None):
        th = h or self.th
        v = v if v is not None else math.dist(p1, p2)
        ov = {"dimtxt": th * 0.9, "dimasz": th * 0.7, "dimexe": th * 0.4, "dimexo": th * 0.3,
              "dimgap": th * 0.25, "dimdec": decimals(v), "dimlfac": 1}
        self.msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, override=ov,
                                dxfattribs={"layer": self.layer("DIMS", 8)}).render()

    def title(self, x, y, lines, w):
        self.layer("TITLE", 7)
        th = self.th
        h = (len(lines) + 1) * 1.8 * th
        self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], "TITLE")
        for k, s in enumerate(lines):
            self.text(x + th, y + h - (k + 1.3) * 1.8 * th, s, (1.2 if k == 0 else 0.8) * th, layer="TITLE")


# ============================================================================ BRICK SHEET
class BrickSheet(Doc):
    """All shapes of the brick library, dimensioned (mm)."""
    CELL_W, CELL_H, COLS = 1700.0, 1450.0, 6

    def __init__(self, lib, qty=None, job="", mcol=None, info=None):
        super().__init__(28.0)
        self.lib, self.qty, self.job, self.mcol, self.info = lib, qty or {}, job, mcol or {}, info or {}

    def build(self):
        allb = self.lib.get("bricks", [])
        used = list(self.mcol) + [m for m, q in self.qty.items() if q and m not in self.mcol]   # used shapes first
        bricks = sorted(allb, key=lambda b: (used.index(b["mark"]) if b["mark"] in used else len(used)))
        th = self.th
        for k, b in enumerate(bricks):
            col, row = k % self.COLS, k // self.COLS
            ox, oy = col * self.CELL_W, -(row + 1) * self.CELL_H
            self.poly([(ox, oy), (ox + self.CELL_W - 60, oy), (ox + self.CELL_W - 60, oy + self.CELL_H - 60),
                       (ox, oy + self.CELL_H - 60)], "FRAME")
            mx = ox + 40
            if b["mark"] in self.mcol:                  # same colour + item circle as in the layers sheet
                c = self.mcol[b["mark"]]
                self.balloon(mx + 55, oy + self.CELL_H - 120, 55, self.info.get(b["mark"], {}).get("no", ""), c)
                mx += 130
            self.text(mx, oy + self.CELL_H - 130, b["mark"], 1.8 * th, layer="MARKS")
            q = self.qty.get(b["mark"])
            nm = self.info.get(b["mark"], {})
            self.text(ox + 40, oy + self.CELL_H - 210,
                      (f"{nm.get('what', '')} · {nm.get('where', '')}" if nm else
                       f"{b.get('quality_grade_internal', '')} · {b.get('shape_family', 'shape n/a')} · {b.get('zone', '')}")[:110],
                      0.8 * th)
            self.text(ox + self.CELL_W - 100, oy + self.CELL_H - 130,
                      f"QTY THIS DESIGN: {q}" if q else "not used in this design", 0.9 * th, "right", "MARKS")
            try:
                self._draw(b, ox + 60, oy + 200)
            except Exception as e:
                self.text(ox + 60, oy + 600, f"cannot draw: {e}", 0.9 * th)
            note = b.get("note") or b.get("dimensions_mm", {}).get("note")
            if note:
                for i, chunk in enumerate(_wrap(note, 95)[:3]):
                    self.text(ox + 40, oy + 130 - i * 40, chunk, 0.7 * th, layer="NOTES")
        n_rows = (len(bricks) + self.COLS - 1) // self.COLS
        y = -(n_rows + 1) * self.CELL_H + 300
        self.title(0, y, [f"BRICK SHAPES - {self.job}",
                          "Source: brick library (mock until the client's real library is delivered). All dimensions mm.",
                          "Wall shapes: width_top = cold face, width_bottom = hot face, length = radial, thickness = course height.",
                          "Bottom wedges: A/B hot face outer/inner, C/D cold face outer/inner. Roles marked 'inferred' need checking.",
                          f"Generated {datetime.date.today().isoformat()}"], 3 * self.CELL_W)
        gaps = self.lib.get("_known_gaps", [])
        yy = y - 120
        for g in gaps:
            for chunk in _wrap(f"GAP: {g.get('issue')} - {g.get('status')}", 160):
                self.text(0, yy, chunk, 0.8 * th, layer="NOTES")
                yy -= 45
        return self.doc

    def _draw(self, b, x, y):
        fam, d = b.get("shape_family"), b.get("dimensions_mm", {})
        f = {"standard_trapezoidal": self._wall, "steel_cladded": self._wall, "bottom_dish_wedge": self._sh,
             "center_block": self._key, "safety_lining_rect": self._safety_rect, "safety_lining_wedge": self._wedge,
             "safety_lining_std": self._std, "purging_plug": self._plug, "seating_block": self._seat}.get(fam)
        if f is None or not any(isinstance(v, (int, float)) for v in d.values()):
            self.text(x, y + 500, "No geometry in the library for this mark.", 0.9 * self.th, layer="NOTES")
            return
        f(d, x, y)

    def _scale(self, *sizes):
        return min(1.0, 1150.0 / max(sizes))

    def _wall(self, d, x, y):
        L, wc, wh, t = d["length"], d["width_top"], d["width_bottom"], d["thickness"]
        s = self._scale(L, 1000)
        cy = y + 650
        pts = [(x, cy - wc / 2 * s), (x + L * s, cy - wh / 2 * s), (x + L * s, cy + wh / 2 * s), (x, cy + wc / 2 * s)]
        self.fill(pts, 254, "BRICK_FILL")
        self.poly(pts, "OUTLINE")
        self.dim(pts[0], pts[1], (x, cy - wc / 2 * s - 90), 0, L)
        self.dim(pts[0], pts[3], (x - 60, cy), 90, wc)
        self.dim(pts[1], pts[2], (x + L * s + 60, cy), 90, wh)
        self.text(x, cy + wc / 2 * s + 60, "PLAN: cold face (left) -> hot face (right)", 0.7 * self.th, layer="NOTES")
        ry = y + 120
        side = [(x, ry), (x + L * s, ry), (x + L * s, ry + t * s), (x, ry + t * s)]
        self.poly(side, "OUTLINE")
        self.dim(side[1], side[2], (x + L * s + 60, ry), 90, t)
        self.text(x, ry + t * s + 30, "SIDE", 0.7 * self.th, layer="NOTES")

    def _sh(self, d, x, y):
        L, wt, wb = d["overall_length"], d["face_width_top"], d["face_width_bottom"]
        s = 0.6
        # side view: length vertical, hot face (top) narrower
        sx = x + 60
        side = [(sx, y + 100), (sx + wb * s, y + 100), (sx + (wb / 2 + wt / 2) * s, y + 100 + L * s),
                (sx + (wb / 2 - wt / 2) * s, y + 100 + L * s)]
        self.fill(side, 254, "BRICK_FILL")
        self.poly(side, "OUTLINE")
        self.dim(side[0], side[1], (sx, y + 50), 0, wb, 0.8 * self.th)
        self.dim(side[3], side[2], (sx, y + 100 + L * s + 50), 0, wt, 0.8 * self.th)
        self.dim(side[1], (side[1][0], side[2][1]), (sx + wb * s + 50, y + 100), 90, L, 0.8 * self.th)
        self.text(sx, y + 100 + L * s + 130, "SIDE (radial)", 0.7 * self.th, layer="NOTES")
        # faces: hot (A outer, B inner) and cold (C outer, D inner), radial width wt / wb
        for k, (o, i, w, lbl) in enumerate(((d["A"], d["B"], wt, "HOT FACE"), (d["C"], d["D"], wb, "COLD FACE"))):
            fx, fy = x + 420 + k * 560, y + 350
            sc = 2.4
            pts = [(fx, fy + (o - i) / 2 * sc), (fx + w * sc, fy), (fx + w * sc, fy + o * sc), (fx, fy + (o + i) / 2 * sc)]
            self.poly(pts, "OUTLINE")
            self.dim(pts[0], pts[3], (fx - 40, fy), 90, i, 0.7 * self.th)
            self.dim(pts[1], pts[2], (fx + w * sc + 40, fy), 90, o, 0.7 * self.th)
            self.dim(pts[0], (pts[1][0], pts[0][1]), (fx, fy - 60), 0, w, 0.7 * self.th)
            self.text(fx, fy + o * sc + 60, f"{lbl} (inner -> outer), scale {sc:g}:1", 0.65 * self.th, layer="NOTES")

    def _key(self, d, x, y):
        di, do, h = d["inner_dia"], d["outer_dia"], d["height"]
        s = 0.8
        bx = x + 40
        el = [(bx, y + 100), (bx + do * s, y + 100), (bx + (do / 2 + di / 2) * s, y + 100 + h * s),
              (bx + (do / 2 - di / 2) * s, y + 100 + h * s)]
        self.fill(el, 254, "BRICK_FILL")
        self.poly(el, "OUTLINE")
        self.dim(el[0], el[1], (bx, y + 50), 0, do, 0.8 * self.th)
        self.dim(el[3], el[2], (bx, y + 100 + h * s + 50), 0, di, 0.8 * self.th)
        self.dim(el[1], (el[1][0], el[2][1]), (bx + do * s + 50, y + 100), 90, h, 0.8 * self.th)
        cx, cy = x + 1150, y + 600
        self.msp.add_circle((cx, cy), do / 2 * s, dxfattribs={"layer": self.layer("OUTLINE", 7)})
        self.msp.add_circle((cx, cy), di / 2 * s, dxfattribs={"layer": self.layer("HIDDEN", 8)})
        bolt = str(d.get("bolt_holes", ""))
        import re
        m = re.search(r"(\d+)\s*x\s*dia\.?\s*([\d.]+).*?([\d.]+)\s*mm\s*PCD", bolt)
        if m:
            n, hd, pcd = int(m.group(1)), float(m.group(2)), float(m.group(3))
            for k in range(n):
                a = math.radians(45 + k * 360 / n)
                self.msp.add_circle((cx + pcd / 2 * s * math.cos(a), cy + pcd / 2 * s * math.sin(a)), hd / 2 * s,
                                    dxfattribs={"layer": self.layer("OUTLINE", 7)})
        self.text(cx, cy - do / 2 * s - 70, "PLAN (hot face)", 0.7 * self.th, "center", "NOTES")
        if bolt:
            self.text(x + 40, y + 1080, bolt, 0.7 * self.th, layer="NOTES")

    def _safety_rect(self, d, x, y):
        a, b, dep, w = d["face_a"], d["face_b"], d["depth"], d["width"]
        s = 3.0
        bx, by = x + 200, y + 250
        pts = [(bx, by), (bx + b * s, by), (bx + (b / 2 + a / 2) * s, by + dep * s), (bx + (b / 2 - a / 2) * s, by + dep * s)]
        self.fill(pts, 254, "BRICK_FILL")
        self.poly(pts, "OUTLINE")
        self.dim(pts[0], pts[1], (bx, by - 60), 0, b, 0.8 * self.th)
        self.dim(pts[3], pts[2], (bx, by + dep * s + 60), 0, a, 0.8 * self.th)
        self.dim(pts[1], (pts[1][0], pts[2][1]), (bx + b * s + 60, by), 90, dep, 0.8 * self.th)
        self.text(bx, by - 170, f"width (thickness) {w:g}   scale {s:g}:1   (dimension roles inferred)", 0.7 * self.th,
                  layer="NOTES")

    def _wedge(self, d, x, y):
        a, b, c = d["a"], d["b"], d["c"]
        s = 3.0
        bx, by = x + 150, y + 250
        dx = c * math.tan(math.radians(float(d.get("taper_deg", 0)))) * s
        pts = [(bx, by), (bx + a * s, by), (bx + a * s + dx, by + c * s), (bx + dx + (a - b) * s, by + c * s)]
        self.fill(pts, 254, "BRICK_FILL")
        self.poly(pts, "OUTLINE")
        self.dim(pts[0], pts[1], (bx, by - 60), 0, a, 0.8 * self.th)
        self.dim(pts[3], pts[2], (bx, by + c * s + 60), 0, b, 0.8 * self.th)
        self.dim(pts[0], (pts[0][0], pts[3][1]), (bx - 60, by), 90, c, 0.8 * self.th)
        self.text(bx, by - 170, f"d = {d.get('d')}, taper {d.get('taper_deg')}°, scale {s:g}:1 (mark unconfirmed)",
                  0.7 * self.th, layer="NOTES")

    def _std(self, d, x, y):
        L, w = d["length"], d["width"]
        s = 2.5
        bx, by = x + 100, y + 300
        pts = [(bx, by), (bx + L * s, by), (bx + L * s, by + w * s), (bx, by + w * s)]
        self.fill(pts, 254, "BRICK_FILL")
        self.poly(pts, "OUTLINE")
        self.dim(pts[0], pts[1], (bx, by - 60), 0, L, 0.8 * self.th)
        self.dim(pts[1], pts[2], (bx + L * s + 60, by), 90, w, 0.8 * self.th)
        t = d.get("thickness", d.get("thickness_range"))
        self.text(bx, by + w * s + 60, f"thickness {t}   scale {s:g}:1", 0.7 * self.th, layer="NOTES")

    def _plug(self, d, x, y):
        H = d["overall_height"]
        ts = str(d.get("top_section", "150 x 134"))
        try:
            w = float(ts.lower().split("x")[0])
        except ValueError:
            w = 150.0
        s = 1.05
        bx, by = x + 300, y + 60
        pts = [(bx, by), (bx + w * s, by), (bx + w * s, by + H * s), (bx, by + H * s)]
        self.fill(pts, NOZZLE_ACI, "BRICK_FILL")
        self.poly(pts, "OUTLINE")
        self.dim(pts[1], pts[2], (bx + w * s + 60, by), 90, H, 0.8 * self.th)
        self.dim(pts[3], pts[2], (bx, by + H * s + 50), 0, w, 0.8 * self.th)
        self.text(bx + w * s + 250, by + 600, f"top section {ts}", 0.8 * self.th, layer="NOTES")
        self.text(bx + w * s + 250, by + 540, f"{d.get('pipe_count')} pipes OD {d.get('pipe_od')} / ID {d.get('pipe_id')}",
                  0.8 * self.th, layer="NOTES")

    def _seat(self, d, x, y):
        do, di, h = d["outer_dia"], d["inner_dia"], d["height"]
        s = 4.0
        bx, by = x + 150, y + 250
        pts = [(bx, by), (bx + do * s, by), (bx + do * s, by + h * s), (bx, by + h * s)]
        self.fill(pts, 254, "BRICK_FILL")
        self.poly(pts, "OUTLINE")
        for xx in (bx + (do - di) / 2 * s, bx + (do + di) / 2 * s):
            self.line((xx, by), (xx, by + h * s), "HIDDEN")
        self.dim(pts[0], pts[1], (bx, by - 60), 0, do, 0.8 * self.th)
        self.dim(pts[1], pts[2], (bx + do * s + 60, by), 90, h, 0.8 * self.th)
        self.text(bx, by + h * s + 60, f"bore Ø{di:g}   scale {s:g}:1", 0.7 * self.th, layer="NOTES")


def _wrap(s, n):
    words, out, cur = str(s).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out


# ============================================================================ LAYER SHEET
class LayerSheet(Doc):
    """Bottom ring layout + course (layer) section + ring plans + course schedule."""

    def __init__(self, sheet, courses, rings, nozzles=(), job="", mcol=None, info=None):
        super().__init__(max(2 * sheet.rout, sheet.zmax - sheet.zmin) / 90.0)
        self.s, self.courses, self.rings, self.nozzles, self.job = sheet, courses, rings, list(nozzles or []), job
        self.zero = sheet.zero                      # same 0-degree direction as the GA plan views
        self.mcol = mcol or assign_mark_colors(courses, rings)
        self.info = info or {}

    def _ball_brick(self, cx, cy, r1, r2, a0, a1, mark):
        """Circle with the item number on a brick (cut pieces: red circle 'C')."""
        am = math.radians((a0 + a1) / 2)
        w = math.radians(abs(a1 - a0)) * (r1 + r2) / 2
        r = 0.4 * min(w, r2 - r1)
        if r <= 0:
            return
        rm = (r1 + r2) / 2
        if mark == "CUT":
            self.balloon(cx + rm * math.cos(am), cy + rm * math.sin(am), r, "C", CUT_ACI)
        else:
            self.balloon(cx + rm * math.cos(am), cy + rm * math.sin(am), r,
                         self.info.get(mark, {}).get("no", "?"), self.mcol.get(mark, 8))

    def _balls_then_text(self, x, y, marks, h, text, layer="RING_NO", right=True):
        """Item circles for `marks` followed (or preceded) by a text line; returns nothing."""
        r = 0.75 * h
        if right:                                   # text right-aligned at x; circles left of the text
            self.text(x, y, text, h, "mright", layer)
            x0 = x - 0.62 * h * len(text) - 1.2 * r
            for k, m in enumerate(reversed(marks)):
                self.balloon(x0 - k * 2.3 * r, y, r, self.info.get(m, {}).get("no", "?"), self.mcol.get(m, 8))
        else:
            for k, m in enumerate(marks):
                self.balloon(x + r + k * 2.3 * r, y, r, self.info.get(m, {}).get("no", "?"), self.mcol.get(m, 8))
            self.text(x + 2.3 * r * len(marks) + 0.3 * r, y, text, h, "mleft", layer)

    def _label_brick(self, cx, cy, r1, r2, a0, a1, mark):
        """Write the mark name on a brick, along its radial length, sized to the brick."""
        am = math.radians((a0 + a1) / 2)
        w = math.radians(abs(a1 - a0)) * (r1 + r2) / 2          # tangential width at mid radius
        h = min(0.55 * w, (r2 - r1) / max(len(mark), 1) * 1.1, 0.5 * self.th)
        if h <= 0:
            return
        rot = math.degrees(am)
        if 90 < rot % 360 < 270:                                  # keep text readable (not upside down)
            rot += 180
        self.rtext(cx + (r1 + r2) / 2 * math.cos(am), cy + (r1 + r2) / 2 * math.sin(am), mark, h, rot, "BRICK_LABELS")

    def colour_key(self, x, y, title="BRICK KEY"):
        """Circle (item number + colour) -> mark -> name (what it is, where it is laid) -> size -> grade
        -> quantity. Returns the height used."""
        th, rh, ts = self.th, 2.4 * self.th, 1.0 * self.th
        self.text(x, y, title + "  -  the numbered circle on every brick in the layouts = item below (same colour in every drawing)",
                  1.6 * th, layer="COLOUR_KEY")
        cols = [("Item", 5), ("Mark", 9), ("Name - what it is and where it is laid", 58), ("Size (mm)", 22),
                ("Grade", 18), ("Pieces", 7), ("Cut", 5)]
        yy = y - 2.6 * th
        xx = x
        for name, w in cols:
            self.text(xx, yy, name, ts, layer="COLOUR_KEY")
            xx += w * th
        def swatch(col, yy):
            self.fill([(x, yy - 0.6 * th), (x + 4 * th, yy - 0.6 * th), (x + 4 * th, yy + 1.2 * th),
                       (x, yy + 1.2 * th)], col, "COLOUR_KEY_FILL")
            self.poly([(x, yy - 0.6 * th), (x + 4 * th, yy - 0.6 * th), (x + 4 * th, yy + 1.2 * th), (x, yy + 1.2 * th)],
                      "COLOUR_KEY")
        for m, c in self.mcol.items():
            yy -= rh
            i = self.info.get(m, {})
            self.balloon(x + 1.6 * th, yy + 0.3 * th, 1.05 * th, i.get("no", "?"), c, "COLOUR_KEY")
            what = i.get("what", "") + (f"  -  {i['where']}" if i.get("where") else "")
            vals = [m, what, i.get("size", ""), i.get("grades", ""), f"{i.get('qty', '')}", f"{i.get('cut') or ''}"]
            xx = x + cols[0][1] * th
            for (name, w), v in zip(cols[1:], vals):
                self.text(xx, yy, str(v)[:int(w * 1.6)], ts, layer="COLOUR_KEY")
                xx += w * th
        yy -= rh
        self.balloon(x + 1.6 * th, yy + 0.3 * th, 1.05 * th, "C", CUT_ACI, "COLOUR_KEY")
        self.text(x + cols[0][1] * th, yy, "CUT = cut piece (cut on site from the A brick of that ring / course)", ts,
                  layer="COLOUR_KEY")
        yy -= rh
        swatch(NOZZLE_ACI, yy)
        self.text(x + cols[0][1] * th, yy, "stirring element / nozzle", ts, layer="COLOUR_KEY")
        return y - yy + 2 * th

    # ------------------------------------------------------------ bottom rings
    def draw_bottom(self, cx, cy):
        th, R = self.th, self.rings
        if not R or not R.get("rings"):
            self.text(cx, cy, "No bottom band / bottom wedge shapes", th, "center")
            return
        if R.get("key"):
            rk = R["key"]["r"]
            pts = [(cx + rk * math.cos(a), cy + rk * math.sin(a)) for a in [k * math.pi / 36 for k in range(72)]]
            self.fill(pts, self.mcol.get(R["key"]["mark"], 9), "BOTTOM_KEY")
            self.msp.add_circle((cx, cy), rk, dxfattribs={"layer": self.layer("BOTTOM_RINGS", 7)})
            self.text(cx, cy, R["key"]["mark"], 0.7 * th, "center")
        stir = [n for n in self.nozzles if n.get("type") == "stirring" and n.get("count") and n.get("pcd")]
        for ring in R["rings"]:
            r1, r2 = ring["r_in"], ring["r_out"]
            seq = []
            items = list(ring["marks"].items())
            total = sum(q for _, q in items)
            if len(items) == 2:                         # interleave A/B evenly
                (ma, na), (mb, nb) = items
                a = b = 0
                for _ in range(total):
                    if b < nb and (b + 0.5) / nb <= (a + 0.5) / max(na, 1):
                        seq.append(mb); b += 1
                    else:
                        seq.append(ma); a += 1
            else:
                for m, q in items:
                    seq += [m] * q
            for k in range(ring["nCut"]):
                seq.insert(round(len(seq) * (k + 1) / (ring["nCut"] + 1)), "CUT")
            elems = []
            for n in stir:
                if r1 <= float(n["pcd"]) / 2 < r2:
                    cnt = int(n["count"])
                    elems += [("ELEM", float(n.get("start_angle") or 0) + k * 360 / cnt, float(n.get("size_b") or 134))
                              for k in range(cnt)]
            n_all = len(seq)
            if not n_all:
                continue
            # free angular gaps between stirring elements; bricks are spread over them pro rata
            spans = sorted(((a % 360) - math.degrees(w / r1) / 2, (a % 360) + math.degrees(w / r1) / 2) for _, a, w in elems)
            gaps = []
            if spans:
                for (s0, e0), (s1, _) in zip(spans, spans[1:] + [(spans[0][0] + 360, 0)]):
                    gaps.append((e0, s1))
            else:
                gaps = [(0.0, 360.0)]
            total = sum(g1 - g0 for g0, g1 in gaps)
            counts = [int(round(n_all * (g1 - g0) / total)) for g0, g1 in gaps]
            counts[-1] += n_all - sum(counts)
            k = 0
            for (g0, g1), c in zip(gaps, counts):
                if c <= 0:
                    continue
                step = (g1 - g0) / c
                for i in range(c):
                    m = seq[min(k, n_all - 1)]
                    k += 1
                    col = CUT_ACI if m == "CUT" else self.mcol.get(m, 8)
                    a_s, a_e = self.zero + g0 + i * step, self.zero + g0 + (i + 1) * step
                    pts = _wedge(cx, cy, r1, r2, a_s, a_e)
                    self.fill(pts, col, "BOTTOM_BRICKS")
                    self.poly(pts, "BOTTOM_RINGS")
                    self._ball_brick(cx, cy, r1, r2, a_s, a_e, m)
            for _, a, w in elems:
                pts = _wedge(cx, cy, r1, r2, self.zero + a - math.degrees(w / r1) / 2, self.zero + a + math.degrees(w / r1) / 2)
                self.fill(pts, NOZZLE_ACI, "NOZZLES")
                self.poly(pts, "NOZZLES")
            rz = math.radians(self.zero + 90)
            # ring number written on the ring (white tag at the top of the layout)
            am = math.radians(self.zero - 90)
            px_, py_ = cx + (r1 + r2) / 2 * math.cos(am), cy + (r1 + r2) / 2 * math.sin(am)
            hh = min(0.6 * (r2 - r1), 0.8 * th)
            self.fill([(px_ - 1.4 * hh, py_ - 0.7 * hh), (px_ + 1.4 * hh, py_ - 0.7 * hh), (px_ + 1.4 * hh, py_ + 0.7 * hh),
                       (px_ - 1.4 * hh, py_ + 0.7 * hh)], 255, "RING_NO_TAG")
            self.text(px_, py_, f"R{ring['ring']}", 0.8 * hh, "center", "RING_NO")
            # ring list (left of the layout): ring number, marks and counts
            rr = R["rings"][-1]["r_out"]
            ly = cy + rr - (ring["ring"] - 0.5) * (2 * rr / max(len(R["rings"]), 1))
            self._balls_then_text(cx - rr - 3 * th, ly, list(ring["marks"]), 0.7 * th,
                                  f"Ring {ring['ring']}: " + " + ".join(f"{m} x {q}" for m, q in ring["marks"].items())
                                  + (f" + {ring['nCut']} cut" if ring["nCut"] else ""))
        rmax = R["rings"][-1]["r_out"]
        self.text(cx, cy - rmax - 2.5 * th, f"BOTTOM WORKING LINING - RING LAYOUT ({R['band']}, {R['grade']})",
                  1.2 * th, "center")
        self.text(cx, cy - rmax - 4.2 * th,
                  f"{len(R['rings'])} rings + key, {R['total']} pieces incl. cut pieces; laid on cold-face sphere "
                  f"R{R.get('cold_sphere_radius')}; plan radius = radius on the cold face", 0.75 * th, "center")
        # ring table
        tx, ty = cx + rmax + 3 * th, cy + rmax
        self.text(tx, ty, "RING TABLE", 1.1 * th)
        rows = [("Ring", "r in-out (mm)", "Marks x qty", "Cut", "Note")]
        if R.get("key"):
            rows.append(("0", f"0-{R['key']['r']:g}", f"{R['key']['mark']} x 1", "", ""))
        for r in R["rings"]:
            rows.append((str(r["ring"]), f"{r['r_in']:.0f}-{r['r_out']:.0f}",
                         " + ".join(f"{m} x {q}" for m, q in r["marks"].items()),
                         f"{r['nCut']} x {r['cut']:.0f}" if r["nCut"] else "",
                         (r.get("note") or "") + ("  ! " + r["flag"] if r.get("flag") else "")))
        widths = [3, 9, 15, 6, 0]
        for k, row in enumerate(rows):
            y = ty - (k + 1.5) * 1.35 * th
            x = tx
            for w, cell in zip(widths, row):
                self.text(x, y, cell, 0.7 * th, layer="RING_TABLE")
                x += w * th
        y = ty - (len(rows) + 2) * 1.35 * th
        self.text(tx, y, f"TOTAL {R['total']} pieces", 0.9 * th, layer="RING_TABLE")

    # ------------------------------------------------------------ course section
    def draw_courses(self, ox, oy):
        s, th, L = self.s, self.th, self.s.L
        axis_x = ox + 30 * th + s.rout
        Y = lambda z: oy + 4 * th + (z - s.zmin)
        rows_by_course = {}
        for r in self.courses:
            rows_by_course.setdefault(r["course"], []).append(r)
        for side in (1, -1):
            # side 1 -> first sector of each band, side -1 -> sector containing +180 of it
            ang_ref = 0.0 if side == 1 else 180.0
            paths, outer = s.boundary_paths(ang_ref)
            T = lambda run: [(axis_x + side * x, Y(z), side * b) for x, z, b in run]
            if outer:
                self.msp.add_lwpolyline(T(outer.all()), format="xyb", dxfattribs={"layer": self.layer("SHELL", 8)})
            for k in range(L + 1):
                self.msp.add_lwpolyline(T(paths[k].all()), format="xyb",
                                        dxfattribs={"layer": self.layer("SHELL" if k == 0 else "LINING", 7)})
            for no, rs in rows_by_course.items():
                r = next((x for x in rs if _in_sector(ang_ref, x["sector"])), rs[0])
                if r.get("r_cold") is None:
                    continue
                y = Y(r["z0"])
                lay = "COURSES_FLAG" if r.get("flag") else "COURSES"
                self.layer("COURSES_FLAG", CUT_ACI)
                self.line((axis_x + side * r["r_cold"], y), (axis_x + side * (r["r_hot"] or 0), y), lay)
                self.text(axis_x + side * (r["r_cold"] + 0.6 * th), y + 0.5 * min(r["h"], th), str(no),
                          min(0.45 * th, 0.8 * r["h"]), "mleft" if side == 1 else "mright", "COURSE_NO")
        # band labels (right side) with pair used
        done = set()
        prev_y = None
        for r in self.courses:
            if (r["band"], r["sector"]) in done or not r.get("pair"):
                continue
            done.add((r["band"], r["sector"]))
            same = [x for x in self.courses if x["band"] == r["band"] and x["sector"] == r["sector"]]
            z_mid = (same[0]["z0"] + same[-1]["z1"]) / 2
            y = Y(z_mid) if prev_y is None else min(Y(z_mid), prev_y - 1.6 * th)
            prev_y = y
            n_flag = sum(1 for x in same if x.get("flag"))
            self._balls_then_text(axis_x + s.rout + 2.5 * th, y, [r["A"], r["B"]], 0.7 * th,
                                  f"{r['band']} {r['sector']}° {r.get('zone', '')}: {r['grade']}, L {r['L']:g} -> {r['pair']}"
                                  + (f"  ({n_flag} flagged courses)" if n_flag else ""), "TEXT", right=False)
        self.text(axis_x, oy + 1.2 * th, "COURSE (LAYER) SECTION - right 0° / left 180°, every course numbered, "
                                         "red = flagged course", 1.0 * th, "center")

    # ------------------------------------------------------------ ring plans per band
    def draw_ring_plan(self, cx, cy, course_rows, scale_r):
        th = self.th
        first = next((r for r in course_rows if r.get("r_hot")), course_rows[0])
        for r in course_rows:
            a0, a1 = [float(x) for x in r["sector"].split("-")]
            if not r.get("pair"):
                m = (a0 + a1) / 2
                ri = r.get("r_hot") or scale_r * 0.6
                self.arc_note(cx, cy, ri, a0 + self.zero, a1 + self.zero)
                mz = math.radians(m + self.zero)
                self.text(cx + ri * 0.7 * math.cos(mz), cy + ri * 0.7 * math.sin(mz),
                          f"{r['sector']}\u00b0: {r.get('flag') or 'no pair'}"[:70], 0.55 * th, "center", "COURSES_FLAG")
                continue
            n = r["nA"] + r["nB"] + r["nCut"]
            if n <= 0:
                continue
            seq = []
            a = b = 0
            for _ in range(r["nA"] + r["nB"]):
                if b < r["nB"] and (b + 0.5) / r["nB"] <= (a + 0.5) / max(r["nA"], 1):
                    seq.append(r["B"]); b += 1
                else:
                    seq.append(r["A"]); a += 1
            for k in range(r["nCut"]):
                seq.insert(round(len(seq) * (k + 1) / (r["nCut"] + 1)), "CUT")
            step = (a1 - a0) / len(seq)
            ri, ro = r["r_hot"], r["r_hot"] + r["L"]
            for i, m in enumerate(seq):
                a_s, a_e = self.zero + a0 + i * step, self.zero + a0 + (i + 1) * step
                pts = _wedge(cx, cy, ri, ro, a_s, a_e)
                self.fill(pts, CUT_ACI if m == "CUT" else self.mcol.get(m, 8), "RING_BRICKS")
                self.poly(pts, "RING_OUTLINE")
                self._ball_brick(cx, cy, ri, ro, a_s, a_e, m)
            m = math.radians((a0 + a1) / 2 + self.zero)
            tx, ty = cx + (ro + 1.5 * th) * math.cos(m), cy + (ro + 1.5 * th) * math.sin(m)
            txt = (f"{r['sector']}°: {r['A']} x {r['nA']} + {r['B']} x {r['nB']}"
                   + (f" + {r['nCut']} cut {r['cut']:.0f}" if r["nCut"] else ""))
            self._balls_then_text(tx - 0.31 * 0.6 * th * len(txt), ty, [r["A"], r["B"]], 0.6 * th, txt, "TEXT", right=False)
        z0 = math.radians(self.zero)
        self.line((cx + 0.2 * scale_r * math.cos(z0), cy + 0.2 * scale_r * math.sin(z0)), (cx, cy), "CENTER")
        self.text(cx + 0.25 * scale_r * math.cos(z0), cy + 0.25 * scale_r * math.sin(z0), "0\u00b0", 0.7 * th, "center")
        self.text(cx, cy - scale_r - 3 * th, f"COURSE {first['course']} - {first['band']} - Z {first['z0']:g}"
                  + (f" (R hot {first['r_hot']:.0f})" if first.get("r_hot") else ""), 0.9 * th, "center")

    def arc_note(self, cx, cy, r, a0, a1):
        self.layer("COURSES_FLAG", CUT_ACI)
        self.msp.add_arc((cx, cy), r, a0, a1, dxfattribs={"layer": "COURSES_FLAG"})

    # ------------------------------------------------------------ schedule table
    def draw_table(self, ox, oy):
        th = 0.55 * self.th
        cols = [("Course", 4), ("Z0", 5), ("H", 3.5), ("Band", 10), ("Sector", 6), ("Zone", 11), ("Grade", 9),
                ("L", 4), ("R hot", 5), ("Pair", 9), ("nA", 3), ("nB", 3), ("Cut", 6), ("Flag", 40)]
        y = oy
        self.text(ox, y + 2 * th, "COURSE (LAYER) SCHEDULE", 1.6 * th)
        self.text(ox, y + 4 * th, "(full table in the DXF on layer TABLE and in course_schedule.csv)", 1.0 * th)
        x = ox
        for name, w in cols:
            self.text(x, y, name, th, layer="TABLE")
            x += w * th * 1.9
        for r in self.courses:
            y -= 1.5 * th
            vals = [r["course"], f"{r['z0']:.0f}", f"{r['h']:g}", r["band"], r["sector"], r.get("zone", ""), r["grade"],
                    f"{r['L']:g}", "" if r.get("r_hot") is None else f"{r['r_hot']:.0f}", r.get("pair") or "-",
                    r["nA"] or "", r["nB"] or "", f"{r['nCut']}x{r['cut']:.0f}" if r["nCut"] else "", r.get("flag") or ""]
            x = ox
            for (name, w), v in zip(cols, vals):
                self.text(x, y, str(v)[:60], th, layer="TABLE_FLAG" if (name == "Flag" and v) else "TABLE")
                x += w * th * 1.9
        self.layer("TABLE_FLAG", CUT_ACI)

    # ------------------------------------------------------------ layout
    def build(self):
        s, th = self.s, self.th
        # 1. course section (left)
        sec_w = 60 * th + 2 * s.rout
        sec_h = (s.zmax - s.zmin) + 8 * th
        self.draw_courses(0, -sec_h)
        x = sec_w + 50 * th                       # room for the ring call-outs left of the bottom layout
        # 2. bottom ring layout
        if self.rings and self.rings.get("rings"):
            rmax = self.rings["rings"][-1]["r_out"]
            self.draw_bottom(x + rmax, -rmax - 2 * th)
            x += 2 * rmax + 40 * th
        # 3. one ring plan per wall band / course group (middle course of each band)
        bands = []
        for r in self.courses:
            if r["band"] not in bands:
                bands.append(r["band"])
        px, py = 0, -sec_h - 6 * th
        rmax_all = s.rout
        for b in bands:
            rows = [r for r in self.courses if r["band"] == b and r.get("pair") and not r.get("flag")]
            if not rows:
                rows = [r for r in self.courses if r["band"] == b and r.get("pair")]
            if not rows:
                continue
            mid_no = rows[len(rows) // 2]["course"]
            group = [r for r in self.courses if r["course"] == mid_no]
            self.draw_ring_plan(px + rmax_all + 8 * th, py - rmax_all - 4 * th, group, rmax_all)
            px += 2 * rmax_all + 22 * th
        # 4. colour key (big, next to the bottom layout) + schedule table (right of everything)
        kx = max(x, px) + 4 * th
        self.colour_key(kx, -2 * th)
        self.draw_table(kx + 135 * th, -2 * th)
        self.title(0, py - 2 * rmax_all - 20 * th, [
            f"LINING LAYERS & BRICK LAYOUT - {self.job}",
            "Bottom ring layout, course (layer) section, ring plans and course schedule computed from the confirmed design.",
            "Ring closure: nA*(hotA+j)+nB*(hotB+j) = phi*R_hot ; nA*(coldA+j)+nB*(coldB+j) = phi*(R_hot+L).",
            "Flagged rows need a shape not in the brick library or a design decision.",
            f"Generated {datetime.date.today().isoformat()}"], 2 * rmax_all + 40 * th)
        return self.doc


def _in_sector(a, sector):
    a0, a1 = [float(x) for x in sector.split("-")]
    return a0 - 1e-9 <= a % 360 < a1 - 1e-9 or (a1 >= 360 and a % 360 >= a0)


def _wedge(cx, cy, r1, r2, a0, a1):
    n = max(1, int(math.ceil(abs(a1 - a0) / 3)))
    outer = [(cx + r2 * math.cos(math.radians(a0 + (a1 - a0) * k / n)), cy + r2 * math.sin(math.radians(a0 + (a1 - a0) * k / n)))
             for k in range(n + 1)]
    inner = [(cx + r1 * math.cos(math.radians(a1 - (a1 - a0) * k / n)), cy + r1 * math.sin(math.radians(a1 - (a1 - a0) * k / n)))
             for k in range(n + 1)]
    return outer + inner
