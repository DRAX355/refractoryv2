import io, re
p = r"d:\ZeroZeta\refractory-master (2)\refractory-master\ui\app.html"
t = io.open(p, encoding="utf-8", newline="").read()
n0 = len(t)

# ---- 1. TRL / Novafrax product names -> neutral ----
for old, new in [("NVX HMR C2R", "GRADE-A"), ("NVX HMR C3R", "GRADE-B"), ("NVX HMR C4", "GRADE-C"), ("NVX HMR C5R", "GRADE-D"),
                 ("NVX MC-4", "GRADE-E"), ("NVX RAM M-95", "GRADE-J"), ("NVX HCH111B", "GRADE-K"), ("M1811S2", "GRADE-L"),
                 ("M1611TB", "GRADE-H"), ("HU28CTS2", "GRADE-I"),
                 ("Legend: C2R", "Legend: GRADE-A"), ("C5R / C3R", "GRADE-D / GRADE-B"), ("C4 / C3R", "GRADE-C / GRADE-B"),
                 ("Novafrax input", "Supplier input"), ("Novafrax", "Supplier"), ("NVX nozzle", "Nozzle"),
                 ("'NVX/BOF/2026/018'", "''"), ("'trlBofSession'", "'refractoryBofSession'"),
                 ("o.app !== 'TRL-BOF-Lining-Copilot' && ", "")]:
    t = t.replace(old, new)
t = t.replace("t-trl", "t-supplier")
t = re.sub(r"\(TRL\|NVX\)", "(GRADE)", t)          # legacy prefix strippers in grade checks (now harmless)

# ---- 2. vessel values: sample drawing is demo-only, defaults start empty ----
t = t.replace("const EXT_DEF = [", "const SAMPLE_EXT_DEF = [   /* DEMO ONLY: loaded by the 'built-in sample' button, never used for real jobs */")
t = t.replace("EXT_DEF.forEach(([k, g, l, u, d, v, c, how, cmp]) => ext[k] = { grp: g, label: l, unit: u, drawing: d, value: v, conf: c, how, cmp: !!cmp });",
              "SAMPLE_EXT_DEF.forEach(([k, g, l, u, d, v, c, how, cmp]) => ext[k] = { grp: g, label: l, unit: u, drawing: '', value: '', conf: 0, how: 'Awaiting extraction', cmp: !!cmp });")
t = t.replace("const SUGGESTED_ZONES = ", "/* (removed hard-coded suggested zones; zones come from the extracted elevations) */ const _unusedZones = ") if False else t
t = re.sub(r"\n\s*const SUGGESTED_ZONES = [^\n]*", "", t)
t = t.replace("if (S.extSrc) { S.ext = defaults().ext; INIT.ext = clone(S.ext); delete S.extSrc; }",
              "{ const e = {}; SAMPLE_EXT_DEF.forEach(([k, g, l, u, d, v, c, how, cmp]) => e[k] = { grp: g, label: l, unit: u, drawing: d, value: v, conf: c, how, cmp: !!cmp }); S.ext = e; INIT.ext = clone(e); delete S.extSrc; }")
t = t.replace("S.extractionSummary = 'Using built-in sample drawing (180 MT BOF).'", "S.extractionSummary = 'DEMO: using built-in sample drawing values (not from your drawing).'")
# job / brick-pair defaults
t = t.replace("heat: 180, heatMax: 190,", "heat: '', heatMax: '',")
t = re.sub(r"pairs: \[(?:pr\([^)]*\)(?:, )?)+\]", "pairs: []", t)
io.open(p, "w", encoding="utf-8", newline="").write(t)
print(n0, len(t), "remaining:", re.findall(r"TRL|NVX|Novafrax|C[2-5]R\b", t)[:20])
