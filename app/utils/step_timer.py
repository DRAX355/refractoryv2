"""
app/utils/step_timer.py
=======================
Records execution time of every step in Part 1 (Ingestion), Part 2 (Extraction),
and Part 3 (Lining Drawings & BOM Generation).

Writes:
  1. Per-job timing files:
     - <job_out_dir>/timings.txt
     - <job_out_dir>/timings.json
  2. Central backend timing log files:
     - data/outputs/process_timings.txt
     - data/outputs/process_timings.json
"""
from __future__ import annotations

import datetime
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger
from app.core.config import settings


class StepTimer:
    def __init__(self, pipeline_name: str, job_id: str, out_dir: Path | None = None) -> None:
        self.pipeline_name = pipeline_name
        self.job_id = str(job_id)
        self.out_dir = Path(out_dir) if out_dir else (Path(settings.outputs_dir) / self.job_id)
        self.started_at = datetime.datetime.now().isoformat(timespec="seconds")
        self.t0 = time.perf_counter()
        self.steps: list[dict[str, Any]] = []

    @contextmanager
    def step(self, name: str):
        s0 = time.perf_counter()
        status = "ok"
        try:
            yield
        except Exception as exc:
            status = f"error: {type(exc).__name__}"
            raise
        finally:
            dt = round(time.perf_counter() - s0, 4)
            total_so_far = round(time.perf_counter() - self.t0, 4)
            self.steps.append({
                "step_index": len(self.steps) + 1,
                "step_name": name,
                "duration_seconds": dt,
                "cumulative_seconds": total_so_far,
                "status": status,
            })
            logger.info(f"[Timing · {self.pipeline_name}] Step {len(self.steps)} '{name}': {dt:.3f}s (cum {total_so_far:.3f}s)")
            self.save()

    def save(self) -> tuple[Path, Path]:
        total_s = round(time.perf_counter() - self.t0, 4)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        central_dir = Path(settings.outputs_dir)
        central_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "pipeline": self.pipeline_name,
            "job_id": self.job_id,
            "started_at": self.started_at,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "total_seconds": total_s,
            "steps": self.steps,
        }

        lines = [
            "=" * 78,
            f"PROCESS TIMING REPORT — {self.pipeline_name}",
            f"Job ID      : {self.job_id}",
            f"Started At  : {self.started_at}",
            f"Total Time  : {total_s:.3f} seconds",
            "-" * 78,
            f"{'#':<4} {'Step Description':<48} {'Duration (s)':>12} {'Cumulative (s)':>14}",
            "-" * 78,
        ]
        for s in self.steps:
            pct = (s["duration_seconds"] / total_s * 100) if total_s > 0 else 0.0
            lines.append(
                f"{s['step_index']:<4} {s['step_name'][:47]:<48} "
                f"{s['duration_seconds']:>9.3f}s ({pct:>4.1f}%) {s['cumulative_seconds']:>11.3f}s"
            )
        lines.append("=" * 78)
        report_txt = "\n".join(lines) + "\n"

        txt_path = self.out_dir / "timings.txt"
        json_path = self.out_dir / "timings.json"
        try:
            txt_path.write_text(report_txt, encoding="utf-8")
            json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[Timing] Could not write job timing file: {exc}")

        return txt_path, json_path

    def finalize(self) -> tuple[Path, Path]:
        txt_path, json_path = self.save()
        central_dir = Path(settings.outputs_dir)
        central_txt = central_dir / "process_timings.txt"
        central_json = central_dir / "process_timings.json"
        try:
            with open(central_txt, "a", encoding="utf-8") as f:
                f.write(txt_path.read_text(encoding="utf-8") + "\n")
            history = []
            if central_json.exists():
                try:
                    history = json.loads(central_json.read_text(encoding="utf-8"))
                except Exception:
                    history = []
            history.append(json.loads(json_path.read_text(encoding="utf-8")))
            central_json.write_text(json.dumps(history[-100:], indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[Timing] Could not append to central process_timings file: {exc}")
        return txt_path, json_path
