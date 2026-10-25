import io
p = r"d:\ZeroZeta\refractory-master (2)\refractory-master\app\services\part3_lining\lining\bom.py"
t = io.open(p, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in t else "\n"
t = t.replace("\r\n", "\n")
s = t.index("def unit_volume(b):")
e = t.index("    m = VOL_NOTE.search(_note(b))")
new = '''def _F(fid, variables):
    """Evaluate a user-editable formula from refractory_core (defaults in refractory_core/defaults/formulas.json)."""
    from refractory_core import get_core
    return get_core().formulas.evaluate(fid, variables)


_VOL_FORMULA = {"standard_trapezoidal": "vol_standard_trapezoidal", "steel_cladded": "vol_standard_trapezoidal",
                "bottom_dish_wedge": "vol_bottom_dish_wedge", "center_block": "vol_center_block",
                "safety_lining_std": "vol_safety_lining_std", "safety_lining_rect": "vol_safety_lining_rect",
                "seating_block": "vol_seating_block"}


def unit_volume(b):
    """(dm3, how) for one library brick; (None, reason) if it cannot be computed.
    The volume formula per shape family is user-editable (refractory_core formulas 'vol_<family>')."""
    fam, d = b.get("shape_family"), b.get("dimensions_mm") or {}
    fid = _VOL_FORMULA.get(fam)
    if fid and not (fam == "safety_lining_std" and "thickness" not in d):
        try:
            from refractory_core import get_core
            v = _F(fid, {k: float(x) for k, x in d.items() if isinstance(x, (int, float))})
            return v, "formula " + fid + ": " + get_core().formulas.get(fid)["expression"]
        except Exception:                 # missing dimensions / invalid formula -> fall back to quoted volume
            pass
'''
t = t[:s] + new + t[e:]
sect = '_F("sector_course_volume", {"angle_from": a0, "angle_to": a1, "r_cold": r["r_cold"], "r_hot": r["r_hot"], "course_height": r["h"]})'
t = t.replace('v = math.radians(a1 - a0) / 2 * (r["r_cold"] ** 2 - r["r_hot"] ** 2) * r["h"] / 1e9', "v = " + sect)
t = t.replace('        phi = math.radians(a1 - a0)\n        v = phi / 2 * (r["r_cold"] ** 2 - r["r_hot"] ** 2) * r["h"] / 1e9', "        v = " + sect)
t = t.replace('q_order = math.ceil(l["qty"] * (1 + l["allow_pct"] / 100))', 'q_order = int(_F("order_qty", {"qty": l["qty"], "allow_pct": l["allow_pct"]}))')
t = t.replace('uw = None if l["unit_vol_dm3"] is None else l["unit_vol_dm3"] * l["density"]', 'uw = None if l["unit_vol_dm3"] is None else _F("unit_weight", {"unit_vol_dm3": l["unit_vol_dm3"], "density": l["density"]})')
io.open(p, "w", encoding="utf-8", newline="").write(t.replace("\n", nl))
print([i + 1 for i, l in enumerate(t.split("\n")) if "_F(" in l])
