@echo off
title LiningOps Copilot - Local Backend + UI Launcher
setlocal
cd /d "%~dp0"

echo ======================================================================
echo   LiningOps Copilot (RefractoryAI) — Local PC Launcher
echo ======================================================================

:: 1. Ensure .env exists
if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo [INFO] Created .env from .env.example
    )
)

:: 2. Find or create virtual environment
if exist ".venv\Scripts\python.exe" (
    set "PY_EXE=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PY_EXE=venv\Scripts\python.exe"
) else (
    echo [1/3] Creating Python virtual environment ^(.venv^)...
    python -m venv .venv
    set "PY_EXE=.venv\Scripts\python.exe"
    echo [2/3] Installing Python dependencies...
    "%PY_EXE%" -m pip install --upgrade pip
    "%PY_EXE%" -m pip install -r requirements.txt
    "%PY_EXE%" -m pip install -e .
)

echo [3/3] Starting Local FastAPI Backend on http://127.0.0.1:8000 ...
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8000/app"
"%PY_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
