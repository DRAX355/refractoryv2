# AI-Driven Refractory Lining Design — RefractoryAI
## ZeroZeta | End-to-End Solution Architecture

> **From Engineering Drawing to CAD Deliverables**

---

## Architecture Overview

RefractoryAI is a full-stack system designed to automate the design of refractory linings for steelmaking vessels (such as BOF converters and Torpedo Ladles).

| Component | Description | Status |
|-----------|-------------|--------|
| **Backend API** | FastAPI backend that processes PDF/DXF drawings, uses AI (litellm/Gemini) for OCR/Geometry extraction, and returns structured vessel data. | ✅ Done |
| **Frontend UI** | Vanilla HTML/JS interactive 8-step wizard. Manages the lining design workflow (from drawing ingestion, geometry confirmation, zone splitting, to bill of materials). | ✅ Done |
| **Geometry Rendering** | Fully dynamic SVG engine (`shellOnlySVG`, `sideSVG`) that reads directly from extracted data to render perfect parametric vessel diagrams. | ✅ Done |
| **CAD Export** | Integration with FreeCAD/ezdxf for final engineering drawings. | 🔲 Pending |

---

## Project Structure

```
refractoryAI/
├── app/
│   ├── main.py                                # FastAPI app entry point (Backend)
│   ├── api/v1/                                # API Routes
│   ├── models/
│   │   └── vessel_geometry.py                 # Core schema (VesselGeometry)
│   ├── services/
│   │   ├── part1_ingestion/                   # Multi-format file handling
│   │   └── part2_extraction/                  # OCR, LLM-based parsing, Geometry assembly
├── ui/
│   └── index.html                             # The full Frontend App (HTML/JS/CSS)
├── data/
│   ├── sample_drawings/                       # Put test drawings here
│   └── outputs/                               # Extraction results (JSON)
├── requirements.txt
├── pytest.ini
└── .env
```

---

## Prerequisites

Before running the backend, ensure you have the following installed:

1. **Python 3.10+**
2. **Node.js** (optional, just for `http-server` if you want to run the UI quickly)
3. **Tesseract OCR** (For local OCR processing)
   - *Windows*: [Download here](https://github.com/UB-Mannheim/tesseract/wiki) (Install to `C:\Program Files\Tesseract-OCR\` and add to PATH)
4. **Poppler** (For PDF to image conversion)
   - *Windows*: [Download here](https://github.com/oschwartz10612/poppler-windows/releases/) (Extract to `C:\poppler\` and add `C:\poppler\Library\bin` to PATH)

---

## Setup & Run Instructions

### 1. Backend Setup

```bash
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate      # Linux/Mac

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
# Ensure you have a .env file (copy from .env.example)
# Add your GEMINI_API_KEY to the .env file!
copy .env.example .env

# 4. Run the Backend API server
uvicorn app.main:app --reload --port 8000
```
*Backend API Documentation will be available at: http://localhost:8000/docs*

### 2. Frontend Setup

The frontend is a lightweight vanilla JS/HTML file that expects to communicate with the backend on port 8000.

```bash
# Open a new terminal
cd ui

# Start a simple HTTP server (Port 3000)
python -m http.server 3000
# OR if using Node: npx http-server -p 3000
```
*Access the Frontend Application at: http://localhost:3000*

---

## How to use the Pipeline

1. **Open the Frontend**: Go to `http://localhost:3000`
2. **Upload a Drawing**: In Step 0 (G0), click the upload area or the "Load sample shell drawing" button to upload a PDF (e.g., `pressure_vessel_profile.png` or a PDF drawing).
3. **AI Extraction**: The frontend will automatically hit the `/api/v1/extraction/process` endpoint. The backend will parse the drawing using LLMs and OCR, extracting dimensions for the Bottom Dish, Lower Cone, Barrel, Top Cone, and Mouth.
4. **Data-Driven UI**: The UI will automatically map the extracted dimensions based on their spatial arrangement.
5. **Dynamic Geometry**: Step 1 (G1) will render a perfect 2D vector graphic of the vessel. If you manually alter any table values, the vector graphic will instantly re-render to reflect the new parametric shape.
6. **Proceed Through Wizard**: Proceed to Step 3 to see the brick lining design overlay onto the automatically extracted shell geometry.

---

## For the Next Developer (Integration Handoff)

- **UI Integration**: The entire frontend is encapsulated in `ui/index.html`. It maintains a global `S` state object.
- **Data Mapping**: In `loadDrawing` (inside `ui/index.html`), the extraction response is smartly mapped by sorting the segments by `index` (bottom-to-top) rather than trusting LLM hallucinated labels. This guarantees that `hDish`, `hLC`, `hBar`, `hTop`, and `hMouth` are always assigned perfectly.
- **SVG Rendering**: Look at `shellOnlySVG()` and `sideSVG()` to see how the geometry parameters (radii, heights, and spherical cap logic) dynamically translate into SVG paths.
- **Backend API**: The core extraction logic resides in `app/services/part2_extraction/geometry_assembler.py`. It leverages LiteLLM (Gemini 1.5 Pro) to structure the OCR data. 
- **Cleanup**: Testing scratchpads and irrelevant files have been stripped from this commit to ensure a clean codebase for immediate integration.

---

## Part 3 — Lining drawings & BOM from the drawing (lining GA, brick shapes, layers & brick layout, nozzles)

**Where it sits in the wizard:** last step, **13 · Summary & BOM** → card **"Lining drawings & BOM from the drawing"**.
The BOM card below it then shows the BOM counted from the generated drawing (and **⬇ BOM (CSV)** downloads that BOM).

**Input — only the values confirmed in steps 1–12 (nothing is defaulted in Part 3):**

| From | Used for |
|------|----------|
| Step 2 – person 1's extraction (edited / confirmed by you) | shell profile (inside of shell: elevations + IDs), plate, taphole (angle, EL, inclination, Ø), stirring (count, PCD), trunnion axis |
| Step 3 – zones | lining bands (heights); mouth start from the mouth course rule |
| Step 4 – angles & grades | zone spans / centres in plan, grade per zone |
| Step 5 – permanent lining | grade, thickness, backfill, permanent brick, reuse |
| Step 6 – lining concept | working thickness per zone (life × wear + residual → standard length / override) |
| Steps 7–11 | bottom (key radius, ring width, ring bands), brick pairs per zone, course height / joint / min. cut / expansion, mouth transition + steel-clad pairs and filling |
| Step 12 | special assemblies and junction treatment |

* Values that person 1 did **not** find on the drawing are marked *"not found"* in step 2 and block generation until you enter them
  (or tick **✓ Value is right**). If the drawing had no readable dimension text, the card offers **Scale to mm** with the real overall height.
* Bricks are laid in horizontal courses: on a cone the drawn (square-to-wall) thickness = brick length × cos(wall angle); zone
  boundaries on a straight wall are horizontal, a thickness change is a step on the hot face.

**Backend:** `POST /api/v1/lining/generate` → job id, `GET /api/v1/lining/jobs/{id}` for progress/result
(`app/api/v1/routes_lining.py` → `app/services/part3_lining/service.py`). Outputs: `data/outputs/<job>/lining_<timestamp>/`.

| File | Content |
|------|---------|
| `lining_ga.dxf/.png/.pdf` | vertical sections (taphole and trunnion planes), plan sections, top view, unrolled map, nozzles |
| `brick_shapes.dxf/.png/.pdf` | every brick used (numbered circle = item no.), dimensioned, with quantity; then the rest of the library |
| `lining_layers.dxf/.png/.pdf` | course section, bottom ring layout and one ring plan per band – **every brick has a numbered circle in its colour**; brick key = item no. → mark → name (what it is, where it is laid) → size → grade → pieces |
| `bom.xlsx/.csv` | BOM counted from the drawing (wall courses, bottom rings, permanent lining, filling, junction courses, assemblies) |
| `course_schedule.csv`, `bottom_rings.csv`, `brick_quantities.csv`, `summary.txt`, `package.zip` | tables, accuracy checks; ZIP of everything, every file named with the job prefix |

Every result is measured again and compared with the inputs (shell outline, layer thickness, inside volume, ring closure,
volume rebuilt course by course, BOM = layout) – shown as *Accuracy checks* in the card.
