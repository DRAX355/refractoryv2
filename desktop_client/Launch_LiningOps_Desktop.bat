@echo off
title LiningOps Copilot - Desktop Client
setlocal

:: ============================================================================
:: LiningOps Copilot — Instant Windows Desktop Client Launcher
:: Central AWS EC2 Server: http://65.1.125.127
:: ============================================================================

set "SERVER_URL=http://65.1.125.127"
set "SCRIPT_DIR=%~dp0"

:: If the compiled self-contained EXE is next to this script, run it directly
if exist "%SCRIPT_DIR%LiningOps_Copilot_Desktop.exe" (
    start "" "%SCRIPT_DIR%LiningOps_Copilot_Desktop.exe" --server "%SERVER_URL%"
    exit /b 0
)
if exist "%SCRIPT_DIR%..\LiningOps_Copilot_Desktop.exe" (
    start "" "%SCRIPT_DIR%..\LiningOps_Copilot_Desktop.exe" --server "%SERVER_URL%"
    exit /b 0
)

set "LOCAL_UI=%SCRIPT_DIR%..\ui\app.html"
if not exist "%LOCAL_UI%" set "LOCAL_UI=%SCRIPT_DIR%ui\app.html"
if not exist "%LOCAL_UI%" set "LOCAL_UI=%LOCALAPPDATA%\LiningOpsCopilot\ui_v2\app.html"

echo ======================================================================
echo   LiningOps Copilot — Desktop Client
echo   Central Server: %SERVER_URL%
echo ======================================================================

if exist "%LOCAL_UI%" (
    for %%I in ("%LOCAL_UI%") do set "ABS_UI=%%~fI"
    setlocal enabledelayedexpansion
    set "TARGET_URL=file:///!ABS_UI:\=/!?api=%SERVER_URL%"
    goto :launch
) else (
    set "TARGET_URL=%SERVER_URL%/ui/app.html"
)

:launch
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
    start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" --app="%TARGET_URL%" --start-maximized
    exit /b 0
)
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
    start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" --app="%TARGET_URL%" --start-maximized
    exit /b 0
)
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --app="%TARGET_URL%" --start-maximized
    exit /b 0
)

python "%SCRIPT_DIR%desktop_app.py" --server "%SERVER_URL%"
