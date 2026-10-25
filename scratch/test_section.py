import sys
sys.path.insert(0, '.')
from app.services.part2_extraction.section_reader import read_section
from app.core.config import settings
r = read_section('inputs/input drawing _ naked shell.dxf', getattr(settings, 'tesseract_cmd', None))
print('ok', r.ok, r.reason, 'scale', r.scale_mm_per_unit, r.scale_source, r.scale_confidence)
print('L', r.total_length_mm, 'maxR', r.max_outer_radius_mm, 'OD', r.outer_diameter_mm, 'ID', r.inner_diameter_mm, 't', r.shell_thickness_mm, 'conf', r.confidence)
for s in r.segments:
    print(f'  {s.kind:<10}{s.label:<22} z {s.z0:8.1f}->{s.z1:8.1f}  rout {s.r_out0:8.1f}->{s.r_out1:8.1f} rin {s.r_in0:8.1f}->{s.r_in1:8.1f}')
for n in r.nozzles:
    print('  nozzle', n.label, round(n.axis_z_mm), round(n.tilt_deg, 1), round(n.r_in_mm), round(n.r_out_mm))
for d in r.dims:
    print('  dim', d.text, d.value_mm, d.orientation, d.consistent)
print(r.warnings)
