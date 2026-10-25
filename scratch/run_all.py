import asyncio, sys, glob, os
from pathlib import Path
sys.path.insert(0, '.')
from loguru import logger
logger.remove()
from app.services.part1_ingestion.ingestion_service import ingest_drawing
from app.services.part2_extraction.extraction_service import extract_vessel_geometry


async def one(f):
    ing = await ingest_drawing(Path(f).read_bytes(), os.path.basename(f))
    res = await extract_vessel_geometry(ing)
    g = res.vessel_geometry
    s = g.shell
    print("=" * 70)
    print(os.path.basename(f), "| type", g.vessel_type, "| conf", g.extraction_confidence)
    print(" OD", s.outer_diameter_mm and s.outer_diameter_mm.value,
          "ID", s.inner_diameter_mm and s.inner_diameter_mm.value,
          "L", s.total_length_mm and s.total_length_mm.value,
          "| method", s.profile.extraction_method)
    for x in s.profile.segments:
        print(f"   {x.label:<22}{x.segment_type!s:<22} h={x.height_mm:>8} rb={x.radius_bottom_mm:>8} rt={x.radius_top_mm:>8}")
    print(" nozzles:", [(n.label if hasattr(n, 'label') else n) for n in (g.nozzles or [])][:6])
    print(" warnings:", getattr(res, 'warnings', None))

files = sys.argv[1:] or sorted(glob.glob('inputs/*'))
for f in files:
    if f.lower().endswith('.dwg'):
        continue
    try:
        asyncio.run(one(f))
    except Exception as e:
        print("FAIL", f, type(e).__name__, e)
