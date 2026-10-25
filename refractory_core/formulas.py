"""Safe expression evaluation (AST whitelist - no eval) and a user-editable formula registry.

* Formulas are plain-text expressions that may use input variables, math functions and *other
  formulas by id*. Editing a formula therefore changes every formula that depends on it.
* Shipped defaults live in defaults/formulas.json and are never modified; user edits are stored as
  overrides, so any formula can be reset to its default.
* Every edit is validated (syntax, unknown names, cycles, test evaluation) before it is accepted and
  is written to the audit log.
"""
from __future__ import annotations

import ast
import json
import math
import operator
from pathlib import Path
from typing import Any, Optional

from .audit import AuditLog

DEFAULTS_DIR = Path(__file__).parent / "defaults"

FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin,
         "acos": math.acos, "atan": math.atan, "atan2": math.atan2, "radians": math.radians,
         "rad": math.radians, "degrees": math.degrees, "deg": math.degrees, "log": math.log,
         "exp": math.exp, "min": min, "max": max, "abs": abs, "round": round, "ceil": math.ceil,
         "floor": math.floor}
CONSTS = {"pi": math.pi, "e": math.e}
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
        ast.Eq: operator.eq, ast.NotEq: operator.ne}


class FormulaError(ValueError):
    pass


def names_in(expression: str) -> set[str]:
    """Variable / formula names used by an expression (functions and constants excluded)."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"syntax error: {e.msg}")
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - called - set(CONSTS)


def safe_eval(expression: str, scope: dict[str, float]) -> float:
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"syntax error: {e.msg}")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in CONSTS:
                return CONSTS[n.id]
            if n.id in scope:
                return scope[n.id]
            raise FormulaError(f"unknown name '{n.id}'")
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 100:
                raise FormulaError("exponent too large")
            return _BIN[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UN:
            return _UN[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCS and not n.keywords:
            return FUNCS[n.func.id](*[ev(a) for a in n.args])
        if isinstance(n, ast.IfExp):
            return ev(n.body) if ev(n.test) else ev(n.orelse)
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in _CMP:
            return _CMP[type(n.ops[0])](ev(n.left), ev(n.comparators[0]))
        raise FormulaError(f"'{ast.unparse(n)}' is not allowed in a formula")

    try:
        val = ev(tree)
    except ZeroDivisionError:
        raise FormulaError("division by zero")
    except (ValueError, TypeError, OverflowError) as e:
        raise FormulaError(str(e))
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise FormulaError("result is not a number")
    return float(val)


class FormulaRegistry:
    def __init__(self, store_dir: str | Path, audit: AuditLog):
        self.audit = audit
        self.path = Path(store_dir) / "formula_overrides.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.defaults: dict[str, dict] = {}
        for fn in ("formulas.json", "formulas_lining.json", "formulas_ui_extraction.json"):
            fp = DEFAULTS_DIR / fn
            if fp.exists():
                for f in json.loads(fp.read_text("utf-8"))["formulas"]:
                    self.defaults[f["id"]] = f
        self.overrides: dict[str, dict] = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}

    # ---------- reading ----------
    def get(self, fid: str) -> dict:
        if fid not in self.defaults:
            raise KeyError(f"unknown formula '{fid}'")
        f = dict(self.defaults[fid])
        f["default_expression"] = f["expression"]
        if fid in self.overrides:
            f["expression"] = self.overrides[fid]["expression"]
        f["modified"] = f["expression"] != f["default_expression"]
        f["depends_on"] = sorted(n for n in names_in(f["expression"]) if n in self.defaults)
        f["dependents"] = self.dependents(fid)
        return f

    def list(self, include_advanced: bool = True) -> list[dict]:
        return [self.get(i) for i in self.defaults if include_advanced or not self.defaults[i].get("advanced")]

    def _expr(self, fid: str) -> str:
        return self.overrides.get(fid, {}).get("expression", self.defaults[fid]["expression"])

    def dependents(self, fid: str) -> list[str]:
        """All formulas that (directly or indirectly) use `fid` - they change when it changes."""
        out, frontier = set(), {fid}
        while frontier:
            nxt = {i for i in self.defaults if i not in out and names_in(self._expr(i)) & frontier}
            out |= nxt
            frontier = nxt
        return sorted(out)

    # ---------- evaluation ----------
    def evaluate(self, fid: str, variables: dict[str, float], _expr_override: dict | None = None) -> float:
        """Evaluate formula `fid`, resolving references to other formulas recursively."""
        ov = _expr_override or {}
        stack: list[str] = []

        def run(i: str) -> float:
            if i in stack:
                raise FormulaError("circular reference: " + " -> ".join(stack + [i]))
            stack.append(i)
            expr = ov.get(i) or self._expr(i)
            scope = dict(variables)
            for n in names_in(expr):
                if n not in scope and n in self.defaults:
                    scope[n] = run(n)
            val = safe_eval(expr, scope)
            stack.pop()
            return val

        return run(fid)

    def has(self, fid: str) -> bool:
        return fid in self.defaults

    # ---------- editing ----------
    def validate(self, fid: str, expression: str, sample: Optional[dict[str, float]] = None) -> dict:
        """Check an edit without saving. -> {"ok", "error", "result", "unknown", "affected"}"""
        try:
            used = names_in(expression)
            all_vars = sorted({v for f in self.defaults.values() for v in f.get("variables", {})})
            probe_vars = {v: 100.0 + idx * 13.7 for idx, v in enumerate(all_vars)}
            for rk in ("R", "R_d", "R_sphere", "dish_r"):
                if rk in probe_vars:
                    probe_vars[rk] = 8000.0
            probe_vars.update(sample or {})
            unknown = sorted(n for n in used if n not in self.defaults and n not in probe_vars)
            if unknown:
                return {"ok": False, "error": f"unknown name(s): {', '.join(unknown)}", "unknown": unknown}
            if fid in used:
                return {"ok": False, "error": "a formula cannot use itself"}
            result = self.evaluate(fid, probe_vars, {fid: expression})
            return {"ok": True, "error": "", "result": result, "affected": self.dependents(fid)}
        except FormulaError as e:
            return {"ok": False, "error": str(e)}

    def set_expression(self, fid: str, expression: str, *, actor: str, reason: str) -> dict:
        if fid not in self.defaults:
            raise KeyError(f"unknown formula '{fid}'")
        if not reason.strip():
            raise FormulaError("a reason is required for every formula change")
        chk = self.validate(fid, expression)
        if not chk["ok"]:
            self.audit.record("formula.update", f"formula:{fid}", actor=actor, after=expression,
                              reason=reason, outcome="DENIED", meta={"error": chk["error"]})
            raise FormulaError(chk["error"])
        before = self._expr(fid)
        self.overrides[fid] = {"expression": expression.strip()}
        self._save()
        self.audit.record("formula.update", f"formula:{fid}", actor=actor, before=before,
                          after=expression.strip(), reason=reason, meta={"affected": chk["affected"]})
        return self.get(fid)

    def reset(self, fid: str, *, actor: str, reason: str) -> dict:
        before = self._expr(fid)
        self.overrides.pop(fid, None)
        self._save()
        self.audit.record("formula.reset", f"formula:{fid}", actor=actor, before=before,
                          after=self.defaults[fid]["expression"], reason=reason or "reset to default")
        return self.get(fid)

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.overrides, indent=2), "utf-8")
