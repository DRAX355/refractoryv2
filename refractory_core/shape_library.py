"""TRL Shape Library Manager, Import Format Validator & Brick Mark Drawing Registry (FR-04.1, FR-04.2, FR-01.3).

* Defines the canonical TRL shape-library import schema (supporting JSON, CSV, and dict payloads).
* Validates every brick shape entry: required dimensions per shape_family, positive geometry,
  source traceability (`source_ref`), and computed-vs-catalog volume cross-checks.
* Manages the default TRL BOF brick library (`trl_brick_library.json` - 252 marks across all TRL series
  plus Barrel Safety Bricks TYP1-TYP30 and 180MT reference shapes) alongside user-imported shape packs
  and individual shape drawing registrations (`FR-01.3`).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from .audit import AuditLog

DEFAULT_LIB_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "services"
    / "part3_lining"
    / "data"
    / "trl_brick_library.json"
)

SHAPE_FAMILY_REQUIRED_DIMS: dict[str, tuple[str, ...]] = {
    "standard_trapezoidal": ("length", "width_top", "width_bottom", "thickness"),
    "steel_cladded": ("length", "width_top", "width_bottom", "thickness"),
    "barrel_safety_brick": ("A", "B", "C", "T"),
    "key_wedge_series": ("a", "b", "h", "s1", "s2"),
    "beveled_key_ht": ("a", "b", "h", "s"),
    "double_tapered": ("a1", "a2", "b1", "b2", "s", "h"),
    "triple_tapered": ("a1", "a2", "b1", "b2", "s1", "s2", "h"),
    "bottom_dish_wedge": ("overall_length", "face_width_top", "face_width_bottom", "A", "B", "C", "D"),
    "center_block": ("inner_dia", "outer_dia", "height"),
    "safety_lining_std": ("length", "width"),
    "safety_lining_rect": ("width", "face_a", "face_b", "depth"),
    "safety_lining_wedge": ("a", "b", "c"),
    "seating_block": ("outer_dia", "inner_dia", "height"),
    "purging_plug": ("overall_height",),
    "tap_hole_block": (),
    "tap_hole_sleeve": (),
}


class ShapeLibraryValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def compute_shape_volume_dm3(brick: dict[str, Any]) -> Optional[float]:
    """Compute geometric volume in dm3 from dimensions_mm for validation & BOM calculation."""
    fam = brick.get("shape_family", "")
    d = brick.get("dimensions_mm") or {}
    try:
        if fam in ("standard_trapezoidal", "steel_cladded"):
            L, wt, wb, t = float(d["length"]), float(d["width_top"]), float(d["width_bottom"]), float(d["thickness"])
            return L * t * (wt + wb) / 2.0 / 1e6
        if fam == "barrel_safety_brick":
            A, B, C, T = float(d["A"]), float(d["B"]), float(d["C"]), float(d["T"])
            return C * T * (A + B) / 2.0 / 1e6
        if fam == "key_wedge_series":
            a, b, h, s1, s2 = float(d["a"]), float(d["b"]), float(d["h"]), float(d["s1"]), float(d["s2"])
            # Prismoid volume across end faces (a x s1) and (b x s2) over length h
            area1 = a * s1
            area2 = b * s2
            aream = ((a + b) / 2.0) * ((s1 + s2) / 2.0)
            return h / 6.0 * (area1 + 4.0 * aream + area2) / 1e6
        if fam == "beveled_key_ht":
            a, b, h, s = float(d["a"]), float(d["b"]), float(d["h"]), float(d["s"])
            ded = float(d.get("bevel_deduction_dm3", 0.61))
            return (h * s * (a + b) / 2.0 / 1e6) - ded
        if fam == "double_tapered":
            a1, a2, b1, b2, s, h = (
                float(d["a1"]),
                float(d["a2"]),
                float(d["b1"]),
                float(d["b2"]),
                float(d["s"]),
                float(d["h"]),
            )
            area_cold = s * (a1 + b1) / 2.0
            area_hot = s * (a2 + b2) / 2.0
            area_mid = s * (a1 + a2 + b1 + b2) / 4.0
            return h / 6.0 * (area_cold + 4.0 * area_mid + area_hot) / 1e6
        if fam == "triple_tapered":
            a1, a2, b1, b2, s1, s2, h = (
                float(d["a1"]),
                float(d["a2"]),
                float(d["b1"]),
                float(d["b2"]),
                float(d["s1"]),
                float(d["s2"]),
                float(d["h"]),
            )
            area_top = s1 * (a2 + b2) / 2.0
            area_bot = s2 * (a1 + b1) / 2.0
            area_mid = ((s1 + s2) / 2.0) * (a1 + a2 + b1 + b2) / 4.0
            return h / 6.0 * (area_top + 4.0 * area_mid + area_bot) / 1e6
        if fam == "bottom_dish_wedge":
            L = float(d["overall_length"])
            wt, wb = float(d["face_width_top"]), float(d["face_width_bottom"])
            A, B, C, D = float(d["A"]), float(d["B"]), float(d["C"]), float(d["D"])
            at = wt * (A + B) / 2.0
            ab = wb * (C + D) / 2.0
            am = ((wt + wb) / 2.0) * (A + B + C + D) / 4.0
            return L / 6.0 * (at + 4.0 * am + ab) / 1e6
        if fam == "center_block":
            import math
            di, do, h = float(d["inner_dia"]), float(d["outer_dia"]), float(d["height"])
            return math.pi * h / 12.0 * (di**2 + di * do + do**2) / 1e6
        if fam == "seating_block":
            import math
            do, di, h = float(d["outer_dia"]), float(d["inner_dia"]), float(d["height"])
            return math.pi / 4.0 * (do**2 - di**2) * h / 1e6
        if fam == "safety_lining_std" and "thickness" in d:
            return float(d["length"]) * float(d["width"]) * float(d["thickness"]) / 1e6
        if fam == "safety_lining_rect":
            return float(d["width"]) * (float(d["face_a"]) + float(d["face_b"])) / 2.0 * float(d["depth"]) / 1e6
    except (KeyError, TypeError, ValueError):
        return None
    return None


def normalize_brick_entry(raw: dict[str, Any], default_source_ref: Optional[dict] = None) -> dict[str, Any]:
    """Normalize an imported brick dictionary into canonical TRL shape-library format."""
    b = dict(raw)
    mark = str(b.get("mark", "")).strip()
    default_fam = "tap_hole_sleeve" if mark.upper().startswith("TS-") else ("tap_hole_block" if mark.upper().startswith("TB-") else "standard_trapezoidal")
    fam = str(b.get("shape_family") or default_fam).strip()
    d = dict(b.get("dimensions_mm") or {})

    # Auto-populate compatibility dimension keys so Part 3 wall/bottom engine and drawing sheets work seamlessly
    if fam == "barrel_safety_brick":
        for k_src, k_dst in (("C", "length"), ("A", "width_top"), ("B", "width_bottom"), ("T", "thickness")):
            if k_src in d and k_dst not in d:
                d[k_dst] = d[k_src]
    elif fam == "key_wedge_series":
        if "h" in d and "length" not in d:
            d["length"] = d["h"]
        if "a" in d and "width_top" not in d:
            d["width_top"] = d["a"]
        if "b" in d and "width_bottom" not in d:
            d["width_bottom"] = d["b"]
        if "s1" in d and "s2" in d and "thickness" not in d:
            d["thickness"] = round((float(d["s1"]) + float(d["s2"])) / 2.0, 2)
    elif fam == "beveled_key_ht":
        for k_src, k_dst in (("h", "length"), ("a", "width_top"), ("b", "width_bottom"), ("s", "thickness")):
            if k_src in d and k_dst not in d:
                d[k_dst] = d[k_src]
    elif fam in ("double_tapered", "triple_tapered"):
        if "h" in d and "overall_length" not in d:
            d["overall_length"] = d["h"]
        if fam == "double_tapered" and "s" in d:
            d.setdefault("face_width_top", d["s"])
            d.setdefault("face_width_bottom", d["s"])
        if fam == "triple_tapered":
            if "s1" in d:
                d.setdefault("face_width_top", d["s1"])
            if "s2" in d:
                d.setdefault("face_width_bottom", d["s2"])
        for k_src, k_dst in (("a2", "A"), ("b2", "B"), ("a1", "C"), ("b1", "D")):
            if k_src in d and k_dst not in d:
                d[k_dst] = d[k_src]

    b["mark"] = mark
    b["shape_family"] = fam
    b["dimensions_mm"] = d
    if "source_ref" not in b:
        b["source_ref"] = default_source_ref or {
            "document_no": "TRLK/SS/BOF/06/08.10.2016",
            "title": b.get("source", "TRL Standard Shape Library"),
            "revision": "08.10.2016",
            "clause_or_page": b.get("source", "Imported entry"),
        }
    return b


def validate_brick_entry(brick: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    """Validate a single brick entry against TRL shape library rules."""
    errors: list[str] = []
    warnings: list[str] = []
    mark = str(brick.get("mark", "")).strip()
    if not mark:
        errors.append("Missing brick 'mark'.")
    fam = str(brick.get("shape_family", "")).strip()
    if not fam:
        errors.append(f"Brick '{mark or '?'}': missing 'shape_family'.")
    elif fam not in SHAPE_FAMILY_REQUIRED_DIMS:
        errors.append(f"Brick '{mark}': unsupported shape_family '{fam}'.")
    else:
        d = brick.get("dimensions_mm") or {}
        req = SHAPE_FAMILY_REQUIRED_DIMS[fam]
        for k in req:
            if k not in d or d[k] in (None, ""):
                errors.append(f"Brick '{mark}' ({fam}): missing required dimension '{k}'.")
            else:
                try:
                    val = float(d[k])
                    if val <= 0:
                        errors.append(f"Brick '{mark}' ({fam}): dimension '{k}' must be > 0 (got {val}).")
                except (TypeError, ValueError):
                    errors.append(f"Brick '{mark}' ({fam}): dimension '{k}' must be numeric (got {d[k]!r}).")

    sref = brick.get("source_ref")
    if not isinstance(sref, dict) or not sref.get("document_no"):
        warnings.append(f"Brick '{mark}': missing structured 'source_ref.document_no'.")

    comp_vol = compute_shape_volume_dm3(brick)
    cat_vol = brick.get("catalog_volume_dm3")
    if cat_vol is not None and comp_vol is not None:
        try:
            cv = float(cat_vol)
            if cv > 0 and abs(comp_vol - cv) / cv > 0.25:
                warnings.append(
                    f"Brick '{mark}': catalog_volume_dm3={cv:.2f} differs from prismoid estimate={comp_vol:.2f} dm3 (>25% bevel/cutout)."
                )
        except (TypeError, ValueError):
            errors.append(f"Brick '{mark}': invalid catalog_volume_dm3 {cat_vol!r}.")

    return (len(errors) == 0), errors, {"mark": mark, "computed_volume_dm3": comp_vol, "warnings": warnings}


class ShapeLibraryStore:
    """Manages the TRL shape library, custom imports, and shape-drawing-to-brick-mark registrations (FR-04.1, FR-01.3)."""

    def __init__(self, store_dir: str | Path, audit: AuditLog, default_lib_path: Optional[Path] = None):
        self.dir = Path(store_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.audit = audit
        self.default_path = Path(default_lib_path or DEFAULT_LIB_PATH)
        self.custom_path = self.dir / "custom_shapes.json"
        self.drawings_reg_path = self.dir / "shape_drawings_registry.json"

        self._base_lib = json.loads(self.default_path.read_text(encoding="utf-8")) if self.default_path.exists() else {"bricks": []}
        self.custom_bricks: dict[str, dict] = (
            json.loads(self.custom_path.read_text(encoding="utf-8")) if self.custom_path.exists() else {}
        )
        self.drawing_registry: dict[str, list[dict]] = (
            json.loads(self.drawings_reg_path.read_text(encoding="utf-8")) if self.drawings_reg_path.exists() else {}
        )

    def get_library(self, series: Optional[str] = None, shape_family: Optional[str] = None, zone: Optional[str] = None) -> dict[str, Any]:
        """Return the merged TRL shape library (base catalog + custom imported marks + registered shape drawings)."""
        merged: dict[str, dict] = {}
        for b in self._base_lib.get("bricks", []):
            nb = normalize_brick_entry(b)
            merged[nb["mark"]] = nb
        for m, b in self.custom_bricks.items():
            nb = normalize_brick_entry(b)
            merged[m] = nb

        bricks = []
        for m, b in merged.items():
            if series and series.lower() not in str(b.get("trl_series", "")).lower():
                continue
            if shape_family and b.get("shape_family") != shape_family:
                continue
            if zone and zone.lower() not in str(b.get("zone", "")).lower():
                continue
            entry = dict(b)
            if m in self.drawing_registry:
                entry["registered_drawings"] = self.drawing_registry[m]
            bricks.append(entry)

        out = dict(self._base_lib)
        out["bricks"] = bricks
        out["total_marks"] = len(bricks)
        return out

    def get_mark(self, mark: str) -> dict[str, Any]:
        lib = self.get_library()
        by_mark = {b["mark"]: b for b in lib["bricks"]}
        if mark not in by_mark:
            raise KeyError(f"Brick mark '{mark}' not found in shape library.")
        return by_mark[mark]

    def validate_import_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a TRL shape-library import payload without saving."""
        raw_bricks = payload.get("bricks")
        if not isinstance(raw_bricks, list) or not raw_bricks:
            return {"ok": False, "errors": ["Payload must contain a non-empty 'bricks' array."], "valid_count": 0}

        default_sref = payload.get("source_ref") or payload.get("document_metadata", {}).get("primary_catalog")
        all_errors: list[str] = []
        all_warnings: list[str] = []
        valid_bricks: list[dict] = []

        for raw in raw_bricks:
            if not isinstance(raw, dict):
                all_errors.append("Every entry in 'bricks' must be a JSON object.")
                continue
            nb = normalize_brick_entry(raw, default_source_ref=default_sref)
            ok, errs, meta = validate_brick_entry(nb)
            if not ok:
                all_errors.extend(errs)
            else:
                valid_bricks.append(nb)
            all_warnings.extend(meta.get("warnings", []))

        return {
            "ok": len(all_errors) == 0,
            "valid_count": len(valid_bricks),
            "error_count": len(all_errors),
            "errors": all_errors,
            "warnings": all_warnings,
        }

    def import_shapes(self, payload: dict[str, Any], *, actor: str, reason: str = "TRL shape library import") -> dict[str, Any]:
        """Validate and import new or updated brick shapes into the store, recording an audit entry."""
        chk = self.validate_import_payload(payload)
        if not chk["ok"]:
            self.audit.record(
                "shape_library.import",
                "shape_library",
                actor=actor,
                reason=reason,
                outcome="DENIED",
                meta={"errors": chk["errors"][:20]},
            )
            raise ShapeLibraryValidationError(chk["errors"])

        default_sref = payload.get("source_ref") or payload.get("document_metadata", {}).get("primary_catalog")
        imported_marks: list[str] = []
        for raw in payload["bricks"]:
            nb = normalize_brick_entry(raw, default_source_ref=default_sref)
            self.custom_bricks[nb["mark"]] = nb
            imported_marks.append(nb["mark"])

        self.custom_path.write_text(json.dumps(self.custom_bricks, indent=2), encoding="utf-8")
        self.audit.record(
            "shape_library.import",
            "shape_library",
            actor=actor,
            after={"imported_count": len(imported_marks), "marks": imported_marks[:50]},
            reason=reason,
        )
        return {
            "imported_count": len(imported_marks),
            "marks": imported_marks,
            "warnings": chk["warnings"],
            "total_library_marks": len(self.get_library()["bricks"]),
        }

    def register_shape_drawing(
        self,
        marks: list[str],
        *,
        filename: str,
        file_hash_sha256: str,
        drawing_number: str = "",
        revision: str = "",
        job_id: str = "",
        file_path: str = "",
        actor: str = "anonymous",
        notes: str = "",
    ) -> dict[str, Any]:
        """Register an uploaded individual shape drawing against one or more brick marks (FR-01.3)."""
        clean_marks = [str(m).strip() for m in marks if str(m).strip()]
        if not clean_marks:
            raise ValueError("At least one brick mark is required to register a shape drawing.")

        import datetime
        record = {
            "filename": filename,
            "sha256": file_hash_sha256,
            "drawing_number": drawing_number or filename,
            "revision": revision or "0",
            "job_id": job_id,
            "file_path": file_path,
            "registered_by": actor,
            "registered_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "notes": notes,
        }
        for m in clean_marks:
            existing = self.drawing_registry.setdefault(m, [])
            # Avoid duplicate entries for the exact same file hash
            if not any(x.get("sha256") == file_hash_sha256 for x in existing):
                existing.append(record)

        self.drawings_reg_path.write_text(json.dumps(self.drawing_registry, indent=2), encoding="utf-8")
        self.audit.record(
            "shape_drawing.register",
            f"marks:{','.join(clean_marks)}",
            actor=actor,
            after=record,
            reason=notes or f"Registered shape drawing {filename} against {len(clean_marks)} mark(s)",
        )
        return {"registered_marks": clean_marks, "drawing": record}

    def list_shape_drawings(self, mark: Optional[str] = None) -> dict[str, list[dict]]:
        if mark:
            return {mark: self.drawing_registry.get(mark, [])}
        return dict(self.drawing_registry)

    def fingerprint(self) -> str:
        """SHA-256 fingerprint of all active brick marks and dimensions (used for FR-04.3 KB versioning)."""
        lib = self.get_library()
        canonical = sorted(
            (b["mark"], b.get("shape_family", ""), b.get("dimensions_mm", {}), b.get("catalog_volume_dm3"))
            for b in lib.get("bricks", [])
        )
        return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()
