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

PAGE_W, PAGE_H = 420.0, 297.0                 # A3 landscape, mm
BORDER = 10.0
TB_H = 30.0                                   # title block height (mm)
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


def title_rows(info, i, n, title, scale):
    """Title block content (label, value) for page i of n."""
    return [
        ("PROJECT", info.get("project", "")),
        ("DRAWING", f"{info.get('set_title', '')} - {title}"),
        ("DRG. NO.", f"{info.get('drawing_no', 'LINING')}-{i:02d}"),
        ("SHEET", f"{i} OF {n}"),
        ("SCALE", f"1:{scale:g} (A3)" if scale else "NTS"),
        ("DATE", _date()),
    ]


# ---------------------------------------------------------------- PDF pages + PNG overview
def render_pages(doc, views, folder, name, info, dpi=90, progress=None):
    """One PDF page per view. Returns (files, extents) where extents[i] = (x0, y0, x1, y1) in model units
    of view i (None when the view could not be rendered)."""
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
        _overview(out, png, dpi)
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
    tb_w = 200 * k
    x0, y0, x1, y1 = W - b - tb_w, H - b - TB_H * k, W - b, H - b
    pg.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=(0, 0, 0), width=1.0)
    rows = title_rows(info, i, n, title, scale)
    rh = (y1 - y0) / len(rows)
    lab_w = 28 * k
    for r, (lab, val) in enumerate(rows):
        yy = y0 + r * rh
        if r:
            pg.draw_line((x0, yy), (x1, yy), color=(0, 0, 0), width=0.5)
        pg.insert_text((x0 + 1.5 * k, yy + rh * 0.72), lab, fontsize=6.2, fontname="helv", color=(0.3, 0.3, 0.3))
        fs = 8.5 if r == 1 else 7.5
        txt = str(val)
        maxc = int((tb_w - lab_w - 3 * k) / (fs * 0.5))
        if len(txt) > maxc:
            txt = txt[:maxc - 1] + "…"
        pg.insert_text((x0 + lab_w, yy + rh * 0.72), txt, fontsize=fs, fontname="hebo" if r == 1 else "helv")
    pg.draw_line((x0 + lab_w - 2 * k, y0), (x0 + lab_w - 2 * k, y1), color=(0, 0, 0), width=0.5)
    # left part of the strip: company / status / edited-input warning
    lx = b + 3 * k
    pg.draw_line((b, y0), (x0, y0), color=(0, 0, 0), width=1.0)
    pg.insert_text((lx, y0 + 7 * k), info.get("company", "REFRACTORY LINING DESIGN"), fontsize=10, fontname="hebo")
    pg.insert_text((lx, y0 + 13 * k), info.get("subtitle", "Generated by the Lining Copilot from the confirmed inputs. "
                                       "All dimensions in mm."), fontsize=6.5, fontname="helv")
    pg.insert_text((lx, y0 + 18.5 * k), info.get("status", "STATUS: FOR CUSTOMER APPROVAL - DRAFT, CHECK BEFORE ISSUE"),
                   fontsize=7, fontname="hebo", color=(0.75, 0, 0))
    if info.get("edited"):
        pg.insert_text((lx, y0 + 24 * k), f"! {info['edited']} input value(s) were EDITED by the designer (not read from "
                       f"the uploaded drawing) - see sheet 'DESIGN INPUT VALUES' of the lining GA.",
                       fontsize=6.5, fontname="hebo", color=(0.85, 0, 0))
    if note:
        pg.insert_text((lx, y0 + 28.5 * k), str(note)[:150], fontsize=6, fontname="helv", color=(0.25, 0.25, 0.25))
    # sheet title above the title block, top-left of the drawing area
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
        # title block (bottom right) + left strip
        tb_w = 200.0
        x0, y0, x1, y1 = PAGE_W - b - tb_w, b, PAGE_W - b, b + TB_H
        lay.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs=attr)
        lay.add_line((b, y1), (x0, y1), dxfattribs=attr)
        rows = title_rows(info, i, n, v["title"], scale)
        rh = TB_H / len(rows)
        for r, (lab, val) in enumerate(rows):
            yy = y1 - (r + 1) * rh
            if r < len(rows) - 1:
                lay.add_line((x0, yy), (x1, yy), dxfattribs=attr)
            lay.add_text(lab, height=1.8, dxfattribs={**attr, "insert": (x0 + 1.5, yy + 1.4)})
            lay.add_text(str(val)[:70], height=2.5 if r == 1 else 2.2, dxfattribs={**attr, "insert": (x0 + 28, yy + 1.2)})
        lay.add_line((x0 + 26, y0), (x0 + 26, y1), dxfattribs=attr)
        lay.add_text(info.get("company", "REFRACTORY LINING DESIGN"), height=3.5,
                     dxfattribs={**attr, "insert": (b + 3, y1 - 7)})
        lay.add_text(info.get("status", "STATUS: FOR CUSTOMER APPROVAL - DRAFT, CHECK BEFORE ISSUE"), height=2.4,
                     dxfattribs={**attr, "insert": (b + 3, y1 - 19), "color": 1})
        if info.get("edited"):
            lay.add_text(f"! {info['edited']} input value(s) EDITED by the designer - see 'DESIGN INPUT VALUES'",
                         height=2.2, dxfattribs={**attr, "insert": (b + 3, y1 - 25), "color": 1})
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