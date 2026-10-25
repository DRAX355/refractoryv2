"""Property store with immutable (locked) chemical / physical-constant properties.

* Locked properties ship in defaults/properties.json. They cannot be changed through this API; any
  attempt raises ImmutablePropertyError and is written to the audit log as DENIED.
* The shipped file's locked values are fingerprinted; `verify_integrity()` detects tampering.
* Editable settings (e.g. default allowance) are stored as audited overrides.
* Product grades are registered by the user/company (nothing company-specific is shipped).
  A grade's `chemistry` is frozen at registration - to correct it, register a new revision.
  Its `bulk_density` / `notes` remain editable (audited).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .audit import AuditLog

DEFAULTS = Path(__file__).parent / "defaults" / "properties.json"


class ImmutablePropertyError(PermissionError):
    pass


class PropertyStore:
    def __init__(self, store_dir: str | Path, audit: AuditLog):
        self.audit = audit
        self.dir = Path(store_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._ov_path = self.dir / "property_overrides.json"
        self._grade_path = self.dir / "grades.json"
        self.props = {p["id"]: p for p in json.loads(DEFAULTS.read_text("utf-8"))["properties"]}
        self.overrides: dict = json.loads(self._ov_path.read_text("utf-8")) if self._ov_path.exists() else {}
        self.grades: dict = json.loads(self._grade_path.read_text("utf-8")) if self._grade_path.exists() else {}

    # ---------- properties ----------
    def _locked_fingerprint(self) -> str:
        locked = sorted((p["id"], p["value"], p["unit"]) for p in self.props.values() if p["locked"])
        return hashlib.sha256(json.dumps(locked).encode()).hexdigest()

    def verify_integrity(self, expected: str | None = None) -> dict:
        """Fingerprint of the locked data. Pass a previously stored fingerprint to detect changes."""
        fp = self._locked_fingerprint()
        return {"fingerprint": fp, "ok": expected is None or expected == fp}

    def get(self, pid: str) -> dict:
        p = dict(self.props[pid])
        if not p["locked"] and pid in self.overrides:
            p["value"] = self.overrides[pid]
        return p

    def value(self, pid: str) -> float:
        return self.get(pid)["value"]

    def list(self, category: str | None = None) -> list[dict]:
        return [self.get(i) for i, p in self.props.items() if category in (None, p["category"])]

    def set(self, pid: str, value: float, *, actor: str, reason: str) -> dict:
        if pid not in self.props:
            raise KeyError(f"unknown property '{pid}'")
        p = self.props[pid]
        if p["locked"]:
            self.audit.record("property.update", f"property:{pid}", actor=actor, before=p["value"], after=value,
                              reason=reason, outcome="DENIED", meta={"why": "locked chemical/physical constant"})
            raise ImmutablePropertyError(f"'{pid}' is a fixed chemical/physical property and cannot be changed")
        if not reason.strip():
            raise ValueError("a reason is required for every change")
        before = self.get(pid)["value"]
        self.overrides[pid] = float(value)
        self._ov_path.write_text(json.dumps(self.overrides, indent=2), "utf-8")
        self.audit.record("property.update", f"property:{pid}", actor=actor, before=before, after=float(value), reason=reason)
        return self.get(pid)

    # ---------- user-registered grades ----------
    def register_grade(self, code: str, *, generic_class: str, chemistry: dict[str, float], bulk_density: float,
                       actor: str, reason: str = "", notes: str = "") -> dict:
        """Add a product grade. `chemistry` (wt-% by oxide/element) is frozen once registered."""
        if code in self.grades:
            raise ImmutablePropertyError(f"grade '{code}' already exists; its chemistry is frozen - register a new revision code")
        total = sum(chemistry.values())
        if chemistry and total > 100.5:
            raise ValueError(f"chemistry sums to {total:g} wt-% (> 100)")
        g = {"code": code, "generic_class": generic_class, "chemistry": dict(chemistry),
             "bulk_density": float(bulk_density), "notes": notes}
        self.grades[code] = g
        self._save_grades()
        self.audit.record("grade.register", f"grade:{code}", actor=actor, after=g, reason=reason)
        return g

    def update_grade(self, code: str, *, actor: str, reason: str, **fields) -> dict:
        g = self.grades[code]
        bad = [k for k in fields if k not in ("bulk_density", "notes")]
        if bad:
            self.audit.record("grade.update", f"grade:{code}", actor=actor, after=fields, reason=reason,
                              outcome="DENIED", meta={"why": f"{bad} are frozen"})
            raise ImmutablePropertyError(f"{bad} cannot be edited (chemistry / class are frozen); register a new revision")
        before = {k: g[k] for k in fields}
        g.update(fields)
        self._save_grades()
        self.audit.record("grade.update", f"grade:{code}", actor=actor, before=before, after=fields, reason=reason)
        return g

    def densities(self) -> dict[str, float]:
        """{grade code: bulk density} in the shape the BOM engine expects."""
        return {c: g["bulk_density"] for c, g in self.grades.items()}

    def _save_grades(self) -> None:
        self._grade_path.write_text(json.dumps(self.grades, indent=2), "utf-8")
