"""
Assembly drawings in TRL Krosaki's format (FR-03.1 / FR-06.4):

  MhpSheet  - MHP (multi-hole purging plug) & MHP seating block, dummy brick for MHP, and the
              MHP position plan on the bottom (PCD, pitch angles, ring crossed). Laid out like the
              TRL sheet "MHP & MHP SEATING BLOCK FOR 160T LD CONVERTER": hatched plug elevation with
              pipes, metal case and connection pipe, end-face plan with the pipe grid, red dimensions,
              underlined names with the volume, quality legend, "FOR CUSTOMER APPROVAL".

Every value comes from the job inputs (step 12 MHP inputs, stirring count / PCD from step 2, bottom
rings from the design). Volumes are computed from the drawn dimensions (prismatoid / ring formula).
"""
import math

from .drawings import Doc, decimals

MHP_ACI = 1          # red hatch (TRL draws the MHP refractory hatched red)
SEAT_ACI = 5         # blue (TRL MGD seating block)
DUMMY_ACI = 8


def _num(v, d=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def mhp_inputs(raw):
    """Normalises the MHP inputs sent by the UI. Returns None when the plug dimensions are missing."""
    raw = raw or {}
    m = {k: _num(raw.get(k)) for k in ("L", "hotW", "hotA", "hotB", "coldW", "coldA", "coldB", "nPipes", "pipeOD",
                                        "pipeID", "caseH", "connL", "connOD", "connID", "dens", "seatOD", "seatID",
                                        "seatH", "seatDens", "dummyL", "dummyColdW", "dummyColdA", "dummyColdB",
                                        "dummyQty", "dummyDens", "startAng")}
    for k in ("mark", "grade", "seatMark", "seatGrade", "dummyMark", "dummyGrade"):
        m[k] = str(raw.get(k) or "").strip()
    if not m["mark"] or not all(m[k] and m[k] > 0 for k in ("L", "hotW", "hotA", "hotB", "coldW", "coldA", "coldB")):
        return None                                    # nothing is assumed: no mark or size -> no MHP drawing
    if not (m["seatMark"] and m["seatOD"] and m["seatH"]):
        m["seatOD"] = m["seatH"] = None                # seating block drawn only when entered
    if not (m["dummyMark"] and m["dummyL"]):
        m["dummyL"] = None                             # dummy brick drawn only when entered
    return m


def _trap_area(a, b, w):
    return (a + b) / 2.0 * w


def plug_volume_dm3(L, hot, cold):
    """Prismatoid volume of a plug whose end faces are trapezoids hot=(a, b, w), cold=(a, b, w)."""
    mid = tuple((h + c) / 2.0 for h, c in zip(hot, cold))
    return L / 6.0 * (_trap_area(*hot) + 4 * _trap_area(*mid) + _trap_area(*cold)) / 1e6


def ring_volume_dm3(od, idia, h):
    return math.pi / 4.0 * (od ** 2 - idia ** 2) * h / 1e6


class MhpSheet(Doc):
    """MHP & seating block, dummy brick for MHP, MHP position plan - one page each."""

    def __init__(self, mhp, stirring=None, rings=None, job="", vessel_name=""):
        super().__init__(14.0)
        self.m = mhp
        self.stir = stirring or {}
        self.rings = rings or {}
        self.job = job
        self.vessel_name = vessel_name

    # ------------------------------------------------------------ TRL style primitives
    def rdim(self, p1, p2, base, angle, text=None, h=None, k=1.0):
        """Red dimension; k = drawing scale of the view (2.0 for a 2:1 detail) - the true size is shown."""
        th = h or self.th
        if angle == 0:
            v = abs(p2[0] - p1[0])
        elif angle == 90:
            v = abs(p2[1] - p1[1])
        else:
            v = math.dist(p1, p2)
        ov = {"dimtxt": th, "dimasz": th * 0.75, "dimexe": th * 0.4, "dimexo": th * 0.3, "dimgap": th * 0.25,
              "dimdec": decimals(v / k), "dimlfac": 1.0 / k, "dimclrd": 1, "dimclre": 5, "dimclrt": 7, "dimtad": 1}
        kw = {"text": text} if text else {}
        self.msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, override=ov,
                                dxfattribs={"layer": self.layer("MHP_DIMS", 1)}, **kw).render()

    def cline(self, p, q, scale=0.3):
        self.layer("MHP_CENTER", 6)
        self.msp.add_line(p, q, dxfattribs={"layer": "MHP_CENTER", "linetype": "CENTER", "ltscale": scale})

    def hidden(self, p, q, scale=0.15):
        self.layer("MHP_HIDDEN", 1)
        self.msp.add_line(p, q, dxfattribs={"layer": "MHP_HIDDEN", "linetype": "DASHED", "ltscale": scale})

    def hatch(self, pts, aci, pattern="ANSI31", scale=1.2, layer="MHP_HATCH"):
        h = self.msp.add_hatch(color=aci, dxfattribs={"layer": self.layer(layer, aci)})
        h.set_pattern_fill(pattern, color=aci, scale=scale)
        h.paths.add_polyline_path([(p[0], p[1]) for p in pts], is_closed=True)

    def utext(self, x, y, s, h, align="left", layer="MHP_TEXT"):
        """Underlined text, like the names on TRL's sheets."""
        self.text(x, y, s, h, align, layer)
        w = 0.9 * h * len(s)
        x0 = x - w / 2 if align == "center" else x
        self.line((x0, y - 0.3 * h), (x0 + w, y - 0.3 * h), layer)

    def leader(self, p_from, p_to, label, h):
        self.line(p_from, p_to, self.layer("MHP_LEADER", 1))
        self.line(p_to, (p_to[0] + 0.62 * h * max(len(s) for s in label.split("\n")), p_to[1]), "MHP_LEADER")
        for k, s in enumerate(label.split("\n")):
            self.text(p_to[0] + 0.2 * h, p_to[1] + 0.35 * h - k * 1.35 * h - (0 if k == 0 else 0), s, h, layer="MHP_TEXT")

    def frame_stamp(self, x0, y0, x1, y1, title):
        th = self.th
        self.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], self.layer("MHP_FRAME", 7))
        sw = 19 * th
        self.poly([(x1 - sw - th, y1 - 4.2 * th), (x1 - th, y1 - 4.2 * th), (x1 - th, y1 - th), (x1 - sw - th, y1 - th)],
                  "MHP_FRAME")
        self.text(x1 - th - sw / 2, y1 - 2.6 * th, "FOR CUSTOMER APPROVAL", 1.05 * th, "center", "MHP_TEXT")
        self.text(x0 + 1.5 * th, y1 - 2.6 * th, title, 1.4 * th, "mleft", "MHP_TEXT")

    def legend(self, x, y, items):
        th = self.th
        self.utext(x, y, "QUALITY LEGEND", 1.5 * th)
        for k, (aci, name, pattern) in enumerate(items):
            yy = y - (k + 1) * 3.2 * th
            box = [(x, yy - 0.9 * th), (x + 5 * th, yy - 0.9 * th), (x + 5 * th, yy + 0.9 * th), (x, yy + 0.9 * th)]
            if pattern:
                self.hatch(box, aci, scale=0.6, layer="MHP_LEGEND")
            else:
                self.fill(box, aci, "MHP_LEGEND")
            self.poly(box, "MHP_OUTLINE")
            self.text(x + 6 * th, yy, name or "-", 1.3 * th, "mleft", "MHP_TEXT")

    # ------------------------------------------------------------ geometry helpers
    def end_face(self, cx, cy, w, a, b, k=1.0):
        """End face of the plug as seen from the top: trapezoid, depth w along x, side a (left) and b (right)."""
        w, a, b = w * k, a * k, b * k
        return [(cx - w / 2, cy - a / 2), (cx + w / 2, cy - b / 2), (cx + w / 2, cy + b / 2), (cx - w / 2, cy + a / 2)]

    def pipe_grid(self, n):
        n = int(n or 0)
        if n <= 0:
            return 0, 0
        rows = max(1, int(round(math.sqrt(n / 1.5))))
        while n % rows and rows > 1:
            rows -= 1
        return n // rows, rows                      # columns, rows

    # ------------------------------------------------------------ page 1: MHP & seating block
    def draw_mhp(self, ox, oy):
        m = self.m
        L, hw, cw = m["L"], m["hotW"], m["coldW"]
        self.th = th = max(14.0, (L + (m["caseH"] or 0) + (m["connL"] or 0) + 1050) / 105)
        caseH, connL = m["caseH"] or 0, m["connL"] or 0
        W = 1800.0
        H = L + caseH + connL + 1250
        self.frame_stamp(ox, oy, ox + W, oy + H, m["mark"] + (f" & {m['seatMark']}" if m["seatOD"] else ""))
        # ---- elevation (refractory body on top of the metal case and the connection pipe)
        xc = ox + 380
        y_pipe0 = oy + 260
        y_case0 = y_pipe0 + connL
        y_body0 = y_case0 + caseH
        y_top = y_body0 + L
        body = [(xc - cw / 2, y_body0), (xc + cw / 2, y_body0), (xc + hw / 2, y_top), (xc - hw / 2, y_top)]
        self.hatch(body, MHP_ACI, scale=1.5)
        self.poly(body, self.layer("MHP_OUTLINE", 7))
        cols, rows = self.pipe_grid(m["nPipes"])
        if cols:                                        # pipe rows seen from the side (dashed)
            pitch = min(hw, cw) * 0.55 / max(cols - 1, 1)
            for k in range(cols):
                dx = (k - (cols - 1) / 2) * pitch
                self.hidden((xc + dx, y_body0), (xc + dx, y_top))
        if caseH:
            case = [(xc - cw / 2, y_case0), (xc + cw / 2, y_case0), (xc + cw / 2, y_body0), (xc - cw / 2, y_body0)]
            self.hatch(case, 8, "ANSI32", 1.0)
            self.poly(case, "MHP_OUTLINE")
            self.leader((xc + cw / 4, y_case0 + caseH / 2), (xc + cw / 2 + 120, y_case0 - 25), "METAL CASE", 1.2 * th)
            self.rdim((xc - cw / 2, y_case0), (xc - cw / 2, y_body0), (xc - cw / 2 - 70, 0), 90, h=1.2 * th)
        if connL and m["connOD"]:
            po = m["connOD"]
            pipe = [(xc - po / 2, y_pipe0), (xc + po / 2, y_pipe0), (xc + po / 2, y_case0), (xc - po / 2, y_case0)]
            self.poly(pipe, "MHP_OUTLINE")
            if m["connID"]:
                for s in (-1, 1):
                    self.line((xc + s * m["connID"] / 2, y_pipe0), (xc + s * m["connID"] / 2, y_case0), "MHP_OUTLINE")
            self.rdim((xc - cw / 2, y_pipe0), (xc - cw / 2, y_case0), (xc - cw / 2 - 70, 0), 90, h=1.2 * th)
            self.leader((xc + po / 2, y_pipe0 + connL * 0.4), (xc + 150, y_pipe0 + connL * 0.55),
                        f"Pipe: OD-{m['connOD']:g}" + (f"\nID-{m['connID']:g}" if m["connID"] else ""), 1.2 * th)
        self.cline((xc, y_pipe0 - 30), (xc, y_top + 30))
        self.rdim((xc - cw / 2, y_body0), (xc - hw / 2, y_top), (xc - cw / 2 - 70, 0), 90, h=1.2 * th)
        self.rdim((xc - hw / 2, y_top), (xc + hw / 2, y_top), (0, y_top + 45), 0, h=1.2 * th)
        self.rdim((xc - cw / 2, y_body0), (xc + cw / 2, y_body0), (0, y_case0 - (25 if not connL else 0) - 1.6 * th), 0, h=1.2 * th)
        vol = plug_volume_dm3(L, (m["hotA"], m["hotB"], hw), (m["coldA"], m["coldB"], cw))
        self.utext(xc - 120, y_pipe0 - 70, m["mark"], 1.4 * th)
        self.utext(xc - 120, y_pipe0 - 70 - 2.0 * th, f"Gross Vol. of Brick={vol:.2f}dm³", 1.05 * th)
        # ---- end faces seen from the top (cold face outline + hot face inside, pipe grid) - detail 2:1
        k2 = 2.0
        pcx, pcy = ox + 520, y_top + 5 * th + max(m["coldA"], m["coldB"]) * k2 / 2 + 2 * th
        cold = self.end_face(pcx, pcy, cw, m["coldA"], m["coldB"], k2)
        hot = self.end_face(pcx, pcy, hw, m["hotA"], m["hotB"], k2)
        self.poly(cold, "MHP_OUTLINE")
        self.poly(hot, "MHP_OUTLINE")
        for p, q in zip(cold, hot):
            self.line(p, q, "MHP_OUTLINE")
        if cols:
            n = int(m["nPipes"])
            px = hw * k2 * 0.55 / max(cols - 1, 1)
            py = min(m["hotA"], m["hotB"]) * k2 * 0.55 / max(rows - 1, 1)
            r = (m["pipeOD"] or 0) / 2 * k2
            c = 0
            for i in range(cols):
                for j in range(rows):
                    if c >= n:
                        break
                    pc = (pcx + (i - (cols - 1) / 2) * px, pcy + (j - (rows - 1) / 2) * py)
                    if r > 0:
                        self.msp.add_circle(pc, r, dxfattribs={"layer": self.layer("MHP_PIPES", 1)})
                    else:                             # pipe size not entered: position only
                        self.msp.add_point(pc, dxfattribs={"layer": self.layer("MHP_PIPES", 1)})
                    c += 1
            self.leader((pcx - px * (cols - 1) / 2, pcy + py * (rows - 1) / 2),
                        (pcx - cw * k2 / 2 - 260, pcy + max(m["coldA"], m["coldB"]) * k2 / 2 + 2 * th),
                        f"{n}Nos Pipes" + (f",\nOD-Ø{m['pipeOD']:g}" if m["pipeOD"] else "") + (f",ID-Ø{m['pipeID']:g}" if m["pipeID"] else ""), 1.2 * th)
        e = max(m["coldA"], m["coldB"]) * k2 / 2
        self.cline((pcx - cw * k2 / 2 - 30, pcy), (pcx + cw * k2 / 2 + 30, pcy))
        self.cline((pcx, pcy - e - 30), (pcx, pcy + e + 30))
        self.rdim(cold[0], cold[3], (pcx - cw * k2 / 2 - 5 * th, 0), 90, h=1.2 * th, k=k2)
        self.rdim(hot[0], hot[3], (pcx - cw * k2 / 2 - 2.2 * th, 0), 90, h=1.2 * th, k=k2)
        self.rdim(hot[1], hot[2], (pcx + cw * k2 / 2 + 2.2 * th, 0), 90, h=1.2 * th, k=k2)
        self.rdim(cold[1], cold[2], (pcx + cw * k2 / 2 + 5 * th, 0), 90, h=1.2 * th, k=k2)
        self.rdim(cold[0], cold[1], (0, pcy - e - 2.2 * th), 0, h=1.2 * th, k=k2)
        self.rdim(hot[3], hot[2], (0, pcy + e + 1.2 * th), 0, h=1.2 * th, k=k2)
        self.text(pcx + cw * k2 / 2 + 8 * th, pcy, "END FACES (scale 2:1)", 0.9 * th, "mleft", "MHP_TEXT")
        self.text(pcx + cw * k2 / 2 + 8 * th, pcy - 1.6 * th, "outer = cold face, inner = hot face", 0.8 * th, "mleft", "MHP_TEXT")
        # ---- seating block (section)
        if m["seatOD"] and m["seatH"]:
            so, si, sh = m["seatOD"], m["seatID"] or 0, m["seatH"]
            sx, sy = ox + 1000, oy + H * 0.52
            k = 2.0                                       # drawn 2:1 (small part), dimensions show the true size
            for s in (-1, 1):
                wall = [(sx + s * si / 2 * k, sy), (sx + s * so / 2 * k, sy), (sx + s * so / 2 * k, sy + sh * k),
                        (sx + s * si / 2 * k, sy + sh * k)]
                self.fill(wall, SEAT_ACI, "MHP_SEAT")
                self.poly(wall, "MHP_OUTLINE")
            self.cline((sx, sy - 30), (sx, sy + sh * k + 30))
            self.rdim((sx - so / 2 * k, sy + sh * k), (sx + so / 2 * k, sy + sh * k), (0, sy + sh * k + 45), 0,
                      f"Ø{so:g}", 1.2 * th)
            if si:
                self.rdim((sx - si / 2 * k, sy), (sx + si / 2 * k, sy), (0, sy - 30), 0, f"Ø{si:g}", 1.2 * th)
            self.rdim((sx - so / 2 * k, sy), (sx - so / 2 * k, sy + sh * k), (sx - so / 2 * k - 60, 0), 90, f"{sh:g}",
                      1.2 * th)
            sv = ring_volume_dm3(so, si, sh)
            self.utext(sx - so / 2 * k - 60, sy - 110, m["seatMark"], 1.3 * th)
            self.utext(sx - so / 2 * k - 40, sy - 110 - 2.0 * th, f"Unit Vol:{sv:.2f}dm³", 1.05 * th)
            self.text(sx + so / 2 * k + 40, sy + sh * k / 2, "(scale 2:1)", 0.8 * th, "mleft", "MHP_TEXT")
        self.legend(ox + 920, oy + 520, [(MHP_ACI, m["grade"] or "quality not entered", True)] + ([(SEAT_ACI, m["seatGrade"], False)]
                                                                         if m["seatOD"] else []))
        self.text(ox + 920, oy + 160, "NOTE: ALL DIMENSIONS ARE IN MM.", 0.9 * th, layer="MHP_TEXT")
        self.text(ox + 920, oy + 160 - 1.6 * th, "VOLUMES COMPUTED FROM THE DRAWN DIMENSIONS.", 0.9 * th, layer="MHP_TEXT")

    # ------------------------------------------------------------ page 2: dummy brick for MHP
    def draw_dummy(self, ox, oy):
        m = self.m
        L = m["dummyL"]
        self.th = th = max(14.0, (L + 1050) / 95)
        cw = m["dummyColdW"] or m["coldW"]
        ca, cb = m["dummyColdA"] or m["coldA"], m["dummyColdB"] or m["coldB"]
        hw, ha, hb = m["hotW"], m["hotA"], m["hotB"]
        W, H = 1500.0, L + 1250
        self.frame_stamp(ox, oy, ox + W, oy + H, m["dummyMark"])
        xc, y0 = ox + 420, oy + 300
        body = [(xc - cw / 2, y0), (xc + cw / 2, y0), (xc + hw / 2, y0 + L), (xc - hw / 2, y0 + L)]
        self.poly(body, self.layer("MHP_OUTLINE", 7))
        self.cline((xc, y0 - 30), (xc, y0 + L + 30))
        self.rdim((xc - cw / 2, y0), (xc - hw / 2, y0 + L), (xc - cw / 2 - 80, 0), 90, h=1.2 * th)
        self.rdim((xc - hw / 2, y0 + L), (xc + hw / 2, y0 + L), (0, y0 + L + 45), 0, h=1.2 * th)
        self.rdim((xc - cw / 2, y0), (xc + cw / 2, y0), (0, y0 - 45), 0, h=1.2 * th)
        k2 = 2.0
        e = max(ca, cb) * k2 / 2
        pcx, pcy = xc + 120, y0 + L + 120 + e + 2 * th
        cold = self.end_face(pcx, pcy, cw, ca, cb, k2)
        hot = self.end_face(pcx, pcy, hw, ha, hb, k2)
        self.poly(cold, "MHP_OUTLINE")
        self.poly(hot, "MHP_OUTLINE")
        for p, q in zip(cold, hot):
            self.line(p, q, "MHP_OUTLINE")
        self.cline((pcx - cw * k2 / 2 - 30, pcy), (pcx + cw * k2 / 2 + 30, pcy))
        self.rdim(cold[0], cold[3], (pcx - cw * k2 / 2 - 5 * th, 0), 90, h=1.2 * th, k=k2)
        self.rdim(hot[0], hot[3], (pcx - cw * k2 / 2 - 2.2 * th, 0), 90, h=1.2 * th, k=k2)
        self.rdim(hot[1], hot[2], (pcx + cw * k2 / 2 + 2.2 * th, 0), 90, h=1.2 * th, k=k2)
        self.rdim(cold[1], cold[2], (pcx + cw * k2 / 2 + 5 * th, 0), 90, h=1.2 * th, k=k2)
        self.text(pcx + cw * k2 / 2 + 8 * th, pcy, "END FACES (scale 2:1)", 0.9 * th, "mleft", "MHP_TEXT")
        vol = plug_volume_dm3(L, (ha, hb, hw), (ca, cb, cw))
        self.utext(ox + 760, oy + 520, m["dummyMark"], 1.6 * th)
        self.utext(ox + 760, oy + 520 - 2.4 * th, f"Gross Vol. of Brick={vol:.2f}dm³", 1.1 * th)
        if m["dummyGrade"]:
            self.legend(ox + 760, oy + 380, [(DUMMY_ACI, m["dummyGrade"], False)])
        self.text(ox + 760, oy + 120, f"Hot face as {m['mark']}. ALL DIMENSIONS ARE IN MM.", 0.9 * th, layer="MHP_TEXT")

    # ------------------------------------------------------------ page 3: MHP position plan on the bottom
    def draw_positions(self, ox, oy):
        m, th = self.m, self.th
        n, pcd = int(self.stir.get("count") or 0), float(self.stir.get("pcd") or 0)
        a0 = float(m.get("startAng") or self.stir.get("start_angle") or 0)
        rr = self.rings.get("rings") or []
        has_rings = bool(rr)
        r_bot = max(float(r["r_out"]) for r in rr) if has_rings else pcd / 2 + 2 * m["coldW"]   # frame size only
        S = 1.0
        self.th = th = max(14.0, r_bot / 70)
        W = 2 * r_bot + 34 * th
        H = 2 * r_bot + 32 * th
        self.frame_stamp(ox, oy, ox + W, oy + H, f"{m['mark']} POSITION PLAN - BOTTOM")
        cx, cy = ox + r_bot + 8 * th, oy + r_bot + 18 * th
        lay = self.layer("MHP_OUTLINE", 7)
        if has_rings:
            self.msp.add_circle((cx, cy), r_bot, dxfattribs={"layer": lay})
        for r in rr:
            self.msp.add_circle((cx, cy), float(r["r_out"]), dxfattribs={"layer": self.layer("MHP_RINGS", 8)})
        if self.rings.get("key"):
            self.msp.add_circle((cx, cy), float(self.rings["key"]["r"]), dxfattribs={"layer": "MHP_RINGS"})
        self.cline((cx - r_bot - 2 * th, cy), (cx + r_bot + 2 * th, cy), 1.0)
        self.cline((cx, cy - r_bot - 2 * th), (cx, cy + r_bot + 2 * th), 1.0)
        rp = pcd / 2 * S
        self.layer("MHP_PCD", 6)
        self.msp.add_circle((cx, cy), rp, dxfattribs={"layer": "MHP_PCD", "linetype": "CENTER", "ltscale": 2})
        cw, hw = m["coldW"], m["hotW"]
        ring_no = next((r["ring"] for r in rr if float(r["r_in"]) <= pcd / 2 <= float(r["r_out"])), None)
        for k in range(n):
            ang = a0 + k * 360.0 / n                     # 0 deg at the top, clockwise (as on TRL's plans)
            t = math.radians(90 - ang)
            u, v = (math.cos(t), math.sin(t)), (-math.sin(t), math.cos(t))
            c = (cx + rp * u[0], cy + rp * u[1])
            fp = [(c[0] + du * u[0] + dv * v[0], c[1] + du * u[1] + dv * v[1]) for du, dv in
                  ((-cw / 2, -m["coldA"] / 2), (cw / 2, -m["coldB"] / 2), (cw / 2, m["coldB"] / 2), (-cw / 2, m["coldA"] / 2))]
            self.hatch(fp, MHP_ACI, scale=1.0)
            self.poly(fp, "MHP_OUTLINE")
            if m["seatOD"]:
                self.msp.add_circle(c, m["seatOD"] / 2, dxfattribs={"layer": self.layer("MHP_SEAT", SEAT_ACI)})
            lt = (cx + (rp + cw + 3 * th) * u[0], cy + (rp + cw + 3 * th) * u[1])
            self.text(lt[0], lt[1], f"{m['mark']}-{k + 1}", 1.2 * th, "center", "MHP_TEXT")
            self.line((cx, cy), (cx + (rp + cw / 2 + 60) * u[0], cy + (rp + cw / 2 + 60) * u[1]), self.layer("MHP_RAY", 5))
        if n >= 2:                                      # pitch angle between the first two plugs
            r_a = rp + cw + 6 * th
            a1, a2 = math.radians(90 - a0), math.radians(90 - a0 - 360.0 / n)
            arc = self.msp.add_arc((cx, cy), r_a, math.degrees(a2), math.degrees(a1),
                                   dxfattribs={"layer": self.layer("MHP_DIMS", 1)})
            am = (a1 + a2) / 2
            self.text(cx + (r_a + 1.2 * th) * math.cos(am), cy + (r_a + 1.2 * th) * math.sin(am), f"{360.0 / n:g}°", 1.3 * th,
                      "center", "MHP_DIMS")
        self.rdim((cx - rp, cy - r_bot - 3.5 * th), (cx + rp, cy - r_bot - 3.5 * th), (0, cy - r_bot - 3.5 * th), 0,
                  f"{pcd:g} P.C.D.", 1.3 * th)
        if has_rings:
            self.rdim((cx - r_bot, cy - r_bot - 7 * th), (cx + r_bot, cy - r_bot - 7 * th), (0, cy - r_bot - 7 * th), 0,
                      f"Ø{2 * r_bot:g}", 1.3 * th)
        note = f"{n} NOS. {m['mark']} ON {pcd:g} P.C.D., {360.0 / max(n, 1):g}° PITCH"
        if ring_no is not None:
            note += f", IN BOTTOM RING {ring_no}"
        tx = ox + 2 * th
        ty = oy + 6.5 * th
        self.text(tx, ty, note, 1.2 * th, layer="MHP_TEXT")
        self.text(tx, ty - 2.2 * th, f"{m['seatMark']} BELOW EACH {m['mark']}" if m["seatOD"] else " ", 1.1 * th,
                  layer="MHP_TEXT")
        self.text(tx, ty - 4.4 * th, f"0° AT THE TOP, ANGLES CLOCKWISE (first {m['mark']} at {a0:g}°)", 1.1 * th,
                  layer="MHP_TEXT")

    def draw_foundation(self, ox, oy):
        """MHP positions on the bottom surfaces (TRL "BOTTOM WORKING FOUNDATION"): top plan with every MHP and
        its pitch, section through two MHPs with the hot face, the cold face of the working lining and the shell
        (inner / outer) and the P.C.D. of the MHP axes on each surface - all from this job's dish radii."""
        m, R = self.m, self.radii
        n, pcd = int(self.stir.get("count") or 0), float(self.stir.get("pcd") or 0)
        a0 = float(m.get("startAng") or self.stir.get("start_angle") or 0)
        surf = [(k, lab, R.get(k)) for k, lab in (("hot", "HOT FACE"), ("cold", "BOTTOM SURFACE OF WORKING LINING"),
                                                   ("shell", "INNER SURFACE OF SHELL"), ("shell_out", "OUTER SURFACE OF SHELL"))
                if R.get(k)]
        rh = R["hot"]
        pcds = [(lab, pcd * r / rh, r) for _, lab, r in surf]
        r_edge = float(R.get("edge") or pcd)
        self.th = th = max(14.0, r_edge / 45)
        W = 4 * r_edge + 75 * th
        H = 2 * r_edge + 30 * th
        self.frame_stamp(ox, oy, ox + W, oy + H, f"{m['mark']} POSITIONS - BOTTOM WORKING FOUNDATION")
        # top plan
        cx, cy = ox + r_edge + 12 * th, oy + H / 2
        for lab, d, _ in pcds:
            self.msp.add_circle((cx, cy), d / 2, dxfattribs={"layer": self.layer("MHP_PCD", 6), "linetype": "CENTER",
                                                            "ltscale": 2})
        self.msp.add_circle((cx, cy), r_edge, dxfattribs={"layer": self.layer("MHP_OUTLINE", 7)})
        self.cline((cx - r_edge - 2 * th, cy), (cx + r_edge + 2 * th, cy), 1.0)
        self.cline((cx, cy - r_edge - 2 * th), (cx, cy + r_edge + 2 * th), 1.0)
        for k in range(n):
            t = math.radians(90 - (a0 + k * 360.0 / n))
            self.line((cx, cy), (cx + (pcd / 2 + 3 * th) * math.cos(t), cy + (pcd / 2 + 3 * th) * math.sin(t)),
                      self.layer("MHP_RAY", 5))
            self.msp.add_circle((cx + pcd / 2 * math.cos(t), cy + pcd / 2 * math.sin(t)), max(m["coldW"] / 2, th / 3),
                                dxfattribs={"layer": self.layer("MHP_OUTLINE", 7)})
        if n >= 2:
            r_a = pcd / 2 + 5 * th
            a1, a2 = 90 - a0 - 360.0 / n, 90 - a0
            self.msp.add_arc((cx, cy), r_a, a1, a2, dxfattribs={"layer": self.layer("MHP_DIMS", 1)})
            am = math.radians((a1 + a2) / 2)
            self.text(cx + (r_a + 1.5 * th) * math.cos(am), cy + (r_a + 1.5 * th) * math.sin(am), f"{360.0 / n:g}°", 1.2 * th,
                      "center", "MHP_DIMS")
        self.text(cx, cy - r_edge - 4 * th, "TOP PLAN", 1.4 * th, "center", "MHP_TEXT")
        self.text(cx, cy - r_edge - 6.2 * th, f"{n} NOS. {m['mark']} ON {pcd:g} P.C.D. (HOT FACE)", 1.0 * th, "center",
                  "MHP_TEXT")
        # section through two MHPs: concentric spherical surfaces (common centre), MHP axes along the radius
        sx = cx + 2 * r_edge + 26 * th
        rmax_s = max(r for _, _, r in surf)
        Cz = cy + r_edge * 0.35 + rh                          # hot face lowest point at cy + 0.35 r_edge
        ph = math.asin(min(0.999, r_edge / rh))
        for key, lab, r in surf:
            pts = [(sx + r * math.sin(-ph + 2 * ph * j / 60), Cz - r * math.cos(-ph + 2 * ph * j / 60)) for j in range(61)]
            self.poly(pts, self.layer("MHP_SURF_HOT" if key == "hot" else "MHP_SURF", 1 if key == "hot" else 7), close=False)
            self.text(pts[-1][0] + 1.2 * th, pts[-1][1], f"R{r:.0f}  {lab}", 0.9 * th, "mleft", "MHP_TEXT")
        ph_m = math.asin(min(0.999, pcd / 2 / rh))
        r_o = rmax_s + 3 * th
        for sg in (1, -1):
            self.line((sx + sg * rh * math.sin(ph_m), Cz - rh * math.cos(ph_m)),
                      (sx + sg * r_o * math.sin(ph_m), Cz - r_o * math.cos(ph_m)), self.layer("MHP_RAY", 5))
        self.cline((sx, Cz - rmax_s - 3 * th), (sx, Cz - rh * math.cos(ph) + 3 * th), 1.0)
        for k_, (lab, d, r) in enumerate(pcds):
            y_ = Cz - r * math.cos(ph_m)
            yb = Cz - rmax_s - (4 + 3.2 * k_) * th
            self.rdim((sx - d / 2, y_), (sx + d / 2, y_), (0, yb), 0, f"Ø{d:.1f} P.C.D.", 1.0 * th)
        self.text(sx, Cz - rh * math.cos(ph) + 6 * th, "SECTION THROUGH TWO MHPs", 1.4 * th, "center", "MHP_TEXT")
        self.text(ox + 2 * th, oy + 2.2 * th, "P.C.D. ON EACH SURFACE = HOT-FACE P.C.D. x SURFACE RADIUS / HOT-FACE RADIUS "
                  "(MHP AXIS ALONG THE DISH RADIUS). RCC FOUNDATION NOT DRAWN: NO FOUNDATION INPUT.", 0.8 * th, layer="MHP_TEXT")

    def build(self):
        self._view(self.m["mark"] + (f" & {self.m['seatMark']}" if self.m["seatOD"] else ""), self.draw_mhp, 0.0, 0.0)
        if self.m.get("dummyL"):
            self._view(self.m["dummyMark"], self.draw_dummy, 0.0, -4000.0)
        if self.stir.get("count") and self.stir.get("pcd"):
            self._view(f"{self.m['mark']} POSITION PLAN", self.draw_positions, 3000.0, -12000.0)
            if getattr(self, "radii", None) and self.radii.get("hot"):
                self._view(f"{self.m['mark']} POSITIONS - BOTTOM WORKING FOUNDATION", self.draw_foundation, 3000.0, -40000.0)
        return self.doc


# ============================================================================ TAPHOLE (FR-06.4)
TB_ACI, TS_ACI = 30, 190            # brown blocks, violet sleeves (as on TRL's taphole sheets)


def taphole_inputs(raw):
    """Normalises the taphole set sent by the UI: pieces listed from the hot face outwards.
    piece = {mark, kind: block|sleeve, shape: round|square, L, doHot, doCold, diHot, diCold,
             stepL, stepD (outer reduced at the hot end), flareR (bore flare radius at the hot end),
             spig, spigD, spigDi (joint ring projecting at the shell-side end; matching socket at the hot end)}."""
    raw = raw or {}
    pieces = []
    for p in raw.get("pieces") or []:
        q = {k: _num(p.get(k)) for k in ("L", "doHot", "doCold", "diHot", "diCold", "stepL", "stepD", "flareR",
                                          "spig", "spigD", "spigDi")}
        q["mark"] = str(p.get("mark") or "").strip()
        q["kind"] = "sleeve" if str(p.get("kind") or "").lower().startswith("s") else "block"
        q["shape"] = "square" if str(p.get("shape") or "").lower().startswith("sq") else "round"
        if not (q["mark"] and q["L"] and q["doHot"] and q["diHot"]):
            continue
        q["doCold"] = q["doCold"] or q["doHot"]
        q["diCold"] = q["diCold"] or q["diHot"]
        pieces.append(q)
    if not pieces:
        return None
    out = {k: _num(raw.get(k)) for k in ("pipeOD", "pipeID", "pipeOut", "opening", "blockDens", "sleeveDens")}
    for k in ("blockGrade", "sleeveGrade", "ramGrade"):
        out[k] = str(raw.get(k) or "").strip()
    out["pieces"] = pieces
    return out


def piece_profile(q, n=24):
    """Upper half of the piece as (outer polyline, bore polyline), x from the hot end (0) to L, y = radius
    (half side for a square block)."""
    L = q["L"]
    ro = lambda x: (q["doHot"] + (q["doCold"] - q["doHot"]) * x / L) / 2
    outer = []
    sL, sD = q.get("stepL") or 0, q.get("stepD") or 0
    if sL and sD:
        outer += [(0.0, sD / 2), (sL, sD / 2), (sL, ro(sL))]
    else:
        outer += [(0.0, ro(0.0))]
    outer += [(L, ro(L))]
    a, b = q["diHot"] / 2, q["diCold"] / 2
    R = q.get("flareR") or 0
    bore = []
    if R and a > b:                                   # radius flare at the hot end, tangent to the bore Ø diCold
        d = a - b
        lf = math.sqrt(max(R * R - (R - d) ** 2, 0.0)) if d < R else R
        for k in range(n + 1):
            x = lf * k / n
            bore.append((x, b + R - math.sqrt(max(R * R - (lf - x) ** 2, 0.0))))
        bore += [(L, b)]
    else:
        bore = [(0.0, a), (L, b)]
    return outer, bore


def piece_volume_dm3(q, steps=400):
    """Volume from the drawn profile (net of the joint: projecting spigot ring = socket recess)."""
    outer, bore = piece_profile(q)

    def y_at(pl, x):
        best = pl[-1][1]
        for (x0, y0), (x1, y1) in zip(pl, pl[1:]):
            if x0 - 1e-9 <= x <= x1 + 1e-9 and x1 > x0:
                return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        return best
    L, v = q["L"], 0.0
    for k in range(steps):
        x = (k + 0.5) * L / steps
        yo, yi = y_at(outer, x), y_at(bore, x)
        area_o = (2 * yo) ** 2 if q["shape"] == "square" and not (q.get("stepL") and x < q["stepL"]) else math.pi * yo * yo
        v += (area_o - math.pi * yi * yi) * L / steps
    return v / 1e6


def piece_polys(q, x0=0.0, flip=1):
    """Closed polygons (upper half) to draw: the wall and, when given, the projecting joint ring."""
    outer, bore = piece_profile(q)
    X = lambda x: x0 + flip * x
    wall = [(X(x), y) for x, y in outer] + [(X(x), y) for x, y in reversed(bore)]
    polys = [wall]
    if q.get("spig") and q.get("spigD") and q.get("spigDi"):
        L, s = q["L"], q["spig"]
        polys.append([(X(L), q["spigDi"] / 2), (X(L + s), q["spigDi"] / 2), (X(L + s), q["spigD"] / 2), (X(L), q["spigD"] / 2)])
    return polys


class TapholeSheet(MhpSheet):
    """ASSEMBLY & DETAIL OF TAP HOLE BLOCKS and ... SLEEVES (TRL format): assembled set + every piece in
    section A-A with an end view, red dimensions, VOL in dm3, quality legend, FOR CUSTOMER APPROVAL."""

    def __init__(self, th_set, taphole=None, job="", lining_len=None):
        Doc.__init__(self, 14.0)
        self.t = th_set
        self.tap = taphole or {}
        self.job = job
        self.lining_len = lining_len

    def half(self, polys, aci, ox, oy, k=1.0):
        """Draws a piece in section: upper and mirrored lower wall, filled, about the axis y = oy."""
        for poly in polys:
            for sg in (1, -1):
                pts = [(ox + x * k, oy + sg * y * k) for x, y in poly]
                self.fill(pts, aci, "TH_FILL")
                self.poly(pts, self.layer("MHP_OUTLINE", 7))

    def assembled(self, ox, oy, pieces, title, with_pipe):
        """Blocks and sleeves (concentric), hot face at the right as on TRL's sheets; length chains, diameters."""
        th, t = self.th, self.t
        chains = [[q for q in pieces if q["kind"] == k] for k in ("block", "sleeve")]
        chains = [c for c in chains if c]
        tot = max(sum(q["L"] for q in c) for c in chains)
        x_hot = ox + tot
        ymax = max(max(q["doHot"], q["doCold"]) for q in pieces) / 2
        x_shell = x_hot
        for ci, c in enumerate(chains):
            x = x_hot
            xs = []
            for q in c:
                aci = TB_ACI if q["kind"] == "block" else TS_ACI
                self.half(piece_polys(q, x, -1), aci, 0, oy)
                yl = (q["diHot"] + q["doHot"]) / 4
                self.text(x - q["L"] / 2, oy - yl, q["mark"], 0.65 * th, "center", "MHP_TEXT")
                xs.append((x - q["L"], x))
                x -= q["L"]
            x_shell = min(x_shell, x)
            ybase = oy + ymax + (2.6 + 2.6 * ci) * th if ci == 0 or len(chains) == 1 else oy - ymax - 2.6 * th
            yref = oy + ymax if ybase > oy else oy - ymax
            for a_, b_ in xs:                                # length chain per kind
                self.rdim((a_, yref), (b_, yref), (0, ybase), 0, h=0.85 * th)
        self.cline((x_shell - (t.get("pipeOut") or 0) - 150, oy), (x_hot + 150, oy))
        self.rdim((x_shell, oy + ymax), (x_hot, oy + ymax), (0, oy + ymax + 5.6 * th), 0, h=0.9 * th)
        q0 = chains[0][0]
        for k_, (d, lab) in enumerate(((q0["doHot"], f"{q0['doHot']:g}SQ" if q0["shape"] == "square" else f"Ø{q0['doHot']:g}"),
                                       (q0["diHot"], f"Ø{q0['diHot']:g}"))):
            self.rdim((x_hot, oy - d / 2), (x_hot, oy + d / 2), (x_hot + (3.0 + 3.0 * k_) * th, 0), 90, lab, 0.85 * th)
        qe = [c[-1] for c in chains]
        k_ = 0
        for q in qe:
            for d in (q["doCold"], q["diCold"]):
                self.rdim((x_shell, oy - d / 2), (x_shell, oy + d / 2), (x_shell - (3.0 + 3.0 * k_) * th, 0), 90, f"Ø{d:g}",
                          0.85 * th)
                k_ += 1
        if with_pipe and t.get("pipeOD"):
            po, pi_ = t["pipeOD"], t.get("pipeID") or 0
            x0, x1 = x_shell - (t.get("pipeOut") or 0), x_hot - 40
            for sg in (1, -1):
                self.poly([(x0, oy + sg * pi_ / 2), (x1, oy + sg * pi_ / 2), (x1, oy + sg * po / 2), (x0, oy + sg * po / 2)],
                          "MHP_OUTLINE")
            self.rdim((x0, oy - po / 2), (x0, oy + po / 2), (x0 - (3.0 + 3.0 * k_) * th, 0), 90, f"Ø{po:g}", 0.85 * th)
            if pi_:
                self.rdim((x0, oy - pi_ / 2), (x0, oy + pi_ / 2), (x0 - (6.0 + 3.0 * k_) * th, 0), 90, f"Ø{pi_:g}", 0.85 * th)
            if t.get("pipeOut"):
                self.rdim((x0, oy - ymax), (x_shell, oy - ymax), (0, oy - ymax - 5.2 * th), 0, h=0.85 * th)
        self.text(x_hot + 1.0 * th, oy + ymax + 1.5 * th, "HOT FACE", 0.8 * th, "mleft", "MHP_TEXT")
        self.text(x_shell - 1.0 * th, oy + ymax + 1.5 * th, "SHELL SIDE", 0.8 * th, "mright", "MHP_TEXT")
        self.utext(ox - 2 * th, oy + ymax + 9 * th, title, 1.2 * th)

    def detail(self, ox, oy, q):
        """One piece: end view (left) + SECTION A-A (right), VOL under the mark."""
        th = self.th
        aci = TB_ACI if q["kind"] == "block" else TS_ACI
        do = max(q["doHot"], q["doCold"])
        cx = ox + do / 2
        if q["shape"] == "square":
            h = do / 2
            self.poly([(cx - h, oy - h), (cx + h, oy - h), (cx + h, oy + h), (cx - h, oy + h)], "MHP_OUTLINE")
            self.rdim((cx - h, oy - h), (cx + h, oy - h), (0, oy - h - 2.4 * th), 0, h=0.85 * th)
            self.rdim((cx + h, oy - h), (cx + h, oy + h), (cx + h + 2.4 * th, 0), 90, h=0.85 * th)
        else:
            for d in {q["doHot"], q["doCold"]}:
                self.msp.add_circle((cx, oy), d / 2, dxfattribs={"layer": self.layer("MHP_OUTLINE", 7)})
        for d in {q["diHot"], q["diCold"]}:
            self.msp.add_circle((cx, oy), d / 2, dxfattribs={"layer": "MHP_OUTLINE"})
        self.cline((cx - do / 2 - 40, oy), (cx + do / 2 + 40, oy))
        self.cline((cx, oy - do / 2 - 40), (cx, oy + do / 2 + 40))
        for sg in (1, -1):                                     # A - A cut marks
            self.text(cx - do / 2 - 1.2 * th, oy + sg * (do / 2 + 1.0 * th), "A", 1.1 * th, "center", "MHP_TEXT")
        sx = cx + do / 2 + 5 * th
        self.half(piece_polys(q, sx, 1), aci, 0, oy)
        self.cline((sx - 40, oy), (sx + q["L"] + 40, oy))
        yo = max(q["doHot"], q["doCold"]) / 2
        self.rdim((sx, oy + yo), (sx + q["L"], oy + yo), (0, oy + yo + 2.4 * th), 0, h=0.85 * th)
        if q.get("spig") and q.get("spigD"):
            self.rdim((sx + q["L"], oy - yo), (sx + q["L"] + q["spig"], oy - yo), (0, oy - yo - 2.4 * th), 0, h=0.85 * th)
            for k_, d in enumerate((q["spigD"], q.get("spigDi"))):
                if d:
                    xr = sx + q["L"] + q["spig"]
                    self.rdim((xr, oy - d / 2), (xr, oy + d / 2), (xr + (2.4 + 2.4 * k_) * th, 0), 90, f"Ø{d:g}", 0.85 * th)
        if q.get("stepL") and q.get("stepD"):
            self.rdim((sx, oy - yo), (sx + q["stepL"], oy - yo), (0, oy - yo - 2.4 * th), 0, h=0.85 * th)
        if q.get("flareR"):
            self.text(sx + 0.6 * th, oy + q["diHot"] / 2 - 1.2 * th, f"R{q['flareR']:g}", 0.8 * th, layer="MHP_DIMS")
        sq = q["shape"] == "square"
        dims = [(sx, q.get("stepD") or q["doHot"], sq and not q.get("stepD"), -1, 1)]
        if q["doCold"] != q["doHot"] or q.get("stepD"):
            dims.append((sx + q["L"], q["doCold"], sq, 1, 1))
        dims.append((sx, q["diHot"], False, -1, 2))
        if q["diCold"] != q["diHot"]:
            dims.append((sx + q["L"], q["diCold"], False, 1, 2))
        for x_, d, isq, sgn, lvl in dims:
            self.rdim((x_, oy - d / 2), (x_, oy + d / 2), (x_ + sgn * 2.4 * lvl * th, 0), 90,
                      f"{d:g}SQ" if isq else f"Ø{d:g}", 0.85 * th)
        self.text(sx + q["L"] / 2, oy + yo + 5.2 * th, "SECTION A-A", 0.95 * th, "center", "MHP_TEXT")
        self.utext(ox, oy - do / 2 - 4.5 * th, q["mark"], 1.3 * th)
        self.text(ox, oy - do / 2 - 6.6 * th, f"VOL-{piece_volume_dm3(q):.2f}dm³", 0.9 * th, layer="MHP_TEXT")
        return sx + q["L"] + 9 * th - ox

    def page(self, ox, oy, kind):
        t = self.t
        pcs = [q for q in t["pieces"] if q["kind"] == kind]
        allp = t["pieces"]
        uniq = []
        for q in pcs:
            if q["mark"] not in [u["mark"] for u in uniq]:
                uniq.append(q)
        dmax = max(max(q["doHot"], q["doCold"]) for q in allp)
        lmax = max(q["L"] for q in pcs)
        b_len = sum(q["L"] for q in allp if q["kind"] == "block")
        s_len = sum(q["L"] for q in allp if q["kind"] == "sleeve")
        total = max(b_len, s_len)
        self.th = th = max(12.0, (total + 900) / 120)
        cell_w = dmax + lmax + 26 * th
        W = max(total + 46 * th, 3 * cell_w + 6 * th)
        rows = (len(uniq) + 2) // 3
        cell_h = dmax + 20 * th
        H = dmax + 34 * th + rows * cell_h
        name = "TAP HOLE BLOCKS" if kind == "block" else "TAP HOLE SLEEVES"
        self.frame_stamp(ox, oy, ox + W, oy + H, f"ASSEMBLY & DETAIL OF {name}")
        ya = oy + H - 15 * th - dmax / 2
        self.assembled(ox + (W - total) / 2, ya, allp if kind == "block" else pcs,
                       "ASSEMBLED TAP HOLE SET (INITIAL)" if kind == "block" else "ASSEMBLED TAP HOLE SLEEVES", True)
        for i, q in enumerate(uniq):
            c, r = i % 3, i // 3
            self.detail(ox + 4 * th + c * cell_w, ya - dmax / 2 - 16 * th - dmax / 2 - r * cell_h, q)
        grade = t.get("blockGrade") if kind == "block" else t.get("sleeveGrade")
        leg = [(TB_ACI if kind == "block" else TS_ACI, grade or "-", False)]
        if kind == "block":
            leg += [(TS_ACI, t.get("sleeveGrade") or "-", False)]
        self.legend(ox + W - 26 * th, oy + 14 * th, leg)
        note = f"ALL DIMENSIONS ARE IN MM. BLOCK SET LENGTH {b_len:g} MM, SLEEVE SET LENGTH {s_len:g} MM"
        if self.lining_len:
            note += f"; LINING THICKNESS ALONG THE TAPHOLE AXIS {self.lining_len:.0f} MM"
        self.text(ox + 2 * th, oy + 3.6 * th, note, 0.8 * th, layer="MHP_TEXT")
        if self.lining_len and abs(b_len - self.lining_len) > 0.05 * self.lining_len:
            self.layer("TH_WARN", 1)
            self.text(ox + 2 * th, oy + 5.4 * th, f"CHECK: BLOCK SET ({b_len:g} MM) DOES NOT MATCH THE LINING ALONG THE TAPHOLE "
                      f"AXIS ({self.lining_len:.0f} MM)", 0.9 * th, layer="TH_WARN")
        if self.tap.get("angle") is not None:
            self.text(ox + 2 * th, oy + 2.0 * th, f"TAPHOLE AT {self.tap['angle']:g}°, EL {self.tap.get('z') or 0:g}, "
                      f"{self.tap.get('inclination') or 0:g}° INCLINATION (FROM THE EXTRACTED DRAWING)", 0.8 * th, layer="MHP_TEXT")

    def build(self):
        kinds = [k for k in ("block", "sleeve") if any(q["kind"] == k for q in self.t["pieces"])]
        for i, k in enumerate(kinds):
            self._view("ASSEMBLY & DETAIL OF TAP HOLE " + ("BLOCKS" if k == "block" else "SLEEVES"), self.page,
                       0.0, -i * 12000.0, k)
        return self.doc
