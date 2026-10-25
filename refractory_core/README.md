# refractory_core

Standard-library-only Python library (no web server needed). Used by the RefractoryAI app, but importable on its own.

```python
from refractory_core import RefractoryCore
core = RefractoryCore("./state")            # all state lives in this folder

# Formulas are editable text; formulas may reference each other by id
core.formulas.evaluate("vol_standard_trapezoidal", {"length": 1000, "thickness": 100, "width_top": 160, "width_bottom": 140})
core.formulas.validate("order_qty", "ceil(qty*(1+allow_pct/100))+2")      # dry run, lists affected formulas
core.formulas.set_expression("order_qty", "ceil(qty*(1+allow_pct/100))+2", actor="alice", reason="site spares")
core.formulas.reset("order_qty", actor="alice", reason="")

# Locked (chemical/physical) properties can never be changed; attempts are audited as DENIED
core.properties.set("compound.MgO.density", 3.0, actor="x", reason="x")   # -> ImmutablePropertyError

# Company grades are registered by you (none shipped); chemistry is frozen once registered
core.properties.register_grade("MY-GRADE-1", generic_class="MgO-C", chemistry={"MgO": 80, "C": 12}, bulk_density=3.0, actor="alice")

# Pre-populate: retrieve recently used / popular settings; nothing applies until the user confirms
core.presets.record_use("barrel_zone", {"vessel": "BOF 180t"}, {"grade": "MY-GRADE-1", "wear": 0.1}, actor="alice")
s = core.presets.suggest("barrel_zone", {"vessel": "BOF 180t"})
settings = core.presets.confirm(s[0]["id"], actor="alice", edits={"wear": 0.12})

core.audit.verify()                         # tamper-evident hash chain
```

Files: `audit.jsonl` (append-only log), `formula_overrides.json`, `property_overrides.json`, `grades.json`, `presets.json`.
Shipped defaults (`defaults/*.json`) are never modified at runtime.
