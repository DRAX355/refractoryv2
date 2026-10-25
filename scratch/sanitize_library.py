"""One-off: replace TRL product grade codes in the brick library with neutral placeholders.
The mapping is written to scratch/trl_grade_mapping.private.json (keep OUT of the open-source repo)."""
import io, json, re
root = r"d:\ZeroZeta\refractory-master (2)\refractory-master"
files = [root + r"\app\services\part3_lining\data\brick_library_mock.json", root + r"\info\brick_library_mock.json"]
MAP = [("TRL HMR C2R", "GRADE-A"), ("TRL HMR C3R", "GRADE-B"), ("TRL HMR C4", "GRADE-C"), ("TRL HMR C5R", "GRADE-D"),
       ("TRL MC-4", "GRADE-E"), ("M1811S1", "GRADE-F"), ("TRL MGD", "GRADE-G"), ("M1611TB", "GRADE-H"), ("HU28GTS2", "GRADE-I"),
       ("HU28CTS2", "GRADE-I")]
for f in files:
    raw = io.open(f, encoding="utf-8", newline="").read()
    for old, new in MAP:                       # longest first is guaranteed by the list order (C4 after C2R/C3R/C5R is safe: exact token)
        raw = re.sub(re.escape(old) + r"(?![\w-])", new, raw)
    BARE = {"C2R": "GRADE-A", "C3R": "GRADE-B", "C4": "GRADE-C", "C5R": "GRADE-D"}
    raw = re.sub(r'("quality_grade_internal":\s*")([^"]*)(")',
                 lambda m: m.group(1) + re.sub(r"\bC[2-5]R?\b", lambda x: BARE[x.group(0)], m.group(2)) + m.group(3), raw)
    raw = re.sub(r'"material_type":\s*"[^"]*",\s*', "", raw)      # Type-1..9 = TRL internal classification
    raw = re.sub(r"\s*\(Type-\d\)", "", raw)
    json.loads(raw)                            # still valid JSON
    left = re.findall(r"TRL|Type-\d|NVX|Novafrax", raw)
    print(f, "remaining TRL markers:", left)
    io.open(f, "w", encoding="utf-8", newline="").write(raw)
io.open(root + r"\scratch\trl_grade_mapping.private.json", "w").write(json.dumps(dict(MAP), indent=2))
