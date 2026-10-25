"""Refractory core library - formulas, locked properties, audit trail and preset suggestions.

    from refractory_core import RefractoryCore
    core = RefractoryCore("./my_data")               # one folder holds all state
    core.formulas.evaluate("vol_standard_trapezoidal",
                           {"length": 1000, "thickness": 100, "width_top": 160, "width_bottom": 140})
    core.formulas.set_expression("order_qty", "ceil(qty * (1 + allow_pct / 100)) + 2",
                                 actor="alice", reason="site spares policy")
    core.audit.verify()
"""
from __future__ import annotations

import os
from pathlib import Path

from .audit import AuditLog
from .formulas import FormulaError, FormulaRegistry, safe_eval
from .presets import PresetStore
from .properties import ImmutablePropertyError, PropertyStore

__version__ = "0.1.0"
__all__ = ["RefractoryCore", "AuditLog", "FormulaRegistry", "PropertyStore", "PresetStore",
           "FormulaError", "ImmutablePropertyError", "safe_eval", "get_core"]


class RefractoryCore:
    def __init__(self, store_dir: str | Path | None = None):
        self.dir = Path(store_dir or os.environ.get("REFRACTORY_CORE_DIR", "data/core"))
        self.audit = AuditLog(self.dir / "audit.jsonl")
        self.formulas = FormulaRegistry(self.dir, self.audit)
        self.properties = PropertyStore(self.dir, self.audit)
        self.presets = PresetStore(self.dir, self.audit)


_default: RefractoryCore | None = None


def get_core() -> RefractoryCore:
    """Process-wide instance (folder from REFRACTORY_CORE_DIR, default data/core)."""
    global _default
    if _default is None:
        _default = RefractoryCore()
    return _default
