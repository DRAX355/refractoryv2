"""
Drawing set output: every view of a sheet on its own page.

  render_pages()  - one PDF page per view (A3 landscape, drawing frame + title block on every page),
                    a PNG overview of all pages (shown in the UI), and the rendered extents of each view.
  add_layouts()   - the same pages as AutoCAD paper-space layouts in the DXF (one layout tab per view,
                    border + title block + a viewport on the model-space view).
  export_dwg()    - DWG copy of a DXF when the free ODA File Converter is installed on the server.

A "view" is {"title": str, "handles": set of model-space entity handles}. The sheet classes collect
them while drawing (see Sheet._view / Doc._view), so a page holds exactly the entities of one view.
"""
import datetime
import math
import threading as _threading

PAGE_W, PAGE_H = 420.0, 297.0                 # A3 landscape, mm (default)
PAPER = "A3"
PAPERS = {"A3": (420.0, 297.0), "A2": (594.0, 420.0), "A1": (841.0, 594.0)}
# The paper size lives in module globals while a set is drawn; generations run in background threads, so two
# jobs at the same time must not change it under each other (that gave mixed A1 / A3 pages). One set at a time.
_PAPER_LOCK = _threading.RLock()


def _set_paper(name):
    """Sheet size of the drawing set being made (TRL's lining GA is A1); returns the previous size."""
    global PAGE_W, PAGE_H, PAPER
    prev = PAPER
    PAPER = name if name in PAPERS else "A3"
    PAGE_W, PAGE_H = PAPERS[PAPER]
    return prev


BORDER = 10.0
TB_H = 44.0                                   # title block height (mm) - TRL Krosaki standard
MM = 72.0 / 25.4                              # PDF points per mm


# ---------------------------------------------------------------- PyMuPDF speed-ups (output unchanged)
class _Content:
    """Collects the PDF page content in a list. PyMuPDF joins it with '+=' on one big string, which gets
    very slow (quadratic) on large drawings."""
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
    """Same PDF path operators as PyMuPDF's Shape.draw_line / draw_polyline / draw_bezier / draw_curve,
    written with plain float maths (PyMuPDF builds several Point objects per vertex)."""
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
        k = 0.55228474983                      # same construction as PyMuPDF's Shape.draw_curve
        x1, y1, x2, y2, x3, y3 = float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]), float(p3[0]), float(p3[1])
        return draw_bezier((x1, y1), (x1 + (x2 - x1) * k, y1 + (y2 - y1) * k),
                           (x3 + (x2 - x3) * k, y3 + (y2 - y3) * k), (x3, y3))

    sh.draw_line, sh.draw_polyline, sh.draw_bezier, sh.draw_curve = draw_line, draw_polyline, draw_bezier, draw_curve


def speed_up(backend):
    """Install the speed-ups on an ezdxf PyMuPdfBackend (recorder). Any failure leaves it unchanged."""
    make = backend.make_backend

    def fast_backend(page, settings):
        rb = make(page, settings)
        try:
            sh = rb.content_shape
            if isinstance(getattr(sh, "totalcont", None), str) and not sh.totalcont:
                sh.totalcont = _Content()
                commit = sh.commit

                def commit_keep(*a, **k):            # commit() resets it to "" - keep using the list
                    r = commit(*a, **k)
                    sh.totalcont = _Content()
                    return r
                sh.commit = commit_keep
            _fast_shape(sh)
        except Exception:
            pass
        return rb
    backend.make_backend = fast_backend
    return backend


# ---------------------------------------------------------------- helpers
def nice_scale(s):
    """Model mm per paper mm -> a standard drawing scale denominator (rounded up)."""
    for n in (1, 2, 2.5, 5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 80, 100, 125, 150, 200, 250, 300, 400, 500,
              600, 750, 1000, 1250, 1500, 2000):
        if n >= s * 0.999:
            return n
    return math.ceil(s)


def _date():
    return datetime.date.today().strftime("%d-%m-%Y")


TB_NOTES = ["1. All dimensions are in mm.", "2. For BOM refer separate sheet attached with this drg.",
            "3. Bricks to be cut at site if required.", "4. Please do not scale the drg., if in doubt please ask."]
TB_PROPERTY = ["THE DRAWINGS & IT'S CONTENTS ARE THE PROPERTY OF TRL KROSAKI REFRACTORIES LTD.",
               "SHOULD NOT BE REPRODUCED, PUBLISHED OR USED IN ANY WAY WITHOUT THE PERMISSION",
               "OF TRL KROSAKI REFRACTORIES LIMITED."]


def title_block(info, i, n, title, scale, note=None):
    """TRL Krosaki title block (as on TRL's 180 MT reference sheets), laid out in paper mm with the origin at the
    bottom-left of the sheet. Returns (lines, texts): lines = [(x0, y0, x1, y1, weight)],
    texts = [(x, y, text, height_mm, bold, rgb)]. Used for the PDF pages and the AutoCAD layouts alike."""
    L, T = [], []
    b = BORDER
    y0, y1 = b, b + TB_H
    xa, xb, xc, xd = b, b + 118.0, b + 228.0, PAGE_W - b          # notes | revisions + status | identification
    for x in (xb, xc):
        L.append((x, y0, x, y1, 0.5))
    L.append((xa, y1, xd, y1, 1.0))
    red, grey, blk = (0.75, 0, 0), (0.3, 0.3, 0.3), (0, 0, 0)
    # --- left: notes, property note, warnings
    T.append((xa + 2, y1 - 4.5, "NOTE:", 2.4, True, blk))
    for k, t in enumerate(TB_NOTES):
        T.append((xa + 2, y1 - 8.5 - k * 3.4, t, 2.0, False, blk))
    L.append((xa, y1 - 23, xb, y1 - 23, 0.4))
    for k, t in enumerate(TB_PROPERTY):
        T.append((xa + 2, y1 - 26.5 - k * 2.8, t, 1.6, False, blk))
    if info.get("edited"):
        T.append((xa + 2, y0 + 4.6, f"! {info['edited']} input value(s) EDITED by the designer - see 'DESIGN INPUT VALUES'",
                  1.7, True, red))
    if note:
        T.append((xa + 2, y0 + 1.6, str(note)[:95], 1.6, False, grey))
    # --- middle: revision table + status boxes
    cols = [("REV", 9), ("KIND OF REVISION", 31), ("PREPARED", 18), ("CHECKED", 17), ("APPROVED", 18), ("DATE", 17)]
    rows_y = [y1, y1 - 7, y1 - 14, y1 - 21, y1 - 28]               # header line positions (4 revision rows)
    for yy in rows_y[1:]:
        L.append((xb, yy, xc, yy, 0.4))
    x = xb
    for k, (c, w) in enumerate(cols):
        if k:
            L.append((x, rows_y[-1], x, y1, 0.4))
        x += w
    revs = [(str(info.get("rev", 0)), info.get("rev_kind", "FIRST ISSUE"), info.get("prepared", ""),
             info.get("checked", ""), info.get("approved", ""), info.get("date") or _date())]
    for r, yy in enumerate([rows_y[2], rows_y[1], rows_y[0]]):   # first issue in the row just above the header
        vals = revs[r] if r < len(revs) else ("",) * 6
        x = xb
        for (c, w), v in zip(cols, vals):
            T.append((x + 1, yy - 5, str(v)[:int(w / 1.4)], 2.0, False, blk))
            x += w
    x = xb
    for c, w in cols:                                              # header at the bottom of the table, as TRL
        T.append((x + 1, rows_y[-1] + 2, c, 1.7, True, blk))
        x += w
    stat = info.get("issue_status", "FOR APPROVAL")
    boxes = ["FOR INFORMATION", "FOR ERECTION", "FOR APPROVAL", "FOR PRODUCTION"]
    bw = (xc - xb) / 2
    for k, nm in enumerate(boxes):
        bx, by = xb + (k % 2) * bw, y0 + (1 - k // 2) * 8.0
        L.append((bx, by + 8, bx + bw, by + 8, 0.4))
        T.append((bx + 1.5, by + 2.6, nm, 1.9, True, blk))
        T.append((bx + bw - 8, by + 2.6, "X" if nm == stat else "NA", 2.0, nm == stat, red if nm == stat else grey))
    L.append((xb + bw, y0, xb + bw, y0 + 16, 0.4))
    # --- right: identification
    ry = [y1, y1 - 8, y1 - 20, y1 - 32, y0]
    for yy in ry[1:-1]:
        L.append((xc, yy, xd, yy, 0.5))
    T.append((xc + 1.5, y1 - 3.0, "CLIENT:", 1.8, False, grey))
    T.append((xc + 16, y1 - 5.6, str(info.get("client") or "")[:70], 3.0, True, blk))
    T.append((xc + 1.5, ry[1] - 3.0, "TITLE:", 1.8, False, grey))
    t1 = str(info.get("set_title", "")) + (f" FOR {info['vessel']}" if info.get("vessel") else "")
    T.append((xc + 16, ry[1] - 5.4, t1[:62], 3.0, True, blk))
    T.append((xc + 16, ry[1] - 10.2, str(title)[:80], 2.3, False, blk))
    w3 = (xd - xc)
    c1, c2 = xc + w3 * 0.58, xc + w3 * 0.76
    for x in (c1, c2):
        L.append((x, ry[3], x, ry[2], 0.5))
    T.append((xc + 1.5, ry[2] - 3.0, "DRG. NO.:", 1.8, False, grey))
    T.append((xc + 3, ry[2] - 8.5, f"{info.get('drawing_no', 'LINING')}-{i:02d}", 2.8, True, blk))
    T.append((c1 + 1.5, ry[2] - 3.0, "REV.:", 1.8, False, grey))
    T.append((c1 + 4, ry[2] - 8.5, str(info.get("rev", 0)), 2.8, True, blk))
    T.append((c2 + 1.5, ry[2] - 3.0, "SHEET", 1.8, False, grey))
    T.append((c2 + 4, ry[2] - 8.5, f"{i} OF {n}", 2.8, True, blk))
    s1, s2 = xc + w3 * 0.18, xc + w3 * 0.40
    for x in (s1, s2):
        L.append((x, y0, x, ry[3], 0.5))
    T.append((xc + 1.5, ry[3] - 3.0, "SIZE:", 1.8, False, grey))
    T.append((xc + 3, ry[3] - 8.5, PAPER, 2.8, True, blk))
    T.append((s1 + 1.5, ry[3] - 3.0, "SCALE:", 1.8, False, grey))
    T.append((s1 + 3, ry[3] - 8.5, f"1:{scale:g}" if scale else "NTS", 2.8, True, blk))
    T.append((s2 + 1.5, ry[3] - 3.0, "CAD FILE NAME:", 1.8, False, grey))
    T.append((s2 + 3, ry[3] - 8.5, str(info.get("cad_file", ""))[:46], 2.2, False, blk))
    return L, T


def title_rows(info, i, n, title, scale):
    """Title block content (label, value) for page i of n."""
    return [
        ("PROJECT", info.get("project", "")),
        ("DRAWING", f"{info.get('set_title', '')} - {title}"),
        ("DRG. NO.", f"{info.get('drawing_no', 'LINING')}-{i:02d}"),
        ("SHEET", f"{i} OF {n}"),
        ("SCALE", f"1:{scale:g} ({PAPER})" if scale else "NTS"),
        ("DATE", _date()),
    ]


# ---------------------------------------------------------------- PDF pages + PNG overview
def render_pages(doc, views, folder, name, info, dpi=90, progress=None):
    """One PDF page per view. Returns (files, extents) where extents[i] = (x0, y0, x1, y1) in model units
    of view i (None when the view could not be rendered). info["paper"] = A3 (default) / A2 / A1."""
    with _PAPER_LOCK:
        prev = _set_paper(info.get("paper") or "A3")
        try:
            return _render_pages(doc, views, folder, name, info, dpi, progress)
        finally:
            _set_paper(prev)


def _render_pages(doc, views, folder, name, info, dpi=90, progress=None):
    try:
        import pymupdf
    except ImportError:                          # older PyMuPDF releases
        import fitz as pymupdf
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, pymupdf as zpdf
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE,
                               color_policy=config.ColorPolicy.COLOR)
    msp = doc.modelspace()
    out = pymupdf.open()
    extents, n = [], len(views)
    area_w = PAGE_W - 2 * BORDER - 10
    area_h = PAGE_H - 2 * BORDER - TB_H - 12
    for i, v in enumerate(views, 1):
        hs = v["handles"]
        backend = speed_up(zpdf.PyMuPdfBackend())
        Frontend(RenderContext(doc), backend, config=cfg).draw_layout(msp, filter_func=lambda e: e.dxf.handle in hs)
        try:
            bb = backend.player().bbox()
            ext = (bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y) if bb.has_data else None
        except Exception:
            ext = None
        extents.append(ext)
        scale = None
        if ext:
            scale = nice_scale(max((ext[2] - ext[0]) / area_w, (ext[3] - ext[1]) / area_h, 1e-9))
        page = layout.Page(PAGE_W, PAGE_H, layout.Units.mm,
                           margins=layout.Margins(top=BORDER + 6, right=BORDER + 5, bottom=BORDER + TB_H + 6,
                                                  left=BORDER + 5))
        try:
            pdf_bytes = backend.get_pdf_bytes(page, settings=layout.Settings(fit_page=True))
            src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception:                       # empty view -> blank page with frame only
            src = pymupdf.open()
            src.new_page(width=PAGE_W * MM, height=PAGE_H * MM)
        out.insert_pdf(src)
        src.close()
        _frame(out[-1], info, i, n, v["title"], scale, v.get("note"))
        if progress:
            progress(i, n, v["title"])
    pdf = folder / f"{name}.pdf"
    out.save(str(pdf), garbage=1, deflate=True)
    png = folder / f"{name}.png"
    try:
        _overview(out, png, max(20, int(round(dpi * 420.0 / PAGE_W))))   # same preview size as an A3 page (A1 = half the dpi)
    except Exception:
        png = None
    out.close()
    return [p for p in (png, pdf) if p], extents


def _frame(pg, info, i, n, title, scale, note=None):
    """Drawing border + title block on a PDF page (A3, units mm -> points)."""
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    W, H = pg.rect.width, pg.rect.height
    k = W / PAGE_W
    b = BORDER * k
    pg.draw_rect(pymupdf.Rect(b, b, W - b, H - b), color=(0, 0, 0), width=1.4)
    lines, texts = title_block(info, i, n, title, scale, note)
    for (ax, ay, bx, by, wgt) in lines:                    # paper mm, origin bottom-left -> PDF points, origin top-left
        pg.draw_line((ax * k, H - ay * k), (bx * k, H - by * k), color=(0, 0, 0), width=wgt * 1.6)
    for (tx, ty, s, hmm, bold, rgb) in texts:
        pg.insert_text((tx * k, H - ty * k), s, fontsize=hmm * MM * 1.35, fontname="hebo" if bold else "helv", color=rgb)
    pg.insert_text((b + 4 * k, b + 6 * k), title, fontsize=11, fontname="hebo")


def _overview(pdf_doc, png, dpi):
    """All pages side by side in one PNG (3 per row) - the picture shown in the UI."""
    from PIL import Image
    ims = []
    for pg in pdf_doc:
        pix = pg.get_pixmap(dpi=dpi, alpha=False)
        ims.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    if not ims:
        return
    cols = 1 if len(ims) == 1 else (2 if len(ims) <= 4 else 3)
    w, h = ims[0].size
    gap = max(8, w // 60)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w + (cols + 1) * gap, rows * h + (rows + 1) * gap), (205, 210, 207))
    for k, im in enumerate(ims):
        r, c = divmod(k, cols)
        sheet.paste(im, (gap + c * (w + gap), gap + r * (h + gap)))
    sheet.save(str(png), optimize=True)


# ---------------------------------------------------------------- AutoCAD paper-space layouts
def _safe_layout_name(s, used):
    bad = '<>/\\":;?*|,=`'
    t = "".join("-" if ch in bad else ch for ch in s).strip()[:60] or "Sheet"
    base, k = t, 2
    while t.lower() in used:
        t = f"{base[:56]} ({k})"
        k += 1
    used.add(t.lower())
    return t


def add_layouts(doc, views, extents, info):
    with _PAPER_LOCK:
        prev = _set_paper(info.get("paper") or "A3")
        try:
            return _add_layouts(doc, views, extents, info)
        finally:
            _set_paper(prev)


def _add_layouts(doc, views, extents, info):
    """One paper-space layout (A3 landscape) per view: border, title block and a viewport showing the
    view at a standard scale. Opening the DXF in AutoCAD shows one layout tab per drawing."""
    used = {lay.name.lower() for lay in doc.layouts}
    n = len(views)
    made = 0
    for i, (v, ext) in enumerate(zip(views, extents), 1):
        if not ext:
            continue
        name = _safe_layout_name(f"{i:02d} {v['title']}", used)
        lay = doc.layouts.new(name)
        lay.page_setup(size=(PAGE_W, PAGE_H), margins=(0, 0, 0, 0), units="mm")
        attr = {"layer": "SHEET_FRAME"}
        if "SHEET_FRAME" not in doc.layers:
            doc.layers.add("SHEET_FRAME", color=7)
        b = BORDER
        lay.add_lwpolyline([(b, b), (PAGE_W - b, b), (PAGE_W - b, PAGE_H - b), (b, PAGE_H - b)], close=True,
                           dxfattribs={**attr, "const_width": 0.5})
        # viewport: drawing area above the title block
        ax0, ay0, ax1, ay1 = b + 5, b + TB_H + 6, PAGE_W - b - 5, PAGE_H - b - 10
        aw, ah = ax1 - ax0, ay1 - ay0
        mw, mh = ext[2] - ext[0], ext[3] - ext[1]
        scale = nice_scale(max(mw / aw, mh / ah, 1e-9))
        lay.add_viewport(center=((ax0 + ax1) / 2, (ay0 + ay1) / 2), size=(aw, ah),
                         view_center_point=((ext[0] + ext[2]) / 2, (ext[1] + ext[3]) / 2), view_height=ah * scale,
                         dxfattribs={"layer": "SHEET_FRAME"})
        # title block: TRL Krosaki standard (same content as the PDF page)
        lines, texts = title_block(info, i, n, v["title"], scale, v.get("note"))
        for (ax, ay, bx, by, wgt) in lines:
            lay.add_line((ax, ay), (bx, by), dxfattribs={**attr, "lineweight": 50 if wgt >= 1 else 25})
        for (tx, ty, s, hmm, bold, rgb) in texts:
            lay.add_text(s, height=hmm, dxfattribs={**attr, "insert": (tx, ty), "color": 1 if rgb[0] > 0.5 else 7})
        lay.add_text(v["title"], height=4.0, dxfattribs={**attr, "insert": (b + 4, PAGE_H - b - 7)})
        made += 1
    if made and "Layout1" in doc.layouts:
        try:
            doc.layouts.delete("Layout1")
        except Exception:
            pass
    return made


# ---------------------------------------------------------------- DWG
def export_dwg(dxf_path):
    """DWG copy of a DXF via the ODA File Converter (free, must be installed on the server).
    Returns the DWG path, or None with the reason."""
    try:
        from ezdxf.addons import odafc
        if not odafc.is_installed():
            return None, "ODA File Converter is not installed on the server - DXF opens directly in AutoCAD"
        dwg = dxf_path.with_suffix(".dwg")
        odafc.convert(str(dxf_path), str(dwg), version="R2018", replace=True)
        return (dwg if dwg.exists() else None), ("" if dwg.exists() else "DWG conversion produced no file")
    except Exception as e:
        return None, f"DWG conversion failed: {e}"
