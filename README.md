# AI-Driven Refractory Lining Design — RefractoryAI (LiningOps Copilot)

> **From Engineering Drawing to Parametric Lining Design, Live Formula Governance, Audit Trail & CAD Deliverables**

---

## 1. Architecture Overview

RefractoryAI is an open-source, vendor-neutral full-stack platform and standalone Python library (`refractory_core`) for designing refractory linings of steelmaking vessels (BOF converters, ladles, etc.).

| Component | Description | Status |
|-----------|-------------|--------|
| **Standalone Core Library (`refractory_core/`)** | Pip-installable library (`pip install -e .`) with zero FastAPI/UI dependencies. Provides the AST-sandboxed Formula Engine (113 formulas), Immutable Chemical Property Registry, SHA-256 Hash-Chained Audit Log, and Context-Aware (RAG-style) Preset Suggestions. | ✅ Ready |
| **Part 1 & 2 — Drawing Ingestion & Extraction** | FastAPI service that ingests PDF/DXF/DWG/Image shell drawings and extracts structured vessel geometry (`VesselGeometry`) via vector path parsing, DXF entity parsing, OCR, and OpenCV. | ✅ Ready |
| **Part 3 — Lining CAD & BOM Generator** | Generates full engineering deliverables (`lining_ga`, `brick_shapes`, `lining_layers` in DXF/PNG/PDF, plus Excel/CSV BOMs and accuracy checks) using live formulas from `refractory_core`. | ✅ Ready |
| **13-Step Lining Design Wizard (`ui/app.html`)** | Interactive browser application for end-to-end lining design, real-time 2D SVG vessel rendering, live formula recalculation (`ui/fx.js`), and inline `Edit ƒx` links. | ✅ Ready |
| **Engineering Control Center (`ui/control_center.html`)** | Dedicated governance UI with typeset mathematical formula rendering, an interactive 4-section **Formula Keyboard**, immutable chemical property locks, smart preset management, and cryptographic audit chain verification. | ✅ Ready |

---

## 2. Project Structure

```text
refractory-master/
├── refractory_core/                           # Standalone, pip-installable core library
│   ├── __init__.py                            # Exports RefractoryCore, get_core, FormulaError, ImmutablePropertyError
│   ├── core.py                                # Facade combining formulas, properties, audit, presets
│   ├── formulas.py                            # AST-whitelisted formula evaluator & dependency graph
│   ├── properties.py                          # Immutable chemical/physical property & grade registry
│   ├── audit.py                               # Append-only SHA-256 hash-chained audit log (JSONL)
│   ├── presets.py                             # Context-similarity (RAG) preset recommender
│   └── defaults/
│       ├── formulas.json                      # Core BOM, brick volume, weight & ordering formulas
│       ├── formulas_lining.json               # Part 3 brick geometry, ring closure & check formulas
│       ├── formulas_ui_extraction.json        # UI wizard & Part 2 geometry extraction formulas
│       └── properties.json                    # Locked oxide chemistry/densities & editable defaults
├── app/
│   ├── main.py                                # FastAPI application entry point (mounts /api/v1 and /ui)
│   ├── api/v1/
│   │   ├── routes_ingestion.py                # Part 1: File upload & validation endpoints
│   │   ├── routes_extraction.py               # Part 2: Geometry extraction endpoints
│   │   ├── routes_lining.py                   # Part 3: Lining CAD & BOM generation endpoints
│   │   └── routes_core.py                     # Core API: /formulas, /properties, /presets, /audit
│   ├── models/
│   │   └── vessel_geometry.py                 # Pydantic schemas for VesselGeometry & segments
│   └── services/
│       ├── part1_ingestion/                   # Multi-format file ingestion
│       ├── part2_extraction/                  # PDF/DXF/OCR/OpenCV extraction & geometry_assembler.py
│       └── part3_lining/                      # Brick calculation, ring closure, DXF/PDF drawings & BOM
├── ui/
│   ├── app.html                               # Main 13-step LiningOps Copilot design wizard
│   ├── control_center.html                    # Formula, Property, Preset & Audit Control Center
│   ├── fx.js                                  # Shared browser math typesetting & live formula evaluator
│   └── index.html                             # Entry redirect / landing page
├── data/
│   ├── sample_drawings/                       # Sample vessel drawings (PDF / DXF)
│   ├── bricks/                                # Generic brick shape & material catalogues
│   └── outputs/                               # Generated extraction JSONs, CAD drawings & BOMs
├── tests/
│   └── test_core_library.py                   # Unit & integration test suite (22 tests)
├── pyproject.toml                             # Packaging config for `pip install -e .`
├── requirements.txt                           # Python dependencies
└── README.md                                  # This guide
```

---

## 3. Prerequisites

1. **Python 3.10 or 3.11+**
2. **Tesseract OCR** *(optional, only required for scanned/raster PDFs; vector PDFs and DXF files work without it)*:
   - Windows: [UB-Mannheim Tesseract installer](https://github.com/UB-Mannheim/tesseract/wiki) (`C:\Program Files\Tesseract-OCR\`)
3. **Poppler** *(optional, for raster PDF page rendering)*:
   - Windows: [poppler-windows releases](https://github.com/oschwartz10612/poppler-windows/releases/)

---

## 4. Quick Start — Setup & Run Instructions

### Step 1: Create Virtual Environment & Install Dependencies

Open a terminal (PowerShell or Command Prompt) inside the `refractory-master` folder:

```powershell
# 1. Create a virtual environment
python -m venv .venv

# 2. Activate the virtual environment
.\.venv\Scripts\activate          # Windows PowerShell / CMD
# source .venv/bin/activate       # Linux / macOS

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install refractory_core in editable mode (optional, for standalone library use)
pip install -e .
```

### Step 2: Configure Environment Variables (Optional)

If a `.env` file does not exist yet, you can copy `.env.example` to `.env`:

```powershell
copy .env.example .env
```
*(Vector PDFs and DXF drawings extract deterministically without requiring external API keys.)*

### Step 3: Start the FastAPI Backend (Also Serves the UI!)

Run `uvicorn` from the `refractory-master` directory:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Once running, open your browser to:

- **Main 13-Step Lining Design Wizard**: [http://127.0.0.1:8000/ui/app.html](http://127.0.0.1:8000/ui/app.html)
- **Formula, Property & Audit Control Center**: [http://127.0.0.1:8000/ui/control_center.html](http://127.0.0.1:8000/ui/control_center.html)
- **Interactive Swagger API Docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

> **Note:** FastAPI automatically mounts the `ui/` directory at `/ui`, so you do **not** need a separate frontend server. However, if you prefer running a separate static server on port 3000 (`python -m http.server 3000 --directory ui`), the UI automatically connects to `http://127.0.0.1:8000/api/v1` (and lets you configure the backend URL in Step 1 via **Change API URL**).

---

## 5. How to Use the Application

### A. Designing a Vessel Lining (`http://127.0.0.1:8000/ui/app.html`)
1. **Step 1 — Job Card & Drawing Upload**:
   - Enter Customer Name and Heat Size, or click **💡 Smart Pre-populate** in the top bar to load popular/recent settings.
   - Click **📁 Choose file…** to upload a naked shell drawing (`.pdf`, `.dxf`, or `.dwg`), or click **Use sample: 180 MT BOF shell drawing**. The selected file name is displayed next to the button (`📄 filename.pdf`) while the backend extracts the vessel geometry.
2. **Step 2 — Confirm Extracted Geometry**:
   - Review the extracted dimensions zone by zone (Bottom Dish, Lower Cone, Barrel, Top Cone, Mouth Ring, Taphole, Trunnion, Stirring). Edit any dimension if needed; the 2D annotated SVG updates live.
3. **Steps 3 to 12 — Zone Splitting, Grades, Thermal/Wear Concept & Ring Closure**:
   - Configure zone heights, angular sectors, permanent lining, working lining thickness, bottom rings, wall brick pairs (`A`/`B`), and special assemblies.
   - Every rule card displays the **live typeset mathematical formula** powering that step alongside an **`Edit ƒx`** button that jumps straight to that formula in the Control Center.
4. **Step 13 — Summary, CAD Deliverables & BOM**:
   - Review all validation checks (`V1`–`V12`) and total BOQ tonnes.
   - Click **Generate Lining Drawings & BOM** to run the Part 3 engine and download DXF/PDF/PNG drawings and Excel/CSV Bill of Materials.

### B. Editing Formulas & Using the Formula Keyboard (`http://127.0.0.1:8000/ui/control_center.html`)
1. Open the **Formulas (`113`)** tab via the **ƒx Formulas & Audit** button in the top bar.
2. Search or filter across all **113 formulas** spanning 15 engineering groups (Brick Volume, Ring Closure, Wizard Steps 2–13, Extraction, Thermal, Validation Checks, etc.).
3. Click any formula to open the **Formula Editor**:
   - View the **live typeset math preview** (fractions, $\pi$, $\sqrt{\cdot}$, $\tan^{-1}$, subscripts, and variable unit badges).
   - Click inside the expression input to open the **4-section interactive Formula Keyboard**:
     1. **Variables in this formula** (one-click insertion of valid variable chips with units)
     2. **Referenced sub-formulas** (composable formula chips)
     3. **Math functions & constants** (`sqrt()`, `sin()`, `cos()`, `tan()`, `atan2()`, `radians()`, `degrees()`, `ceil()`, `floor()`, `round()`, `min()`, `max()`, `abs()`, `pi`)
     4. **Numbers & operators keypad** (`0–9`, `+`, `−`, `×`, `÷`, `xʸ`, `( )`, `⌫`, `Clear`)
4. Enter an **Audit Reason** and click **Save & Propagate**. All dependent calculations in both the backend and the 13-step UI wizard automatically recalculate on the next render or tab focus.

### C. Immutable Properties & Cryptographic Audit Log
- **Properties Tab**: Chemical oxide properties (`MgO`, `Al2O3`, `CaO`, `SiO2`, `Cr2O3`, `ZrO2`, `Fe2O3`, `C`, `SiC`) and grade chemical compositions are **cryptographically locked** (`locked: true`). Any attempt to modify them via API or code raises `ImmutablePropertyError` and records a `DENIED` event in the audit log.
- **Audit Log Tab**: Every formula change, property update, denial, and preset confirmation is recorded in an append-only SHA-256 hash chain (`data/core_state/audit.jsonl`). Click **Verify Hash Chain** to verify tamper-evidence across all entries.

---

## 6. Using `refractory_core` as a Standalone Python Library

Other teams or projects can import `refractory_core` directly without FastAPI:

```python
from pathlib import Path
from refractory_core import RefractoryCore

# Initialize with a state directory for persistence & audit logs
core = RefractoryCore(state_dir=Path("./my_state"))

# 1. Evaluate any of the 113 engineering formulas
vol_dm3 = core.formulas.evaluate("vol_standard_trapezoidal", {
    "length": 750,
    "thickness": 100,
    "width_top": 154,
    "width_bottom": 128,
})
print(f"Brick volume: {vol_dm3:.3f} dm³")

# 2. Customize a formula (automatically validated, persisted, and audited)
core.formulas.set_expression(
    "order_qty",
    "ceil(qty * (1 + allow_pct / 100)) + 5",
    actor="engineer@company.com",
    reason="Add 5 spare bricks per line item",
)

# 3. Read locked chemical properties
mgo_density = core.properties.value("compound.MgO.density")  # 3.58 g/cm³

# 4. Verify the SHA-256 audit log chain
assert core.audit.verify()["ok"]
```

---

## 7. Running Automated Tests

Run the test suite from the `refractory-master` directory:

```powershell
pytest tests/test_core_library.py -v
```

All **22 unit and integration tests** cover formula evaluation, cascading edits, AST sandbox security, circular reference detection, immutable chemical properties, SHA-256 tamper detection, RAG preset ranking, Part 2 extraction formulas, and Part 3 BOM/ring closure integration.
