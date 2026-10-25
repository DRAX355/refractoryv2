"""Pre-populate suggestions: retrieval over previously confirmed settings.

How it works (local, deterministic, no external service - this is the *retrieval* half of RAG; an LLM
can be plugged in on top by passing the suggestions to it, but is not required):
  1. Each time the user confirms a settings set, it is stored with its context (e.g. vessel type, zone).
  2. `suggest(context)` ranks stored sets by  0.6 x context similarity (TF-IDF cosine)
                                             + 0.2 x popularity (times confirmed)
                                             + 0.2 x recency (30-day half-life).
  3. Nothing is applied automatically: a suggestion is `pending` until the user confirms or rejects it.
     Both outcomes are audited.
  4. To avoid overwhelming the user, a suggestion lists `review_fields` (values that differ from what is
     usually used) separately from `usual_fields` (the common values, collapsed by default).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .audit import AuditLog

_TOK = re.compile(r"[a-z0-9]+")


def _tokens(ctx: dict) -> list[str]:
    return [t for k, v in sorted(ctx.items()) for t in _TOK.findall(f"{k} {v}".lower())]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PresetStore:
    def __init__(self, store_dir: str | Path, audit: AuditLog):
        self.audit = audit
        self.path = Path(store_dir) / "presets.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: dict[str, dict] = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.items, indent=2), "utf-8")

    # ---------- learning ----------
    def record_use(self, kind: str, context: dict, settings: dict, *, actor: str) -> dict:
        """Store / bump a confirmed settings set. `kind` = what the settings are for (e.g. 'barrel_zone')."""
        pid = hashlib.sha1(json.dumps([kind, settings], sort_keys=True, default=str).encode()).hexdigest()[:12]
        it = self.items.setdefault(pid, {"id": pid, "kind": kind, "settings": settings, "contexts": [],
                                         "uses": 0, "last_used": None, "users": []})
        it["uses"] += 1
        it["last_used"] = _now().isoformat()
        if context and context not in it["contexts"]:
            it["contexts"].append(context)
        if actor not in it["users"]:
            it["users"].append(actor)
        self._save()
        self.audit.record("preset.use", f"preset:{pid}", actor=actor, after=settings, meta={"kind": kind, "context": context})
        return it

    # ---------- retrieval ----------
    def suggest(self, kind: str, context: dict, top_k: int = 3) -> list[dict]:
        cands = [i for i in self.items.values() if i["kind"] == kind]
        if not cands:
            return []
        docs = [Counter(t for c in (i["contexts"] or [{}]) for t in _tokens(c)) for i in cands]
        df = Counter(t for d in docs for t in d)
        N = len(docs)
        idf = lambda t: math.log((1 + N) / (1 + df.get(t, 0))) + 1

        def vec(c: Counter) -> dict:
            return {t: n * idf(t) for t, n in c.items()}

        q = vec(Counter(_tokens(context)))
        qn = math.sqrt(sum(v * v for v in q.values())) or 1.0
        max_uses = max(i["uses"] for i in cands)
        consensus = self._consensus(cands)
        out = []
        for it, d in zip(cands, docs):
            dv = vec(d)
            dn = math.sqrt(sum(v * v for v in dv.values())) or 1.0
            sim = sum(q.get(t, 0) * v for t, v in dv.items()) / (qn * dn)
            pop = math.log1p(it["uses"]) / math.log1p(max_uses) if max_uses else 0
            age_days = (_now() - datetime.fromisoformat(it["last_used"])).total_seconds() / 86400 if it["last_used"] else 999
            rec = 0.5 ** (age_days / 30)
            score = 0.6 * sim + 0.2 * pop + 0.2 * rec
            review = {k: v for k, v in it["settings"].items() if consensus.get(k) != v}
            usual = {k: v for k, v in it["settings"].items() if k not in review}
            out.append({"id": it["id"], "score": round(score, 4), "status": "pending",
                        "why": f"similar context {sim:.0%}, confirmed {it['uses']}x, last used {int(age_days)} day(s) ago",
                        "settings": it["settings"], "review_fields": review, "usual_fields": usual})
        return sorted(out, key=lambda s: -s["score"])[:top_k]

    @staticmethod
    def _consensus(cands: list[dict]) -> dict:
        """Most common value of each field across stored sets of this kind."""
        votes: dict[str, Counter] = {}
        for i in cands:
            for k, v in i["settings"].items():
                votes.setdefault(k, Counter())[json.dumps(v, sort_keys=True, default=str)] += i["uses"]
        return {k: json.loads(c.most_common(1)[0][0]) for k, c in votes.items()}

    # ---------- user decision ----------
    def confirm(self, preset_id: str, *, actor: str, edits: dict | None = None, context: dict | None = None) -> dict:
        """User accepted a suggestion (optionally after editing some fields). Returns the settings to apply."""
        it = self.items[preset_id]
        applied = {**it["settings"], **(edits or {})}
        self.audit.record("preset.confirm", f"preset:{preset_id}", actor=actor, before=it["settings"], after=applied,
                          meta={"edited_fields": sorted(edits or {})})
        self.record_use(it["kind"], context or {}, applied, actor=actor)
        return applied

    def reject(self, preset_id: str, *, actor: str, reason: str = "") -> None:
        self.audit.record("preset.reject", f"preset:{preset_id}", actor=actor, reason=reason)
