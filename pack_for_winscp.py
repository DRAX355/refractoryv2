"""
Packages the RefractoryAI / LiningOps Copilot Server for WinSCP upload to AWS EC2 (65.1.125.127),
and creates a lightweight Portable Windows Desktop Client zip for engineering workstations.
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER_ZIP = ROOT / "server_deploy_ec2.zip"
CLIENT_ZIP = ROOT / "LiningOps_Desktop_Client_Portable.zip"

INCLUDE_DIRS = ["app", "refractory_core", "ui", "info", "deploy", "desktop_client"]
INCLUDE_FILES = ["requirements.txt", "pyproject.toml", ".env.example", "README.md"]

EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".git", "venv", ".venv", "node_modules"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd", ".zip", ".exe"}
LF_EXTENSIONS = {".sh", ".service", ".conf", ".py", ".txt", ".toml", ".md", ".json", ".yaml", ".yml"}


def should_skip(path: Path) -> bool:
    if any(part in EXCLUDE_PARTS for part in path.parts):
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return False


def write_Entry(zf: zipfile.ZipFile, src: Path, arcname: str) -> None:
    if src.suffix.lower() in LF_EXTENSIONS:
        try:
            raw = src.read_bytes()
            normalized = raw.replace(b"\r\n", b"\n")
            zf.writestr(arcname, normalized)
            return
        except Exception:
            pass
    zf.write(src, arcname)


def build_server_zip() -> None:
    count = 0
    with zipfile.ZipFile(SERVER_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in INCLUDE_FILES:
            fpath = ROOT / fname
            if fpath.exists():
                write_Entry(zf, fpath, fname)
                count += 1

        for dname in INCLUDE_DIRS:
            dpath = ROOT / dname
            if not dpath.exists():
                continue
            for root_dir, dirs, files in os.walk(dpath):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_PARTS]
                for f in files:
                    fpath = Path(root_dir) / f
                    if should_skip(fpath):
                        continue
                    arcname = fpath.relative_to(ROOT).as_posix()
                    write_Entry(zf, fpath, arcname)
                    count += 1

    size_mb = SERVER_ZIP.stat().st_size / (1024 * 1024)
    print(f"[OK] Created WinSCP EC2 Server bundle: {SERVER_ZIP} ({count} files, {size_mb:.2f} MB)")


def build_client_zip() -> None:
    count = 0
    with zipfile.ZipFile(CLIENT_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for exe_name in ("LiningOps_Copilot_Setup.exe", "LiningOps_Copilot_Desktop.exe"):
            exe_file = ROOT / exe_name
            if exe_file.exists():
                zf.write(exe_file, exe_name)
                count += 1
        for dname in ["ui", "desktop_client"]:
            dpath = ROOT / dname
            if not dpath.exists():
                continue
            for root_dir, dirs, files in os.walk(dpath):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_PARTS]
                for f in files:
                    fpath = Path(root_dir) / f
                    if fpath.name in ("LiningOps_Copilot_Desktop.exe", "LiningOps_Copilot_Setup.exe"):
                        arcname = fpath.relative_to(ROOT).as_posix()
                        zf.write(fpath, arcname)
                        count += 1
                        continue
                    if should_skip(fpath):
                        continue
                    arcname = fpath.relative_to(ROOT).as_posix()
                    zf.write(fpath, arcname)
                    count += 1
    size_mb = CLIENT_ZIP.stat().st_size / (1024 * 1024)
    print(f"[OK] Created Portable Desktop Client bundle: {CLIENT_ZIP} ({count} files, {size_mb:.2f} MB)")


if __name__ == "__main__":
    build_server_zip()
    build_client_zip()
