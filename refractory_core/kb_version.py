"""Knowledge Base Versioning & Design Version Stamping (FR-04.3).

* Computes a deterministic version + cryptographic SHA-256 fingerprint of the active Knowledge Base:
    - Formulas (defaults + active overrides + source_ref)
    - Properties & registered material grades (locked constants + active settings + source_ref)
    - TRL Shape Library (base 252 marks + custom imported marks + registered shape drawings)
* Saves immutable snapshot JSONs (`data/core/kb_versions/kb_<version_id>.json`) on the server
  whenever a design is stamped or a snapshot is requested.
* Maintains a lightweight version reference index (`data/core/kb_versions/design_kb_stamps.json`)
  that maps every generated design/job to the exact Knowledge Base snapshot JSON it used, so both
  the server and the local client/APK can display and audit the exact KB version table.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from .audit import AuditLog
from .formulas import FormulaRegistry
from .properties import PropertyStore
from .shape_library import ShapeLibraryStore


class KnowledgeBaseVersionStore:
    def __init__(
        self,
        store_dir: str | Path,
        audit: AuditLog,
        formulas: FormulaRegistry,
        properties: PropertyStore,
        shapes: ShapeLibraryStore,
    ):
        self.dir = Path(store_dir)
        self.versions_dir = self.dir / "kb_versions"
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.stamps_path = self.versions_dir / "design_kb_stamps.json"
        self.audit = audit
        self.formulas = formulas
        self.properties = properties
        self.shapes = shapes

    def _compute_payload(self) -> dict[str, Any]:
        flist = self.formulas.list(include_advanced=True)
        plist = self.properties.list()
        glist = sorted(self.properties.grades.values(), key=lambda g: g["code"])
        slib = self.shapes.get_library()

        f_canonical = [
            {
                "id": f["id"],
                "expression": f["expression"],
                "modified": f["modified"],
                "source_ref": f.get("source_ref", {}),
            }
            for f in sorted(flist, key=lambda x: x["id"])
        ]
        p_canonical = [
            {
                "id": p["id"],
                "value": p["value"],
                "unit": p["unit"],
                "locked": p["locked"],
                "source_ref": p.get("source_ref", {}),
            }
            for p in sorted(plist, key=lambda x: x["id"])
        ]
        f_hash = hashlib.sha256(json.dumps(f_canonical, sort_keys=True).encode("utf-8")).hexdigest()
        p_hash = hashlib.sha256(json.dumps({"props": p_canonical, "grades": glist}, sort_keys=True).encode("utf-8")).hexdigest()
        s_hash = self.shapes.fingerprint()

        combined = hashlib.sha256(f"{f_hash}:{p_hash}:{s_hash}".encode("utf-8")).hexdigest()
        short_hash = combined[:10]
        version_id = f"KB-v1.0-{short_hash}"

        return {
            "kb_version": version_id,
            "sha256": combined,
            "components": {
                "formulas": {
                    "count": len(flist),
                    "modified_count": sum(1 for f in flist if f.get("modified")),
                    "sha256": f_hash,
                },
                "properties_and_grades": {
                    "properties_count": len(plist),
                    "grades_count": len(glist),
                    "locked_fingerprint": self.properties.verify_integrity()["fingerprint"],
                    "sha256": p_hash,
                },
                "shape_library": {
                    "library_id": slib.get("library_id", "trl_krosaki_bof_standard_v1"),
                    "total_marks": slib.get("total_marks", len(slib.get("bricks", []))),
                    "primary_document": (slib.get("document_metadata") or {}).get("primary_catalog", {}).get(
                        "document_no", "TRLK/SS/BOF/06/08.10.2016"
                    ),
                    "sha256": s_hash,
                },
            },
            "snapshot": {
                "formulas": f_canonical,
                "formula_overrides": dict(self.formulas.overrides),
                "properties": p_canonical,
                "grades": glist,
                "custom_shapes": dict(self.shapes.custom_bricks),
                "shape_drawings_registry": dict(self.shapes.drawing_registry),
            },
        }

    def current_info(self) -> dict[str, Any]:
        """Return lightweight current KB version metadata without writing a snapshot file."""
        data = self._compute_payload()
        snapshot_file = f"kb_{data['kb_version']}.json"
        return {
            "kb_version": data["kb_version"],
            "sha256": data["sha256"],
            "snapshot_file": snapshot_file,
            "snapshot_saved": (self.versions_dir / snapshot_file).exists(),
            "components": data["components"],
        }

    def save_snapshot(self, *, actor: str = "system", reason: str = "Knowledge base snapshot") -> dict[str, Any]:
        """Persist the full KB version JSON on the server if not already saved."""
        data = self._compute_payload()
        ver = data["kb_version"]
        filename = f"kb_{ver}.json"
        path = self.versions_dir / filename
        is_new = not path.exists()
        if is_new:
            data["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            data["created_by"] = actor
            data["reason"] = reason
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.audit.record(
                "kb.snapshot",
                f"kb:{ver}",
                actor=actor,
                after={"kb_version": ver, "sha256": data["sha256"], "file": filename},
                reason=reason,
            )
        return {
            "kb_version": ver,
            "sha256": data["sha256"],
            "snapshot_file": filename,
            "snapshot_path": str(path),
            "is_new": is_new,
            "components": data["components"],
        }

    def stamp_design(
        self,
        *,
        job_id: str,
        job_ref: str = "",
        output_dir: Optional[str | Path] = None,
        actor: str = "anonymous",
        source_documents: Optional[list[dict]] = None,
    ) -> dict[str, Any]:
        """Save the active KB snapshot on the server and stamp a generated design with the KB version (FR-04.3)."""
        snap = self.save_snapshot(actor=actor, reason=f"Stamped design job {job_ref or job_id}")
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        stamp = {
            "job_id": job_id,
            "job_ref": job_ref or job_id,
            "kb_version": snap["kb_version"],
            "kb_sha256": snap["sha256"],
            "kb_snapshot_json": snap["snapshot_file"],
            "components": snap["components"],
            "source_documents": source_documents or [],
            "stamped_by": actor,
            "stamped_at": ts,
        }

        # Append to server-side / APK reference table (design_kb_stamps.json)
        stamps = self.list_stamps()
        stamps = [s for s in stamps if s.get("job_id") != job_id]
        stamps.insert(0, stamp)
        self.stamps_path.write_text(json.dumps(stamps[:500], indent=2), encoding="utf-8")

        # Also save kb_stamp.json directly inside the design output folder if provided
        if output_dir:
            od = Path(output_dir)
            od.mkdir(parents=True, exist_ok=True)
            (od / "kb_stamp.json").write_text(json.dumps(stamp, indent=2), encoding="utf-8")

        self.audit.record(
            "kb.stamp_design",
            f"job:{job_id}",
            actor=actor,
            after={"kb_version": snap["kb_version"], "kb_snapshot_json": snap["snapshot_file"]},
            reason=f"Stamped design {job_ref or job_id} with Knowledge Base {snap['kb_version']}",
        )
        return stamp

    def list_versions(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.versions_dir.glob("kb_*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append(
                    {
                        "kb_version": d.get("kb_version", p.stem),
                        "sha256": d.get("sha256", ""),
                        "snapshot_file": p.name,
                        "created_at": d.get("created_at", ""),
                        "created_by": d.get("created_by", ""),
                        "components": d.get("components", {}),
                    }
                )
            except Exception:
                continue
        return out

    def get_version(self, kb_version: str) -> dict[str, Any]:
        clean = kb_version if kb_version.startswith("kb_") else f"kb_{kb_version}"
        if not clean.endswith(".json"):
            clean += ".json"
        p = self.versions_dir / clean
        if not p.exists():
            raise KeyError(f"Knowledge base snapshot '{kb_version}' not found.")
        return json.loads(p.read_text(encoding="utf-8"))

    def list_stamps(self) -> list[dict[str, Any]]:
        if not self.stamps_path.exists():
            return []
        try:
            return json.loads(self.stamps_path.read_text(encoding="utf-8"))
        except Exception:
            return []
