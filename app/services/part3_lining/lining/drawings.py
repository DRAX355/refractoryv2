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
import re

import ezdxf
from ezdxf.enums import TextEntityAlignment as TA

from .geometry import reverse_run, sample, radius_at_z
from .sheet import Sheet, clean, decimals, _clip_to_region
from .bricks import band_kind
from .model import sector_at

ALIGN = {"left": TA.LEFT, "center": TA.MIDDLE_CENTER, "right": TA.RIGHT,
         "mleft": TA.MIDDLE_LEFT, "mright": TA.MIDDLE_RIGHT, "bcenter": TA.BOTTOM_CENTER}
# distinct colours; red (1, cut pieces) and magenta (6, nozzles) are reserved
MARK_COLORS = [3, 5, 30, 4, 2, 40, 150, 94, 52, 190, 34, 70, 110, 172, 132, 62, 142, 22]
FAMILY_TEXT = {"standard_trapezoidal": "wall brick", "steel_cladded": "steel-clad mouth brick",
               "bottom_dish_wedge": "bottom wedge brick", "center_block": "centre key block",
               "safety_lining_std": "permanent lining brick", "safety_lining_rect": "safety lining brick",
               "barrel_safety_brick": "barrel safety brick", "key_wedge_series": "key & wedge brick",
               "beveled_key_ht": "beveled key brick (HT)", "double_tapered": "wear lining double-tapered brick",
               "triple_tapered": "triple-tapered surround/bottom brick",
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


class _Content:
    """Collects PDF page content pieces in a list (used by save_previews; behaves like the str it replaces)."""
    __slots__ = ("parts",)

    def __init__(self):
        self.parts = []

    def __iadd__(self, s):
        self.parts.append(s)
        return self

    def __str__(self):
        return "".join(self.parts)

    def __len__(self):
        return sum(len(x) for x in self.parts)

    def __bool__(self):
        return any(self.parts)

    def encode(self, *a, **k):
        return str(self).encode(*a, **k)


def _fast_shape(sh):
    """Speed-up for the PDF preview: the same path operators PyMuPDF's Shape.draw_line / draw_polyline /
    draw_bezier / draw_curve write, produced with plain float maths (PyMuPDF creates several Point
    objects per vertex, which is most of the render time on big sheets). Drawing is unchanged."""
    m = sh.ipctm
    a, b, c, d, e, f = float(m.a), float(m.b), float(m.c), float(m.d), float(m.e), float(m.f)

    def fmt(p):
        x, y = float(p[0]), float(p[1])
        return f"{x * a + y * c + e:.3f} {x * b + y * d + f:.3f}"

    def last():
        lp = sh.last_point
        return None if lp is None else (float(lp[0]), float(lp[1]))

    def move_if_needed(p1, out):
        if last() != p1:
            out.append(fmt(p1) + " m\n")

    def draw_line(p1, p2):
        p1, p2 = (float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1]))
        out = []
        move_if_needed(p1, out)
        out.append(fmt(p2) + " l\n")
        sh.draw_cont += "".join(out)
        sh.last_point = p2
        return p2

    def draw_polyline(points):
        pts = [(float(p[0]), float(p[1])) for p in points]
        out = []
        move_if_needed(pts[0], out)
        out.extend(fmt(p) + " l\n" for p in pts[1:])
        sh.draw_cont += "".join(out)
        sh.last_point = pts[-1]
        return pts[-1]

    def draw_bezier(p1, p2, p3, p4):
        p1, p4 = (float(p1[0]), float(p1[1])), (float(p4[0]), float(p4[1]))
        out = []
        move_if_needed(p1, out)
        out.append(f"{fmt(p2)} {fmt(p3)} {fmt(p4)} c\n")
        sh.draw_cont += "".join(out)
        sh.last_point = p4
        return p4

    def draw_curve(p1, p2, p3):
        k = 0.55228474983                       # same construction as PyMuPDF's Shape.draw_curve
        x1, y1, x2, y2, x3, y3 = float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]), float(p3[0]), float(p3[1])
        return draw_bezier((x1, y1), (x1 + (x2 - x1) * k, y1 + (y2 - y1) * k),
                           (x3 + (x2 - x3) * k, y3 + (y2 - y3) * k), (x3, y3))

    sh.draw_line, sh.draw_polyline, sh.draw_bezier, sh.draw_curve = draw_line, draw_polyline, draw_bezier, draw_curve


def save_previews(msp, folder, name, size=(36, 24), dpi=150, skip_prefix=("TABLE",)):
    """PNG + PDF previews of a modelspace. Fast vector path via PyMuPDF; matplotlib as fallback.
    Layers starting with `skip_prefix` (the long schedule table) stay in the DXF but are left out of
    the previews - the same table is delivered as CSV and shown in the UI."""
    outs = []
    keep = lambda e: not str(e.dxf.layer).upper().startswith(tuple(skip_prefix))
    try:
        from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, pymupdf
        backend = pymupdf.PyMuPdfBackend()
        _make = backend.make_backend

        def _fast_backend(page, settings):     # speed-up: PyMuPDF joins the page content with "+=", which
            rb = _make(page, settings)         # gets very slow (quadratic) on big sheets - collect in a list
            try:
                sh = rb.content_shape
                if isinstance(getattr(sh, "totalcont", None), str) and not sh.totalcont:
                    sh.totalcont = _Content()
                    _commit = sh.commit

                    def _commit_keep(*a, **k):  # commit() resets it to "" - keep using the list
                        r = _commit(*a, **k)
                        sh.totalcont = _Content()
                        return r
                    sh.commit = _commit_keep
                _fast_shape(sh)
            except Exception:
                pass
            return rb
        backend.make_backend = _fast_backend
        cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE,
                                   color_policy=config.ColorPolicy.COLOR)
        Frontend(RenderContext(msp.doc), backend, config=cfg).draw_layout(msp, filter_func=keep)
        page = layout.Page(size[0] * 25.4, size[1] * 25.4, layout.Units.mm, margins=layout.Margins.all(8))
        settings = layout.Settings(fit_page=True)
        pdf = folder / f"{name}.pdf"
        pdf_bytes = backend.get_pdf_bytes(page, settings=settings)
        pdf.write_bytes(pdf_bytes)
        png = folder / f"{name}.png"
        try:                                    # rasterise the PDF just made (much faster than drawing twice)
            try:
                import pymupdf as _fz
            except ImportError:
                import fitz as _fz
            with _fz.open(stream=pdf_bytes, filetype="pdf") as d:
                d[0].get_pixmap(dpi=dpi, alpha=False).save(str(png))
        except Exception:
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


# ============================================================================ SHARED TRL DETAIL (GA + zone drawings)
def mhp_in_section(dw, m, side, axis_x, Y, hot, rp, in_plane, dim_h=None):
    """MHP in the vertical section as TRL draws it: tapered plug (hot face width at the hot face, cold face
    width at depth L) along the dish normal, metal case, seating block and the gas pipe out through the
    shell; in-plane plugs hatched in their quality, P.C.D./2 dimensioned from the vessel axis."""
    th = dw.th
    z0 = z_at_x(hot, rp)
    za, zb = z_at_x(hot, rp - 25), z_at_x(hot, rp + 25)
    if z0 is None or za is None or zb is None:
        return False
    slope = (zb - za) / 50.0
    k = math.hypot(1.0, slope)
    t, nrm = (1 / k, slope / k), (slope / k, -1 / k)          # along the hot face / into the lining
    P = lambda r, z: (axis_x + side * r, Y(z), 0)

    def at(d, w):                                             # point at depth d along the normal, offset w along t
        return P(rp + d * nrm[0] + w * t[0], z0 + d * nrm[1] + w * t[1])

    def box(d0, d1, w0, w1):
        return [at(d0, -w0 / 2), at(d0, w0 / 2), at(d1, w1 / 2), at(d1, -w1 / 2)]
    lay = dw.layer("MHP", 1)
    L, hw, cw = m["L"], m["hotW"], m["coldW"]
    plug = box(0, L, hw, cw)
    if in_plane:
        dw.fill(plug, 255, "MHP_MASK")
        h = dw.msp.add_hatch(color=1, dxfattribs={"layer": lay})
        h.set_pattern_fill("ANSI31", color=1, scale=max(0.5, th / 25))
        h.paths.add_polyline_path([(p[0], p[1]) for p in plug], is_closed=True)
    dw.poly(plug, lay, close=True)
    d = L
    if in_plane and (m.get("caseH") or 0) > 0:
        case = box(d, d + m["caseH"], cw, cw)
        dw.fill(case, 8, "MHP_CASE")
        dw.poly(case, lay, close=True)
        d += m["caseH"]
    if in_plane and (m.get("seatOD") or 0) > 0 and (m.get("seatH") or 0) > 0:
        so, si = m["seatOD"], m.get("seatID") or 0
        for sgn in (-1, 1):
            wall = [at(d, sgn * si / 2), at(d, sgn * so / 2), at(d + m["seatH"], sgn * so / 2), at(d + m["seatH"], sgn * si / 2)]
            dw.fill(wall, 5, "MHP_SEAT")
            dw.poly(wall, lay, close=True)
    if in_plane and (m.get("connL") or 0) > 0 and (m.get("connOD") or 0) > 0:
        po = m["connOD"]
        dw.poly(box(d, d + m["connL"], po, po), lay, close=True)
        tip = at(d + m["connL"], 0)
    else:
        tip = at(d, 0)
    if in_plane:
        dw.layer("CENTER", 1)
        dw.msp.add_line(at(-0.4 * th, 0), tip, dxfattribs={"layer": "CENTER", "linetype": "CENTER"})
        # P.C.D./2 from the vessel axis, above the hot face (TRL: 1140 each side)
        yb = Y(z0) + (6.0 if side == 1 else 6.0) * th
        x_axis, x_c = axis_x, axis_x + side * rp
        if dim_h:
            dim_h(min(x_axis, x_c), max(x_axis, x_c), Y(z0), yb, rp)
        c = at(L + (m.get("caseH") or 0) + 0.5 * (m.get("connL") or 0), 0)
        q = (c[0] + side * 2.5 * th, c[1] - 1.5 * th)
        dw.line(c[:2], q, lay)
        dw.text(q[0] + side * 0.3 * th, q[1], f"{m['mark']} ({m.get('grade') or ''})".replace(" ()", ""),
                  0.55 * th, "mleft" if side == 1 else "mright", "MHP")
    return True

# ------------------------------------------------------------ TRL detail (after the course schedule)


def taphole_in_section(dw, t, side, axis_x, Y, p_s, p_o, p_e, shell_t):
    """The assembled taphole set (blocks + sleeves + steel pipe) along the extracted taphole axis, hot end
    of the first piece at the hot face, as on TRL's GA."""
    from .assemblies import piece_polys, TB_ACI, TS_ACI
    th = dw.th
    if True:
        ux, uz = p_s[0] - p_e[0], p_s[1] - p_e[1]
        k = math.hypot(ux, uz) or 1.0
        ux, uz = ux / k, uz / k
        vx, vz = -uz, ux
        M = lambda x, y: (axis_x + side * (p_e[0] + x * ux + y * vx), Y(p_e[1] + x * uz + y * vz))
        lay = dw.layer("TAPHOLE_SET", TB_ACI)
        for kind, aci in (("block", TB_ACI), ("sleeve", TS_ACI)):
            x = 0.0
            for q in (q for q in t["pieces"] if q["kind"] == kind):
                for poly in piece_polys(q, x, 1):
                    for sg in (1, -1):
                        pts = [M(px, sg * py) for px, py in poly]
                        dw.fill(pts, aci, lay)
                        dw.poly(pts, lay)
                c = M(x + q["L"] / 2, (q["diHot"] + q["doHot"]) / 4)
                if q["L"] > 2.2 * th:
                    dw.text(c[0], c[1], q["mark"], 0.32 * th, "center", "TAPHOLE_SET")
                x += q["L"]
        sl = sum(q["L"] for q in t["pieces"] if q["kind"] == "sleeve")
        if t.get("pipeOD"):
            po, pi_ = t["pipeOD"], t.get("pipeID") or 0
            x1 = sl + (t.get("pipeOut") or 0)
            for sg in (1, -1):
                dw.poly([M(0, sg * pi_ / 2), M(x1, sg * pi_ / 2), M(x1, sg * po / 2), M(0, sg * po / 2)], lay)
        if t.get("opening"):                     # shell opening
            d = t["opening"] / 2
            ln = math.dist(p_e, p_o)
            for sg in (1, -1):
                dw.line(M(ln - shell_t - 0.5 * th, sg * d), M(ln + th, sg * d), lay)
        dw.layer("CENTER", 1)
        dw.msp.add_line(M(-1.5 * th, 0), M(math.dist(p_e, p_s) + 2 * th, 0), dxfattribs={"layer": "CENTER", "linetype": "CENTER"})


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
        """Points along a radial nozzle axis from outside the shell to the hot face (r, z) at `angle`.
        Positive inclination = outer end higher (perpendicular to top cone wall, as shown on shell drawing)."""
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
                    nx, nz = -math.sin(inc), math.cos(inc)       # normal to the axis (+cos, +sin) in (r, z)
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
                if not n.get("size_a"):
                    continue                                # element size not entered - not drawn (nothing assumed)
                w = float(n["size_a"])
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
                if pts and n.get("mhp") and self._draw_mhp(n["mhp"], side, axis_x, Y, hot, rp, in_plane):
                    continue                                    # the MHP itself is drawn (plug, case, pipe, PCD dim)
                if pts:
                    if in_plane:
                        self.fill(pts, NOZZLE_ACI, lay)
                    self.poly(pts, lay, close=True)
                    tx = axis_x + side * rp
                    self.text(tx, pts[2][1] + 0.8 * th, _stir_label(n) + ("" if in_plane else " (proj.)"),
                              0.6 * th, "bcenter", "NOZZLES")

    def _plan_detail(self, ox, oy, z, rows, perm_layers, perm_specs, tag, joint):
        """Plan section (TRL Section B-B / D-D style): working thickness tag in every zone and the permanent
        ring drawn brick by brick (brick width from the band's permanent input)."""
        th, L, eps = self.th, self.L, 1e-6
        cx, cy = self._plan_frame(ox, oy)
        sg = next((s_ for s_ in self.segs if s_.p0[1] - eps <= z <= s_.p1[1] + eps), None)
        if sg is None:
            return
        bi = self.seg_band[sg.name]
        band = self.bands[bi]
        if band_kind(self, bi) == "bottom":
            return
        perm = perm_specs.get(band["name"]) or band.get("permanent") or {}
        for sec in band["sectors"]:
            a0, a1 = float(sec["from"]), float(sec["to"])
            am = (a0 + a1) / 2
            paths, _ = self.boundary_paths(am)
            rc, rh = self._rad(paths[L - 1], z), self._rad(paths[L], z)
            row = next((r for r in rows if r.get("band") == band["name"] and str(r.get("sector")) == f"{a0:g}-{a1:g}"
                        and float(r["z0"]) - eps <= z < float(r["z1"]) - eps), None)
            t_w = float(row["L"]) if row and row.get("pair") else rc - rh
            if rc - rh > 2 * th:
                p = self.polar(cx, cy, (rc + rh) / 2, am)
                tag(p[0], p[1], f"{t_w:g}", 0.9 * th)
            w = float(perm.get("width") or 0)
            for k in perm_layers:
                if w <= 0 or float(sec["layers"][k]["thickness"] or 0) <= 0:
                    continue
                r1, r2 = self._rad(paths[k], z), self._rad(paths[k + 1], z)
                rm = (r1 + r2) / 2
                span = (a1 - a0) % 360 or 360
                n = max(1, int(round(math.radians(span) * rm / w)))
                for j in range(1, n + (1 if span < 360 else 0)):
                    a = a0 + span * j / n
                    self.msp.add_line(self.polar(cx, cy, r1, a), self.polar(cx, cy, r2, a), dxfattribs=joint)

    def _draw_mhp(self, m, side, axis_x, Y, hot, rp, in_plane):
        return mhp_in_section(self, m, side, axis_x, Y, hot, rp, in_plane, self.dim_h)

    def draw_trl_detail(self, courses, rings=None, mhp=None, perm_specs=None, th_set=None, only_bands=None,
                        ring_numbers=True):
        """Adds the detail TRL shows on its lining GA, all from this job's design (nothing assumed):
        - permanent lining drawn brick by brick (brick height along the wall from the band's permanent input,
          staggered between permanent layers),
        - working courses: brick length tag (white box) on every course, course number every 5th course,
          one mark call-out per run of courses with the same brick pair (e.g. "LC-10A & LC-10B"),
        - bottom: ring numbers 1..n and "KEY" on the section, the ring brick length, the MHP's neighbouring
          rings hatched in the surround quality.
        Adds entities only; the views already drawn are not changed."""
        th, L, eps = self.th, self.L, 1e-6
        rows = [r for r in (courses or []) if r.get("z1") is not None and r.get("sector")]
        if only_bands is not None:                     # zone drawings: only the bands of the zone
            names = {self.bands[b]["name"] for b in only_bands}
            rows = [r for r in rows if r.get("band") in names]
        perm_layers = [k for k in range(L) if str(self.layers[k]).upper().startswith("PERMANENT")]
        bandix = {b["name"]: i for i, b in enumerate(self.bands)}
        joint = {"layer": self.layer("PERM_BRICK_JOINTS", 7), "color": 7, "lineweight": 13}

        def sec_ok(r, ang):
            try:
                a0, a1 = [float(x) for x in str(r["sector"]).split("-")]
            except ValueError:
                return False
            a = ang % 360.0
            return a0 - eps <= a < a1 - eps or (a1 >= 360 - eps and abs(a) < eps)

        def along(pts):                               # cumulative length of a polyline
            acc = [0.0]
            for p, q in zip(pts, pts[1:]):
                acc.append(acc[-1] + math.dist(p, q))
            return acc

        def at(pts, acc, d):
            d = max(0.0, min(d, acc[-1]))
            for i in range(1, len(acc)):
                if acc[i] >= d - 1e-9:
                    f = (d - acc[i - 1]) / max(acc[i] - acc[i - 1], 1e-12)
                    return (pts[i - 1][0] + f * (pts[i][0] - pts[i - 1][0]), pts[i - 1][1] + f * (pts[i][1] - pts[i - 1][1]))
            return pts[-1]

        def tag(x, y, txt, h, rot=0.0):
            w = 0.62 * h * len(txt) + 0.5 * h
            c, s_ = math.cos(math.radians(rot)), math.sin(math.radians(rot))
            box = [(x + c * dx - s_ * dy, y + s_ * dx + c * dy) for dx, dy in
                   ((-w / 2, -0.7 * h), (w / 2, -0.7 * h), (w / 2, 0.7 * h), (-w / 2, 0.7 * h))]
            hb = self.msp.add_hatch(dxfattribs={"layer": self.layer("TRL_TAGS", 7)})
            hb.set_solid_fill(rgb=(255, 255, 255))
            hb.paths.add_polyline_path(box, is_closed=True)
            self.msp.add_lwpolyline(box, close=True, dxfattribs={"layer": "TRL_TAGS", "lineweight": 0})
            self.msp.add_text(txt, height=h, rotation=rot, dxfattribs={"layer": "TRL_TAGS"}).set_placement(
                (x, y), align=ALIGN["center"])

        rr = (rings or {}).get("rings") or []
        rb = bandix.get((rings or {}).get("band"))
        stir = next((n for n in self.nozzles if n.get("type") == "stirring" and n.get("count") and n.get("pcd")), None)
        mhp_ring = None
        if stir and rr:
            mhp_ring = next((x["ring"] for x in rr if float(x["r_in"]) <= float(stir["pcd"]) / 2 < float(x["r_out"])), None)
        for view, ox, oy, cut, vix in self._frames:
            if view == "plan" and only_bands is None:
                n0 = len(self.msp)
                try:
                    self._plan_detail(ox, oy, cut, rows, perm_layers, perm_specs or {}, tag, joint)
                except Exception:
                    pass
                if vix is not None and vix < len(self.views):
                    self.views[vix]["handles"].update(e.dxf.handle for e in list(self.msp)[n0:])
                continue
            if view != "vertical":
                continue
            n0 = len(self.msp)
            axis_x = ox + 44 * th + self.rout
            Y = lambda z: oy + 6 * th + (z - self.zmin)
            for side, ang in ((1, cut), (-1, cut + 180)):
                paths, _ = self.boundary_paths(ang)
                P = lambda p: (axis_x + side * p[0], Y(p[1]))
                # ---- permanent lining, brick by brick
                for bi, band in enumerate(self.bands):
                    if only_bands is not None and bi not in only_bands:
                        continue
                    perm = (perm_specs or {}).get(band["name"]) or band.get("permanent") or {}
                    i0, i1 = self.band_idx[bi]
                    for n_l, k in enumerate(perm_layers):
                        lay_in = (perm.get("layers") or [None] * (n_l + 1))
                        spec = lay_in[n_l] if n_l < len(lay_in) and lay_in[n_l] else perm
                        step_len = float(spec.get("height") or perm.get("height") or 0)
                        if step_len <= 0 or float(sector_at(band, ang % 360)["layers"][k]["thickness"] or 0) <= 0:
                            continue
                        a_pts, b_pts = sample(paths[k].run(i0, i1)), sample(paths[k + 1].run(i0, i1))
                        aa, ab = along(a_pts), along(b_pts)
                        if aa[-1] < step_len:
                            continue
                        n = max(1, int(round(aa[-1] / step_len)))
                        off = 0.5 if n_l % 2 else 0.0             # staggered joints between layers
                        for j in range(1, n + (1 if off else 0)):
                            f = (j - off) / n
                            if not 0.0 < f < 1.0:
                                continue
                            self.msp.add_line(P(at(a_pts, aa, f * aa[-1])), P(at(b_pts, ab, f * ab[-1])), dxfattribs=joint)
                # ---- working courses: length tags, course numbers, mark call-outs
                cold, hot = paths[L - 1].all(), paths[L].all()
                mine = sorted((r for r in rows if sec_ok(r, ang) and r.get("pair")), key=lambda r: float(r["z0"]))
                groups = []
                for r in mine:
                    zm = (float(r["z0"]) + float(r["z1"])) / 2
                    rc, rh = radius_at_z(cold, zm), radius_at_z(hot, zm)
                    if rc is None or rh is None or rc - rh < 2.5 * th:
                        continue
                    h = min(0.34 * th, 0.5 * float(r["h"]))
                    xm = rh + min(float(r.get("L") or 0), rc - rh) * 0.5
                    tag(*P((xm, zm)), f"{float(r['L']):g}", h)
                    if int(r["course"]) % 5 == 0:
                        self.text(*P((rh - 0.35 * th, zm)), str(r["course"]), 0.45 * th,
                                  "mright" if side == 1 else "mleft", "COURSE_NO")
                    if groups and groups[-1]["pair"] == r["pair"] and groups[-1]["band"] == r["band"]:
                        groups[-1]["rows"].append((zm, rh))
                    else:
                        groups.append({"pair": r["pair"], "band": r["band"], "rows": [(zm, rh)]})
                for g in groups:
                    zm, rh = g["rows"][len(g["rows"]) // 2]
                    a, b = (g["pair"].split("+") + [""])[:2]
                    txt = f"{a} & {b}" if b else a
                    p0 = P((rh, zm))
                    p1 = (p0[0] - side * 3.0 * th, p0[1] + 0.8 * th)
                    self.line(p0, p1, self.layer("MARK_CALLOUT", 1))
                    self.text(p1[0] - side * 0.2 * th, p1[1], txt, 0.5 * th, "mright" if side == 1 else "mleft",
                              "MARK_CALLOUT")
                # ---- bottom: ring numbers, KEY, ring brick length, MHP surround rings
                if rb is not None and rr and (only_bands is None or rb in only_bands):
                    i0, i1 = self.band_idx[rb]
                    crun, hrun = paths[L - 1].run(i0, i1), paths[L].run(i0, i1)
                    t_w = float(sector_at(self.bands[rb], ang % 360)["layers"][L - 1]["thickness"] or 0)
                    for x in rr:
                        rm = (float(x["r_in"]) + float(x["r_out"])) / 2
                        zh, zc = z_at_x(hrun, rm), z_at_x(crun, rm)
                        if zh is None:
                            continue
                        if ring_numbers:
                            self.text(*P((rm, zh - 0.9 * th)), str(x["ring"]), 0.55 * th, "center", "RING_NO")
                        if mhp and mhp_ring is not None and abs(int(x["ring"]) - int(mhp_ring)) == 1 and zc is not None:
                            ri, ro = float(x["r_in"]), float(x["r_out"])
                            q = [(ri, z_at_x(crun, ri)), (ro, z_at_x(crun, ro)), (ro, z_at_x(hrun, ro)), (ri, z_at_x(hrun, ri))]
                            if all(v[1] is not None for v in q):
                                hh = self.msp.add_hatch(color=3, dxfattribs={"layer": self.layer("MHP_SURROUND", 3)})
                                hh.set_pattern_fill("ANSI31", color=3, scale=max(1.0, th / 8))
                                hh.paths.add_polyline_path([P(v) for v in q], is_closed=True)
                    if (rings or {}).get("key"):
                        zh = z_at_x(hrun, 0.0) or z_at_x(hrun, 1.0)
                        if zh is not None:
                            tag(*P((0.0 + 1e-3, zh - 2.2 * th)), "KEY", 0.5 * th, 90)
                    x = rr[len(rr) // 2]
                    rm = (float(x["r_in"]) + float(x["r_out"])) / 2
                    zh, zc = z_at_x(hrun, rm), z_at_x(crun, rm)
                    if zh is not None and zc is not None and t_w:
                        tag(*P((rm, (zh + zc) / 2)), f"{t_w:g}", 0.42 * th, 90)
                if th_set:
                    self._draw_taphole_set(th_set, side, ang, axis_x, Y)
            if vix is not None and vix < len(self.views):
                self.views[vix]["handles"].update(e.dxf.handle for e in list(self.msp)[n0:])

    def _draw_taphole_set(self, t, side, ang, axis_x, Y):
        for n in self._radial_nozzles():
            if n.get("type") != "taphole" or abs(((float(n["angle"]) - ang + 180) % 360) - 180) > 1.0:
                continue
            p_s, p_o, p_e = self._radial_axis(n, ang % 360)
            taphole_in_section(self, t, side, axis_x, Y, p_s, p_o, p_e, self.shell_t)

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
            if not n.get("size_a"):
                self.arc_line(cx, cy, rp, 0, 360, self.layer("CENTER", 1))
                continue                                    # element size not entered - only the PCD is drawn
            a_s = float(n["size_a"]) / 2
            b_s = float(n.get("size_b") or n["size_a"]) / 2
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
        self.views = []                             # (title, entity handles) per view -> one page each

    def _view(self, title, fn, *args, **kw):
        """Draws one view and records its entities, so it can be put on its own page / layout."""
        n0 = len(self.msp)
        out = fn(*args, **kw)
        self.views.append({"title": title, "handles": {e.dxf.handle for e in list(self.msp)[n0:]}})
        return out

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
    """The brick shapes used in this job (from the client brick library), dimensioned (mm). The full catalogue is
    drawn only when nothing of the library is used (e.g. a library check run)."""
    CELL_W, CELL_H, COLS = 1700.0, 1450.0, 6

    def __init__(self, lib, qty=None, job="", mcol=None, info=None):
        super().__init__(28.0)
        self.lib, self.qty, self.job, self.mcol, self.info = lib, qty or {}, job, mcol or {}, info or {}

    def build(self):
        allb = self.lib.get("bricks", [])
        used = list(self.mcol) + [m for m, q in self.qty.items() if q and m not in self.mcol]   # used shapes first
        bricks = sorted(allb, key=lambda b: (used.index(b["mark"]) if b["mark"] in used else len(used)))
        in_job = [b for b in bricks if b["mark"] in used]
        if in_job:                                  # only the shapes of this job (TRL sheets show the job's shapes)
            bricks = in_job
        th = self.th
        row_n0, row_marks = [], []                  # one page per row of shapes
        for k, b in enumerate(bricks):
            col, row = k % self.COLS, k // self.COLS
            if col == 0:
                row_n0.append(len(self.msp))
                row_marks.append([])
            row_marks[-1].append(b["mark"])
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
        ents = list(self.msp)
        bounds_ = row_n0 + [len(ents)]
        for r, marks in enumerate(row_marks):
            hs = {e.dxf.handle for e in ents[bounds_[r]:bounds_[r + 1]]}
            used_here = [m for m in marks if self.qty.get(m)]
            self.views.append({"title": f"BRICK SHAPES {r + 1} - " + (", ".join(marks[:3]) + (" ..." if len(marks) > 3 else "")),
                               "handles": hs,
                               "note": f"{len(used_here)} of {len(marks)} shapes on this sheet are used in this design"})
        n_rows = (len(bricks) + self.COLS - 1) // self.COLS
        y = -(n_rows + 1) * self.CELL_H + 300
        self.title(0, y, [f"BRICK SHAPES - {self.job}",
                          "Source: client brick library (TRL Krosaki BOF standard shapes). All dimensions mm.",
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
        if fam in ("key_wedge_series", "beveled_key_ht", "barrel_safety_brick") \
                and not all(k in d for k in ("length", "width_top", "width_bottom", "thickness")):
            fam = None                              # TRL family without the wall-brick dimensions -> not drawn
        if fam in ("double_tapered", "triple_tapered") \
                and not all(k in d for k in ("overall_length", "face_width_top", "face_width_bottom", "A", "B", "C", "D")):
            fam = None
        f = {"standard_trapezoidal": self._wall, "steel_cladded": self._wall, "bottom_dish_wedge": self._sh,
             "barrel_safety_brick": self._wall, "key_wedge_series": self._wall, "beveled_key_ht": self._wall,
             "double_tapered": self._sh, "triple_tapered": self._sh,
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
        self.gcol = dict(sheet.colors)              # quality (grade) colours - the same as the lining GA legend

    def gc(self, grade):
        """Fill colour of a brick = colour of its quality grade (as in the GA quality legend)."""
        return self.gcol.get(grade, 8)

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
    def _draw_herringbone_grid(self, cx, cy, r_hb, pitch_w=155.0, pitch_l=240.0, col_aci=3, stir=()):
        """Draws a true 45-degree interlocking herringbone brick course grid clipped inside radius r_hb."""
        if r_hb <= 50:
            return
        ang = math.radians(45.0)
        ca, sa = math.cos(ang), math.sin(ang)
        ux, uy = ca, sa
        vx, vy = -sa, ca
        pw = max(80.0, float(pitch_w or 155.0))
        pl = max(140.0, float(pitch_l or 240.0))
        n_rows = int(math.ceil(r_hb / pw)) + 1
        n_cols = int(math.ceil(r_hb / pl)) + 1
        lay_fill = self.layer("BOTTOM_BRICKS", col_aci)
        lay_line = self.layer("BOTTOM_RINGS", 7)
        # Base fill circle for the herringbone zone
        base_pts = [(cx + r_hb * math.cos(k * math.pi / 36), cy + r_hb * math.sin(k * math.pi / 36)) for k in range(72)]
        self.fill(base_pts, col_aci, lay_fill)
        self.msp.add_circle((cx, cy), r_hb, dxfattribs={"layer": lay_line, "lineweight": 25})

        def clip_seg(p0, p1):
            # Clip line segment p0->p1 to circle (cx, cy, r_hb)
            x0, y0 = p0[0] - cx, p0[1] - cy
            x1, y1 = p1[0] - cx, p1[1] - cy
            dx, dy = x1 - x0, y1 - y0
            a = dx * dx + dy * dy
            if a < 1e-9:
                return None
            b = 2 * (x0 * dx + y0 * dy)
            c = x0 * x0 + y0 * y0 - r_hb * r_hb
            disc = b * b - 4 * a * c
            if disc <= 0:
                return None
            sd = math.sqrt(disc)
            t0 = max(0.0, (-b - sd) / (2 * a))
            t1 = min(1.0, (-b + sd) / (2 * a))
            if t0 >= t1 - 1e-6:
                return None
            return (cx + x0 + t0 * dx, cy + y0 + t0 * dy), (cx + x0 + t1 * dx, cy + y0 + t1 * dy)

        for r in range(-n_rows, n_rows + 1):
            v0 = r * pw
            # Continuous diagonal course line
            cs = clip_seg((cx - r_hb * 1.2 * ux + v0 * vx, cy - r_hb * 1.2 * uy + v0 * vy),
                          (cx + r_hb * 1.2 * ux + v0 * vx, cy + r_hb * 1.2 * uy + v0 * vy))
            if cs:
                self.line(cs[0], cs[1], lay_line)
            if r == n_rows:
                continue
            v1 = (r + 1) * pw
            shift = (abs(r) % 2) * (pl * 0.5)
            for c in range(-n_cols, n_cols + 1):
                u0 = c * pl + shift
                p_a = (cx + u0 * ux + v0 * vx, cy + u0 * uy + v0 * vy)
                p_b = (cx + u0 * ux + v1 * vx, cy + u0 * uy + v1 * vy)
                cs_j = clip_seg(p_a, p_b)
                if cs_j:
                    self.line(cs_j[0], cs_j[1], lay_line)
                # Herringbone interlocking chevron cross-joint in alternating blocks
                if (r + c) % 2 == 0:
                    um = u0 + pl * 0.5
                    vm = 0.5 * (v0 + v1)
                    p_c1 = (cx + u0 * ux + vm * vx, cy + u0 * uy + vm * vy)
                    p_c2 = (cx + um * ux + v0 * vx, cy + um * uy + v0 * vy)
                    p_c3 = (cx + (u0 + pl) * ux + vm * vx, cy + (u0 + pl) * uy + vm * vy)
                    for seg_a, seg_b in ((p_c1, p_c2), (p_c2, p_c3)):
                        cs_c = clip_seg(seg_a, seg_b)
                        if cs_c:
                            self.line(cs_c[0], cs_c[1], lay_line)

    def draw_bottom(self, cx, cy):
        th, R = self.th, self.rings
        if not R or not R.get("rings"):
            self.text(cx, cy, "No bottom band / bottom wedge shapes", th, "center")
            return
        is_hb = str(R.get("pattern") or "").lower() == "herringbone" or "herring" in str(self.job or "").lower()
        if R.get("key") and not is_hb:
            rk = R["key"]["r"]
            pts = [(cx + rk * math.cos(a), cy + rk * math.sin(a)) for a in [k * math.pi / 36 for k in range(72)]]
            self.fill(pts, self.gc(R.get("grade")), "BOTTOM_KEY")
            self.msp.add_circle((cx, cy), rk, dxfattribs={"layer": self.layer("BOTTOM_RINGS", 7)})
            self.text(cx, cy, R["key"]["mark"], 0.7 * th, "center")
        stir = [n for n in self.nozzles if n.get("type") == "stirring" and n.get("count") and n.get("pcd")]
        hb_ring_cutoff = max(1, len(R["rings"]) - 2) if is_hb and len(R["rings"]) > 3 else 0
        if is_hb and hb_ring_cutoff > 0:
            r_hb = R["rings"][hb_ring_cutoff - 1]["r_out"]
            w_avg = R["rings"][0].get("width") or 155.0
            self._draw_herringbone_grid(cx, cy, r_hb, pitch_w=w_avg, pitch_l=w_avg * 1.65, col_aci=self.gc(R.get("grade")), stir=stir)
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
                if not (n.get("size_b") or n.get("size_a")):
                    continue
                if r1 <= float(n["pcd"]) / 2 < r2:
                    cnt = int(n["count"])
                    elems += [("ELEM", float(n.get("start_angle") or 0) + k * 360 / cnt, float(n.get("size_b") or n["size_a"]))
                              for k in range(cnt)]
            n_all = len(seq)
            if not n_all:
                continue
            if is_hb and ring["ring"] <= hb_ring_cutoff:
                # In Herringbone mode, central courses are drawn as the 45° herringbone grid above; still draw MHP plugs & left ring list
                for _, a, w in elems:
                    pts = _wedge(cx, cy, r1, r2, self.zero + a - math.degrees(w / r1) / 2, self.zero + a + math.degrees(w / r1) / 2)
                    self.fill(pts, NOZZLE_ACI, "NOZZLES")
                    self.poly(pts, "NOZZLES")
                rr = R["rings"][-1]["r_out"]
                ly = cy + rr - (ring["ring"] - 0.5) * (2 * rr / max(len(R["rings"]), 1))
                self._balls_then_text(cx - rr - 3 * th, ly, list(ring["marks"]), 0.7 * th,
                                      f"Course {ring['ring']} (HB): " + " + ".join(f"{m} x {q}" for m, q in ring["marks"].items())
                                      + (f" + {ring['nCut']} cut" if ring["nCut"] else ""))
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
                    col = CUT_ACI if m == "CUT" else self.gc(R.get("grade"))
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
        for n in stir:                                 # name each element (MHP-1 ... when the MHP is entered)
            lab = _stir_label(n)
            if lab == "STIR":
                continue
            cnt, rp = int(n["count"]), float(n["pcd"]) / 2
            for k in range(cnt):
                am = math.radians(self.zero + float(n.get("start_angle") or 0) + k * 360 / cnt)
                rl = rp + float(n.get("size_a") or 150) / 2 + 1.2 * th
                self.text(cx + rl * math.cos(am), cy + rl * math.sin(am), f"{lab}-{k + 1}", 0.7 * th, "center", "NOZZLES")
        rmax = R["rings"][-1]["r_out"]
        pat_title = "HERRINGBONE & PERIMETER RING LAYOUT" if is_hb else "RING LAYOUT"
        self.text(cx, cy - rmax - 2.5 * th, f"BOTTOM WORKING LINING - {pat_title} ({R['band']}, {R['grade']})",
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
            P = lambda run: [(axis_x + side * x, Y(z)) for x, z in sample(run)]
            if outer:                               # shell (grey) and every layer in its quality colour
                self.fill(P(outer.all() + reverse_run(paths[0].all())), 8, "SHELL_FILL")
            for bi, band in enumerate(s.bands):
                i0, i1 = s.band_idx[bi]
                sec = sector_at(band, ang_ref % 360)
                for j in range(1, L + 1):
                    g = sec["layers"][j - 1]["grade"]
                    self.fill(P(paths[j - 1].run(i0, i1) + reverse_run(paths[j].run(i0, i1))), self.gc(g), "GRADE_FILL")
            if outer:
                self.msp.add_lwpolyline(T(outer.all()), format="xyb", dxfattribs={"layer": self.layer("SHELL", 8)})
            for k in range(L + 1):
                self.msp.add_lwpolyline(T(paths[k].all()), format="xyb",
                                        dxfattribs={"layer": self.layer("SHELL" if k == 0 else "LINING", 7)})
            R = self.rings or {}
            rb = next((bi for bi, b in enumerate(s.bands) if b["name"] == R.get("band")), None)
            if rb is not None and R.get("rings"):    # bottom rings: joint square to the bottom + ring number
                i0, i1 = s.band_idx[rb]
                crun, hrun = paths[L - 1].run(i0, i1), paths[L].run(i0, i1)
                t = sector_at(s.bands[rb], ang_ref % 360)["layers"][L - 1]["thickness"]
                s0 = s.segs[i0]
                region = sample(crun + reverse_run(hrun))

                def normal(rad, zc):
                    if s0.shape == "arc" and s0.C is not None:
                        dx, dz = s0.C[0] - rad, s0.C[1] - zc
                        nn = math.hypot(dx, dz) or 1.0
                        return dx / nn, dz / nn
                    return 0.0, 1.0
                for ring in R["rings"]:
                    rad = ring["r_out"]
                    zc = z_at_x(crun, rad)
                    if zc is not None and ring is not R["rings"][-1]:
                        dx, dz = normal(rad, zc)
                        xe, ze = _clip_to_region((rad, zc), (rad + dx * t, zc + dz * t), region)
                        self.line((axis_x + side * rad, Y(zc)), (axis_x + side * xe, Y(ze)), "BOTTOM_RINGS")
                    rm = (ring["r_in"] + ring["r_out"]) / 2
                    zm = z_at_x(crun, rm)
                    if zm is not None and (ring["ring"] % 2 == 1 or ring is R["rings"][-1]):
                        dx, dz = normal(rm, zm)
                        self.text(axis_x + side * (rm + dx * 0.5 * t), Y(zm + dz * 0.5 * t), str(ring["ring"]),
                                  min(0.45 * th, 0.6 * (ring["r_out"] - ring["r_in"])), "center", "RING_NO")
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
                self.fill(pts, CUT_ACI if m == "CUT" else self.gc(r.get("grade")), "RING_BRICKS")
                self.poly(pts, "RING_OUTLINE")
                self._ball_brick(cx, cy, ri, ro, a_s, a_e, m)
            m = math.radians((a0 + a1) / 2 + self.zero)
            tx, ty = cx + (ro + 1.5 * th) * math.cos(m), cy + (ro + 1.5 * th) * math.sin(m)
            txt = (f"{r['sector']}°: {r['A']} x {r['nA']} + {r['B']} x {r['nB']}"
                   + (f" + {r['nCut']} cut {r['cut']:.0f}" if r["nCut"] else ""))
            if a1 - a0 >= 359.9:                    # full ring: note under the ring, clear of the bricks
                tx, ty = cx, cy - ro - 1.5 * th
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

    # ------------------------------------------------------------ quality legend
    def quality_legend(self, x, y):
        """Quality (grade) colours used on this sheet - the same colours as the GA legend."""
        th = self.th
        used = []
        if self.rings and self.rings.get("grade"):
            used.append(self.rings["grade"])
        for bi, b in enumerate(self.s.bands):
            for sec in b["sectors"]:
                for l in sec["layers"]:
                    if l["grade"] not in used:
                        used.append(l["grade"])
        for r in self.courses:
            if r.get("grade") and r["grade"] not in used:
                used.append(r["grade"])
        self.text(x, y, "QUALITY LEGEND", 1.6 * th, layer="LEGEND")
        yy = y - 1.2 * th
        cols = 3
        for k, g in enumerate(used):
            c, rr = k % cols, k // cols
            bx, by = x + c * 22 * th, yy - (rr + 1) * 2.6 * th
            box = [(bx, by), (bx + 4 * th, by), (bx + 4 * th, by + 1.8 * th), (bx, by + 1.8 * th)]
            self.fill(box, self.gc(g), "LEGEND_FILL")
            self.poly(box, "LEGEND")
            self.text(bx + 5 * th, by + 0.9 * th, g, 1.0 * th, "mleft", "LEGEND")
        n_rows = (len(used) + cols - 1) // cols
        by = yy - (n_rows + 1) * 2.6 * th
        for k, (col, txt) in enumerate(((CUT_ACI, "CUT piece (cut on site)"), (NOZZLE_ACI, "stirring element / nozzle"))):
            bx = x + k * 22 * th
            box = [(bx, by), (bx + 4 * th, by), (bx + 4 * th, by + 1.8 * th), (bx, by + 1.8 * th)]
            self.fill(box, col, "LEGEND_FILL")
            self.poly(box, "LEGEND")
            self.text(bx + 5 * th, by + 0.9 * th, txt, 1.0 * th, "mleft", "LEGEND")
        self.text(x, by - 2.4 * th, "Brick fill = quality grade colour.  Numbered circle on a brick = item number in the "
                                    "BRICK KEY (mark, size, quantity).", 0.9 * th, layer="LEGEND")

    # ------------------------------------------------------------ bottom BOQ (marks x rings)
    def boq_matrix(self, x, y):
        """Bottom working lining brick BOQ: one row per brick mark, one column per ring, total per mark
        (the customer's 'BOTTOM DISH WORKING LINING BRICK BOQ')."""
        R, th = self.rings, self.th
        if not R or not R.get("rings"):
            return
        rings = R["rings"]
        marks = []
        for r in rings:
            for m in r["marks"]:
                if m not in marks:
                    marks.append(m)
        cw, rh, lw = 3.6 * th, 2.2 * th, 12 * th
        n = len(rings)
        W = lw + (n + 1) * cw + 1.5 * cw
        rows = list(reversed(marks)) + (["KEY"] if R.get("key") else [])
        H = (len(rows) + 1) * rh
        self.text(x + W / 2, y + 1.4 * th, f"BOTTOM DISH WORKING LINING BRICK BOQ  ({R.get('grade', '')})", 1.3 * th,
                  "bcenter", "BOQ")
        lay = "BOQ"
        self.poly([(x, y), (x + W, y), (x + W, y - H), (x, y - H)], lay)
        for k in range(1, len(rows) + 1):
            self.line((x, y - k * rh), (x + W, y - k * rh), lay)
        xs = [x + lw + i * cw for i in range(n + 1)] + [x + W - 1.5 * cw]
        for xx in xs:
            self.line((xx, y), (xx, y - H), lay)
        for k, m in enumerate(rows):
            yy = y - (k + 0.5) * rh
            self.text(x + 0.5 * th, yy, m, 0.9 * th, "mleft", lay)
            tot = 0
            for i, r in enumerate(rings):
                if m == "KEY":
                    continue
                q = r["marks"].get(m, 0)
                if q:
                    self.text(x + lw + (i + 0.5) * cw, yy, str(q), 0.8 * th, "center", lay)
                    tot += q
            if m == "KEY":
                self.text(x + lw + 0.5 * cw, yy, "1", 0.8 * th, "center", lay)
                tot = 1
            self.text(x + W - 0.75 * cw, yy, str(tot), 0.9 * th, "center", lay)
        # cut pieces row + ring numbers row
        yy = y - (len(rows) + 0.5) * rh
        self.text(x + 0.5 * th, yy, "RING NO.", 0.9 * th, "mleft", lay)
        for i, r in enumerate(rings):
            self.text(x + lw + (i + 0.5) * cw, yy, str(r["ring"]), 0.8 * th, "center", lay)
        self.text(x + W - 0.75 * cw, yy, "TOTAL", 0.8 * th, "center", lay)
        self.poly([(x, y - H), (x + W, y - H), (x + W, y - H - rh), (x, y - H - rh)], lay)
        yy = y - H - 0.5 * rh
        self.text(x + 0.5 * th, yy, "CUT PIECES", 0.9 * th, "mleft", lay)
        for i, r in enumerate(rings):
            if r["nCut"]:
                self.text(x + lw + (i + 0.5) * cw, yy, str(r["nCut"]), 0.8 * th, "center", lay)
        self.text(x + W - 0.75 * cw, yy, str(sum(r["nCut"] for r in rings)), 0.9 * th, "center", lay)
        self.text(x, y - H - rh - 1.6 * th, f"TOTAL BOTTOM PIECES (incl. key and cut pieces): {R.get('total', '')}", 1.0 * th,
                  layer=lay)

    # ------------------------------------------------------------ layout
    def build(self):
        s, th = self.s, self.th
        # 1. course section (left)
        sec_w = 60 * th + 2 * s.rout
        sec_h = (s.zmax - s.zmin) + 8 * th
        self._view("COURSE (LAYER) SECTION", self.draw_courses, 0, -sec_h)
        x = sec_w + 50 * th                       # room for the ring call-outs left of the bottom layout
        # 2. bottom ring layout
        if self.rings and self.rings.get("rings"):
            rmax = self.rings["rings"][-1]["r_out"]
            self._view("BOTTOM WORKING LINING - RING LAYOUT", self.draw_bottom, x + rmax, -rmax - 2 * th)
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
            self._view(f"COURSE {mid_no} BRICK LAYOUT - {str(b).upper()}", self.draw_ring_plan,
                       px + rmax_all + 8 * th, py - rmax_all - 4 * th, group, rmax_all)
            px += 2 * rmax_all + 22 * th
        # 4. colour key (big, next to the bottom layout) + schedule table (right of everything)
        kx = max(x, px) + 4 * th
        key_h = self._view("BRICK KEY (ITEM NUMBERS)", self.colour_key, kx, -2 * th)
        self._view("COURSE (LAYER) SCHEDULE", self.draw_table, kx + 135 * th, -2 * th)
        yq = -2 * th - (key_h or 40 * th) - 8 * th
        self._view("QUALITY LEGEND", self.quality_legend, kx, yq)
        if self.rings and self.rings.get("rings"):
            self._view("BOTTOM DISH BRICK BOQ (RING x MARK)", self.boq_matrix, kx, yq - 30 * th)
        self.title(0, py - 2 * rmax_all - 20 * th, [
            f"LINING LAYERS & BRICK LAYOUT - {self.job}",
            "Bottom ring layout, course (layer) section, ring plans and course schedule computed from the confirmed design.",
            "Ring closure: nA*(hotA+j)+nB*(hotB+j) = phi*R_hot ; nA*(coldA+j)+nB*(coldB+j) = phi*(R_hot+L).",
            "Flagged rows need a shape not in the brick library or a design decision.",
            f"Generated {datetime.date.today().isoformat()}"], 2 * rmax_all + 40 * th)
        return self.doc


# ============================================================================ ZONE DRAWINGS
class _DetailInto:
    """Lets the GA detail routines (LiningSheet.draw_trl_detail, MHP, taphole set) draw into another drawing
    (a zone drawing): geometry comes from the GA sheet, output goes to the target doc at its own origin."""

    def __init__(self, sheet, doc, axis_x, y_of_zmin):
        self._s, self._d = sheet, doc
        self.th, self.msp, self.views = doc.th, doc.msp, []
        self._frames = [("vertical", axis_x - 44 * doc.th - sheet.rout, y_of_zmin - 6 * doc.th, 0.0, None)]

    def __getattr__(self, k):
        return getattr(self._s, k)

    def layer(self, *a, **k):
        return self._d.layer(*a, **k)

    def text(self, *a, **k):
        return self._d.text(*a, **k)

    def line(self, *a, **k):
        return self._d.line(*a, **k)

    def fill(self, *a, **k):
        return self._d.fill(*a, **k)

    def poly(self, pts, layer, close=False):
        return self._d.poly(pts, layer, close)

    def dim_h(self, x1, x2, y, y_base, v):
        self._d.dim((x1, y), (x2, y), (x1, y_base), 0, v=v)

    def _draw_taphole_set(self, t, side, ang, axis_x, Y):
        z0, z1 = self.zone_z
        for n in self._s._radial_nozzles():
            if n.get("type") != "taphole" or not z0 <= float(n["z"]) <= z1 \
                    or abs(((float(n["angle"]) - ang + 180) % 360) - 180) > 1.0:
                continue
            p_s, p_o, p_e = self._s._radial_axis(n, ang % 360)
            taphole_in_section(self, t, side, axis_x, Y, p_s, p_o, p_e, self._s.shell_t)


class ZoneSheet(LayerSheet):
    """One drawing (page) per zone - Bottom, Lower cone, Barrel, Top cone, Mouth: the zone's vertical section
    (both sides, layers in their quality colours, every course / bottom ring), its dimensions, the brick layout
    of one course (bottom: the ring layout), a course summary and the zone's lines of the BOM."""

    def __init__(self, sheet, courses, rings, nozzles=(), job="", mcol=None, info=None, bom=None):
        super().__init__(sheet, courses, rings, nozzles, job, mcol, info)
        self.bom = bom or {}

    # ------------------------------------------------------------ zones = consecutive bands of one section
    def zones(self):
        out = []
        for bi, b in enumerate(self.s.bands):
            name = str(b.get("section") or b["name"])
            if out and out[-1][0] == name:
                out[-1][1].append(bi)
            else:
                out.append((name, [bi]))
        return out

    def bom_lines(self, name, bis):
        keys = {name} | {str(self.s.bands[b]["name"]) for b in bis}
        return [o for o in self.bom.get("lines", []) if any(k and k in str(o.get("zone", "")) for k in keys)]

    # ------------------------------------------------------------ vertical section of the zone
    def draw_zone_section(self, ox, oy, name, bis):
        s, th, L = self.s, self.th, self.s.L
        I0, I1 = s.band_idx[bis[0]][0], s.band_idx[bis[-1]][1]
        z0, z1 = min(s.band_z(b)[0] for b in bis), max(s.band_z(b)[1] for b in bis)
        bnames = {s.bands[b]["name"] for b in bis}
        rz = 1.0
        for ang in (0.0, 180.0):
            paths, outer = s.boundary_paths(ang)
            rz = max(rz, max(p[0] for p in (outer or paths[0]).run(I0, I1)))
        axis_x = ox + rz + 16 * th
        Y = lambda z: oy + (z - z0)
        rows_by_course = {}
        for r in self.courses:
            if r["band"] in bnames:
                rows_by_course.setdefault(r["course"], []).append(r)
        ends = {}
        zlo, zhi = z0, z1                         # drawn extent (the lining can rise above the band top, e.g. the bottom dish)
        for ang in (0.0, 180.0):
            paths, outer = s.boundary_paths(ang)
            for pth in ([outer] if outer else []) + list(paths):
                zs_ = [q[1] for q in sample(pth.run(I0, I1))]
                zlo, zhi = min(zlo, min(zs_)), max(zhi, max(zs_))
        for side in (1, -1):
            ang_ref = 0.0 if side == 1 else 180.0
            paths, outer = s.boundary_paths(ang_ref)
            T = lambda run: [(axis_x + side * x, Y(z), side * b) for x, z, b in run]
            P = lambda run: [(axis_x + side * x, Y(z)) for x, z in sample(run)]
            if outer:
                self.fill(P(outer.run(I0, I1) + reverse_run(paths[0].run(I0, I1))), 8, "SHELL_FILL")
            for bi in bis:
                i0, i1 = s.band_idx[bi]
                sec = sector_at(s.bands[bi], ang_ref % 360)
                for j in range(1, L + 1):
                    if float(sec["layers"][j - 1]["thickness"] or 0) <= 0:
                        continue
                    self.fill(P(paths[j - 1].run(i0, i1) + reverse_run(paths[j].run(i0, i1))),
                              self.gc(sec["layers"][j - 1]["grade"]), "GRADE_FILL")
            if outer:
                self.msp.add_lwpolyline(T(outer.run(I0, I1)), format="xyb", dxfattribs={"layer": self.layer("SHELL", 8)})
            for k in range(L + 1):
                self.msp.add_lwpolyline(T(paths[k].run(I0, I1)), format="xyb",
                                        dxfattribs={"layer": self.layer("SHELL" if k == 0 else "LINING", 7)})
            # zone limits: cut line from the shell to the hot face at the bottom and the top of the zone
            for e in (0, -1):
                p_out = (outer or paths[0]).run(I0, I1)[e]
                p_hot = paths[L].run(I0, I1)[e]
                if abs(p_out[0] - p_hot[0]) > 1e-6 or abs(p_out[1] - p_hot[1]) > 1e-6:
                    self.line((axis_x + side * p_out[0], Y(p_out[1])), (axis_x + side * p_hot[0], Y(p_hot[1])), "ZONE_LIMIT")
                ends[(side, e)] = (p_out, p_hot)
            R = self.rings or {}
            rb = next((bi for bi in bis if s.bands[bi]["name"] == R.get("band")), None)
            if rb is not None and R.get("rings"):    # bottom rings: joint square to the bottom + ring number
                i0, i1 = s.band_idx[rb]
                crun, hrun = paths[L - 1].run(i0, i1), paths[L].run(i0, i1)
                t = sector_at(s.bands[rb], ang_ref % 360)["layers"][L - 1]["thickness"]
                s0 = s.segs[i0]
                region = sample(crun + reverse_run(hrun))

                def normal(rad, zc):
                    if s0.shape == "arc" and s0.C is not None:
                        dx, dz = s0.C[0] - rad, s0.C[1] - zc
                        nn = math.hypot(dx, dz) or 1.0
                        return dx / nn, dz / nn
                    return 0.0, 1.0
                for ring in R["rings"]:
                    rad = ring["r_out"]
                    zc = z_at_x(crun, rad)
                    if zc is not None and ring is not R["rings"][-1]:
                        dx, dz = normal(rad, zc)
                        xe, ze = _clip_to_region((rad, zc), (rad + dx * t, zc + dz * t), region)
                        self.line((axis_x + side * rad, Y(zc)), (axis_x + side * xe, Y(ze)), "BOTTOM_RINGS")
                    rm = (ring["r_in"] + ring["r_out"]) / 2
                    zm = z_at_x(crun, rm)
                    if zm is not None:
                        dx, dz = normal(rm, zm)
                        self.text(axis_x + side * (rm + dx * 0.5 * t), Y(zm + dz * 0.5 * t), str(ring["ring"]),
                                  min(0.45 * th, 0.6 * (ring["r_out"] - ring["r_in"])), "center", "RING_NO")
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
        self.line((axis_x, Y(zlo) - 2 * th), (axis_x, Y(zhi) + 2 * th), "CENTER")
        # dimensions: zone height (left), elevations, inside (hot-face) and shell diameters at both ends
        xd = axis_x - rz - 4 * th
        self.dim((xd, Y(z0)), (xd, Y(z1)), (xd - 2 * th, Y(z0)), 90, v=z1 - z0)
        for z in (z0, z1):
            self.text(axis_x + rz + 1.0 * th, Y(z), f"EL {z:,.0f}".replace(",", " "), 0.8 * th, "mleft", "ELEVATIONS")
        for e, sgn in ((0, -1), (-1, 1)):
            (o_r, h_r), (o_l, h_l) = ends[(1, e)], ends[(-1, e)]
            if h_r[0] > 1 and h_l[0] > 1:
                yb = Y(zhi if e == -1 else zlo) + sgn * 2.5 * th
                self.dim((axis_x + h_r[0], Y(h_r[1])), (axis_x - h_l[0], Y(h_l[1])), (axis_x, yb), 0, v=h_r[0] + h_l[0])
                self.text(axis_x - h_l[0] - 0.8 * th, yb, "inside dia. (hot face)", 0.6 * th, "mright", "DIMS_TEXT")
            if o_r[0] > 1 and o_l[0] > 1:
                yb = Y(zhi if e == -1 else zlo) + sgn * 5.5 * th
                self.dim((axis_x + o_r[0], Y(o_r[1])), (axis_x - o_l[0], Y(o_l[1])), (axis_x, yb), 0, v=o_r[0] + o_l[0])
                self.text(axis_x - o_l[0] - 0.8 * th, yb, "shell outside dia.", 0.6 * th, "mright", "DIMS_TEXT")
        # layer build-up of every sector of the zone (right of the section)
        lx, ly = axis_x + rz + 9 * th, Y(z1) - th
        self.text(lx, ly, "LINING BUILD-UP (shell -> hot face)", 0.9 * th, layer="BUILD_UP")
        for bi in bis:
            for sec in s.bands[bi]["sectors"]:
                ly -= 1.5 * th
                lays = [f"{self.s.layers[j]} {l['grade']} {float(l['thickness']):g}" for j, l in enumerate(sec["layers"])
                        if float(l["thickness"] or 0) > 0]
                self.text(lx, ly, f"{s.bands[bi]['name']} {sec.get('from', 0):g}-{sec.get('to', 360):g}°"
                          f" {sec.get('name', '')}: " + " + ".join(lays), 0.65 * th, layer="BUILD_UP")
        done = set()
        for r in self.courses:
            if r["band"] not in bnames or (r["band"], r["sector"]) in done or not r.get("pair"):
                continue
            done.add((r["band"], r["sector"]))
            ly -= 1.5 * th
            self._balls_then_text(lx, ly, [r["A"], r["B"]], 0.65 * th,
                                  f"{r['sector']}° {r.get('zone', '')}: {r['grade']}, L {r['L']:g} -> pair {r['pair']}",
                                  "BUILD_UP", right=False)
        # TRL detail, same routines as the GA: permanent bricks, length tags, course numbers, mark call-outs,
        # MHP surround rings, taphole set; MHP plug drawn in place (only what is entered for this job)
        det = getattr(self, "detail", None)
        if det:
            try:
                dv = _DetailInto(s, self, axis_x, Y(s.zmin))
                dv.zone_z = (z0, z1)
                LiningSheet.draw_trl_detail(dv, self.courses, self.rings, det.get("mhp"), det.get("perm"), det.get("th_set"),
                                            only_bands=set(bis), ring_numbers=False)
                m = det.get("mhp")
                rb_ = next((bi for bi in bis if s.bands[bi]["name"] == (self.rings or {}).get("band")), None)
                if m and rb_ is not None:
                    for n in s._stirring():
                        cnt, rp = int(n["count"]), float(n["pcd"]) / 2
                        angs = [(float(n.get("start_angle") or 0) + k * 360.0 / cnt) % 360 for k in range(cnt)]
                        for side, a_ in ((1, 0.0), (-1, 180.0)):
                            if min(abs(((e - a_ + 180) % 360) - 180) for e in angs) > 1.0:
                                continue
                            paths_, _ = s.boundary_paths(a_)
                            i0_, i1_ = s.band_idx[rb_]
                            mhp_in_section(dv, m, side, axis_x, Y, paths_[L].run(i0_, i1_), rp, True, dv.dim_h)
            except Exception as e:                          # the zone drawing without the extra detail is still made
                self.text(axis_x, Y(zlo) - 11 * th, f"(detail not drawn: {type(e).__name__}: {e})", 0.6 * th, "center")
        self.text(axis_x, Y(zlo) - 9 * th, f"{name.upper()} - VERTICAL SECTION (right 0° / left 180°), "
                                          f"EL {z0:.0f} - {z1:.0f}, every course / ring numbered", 1.0 * th, "center")
        return axis_x, rz, Y(zlo), Y(zhi)

    # ------------------------------------------------------------ course summary of the zone
    def draw_zone_courses(self, ox, oy, bnames):
        th = 0.7 * self.th
        groups = []
        for r in sorted((r for r in self.courses if r["band"] in bnames), key=lambda r: (r["band"], r["sector"], r["course"])):
            key = (r["band"], r["sector"], r.get("zone", ""), r["grade"], r["L"], r.get("pair"), r.get("A"), r.get("B"))
            if groups and groups[-1]["key"] == key and groups[-1]["last"] == r["course"] - 1:
                g = groups[-1]
            else:
                g = {"key": key, "first": r["course"], "nA": 0, "nB": 0, "cut": 0, "flag": 0}
                groups.append(g)
            g["last"] = r["course"]
            g["nA"] += r["nA"] or 0
            g["nB"] += r["nB"] or 0
            g["cut"] += r["nCut"] or 0
            g["flag"] += 1 if r.get("flag") else 0
        if not groups:
            return 0
        cols = [("Courses", 6), ("Band", 10), ("Sector", 6), ("Zone", 11), ("Grade", 8), ("L", 4), ("Pair", 9),
                ("A", 6), ("Pcs A", 4), ("B", 6), ("Pcs B", 4), ("Cut", 3), ("Flagged", 4)]
        self.text(ox, oy + 2.2 * th, "COURSE SUMMARY (consecutive courses with the same bricks; every course in "
                                     "course_schedule.csv and on the layers drawing)", 1.3 * th, layer="ZONE_TABLE")
        y, x = oy, ox
        for c, w in cols:
            self.text(x, y, c, th, layer="ZONE_TABLE")
            x += w * th * 1.9
        for g in groups:
            band, sector, zone, grade, L, pair, A, B = g["key"]
            y -= 1.5 * th
            vals = [f"{g['first']}-{g['last']}" if g["last"] != g["first"] else str(g["first"]), band, sector, zone, grade,
                    f"{L:g}", pair or "-", A or "", g["nA"] or "", B or "", g["nB"] or "", g["cut"] or "", g["flag"] or ""]
            x = ox
            for (c, w), v in zip(cols, vals):
                self.text(x, y, str(v)[:int(w * 1.9)], th, layer="ZONE_TABLE_FLAG" if (c == "Flagged" and v) else "ZONE_TABLE")
                x += w * th * 1.9
        self.layer("ZONE_TABLE_FLAG", CUT_ACI)
        return oy - y + 4 * th

    # ------------------------------------------------------------ BOM of the zone
    def draw_zone_bom(self, ox, oy, lines):
        th = 0.7 * self.th
        cols = [("Group", 12), ("Zone", 17), ("Grade", 8), ("Mark", 9), ("Size (mm)", 14), ("Qty", 4), ("Order qty", 5),
                ("Tonnes", 5)]
        self.text(ox, oy + 2.2 * th, "ZONE BOM (lines of the full BOM that belong to this zone)", 1.3 * th, layer="ZONE_BOM")
        y, x = oy, ox
        for c, w in cols:
            self.text(x, y, c, th, layer="ZONE_BOM")
            x += w * th * 1.9
        tot_t, tot_q = 0.0, 0
        for o in lines:
            y -= 1.5 * th
            vals = [o.get("group", ""), o.get("zone", ""), o.get("grade", ""), o.get("mark", ""), o.get("size", ""),
                    "" if o.get("qty") is None else o["qty"], "" if o.get("order_qty") is None else o["order_qty"],
                    "" if o.get("total_t") is None else f"{o['total_t']:.2f}"]
            x = ox
            for (c, w), v in zip(cols, vals):
                self.text(x, y, str(v)[:int(w * 1.9)], th, layer="ZONE_BOM")
                x += w * th * 1.9
            tot_t += float(o.get("total_t") or 0)
            tot_q += int(o.get("order_qty") or 0)
        y -= 1.8 * th
        self.text(ox, y, f"ZONE TOTAL: {tot_q} pieces to order, {tot_t:.2f} t"
                         + ("" if lines else "  (no BOM line found for this zone)"), 1.1 * th, layer="ZONE_BOM")
        return oy - y + 4 * th

    # ------------------------------------------------------------ one page per zone
    def draw_zone(self, ox, oy, name, bis):
        s, th = self.s, self.th
        bnames = {s.bands[b]["name"] for b in bis}
        axis_x, rz, yb, yt = self.draw_zone_section(ox, oy, name, bis)
        px = axis_x + rz + 75 * th                 # right of the section and its build-up labels
        R = self.rings or {}
        if R.get("rings") and R.get("band") in bnames:
            rmax = R["rings"][-1]["r_out"]
            self.draw_bottom(px + 40 * th + rmax, (yb + yt) / 2)
            right = px + 2 * rmax + 85 * th
        else:
            rows = [r for r in self.courses if r["band"] in bnames and r.get("pair") and not r.get("flag")]
            rows = rows or [r for r in self.courses if r["band"] in bnames and r.get("pair")]
            right = px
            if rows:
                mid_no = rows[len(rows) // 2]["course"]
                group = [r for r in self.courses if r["course"] == mid_no]
                self.draw_ring_plan(px + rz, (yb + yt) / 2, group, rz)
                self.text(px + rz, (yb + yt) / 2 + rz + 3 * th, f"BRICK LAYOUT OF COURSE {mid_no} (plan)", 1.0 * th, "center")
                right = px + 2 * rz + 30 * th
        ty = min(yb - 14 * th, (yb + yt) / 2 - rz - 10 * th)
        h = self.draw_zone_courses(ox, ty, bnames)
        self.draw_zone_bom(ox if not h else ox + 155 * 0.7 * th, ty, self.bom_lines(name, bis))
        self.text(ox, yt + 10 * th, f"ZONE DRAWING - {name.upper()}  ({self.job})", 1.6 * th)
        return right

    def build(self):
        ox = 0.0
        zs = self.zones()
        for k, (name, bis) in enumerate(zs, 1):
            right = self._view(f"ZONE {k}/{len(zs)} - {name.upper()}", self.draw_zone, ox, 0.0, name, bis)
            ox = max(right or 0, ox) + 40 * self.th
        return self.doc


# ============================================================================ DETAIL BRICK SHAPES
_RE_WALL = re.compile(r"^\s*([\d.]+)\s*x\s*([\d.]+)\s*/\s*([\d.]+)\s*x\s*([\d.]+)\s*$")
_RE_RECT = re.compile(r"^\s*([\d.]+)\s*x\s*([\d.]+)\s*x\s*([\d.]+)\s*$")
_RE_KEY = re.compile(r"^\s*D\s*([\d.]+)\s*/\s*([\d.]+)\s*x\s*([\d.]+)\s*$")


class BottomLayoutSheet(ZoneSheet):
    """CIRCULAR BOTTOM BRICK LAYOUT in TRL's format (REF sheet 3): bottom section with every ring numbered, the
    MHPs, permanent bricks and dimensions; plan with every bottom brick; ring x mark BOQ - one sheet."""

    def draw_all(self, rb):
        s, th, R = self.s, self.th, self.rings
        axis_x, rz, ylo, yhi = self.draw_zone_section(0.0, 0.0, s.bands[rb]["name"], [rb])
        t = float(sector_at(s.bands[rb], 0.0)["layers"][s.L - 1]["thickness"] or 0)
        rc = R.get("cold_sphere_radius")
        notes = []
        if rc:
            notes.append(f"COLD FACE (WORKING LINING) R{float(rc):.0f}")
            if t:
                notes.append(f"HOT FACE R{float(rc) - t:.0f}   WORKING LINING {t:g} MM")
        if R.get("key"):
            km = R["key"]["mark"]
            notes.append(("KEY" if km.upper() == "KEY" else f"KEY {km}") + f" - RADIUS {float(R['key']['r']):g} MM")
        for k, txt in enumerate(notes):
            self.text(axis_x - rz, yhi + (4 + 1.6 * (len(notes) - k)) * th, txt, 0.9 * th, layer="BOTTOM_NOTES")
        rmax = float(R["rings"][-1]["r_out"])
        self.boq_matrix(axis_x + rz + 50 * th, yhi + 2 * th)        # row 1: section | ring x mark BOQ
        cx, cy = 34 * th + rmax, ylo - 22 * th - rmax                # row 2: plan with every brick + ring table
        self.draw_bottom(cx, cy)

    def build(self):
        R = self.rings or {}
        rb = next((bi for bi, b in enumerate(self.s.bands) if b["name"] == R.get("band")), None)
        if rb is None or not R.get("rings"):
            return self.doc
        self._view(f"CIRCULAR BOTTOM BRICK LAYOUT - {self.s.bands[rb]['name'].upper()}", self.draw_all, rb)
        return self.doc


def shape_marks(bom, lib):
    """Every brick mark of the design (from the BOM), once, with its dimensions, quantity, grades and zones."""
    libd = {b["mark"]: b for b in (lib or {}).get("bricks", [])}
    out = {}
    for o in (bom or {}).get("lines", []):
        mark = str(o.get("mark") or "").strip()
        if not mark or mark.endswith("(cut)") or str(o.get("group", "")).startswith("Monolithic") or o.get("qty") is None:
            continue
        m = out.setdefault(mark, {"mark": mark, "size": o.get("size") or "", "family": o.get("family") or "",
                                  "qty": 0, "order_qty": 0, "grades": [], "zones": [], "vol_bom": o.get("unit_vol_dm3"),
                                  "unit_wt": o.get("unit_wt_kg"), "group": o.get("group", ""), "lib": libd.get(mark)})
        cut = next((x for x in bom["lines"] if x.get("mark") == mark + " (cut)" and x.get("zone") == o.get("zone")), None)
        m["qty"] += int(o.get("qty") or 0) + int((cut or {}).get("qty") or 0)
        m["order_qty"] += int(o.get("order_qty") or 0) + int((cut or {}).get("order_qty") or 0)
        for k, v in (("grades", o.get("grade")), ("zones", o.get("zone"))):
            if v and v not in m[k]:
                m[k].append(v)
    return list(out.values())


def shape_geometry(m):
    """Decide how a mark is drawn and with which dimensions: wall wedge, bottom wedge, centre key, rectangular."""
    size, lib = m["size"], (m.get("lib") or {})
    d = lib.get("dimensions_mm") or {}
    if lib.get("shape_family") == "bottom_dish_wedge" and all(k in d for k in ("A", "B", "C", "D", "overall_length")):
        return {"kind": "bottom_wedge", "L": float(d["overall_length"]), "ft": float(d["face_width_top"]),
                "fb": float(d["face_width_bottom"]), "A": float(d["A"]), "B": float(d["B"]), "C": float(d["C"]), "D": float(d["D"])}
    g = _RE_KEY.match(size)
    if g:
        return {"kind": "key", "d1": float(g[1]), "d2": float(g[2]), "L": float(g[3])}
    g = _RE_WALL.match(size)
    if g:
        return {"kind": "wall_wedge", "L": float(g[1]), "wc": float(g[2]), "wh": float(g[3]), "h": float(g[4])}
    g = _RE_RECT.match(size)
    if g:
        return {"kind": "rect", "w": float(g[1]), "t": float(g[2]), "h": float(g[3])}
    return {"kind": "unknown"}


def shape_volume(g):
    """Volume in dm3 from the drawn dimensions (to compare with the BOM's unit volume)."""
    k = g["kind"]
    if k == "wall_wedge":
        return (g["wc"] + g["wh"]) / 2 * g["L"] * g["h"] / 1e6
    if k == "rect":
        return g["w"] * g["t"] * g["h"] / 1e6
    if k == "key":
        return math.pi * g["L"] / 12 * (g["d1"] ** 2 + g["d1"] * g["d2"] + g["d2"] ** 2) / 1e6
    if k == "bottom_wedge":
        ah, ac = (g["A"] + g["B"]) / 2 * g["ft"], (g["C"] + g["D"]) / 2 * g["fb"]
        return g["L"] * (ah + ac) / 2 / 1e6
    return None


class ShapeSheet(Doc):
    """DETAIL BRICK SHAPES in TRL's format: for every brick mark of the design a small dimensioned drawing
    (side view + face view(s), red dimensions, centre line, mark name underlined) - 8 marks per A3 sheet."""
    COLS, ROWS = 4, 2
    CELL_W = 1350.0

    def __init__(self, bom, lib, job=""):
        super().__init__(28.0)
        self.marks = shape_marks(bom, lib)
        self.job = job

    # ------------------------------------------------------------ red dimension like TRL's sheets
    def rdim(self, p1, p2, base, angle, text=None):
        th = self.th
        v = math.dist(p1, p2) if angle not in (0, 90) else abs((p2[0] - p1[0]) if angle == 0 else (p2[1] - p1[1]))
        ov = {"dimtxt": th * 0.85, "dimasz": th * 0.6, "dimexe": th * 0.35, "dimexo": th * 0.25, "dimgap": th * 0.2,
              "dimdec": decimals(v), "dimlfac": 1, "dimclrd": 1, "dimclre": 5, "dimclrt": 7, "dimtad": 1}
        kw = {"text": text} if text else {}
        self.msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, override=ov,
                                dxfattribs={"layer": self.layer("SHAPE_DIMS", 1)}, **kw).render()

    def cline(self, p, q):
        self.layer("SHAPE_CENTER", 6)
        self.msp.add_line(p, q, dxfattribs={"layer": "SHAPE_CENTER", "linetype": "CENTER", "ltscale": 0.4})

    def trap(self, x, y, wb, wt, hgt, lay="SHAPE_OUTLINE"):
        """Symmetric trapezoid, centre x, bottom y: bottom width wb, top width wt, height hgt."""
        pts = [(x - wb / 2, y), (x + wb / 2, y), (x + wt / 2, y + hgt), (x - wt / 2, y + hgt)]
        self.poly(pts, lay)
        return pts

    # ------------------------------------------------------------ one mark
    def draw_mark(self, ox, oy, m, cell_h):
        th, g = self.th, shape_geometry(m)
        self.layer("SHAPE_OUTLINE", 7)
        y0 = oy + 330                             # views stand on this line; texts below it
        k = g["kind"]
        if k == "wall_wedge":                     # side view (course height x length) + face view (cold / hot face)
            L, wc, wh, h = g["L"], g["wc"], g["wh"], g["h"]
            x1 = ox + 180
            self.poly([(x1, y0), (x1 + h, y0), (x1 + h, y0 + L), (x1, y0 + L)], "SHAPE_OUTLINE")
            self.rdim((x1, y0 + L), (x1 + h, y0 + L), (x1, y0 + L + 2.2 * th), 0)
            self.rdim((x1 + h, y0), (x1 + h, y0 + L), (x1 + h + 2.2 * th, y0), 90)
            xc = x1 + h + 260 + max(wc, wh) / 2
            self.trap(xc, y0, wh, wc, L)
            self.cline((xc, y0 - 0.8 * th), (xc, y0 + L + 0.8 * th))
            self.rdim((xc - wc / 2, y0 + L), (xc + wc / 2, y0 + L), (xc, y0 + L + 2.2 * th), 0)
            self.rdim((xc - wh / 2, y0), (xc + wh / 2, y0), (xc, y0 - 2.2 * th), 0)
            self.text(xc, y0 + L + 4.4 * th, "cold face", 0.6 * th, "center", "SHAPE_NOTE")
            self.text(xc, y0 - 4.4 * th, "hot face", 0.6 * th, "center", "SHAPE_NOTE")
        elif k == "bottom_wedge":                 # radial section + hot face + cold face
            L, ft, fb = g["L"], g["ft"], g["fb"]
            xs = ox + 150 + fb / 2
            self.trap(xs, y0, fb, ft, L)
            self.cline((xs, y0 - 0.8 * th), (xs, y0 + L + 0.8 * th))
            self.rdim((xs - ft / 2, y0 + L), (xs + ft / 2, y0 + L), (xs, y0 + L + 2.2 * th), 0)
            self.rdim((xs - fb / 2, y0), (xs + fb / 2, y0), (xs, y0 - 2.2 * th), 0)
            self.rdim((xs + fb / 2, y0), (xs + ft / 2, y0 + L), (xs + fb / 2 + 2.6 * th, y0), 90)
            for j, (wo, wi, dep, lab) in enumerate(((g["A"], g["B"], ft, "hot face"), (g["C"], g["D"], fb, "cold face"))):
                xc = xs + fb / 2 + 330 + j * 330 + max(wo, wi) / 2
                yb = y0 + L / 2 - dep / 2 + (j * 0)
                self.trap(xc, yb, wi, wo, dep)
                self.rdim((xc - wo / 2, yb + dep), (xc + wo / 2, yb + dep), (xc, yb + dep + 2.2 * th), 0)
                self.rdim((xc - wi / 2, yb), (xc + wi / 2, yb), (xc, yb - 2.2 * th), 0)
                self.text(xc, yb + dep + 4.6 * th, lab, 0.6 * th, "center", "SHAPE_NOTE")
        elif k == "key":                          # elevation + plan
            L, d1, d2 = g["L"], g["d1"], g["d2"]
            xc = ox + 150 + d2 / 2
            self.trap(xc, y0, d2, d1, L)
            self.cline((xc, y0 - 0.8 * th), (xc, y0 + L + 0.8 * th))
            self.rdim((xc - d1 / 2, y0 + L), (xc + d1 / 2, y0 + L), (xc, y0 + L + 2.2 * th), 0, f"Ø{d1:g}")
            self.rdim((xc - d2 / 2, y0), (xc + d2 / 2, y0), (xc, y0 - 2.2 * th), 0, f"Ø{d2:g}")
            self.rdim((xc + d2 / 2, y0), (xc + d1 / 2, y0 + L), (xc + d2 / 2 + 2.6 * th, y0), 90)
            pc = (xc + d2 / 2 + 220 + d2 / 2, y0 + L / 2)
            self.msp.add_circle(pc, d2 / 2, dxfattribs={"layer": "SHAPE_OUTLINE"})
            self.msp.add_circle(pc, d1 / 2, dxfattribs={"layer": self.layer("SHAPE_HIDDEN", 8)})
            self.cline((pc[0] - d2 / 2 - 40, pc[1]), (pc[0] + d2 / 2 + 40, pc[1]))
            self.cline((pc[0], pc[1] - d2 / 2 - 40), (pc[0], pc[1] + d2 / 2 + 40))
            self.text(pc[0], pc[1] + d2 / 2 + 1.5 * th, "plan", 0.6 * th, "center", "SHAPE_NOTE")
        elif k == "rect":                         # face (width x height) + side (thickness x height)
            w, t, h = g["w"], g["t"], g["h"]
            x1 = ox + 200
            self.poly([(x1, y0), (x1 + w, y0), (x1 + w, y0 + h), (x1, y0 + h)], "SHAPE_OUTLINE")
            self.rdim((x1, y0 + h), (x1 + w, y0 + h), (x1, y0 + h + 2.2 * th), 0)
            self.rdim((x1, y0), (x1, y0 + h), (x1 - 2.2 * th, y0), 90)
            x2 = x1 + w + 250
            self.poly([(x2, y0), (x2 + t, y0), (x2 + t, y0 + h), (x2, y0 + h)], "SHAPE_OUTLINE")
            self.rdim((x2, y0 + h), (x2 + t, y0 + h), (x2, y0 + h + 2.2 * th), 0)
            self.text(x1 + w / 2, y0 - 2 * th, "face", 0.6 * th, "center", "SHAPE_NOTE")
            self.text(x2 + t / 2, y0 - 2 * th, "side (thickness)", 0.6 * th, "center", "SHAPE_NOTE")
        else:
            self.text(ox + 100, y0 + 300, f"No drawing rule for size '{m['size']}' - shape dimensions needed", 0.8 * th,
                      layer="SHAPE_NOTE")
        # mark name (underlined, like TRL) + data
        tx, ty = ox + 100, oy + 210
        self.text(tx, ty, m["mark"], 1.7 * th, layer="SHAPE_MARK")
        self.line((tx, ty - 0.35 * th), (tx + 1.05 * th * len(m["mark"]), ty - 0.35 * th), "SHAPE_MARK")
        v = shape_volume(g)
        vb = m.get("vol_bom")
        vtxt = f"vol {v:.2f} dm3" if v else "vol -"
        if v and vb:
            dv = abs(v - vb) / vb if vb else 0
            vtxt += f" (BOM {vb:.2f}{'' if dv <= 0.02 else ' - CHECK'})"
        wt = f" · {m['unit_wt']:.1f} kg" if m.get("unit_wt") else ""
        self.text(tx, ty - 1.6 * th, f"{', '.join(m['grades'])} · {m['size']}"[:60], 0.65 * th, layer="SHAPE_NOTE")
        self.text(tx, ty - 2.6 * th, f"{vtxt}{wt}"[:60], 0.65 * th,
                  layer="SHAPE_CHECK" if "CHECK" in vtxt else "SHAPE_NOTE")
        self.text(tx, ty - 3.6 * th, f"qty {m['qty']:,} · order {m['order_qty']:,}".replace(",", " ")
                  + f" · {', '.join(m['zones'])}"[:40], 0.65 * th, layer="SHAPE_NOTE")
        self.layer("SHAPE_CHECK", 1)
        self.layer("SHAPE_MARK", 7)

    # ------------------------------------------------------------ one sheet = 8 marks
    def draw_page(self, ox, oy, marks, cell_h):
        th = self.th
        W, H = self.COLS * self.CELL_W, self.ROWS * cell_h
        self.poly([(ox, oy - H), (ox + W, oy - H), (ox + W, oy + 3 * th), (ox, oy + 3 * th)], "SHAPE_FRAME")
        self.text(ox + 2 * th, oy - 1.2 * th, f"DETAIL BRICK SHAPES - {self.job}", 1.5 * th, layer="SHAPE_MARK")
        sx = ox + W - 19 * th                    # stamp, as on TRL's sheets
        self.poly([(sx, oy - 2.2 * th), (ox + W - th, oy - 2.2 * th), (ox + W - th, oy + 1.8 * th), (sx, oy + 1.8 * th)],
                  "SHAPE_FRAME")
        self.text((sx + ox + W - th) / 2, oy - 0.2 * th, "FOR CUSTOMER APPROVAL", 0.9 * th, "center", "SHAPE_MARK")
        for i, m in enumerate(marks):
            c, r = i % self.COLS, i // self.COLS
            self.draw_mark(ox + c * self.CELL_W, oy - (r + 1) * cell_h, m, cell_h)
        if getattr(self, "skipped", None) and marks is self._last:
            self.text(ox + 2 * th, oy - H + 2.4 * th, "Not drawn on these sheets (separate assembly drawings): "
                      + ", ".join(self.skipped), 0.6 * th, layer="SHAPE_NOTE")
        self.text(ox + 2 * th, oy - H + 1.2 * th, "All dimensions in mm. Red = dimensions. vol = from the drawn dimensions; "
                                                  "BOM = unit volume used in the BOM (CHECK if they differ by more than 2 %).",
                  0.6 * th, layer="SHAPE_NOTE")

    def build(self):
        order = {"Working lining - bottom": 0, "Working lining - wall": 1, "Permanent lining": 2}
        marks = sorted(self.marks, key=lambda m: (order.get(m["group"], 3), m["mark"]))
        self.skipped = [m["mark"] for m in marks if shape_geometry(m)["kind"] == "unknown"]   # assemblies
        marks = [m for m in marks if m["mark"] not in self.skipped]
        per = self.COLS * self.ROWS
        Lmax = 0.0
        for m in marks:
            g = shape_geometry(m)
            Lmax = max(Lmax, g.get("L") or g.get("h") or 300)
        cell_h = Lmax + 650
        pages = [marks[i:i + per] for i in range(0, len(marks), per)] or [[]]
        self._last = pages[-1]
        for p, chunk in enumerate(pages):
            oy = -p * (self.ROWS * cell_h + 600)
            self._view(f"DETAIL BRICK SHAPES {p + 1}/{len(pages)} - " + ", ".join(m["mark"] for m in chunk[:4])
                       + (" ..." if len(chunk) > 4 else ""), self.draw_page, 0.0, oy, chunk, cell_h)
        return self.doc


def _stir_label(n):
    """Short label of a stirring element on the GA: the MHP mark when given, else STIR."""
    lab = str(n.get("label") or "")
    return "STIR" if not lab or lab.lower().startswith("stirring") else lab


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
