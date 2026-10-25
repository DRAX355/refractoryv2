import ezdxf, sys
from collections import Counter
d = ezdxf.readfile('inputs/input drawing _ naked shell.dxf'); m = d.modelspace()
print(Counter(e.dxftype() for e in m)); print(Counter(e.dxf.layer for e in m))
print('units', d.header.get('$INSUNITS'))
for e in m:
    t = e.dxftype()
    if t in ('TEXT', 'MTEXT'):
        print(t, e.dxf.layer, repr(e.dxf.text if t == 'TEXT' else e.text), tuple(round(v) for v in e.dxf.insert))
    elif t == 'DIMENSION':
        print('DIM', e.dxf.layer, e.dxf.get('text'), round(e.dxf.get('actual_measurement', 0) or 0, 1))
print('--- geometry by layer')
for e in m:
    t = e.dxftype()
    if t == 'LINE':
        print('LINE', e.dxf.layer, [round(v) for v in (e.dxf.start.x,e.dxf.start.y)], [round(v) for v in (e.dxf.end.x,e.dxf.end.y)])
    elif t == 'ARC':
        print('ARC', e.dxf.layer, [round(v) for v in (e.dxf.center.x,e.dxf.center.y)], round(e.dxf.radius), round(e.dxf.start_angle), round(e.dxf.end_angle))
    elif t == 'LWPOLYLINE':
        print('LWP', e.dxf.layer, e.closed, [(round(p[0]), round(p[1]), round(p[4], 2)) for p in e.get_points()][:30])
    elif t == 'CIRCLE':
        print('CIRCLE', e.dxf.layer, [round(v) for v in (e.dxf.center.x,e.dxf.center.y)], round(e.dxf.radius))
