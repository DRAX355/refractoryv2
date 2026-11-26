#!/usr/bin/env bash
# ==============================================================================
# FR-01.5 — Install & Verify DWG-to-DXF Converter on Target Linux / AWS Server
# ==============================================================================
# Installs LibreDWG (`dwg2dxf` headless CLI alternative) and optionally ODA File
# Converter (with Xvfb for headless execution), then verifies DWG support.
set -euo pipefail

echo "[FR-01.5] Installing LibreDWG (dwg2dxf) and headless X11 dependencies..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y libredwg-tools xvfb libxcb-xinerama0 libqt5gui5 || true
elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y libredwg xorg-x11-server-Xvfb || true
fi

if command -v dwg2dxf >/dev/null 2>&1; then
    echo "[FR-01.5] SUCCESS: LibreDWG (dwg2dxf) is installed at $(command -v dwg2dxf)"
    dwg2dxf --version | head -n 2 || true
fi

if command -v ODAFileConverter >/dev/null 2>&1; then
    echo "[FR-01.5] SUCCESS: ODAFileConverter is installed at $(command -v ODAFileConverter)"
fi

echo "[FR-01.5] Verifying via Python get_dwg_converter_status()..."
python3 -c "from app.services.part1_ingestion.file_handler import get_dwg_converter_status; import json; print(json.dumps(get_dwg_converter_status(), indent=2))"
