"""Volumes and checks calculated from the same geometry that is drawn."""
import math

from .geometry import region_area_moment, reverse_run, sample
from .model import angle_breaks


def build_report(sheet):
    from refractory_core import get_core
    f = get_core().formulas
    units = sheet.vessel.get("units", "")
    k, vu = (1e-9, "m3") if units == "mm" else (1.0, f"{units}3")
    L = sheet.L
    lines, by_grade, by_layer = [], {}, {}

    # cavity (inside) volume: average over angular intervals
    br = angle_breaks(sheet.design)
    cavity = 0.0
    for a, b in zip(br, br[1:]):
        paths, _ = sheet.boundary_paths((a + b) / 2)
        hot = paths[L].all()
        pts = sample(hot) + [(0.0, hot[-1][1]), (0.0, hot[0][1])]
        cavity += f.evaluate("revolved_volume", {
            "area_moment": region_area_moment(pts)[1],
            "angle_from": a, "angle_to": b, "unit_factor": 1.0,
        })

    rows = []
    for bi, band in enumerate(sheet.bands):
        i0, i1 = sheet.band_idx[bi]
        for sec in band["sectors"]:
            paths, _ = sheet.boundary_paths((sec["from"] + sec["to"]) / 2)
            for j in range(1, L + 1):
                pts = sample(paths[j - 1].run(i0, i1) + reverse_run(paths[j].run(i0, i1)))
                vol = f.evaluate("revolved_volume", {
                    "area_moment": region_area_moment(pts)[1],
                    "angle_from": sec["from"], "angle_to": sec["to"], "unit_factor": 1.0,
                })
                l = sec["layers"][j - 1]
                rows.append((band["name"], f"{sec['from']:g}-{sec['to']:g}", sheet.layers[j - 1], l["grade"], l["thickness"], vol))
                by_grade[l["grade"]] = by_grade.get(l["grade"], 0.0) + vol
                by_layer[sheet.layers[j - 1]] = by_layer.get(sheet.layers[j - 1], 0.0) + vol

    lines.append(f"LINING SUMMARY - {sheet.vessel.get('job', '')}   (volumes in {vu}; geometric, before cutting/joint allowances)")
    lines.append("")
    lines.append(f"{'band':<16}{'sector':<12}{'layer':<14}{'grade':<20}{'thickness':>10}{'volume':>12}")
    for r in rows:
        lines.append(f"{r[0]:<16}{r[1]:<12}{r[2]:<14}{r[3]:<20}{r[4]:>10g}{r[5] * k:>12.3f}")
    lines += ["", "VOLUME BY GRADE"]
    for g, v in by_grade.items():
        lines.append(f"  {g:<24}{v * k:>10.3f} {vu}")
    lines += ["", "VOLUME BY LAYER"]
    for g, v in by_layer.items():
        lines.append(f"  {g:<24}{v * k:>10.3f} {vu}")
    lines.append(f"  {'TOTAL LINING':<24}{sum(by_layer.values()) * k:>10.3f} {vu}")
    lines += ["", "CHECKS", f"  Inside (hot-face) volume : {cavity * k:.3f} {vu}"]
    hs = sheet.design.get("heat_size")
    if hs:
        spec_v = f.evaluate("specific_volume", {"cavity_m3": cavity * k, "heat_size": float(hs)})
        lines.append(f"  Specific volume          : {spec_v:.3f} {vu}/t   (heat size {hs:g} t)")
    return "\n".join(lines)
