"""Append-only, hash-chained audit log (JSON Lines).

Every entry stores the SHA-256 of the previous entry, so editing or deleting any line breaks the
chain and `verify()` reports exactly where. The log is never rewritten, only appended to.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def _digest(entry: dict) -> str:
        body = {k: v for k, v in entry.items() if k != "hash"}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

    def _last(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS
        last = None
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if not last:
            return 0, GENESIS
        e = json.loads(last)
        return e["seq"], e["hash"]

    def record(self, action: str, entity: str, *, actor: str = "system", before: Any = None,
               after: Any = None, reason: str = "", outcome: str = "OK", meta: Optional[dict] = None) -> dict:
        """Append one entry. `outcome` is OK or DENIED (blocked attempts are audited too)."""
        with self._lock:
            seq, prev = self._last()
            entry = {"seq": seq + 1, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                     "actor": actor, "action": action, "entity": entity, "outcome": outcome,
                     "reason": reason, "before": before, "after": after, "meta": meta or {}, "prev_hash": prev}
            entry["hash"] = self._digest(entry)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            return entry

    def entries(self, *, entity: str | None = None, action: str | None = None,
                actor: str | None = None, limit: int | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                e = json.loads(line)
                if entity and not e["entity"].startswith(entity):
                    continue
                if action and e["action"] != action:
                    continue
                if actor and e["actor"] != actor:
                    continue
                out.append(e)
        return out[-limit:] if limit else out

    def verify(self) -> dict:
        """Re-compute the whole chain. -> {"ok": bool, "entries": n, "broken_at": seq|None, "reason": str}"""
        prev, n = GENESIS, 0
        for e in self.entries():
            n += 1
            if e["prev_hash"] != prev:
                return {"ok": False, "entries": n, "broken_at": e["seq"], "reason": "chain link broken (entry removed or reordered)"}
            if self._digest(e) != e["hash"]:
                return {"ok": False, "entries": n, "broken_at": e["seq"], "reason": "entry content was modified"}
            prev = e["hash"]
        return {"ok": True, "entries": n, "broken_at": None, "reason": ""}
