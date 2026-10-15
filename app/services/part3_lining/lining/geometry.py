"""
Geometry of an axisymmetric vessel profile in the (r, z) plane.

The profile is a chain of line / arc segments travelling from the bottom centre upward.
The vessel INTERIOR is always on the LEFT of the travel direction.
Offsetting a segment to the left by d moves it toward the vessel interior (lining thickness).

Curves are exchanged as "runs": lists of (x, y, bulge) vertices (DXF polyline convention:
bulge = tan(sweep/4), positive = counter-clockwise). The last vertex has bulge 0.
"""
import math

TWO_PI = 2 * math.pi


def add(a, b): return (a[0] + b[0], a[1] + b[1])
def sub(a, b): return (a[0] - b[0], a[1] - b[1])
def mul(a, s): return (a[0] * s, a[1] * s)
def dot(a, b): return a[0] * b[0] + a[1] * b[1]
def cross(a, b): return a[0] * b[1] - a[1] * b[0]
def length(a): return math.hypot(a[0], a[1])
def left(t): return (-t[1], t[0])
def dist(a, b): return length(sub(a, b))
def angle_of(p, c): return math.atan2(p[1] - c[1], p[0] - c[0])


def unit(a):
    n = length(a)
    if n == 0:
        raise ValueError("zero-length segment")
    return (a[0] / n, a[1] / n)


# ====================================================================== segments
class Seg:
    """One profile segment. shape = 'line' or 'arc'. For arcs: radius, convex.
    convex=True  -> the arc bulges outward (like a dish bottom or a knuckle): centre is on the interior side.
    convex=False -> the arc bulges inward: centre is outside the vessel."""

    def __init__(self, name, p0, p1, shape="line", radius=None, convex=True):
        self.name, self.p0, self.p1 = name, tuple(p0), tuple(p1)
        self.shape, self.R, self.ccw = shape, radius, bool(convex)
        if dist(self.p0, self.p1) < 1e-9:
            raise ValueError(f"segment '{name}' has zero length")
        if shape == "arc":
            if not radius or radius <= 0:
                raise ValueError(f"segment '{name}': arc radius must be > 0")
            chord = dist(self.p0, self.p1)
            if chord / 2 > radius + 1e-6:
                raise ValueError(f"segment '{name}': arc radius {radius:g} is too small for its chord {chord:g}")
            h = math.sqrt(max(radius * radius - chord * chord / 4, 0.0))
            mid = mul(add(self.p0, self.p1), 0.5)
            n = left(unit(sub(self.p1, self.p0)))
            self.C = add(mid, mul(n, h if self.ccw else -h))
            a0, a1 = angle_of(self.p0, self.C), angle_of(self.p1, self.C)
            self.sweep = ((a1 - a0) if self.ccw else (a0 - a1)) % TWO_PI
        else:
            self.C, self.sweep = None, 0.0

    def tangent(self, which):
        if self.shape == "line":
            return unit(sub(self.p1, self.p0))
        rv = sub(self.p0 if which == 0 else self.p1, self.C)
        return unit((-rv[1], rv[0]) if self.ccw else (rv[1], -rv[0]))


# ====================================================================== offsetting
def _off_geom(seg, d):
    if seg.shape == "line":
        t = seg.tangent(0)
        return ("line", add(seg.p0, mul(left(t), d)), t)
    r = seg.R - d if seg.ccw else seg.R + d
    if r <= 1e-6:
        raise ValueError(f"thickness {d:g} is larger than the arc radius of segment '{seg.name}'")
    return ("circle", seg.C, r)


def _line_line(P1, t1, P2, t2):
    d = cross(t1, t2)
    if abs(d) < 1e-12:
        return []
    return [add(P1, mul(t1, cross(sub(P2, P1), t2) / d))]


def _line_circle(P, t, C, r):
    w = sub(P, C)
    b, c = dot(t, w), dot(w, w) - r * r
    disc = b * b - c
    if disc < 0:
        if disc > -1e-6 * max(r * r, 1.0):
            disc = 0.0
        else:
            return []
    s = math.sqrt(disc)
    return [add(P, mul(t, -b + s)), add(P, mul(t, -b - s))]


def _circle_circle(C1, r1, C2, r2):
    d = dist(C1, C2)
    if d < 1e-12:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h2 = r1 * r1 - a * a
    if h2 < 0:
        if h2 > -1e-6 * max(r1 * r1, 1.0):
            h2 = 0.0
        else:
            return []
    h = math.sqrt(h2)
    u = unit(sub(C2, C1))
    m = add(C1, mul(u, a))
    return [add(m, mul(left(u), h)), add(m, mul(left(u), -h))]


def _intersect(ga, gb):
    if ga[0] == "line" and gb[0] == "line":
        return _line_line(ga[1], ga[2], gb[1], gb[2])
    if ga[0] == "line":
        return _line_circle(ga[1], ga[2], gb[1], gb[2])
    if gb[0] == "line":
        return _line_circle(gb[1], gb[2], ga[1], ga[2])
    return _circle_circle(ga[1], ga[2], gb[1], gb[2])


def _joint(sa, da, sb, db):
    """Point where the offset of segment a meets the offset of segment b."""
    P = sa.p1
    ta, tb = sa.tangent(1), sb.tangent(0)
    na, nb = left(ta), left(tb)
    if abs(cross(ta, tb)) < 1e-5 and dot(ta, tb) > 0:          # smooth (tangent) joint (split lines are rounded)
        if abs(ta[1]) > 1e-6:
            # zone boundary on a wall: bricks are laid in horizontal courses, so the boundary
            # (and a thickness change = step on the hot face) is horizontal, at the height of the joint
            pa = add(add(P, mul(na, da)), mul(ta, -na[1] * da / ta[1]))
            pb = add(add(P, mul(nb, db)), mul(tb, -nb[1] * db / tb[1]))
            return pb if abs(da - db) <= 1e-9 else (pa, pb)
        if abs(da - db) > 1e-9:
            # thickness changes on a smooth curved wall: step on the hot face, square to the wall
            return add(P, mul(na, da)), add(P, mul(nb, db))
        return add(P, mul(na, da))

    if abs(da - db) > 1e-9 and sa.shape == 'line' and sb.shape == 'line':
        if abs(ta[1]) > 1e-6 and abs(tb[1]) > 1e-6:
            pa = add(add(P, mul(na, da)), mul(ta, -na[1] * da / ta[1]))
            pb = add(add(P, mul(nb, db)), mul(tb, -nb[1] * db / tb[1]))
            return pa, pb

    nominal = add(P, mul(add(mul(na, da), mul(nb, db)), 0.5))
    cands = _intersect(_off_geom(sa, da), _off_geom(sb, db))
    if not cands:
        raise ValueError(f"offset curves of '{sa.name}' and '{sb.name}' do not meet (thickness too large?)")
    return min(cands, key=lambda p: dist(p, nominal))


class OPath:
    """An offset copy of the whole profile. J[i] = start of segment i (J[n] = end of last)."""

    def __init__(self, segs, J, bulges, E=None):
        # J[i] = start of segment i; E[i] = end of segment i-1 (differs from J[i] only at a hot-face step)
        self.segs, self.J, self.bulges = segs, J, bulges
        self.E = E or list(J)

    def run(self, i, j):
        v = []
        for k in range(i, j + 1):
            v.append((self.J[k][0], self.J[k][1], self.bulges[k]))
            e = self.E[k + 1]
            if k < j and dist(e, self.J[k + 1]) > 1e-9:      # step between two segments of the run
                v.append((e[0], e[1], 0.0))
        e = self.E[j + 1]
        v.append((e[0], e[1], 0.0))
        return v

    def all(self):
        return self.run(0, len(self.segs) - 1)


def offset_path(segs, dists):
    """Offset every segment i inward by dists[i] (negative = outward)."""
    n = len(segs)
    J = [None] * (n + 1)
    E = [None] * (n + 1)
    J[0] = add(segs[0].p0, mul(left(segs[0].tangent(0)), dists[0]))
    E[0] = J[0]
    for i in range(1, n):
        p = _joint(segs[i - 1], dists[i - 1], segs[i], dists[i])
        if isinstance(p[0], tuple):                           # step: (end of i-1, start of i)
            E[i], J[i] = p
        else:
            E[i] = J[i] = p
    J[n] = add(segs[-1].p1, mul(left(segs[-1].tangent(1)), dists[-1]))
    E[n] = J[n]
    bulges = []
    for i, s in enumerate(segs):
        A, B = J[i], E[i + 1]
        if s.shape == "line":
            if dot(sub(B, A), s.tangent(0)) <= 1e-9:
                raise ValueError(f"thickness too large: segment '{s.name}' disappears")
            bulges.append(0.0)
        else:
            if dist(A, B) < 1e-9:
                bulges.append(0.0)
                continue
            aA, aB = angle_of(A, s.C), angle_of(B, s.C)
            sweep = ((aB - aA) if s.ccw else (aA - aB)) % TWO_PI
            if sweep > math.pi + 1e-6:
                raise ValueError(f"thickness too large: arc segment '{s.name}' disappears")
            bulges.append(math.tan(sweep / 4) * (1 if s.ccw else -1))
    return OPath(segs, J, bulges, E)


# ====================================================================== runs (bulge polylines)
def reverse_run(v):
    out = [(v[i][0], v[i][1], -v[i - 1][2]) for i in range(len(v) - 1, 0, -1)]
    out.append((v[0][0], v[0][1], 0.0))
    return out


def arc_from_bulge(p, q, b):
    """-> (centre, radius, start_angle, signed_sweep)"""
    sweep = 4 * math.atan(b)
    c = dist(p, q)
    s = abs(b) * c / 2
    r = (c * c / 4 + s * s) / (2 * s)
    mid = mul(add(p, q), 0.5)
    n = left(unit(sub(q, p)))
    sign = 1 if b > 0 else -1
    C = add(mid, mul(n, sign * (r - s)))
    return C, r, angle_of(p, C), sweep


def sample(v, step_deg=2.0):
    """Polyline points of a run (arcs subdivided)."""
    pts = [(v[0][0], v[0][1])]
    for i in range(len(v) - 1):
        p, q, b = v[i][:2], v[i + 1][:2], v[i][2]
        if abs(b) > 1e-12 and dist(p, q) > 1e-12:
            C, r, a0, sw = arc_from_bulge(p, q, b)
            n = max(1, int(math.ceil(abs(math.degrees(sw)) / step_deg)))
            for k in range(1, n):
                a = a0 + sw * k / n
                pts.append((C[0] + r * math.cos(a), C[1] + r * math.sin(a)))
        pts.append(q)
    return pts


def radius_at_z(v, z):
    """Largest x where the run crosses the horizontal line at height z (None if it does not)."""
    xs = []
    for i in range(len(v) - 1):
        p, q, b = v[i][:2], v[i + 1][:2], v[i][2]
        if abs(b) < 1e-12:
            if abs(q[1] - p[1]) < 1e-12:
                continue
            s = (z - p[1]) / (q[1] - p[1])
            if -1e-9 <= s <= 1 + 1e-9:
                xs.append(p[0] + s * (q[0] - p[0]))
        else:
            C, r, a0, sw = arc_from_bulge(p, q, b)
            dy = z - C[1]
            if abs(dy) > r + 1e-9:
                continue
            dx = math.sqrt(max(r * r - dy * dy, 0.0))
            for x in (C[0] + dx, C[0] - dx):
                a = math.atan2(dy, x - C[0])
                t = ((a - a0) % TWO_PI) if sw > 0 else ((a0 - a) % TWO_PI)
                if t <= abs(sw) + 1e-9:
                    xs.append(x)
    return max(xs) if xs else None


def bounds(v):
    pts = sample(v)
    xs, zs = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), max(xs), min(zs), max(zs)


def region_area_moment(pts):
    """Area and first moment about the axis (integral of x dA) of a closed polygon (abs values)."""
    A = M = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        c = x0 * y1 - x1 * y0
        A += c
        M += (x0 + x1) * c
    return abs(A) / 2, abs(M) / 6


def revolved_volume(run_outer, run_inner):
    """Volume of the full revolution of the region between two runs (outer forward, inner reversed)."""
    pts = sample(run_outer + reverse_run(run_inner))
    return 2 * math.pi * region_area_moment(pts)[1]
