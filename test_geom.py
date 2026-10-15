from app.services.part3_lining.lining.geometry import _joint, _intersect, add, mul, left, TWO_PI, angle_of
import math

class DummySeg:
    def __init__(self, name, shape, p0, p1):
        self.name = name
        self.shape = shape
        self.p0 = p0
        self.p1 = p1
    def tangent(self, t):
        dx, dy = self.p1[0] - self.p0[0], self.p1[1] - self.p0[1]
        l = math.hypot(dx, dy)
        return (dx/l, dy/l)

barrel = DummySeg("Barrel", "line", (2500, 4000), (2500, 7378))
topcone = DummySeg("Top cone [7378-9200]", "line", (2500, 7378), (2000, 9200))
mouth = DummySeg("Mouth", "line", (2000, 9200), (2000, 10000))

J_barrel = (2500 - 850, 4000)
# Joint 1: Barrel and Top cone
da = 850
db = 750
ta = barrel.tangent(1)
tb = topcone.tangent(0)
print(f"Barrel-Cone: da={da}, db={db}")
j1 = _joint(barrel, da, topcone, db)
if isinstance(j1, tuple):
    E_barrel, J_topcone = j1
else:
    E_barrel = J_topcone = j1
print(f"J_topcone: {J_topcone}")

# Joint 2: Top cone and Mouth
da = 750
db = 550
ta = topcone.tangent(1)
tb = mouth.tangent(0)
print(f"Cone-Mouth: da={da}, db={db}")
j2 = _joint(topcone, da, mouth, db)
if isinstance(j2, tuple):
    E_topcone, J_mouth = j2
else:
    E_topcone = J_mouth = j2
print(f"E_topcone: {E_topcone}")

dx, dy = E_topcone[0] - J_topcone[0], E_topcone[1] - J_topcone[1]
dot = dx * topcone.tangent(0)[0] + dy * topcone.tangent(0)[1]
print(f"Top cone dot product: {dot}")
