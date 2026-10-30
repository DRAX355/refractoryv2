@echo off
title Build ZeroZeta LiningOps Copilot Desktop and Setup EXEs
setlocal

cd /d "%~dp0\.."
echo ======================================================================
echo   Building ZeroZeta — LiningOps Copilot Desktop and Installer EXEs
echo   Pre-configured for EC2 Central Server: http://65.1.125.127
echo ======================================================================

:: 1. Pack the ui/ folder into ui_bundle.zip for embedding inside the EXE
powershell -NoProfile -Command "$ProgressPreference = 'SilentlyContinue'; Add-Type -AssemblyName System.IO.Compression.FileSystem; if (Test-Path 'desktop_client\ui_bundle.zip') { Remove-Item 'desktop_client\ui_bundle.zip' -Force }; [System.IO.Compression.ZipFile]::CreateFromDirectory((Resolve-Path 'ui').Path, (Join-Path (Resolve-Path 'desktop_client').Path 'ui_bundle.zip'))"

:: 2. Compile self-contained Windows GUI executable using built-in .NET C# compiler
set "CSC=%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%SystemRoot%\Microsoft.NET\Framework\v4.0.30319\csc.exe"

"%CSC%" /nologo /target:winexe /optimize+ ^
    /out:"LiningOps_Copilot_Desktop.exe" ^
    /win32icon:"desktop_client\app_icon.ico" ^
    /r:Microsoft.CSharp.dll ^
    /r:System.IO.Compression.dll ^
    /r:System.IO.Compression.FileSystem.dll ^
    /r:System.Windows.Forms.dll ^
    /resource:"desktop_client\ui_bundle.zip" ^
    /resource:"desktop_client\app_icon.ico" ^
    "desktop_client\LiningOpsLauncher.cs"

copy /Y "LiningOps_Copilot_Desktop.exe" "desktop_client\LiningOps_Copilot_Desktop.exe" >nul
copy /Y "LiningOps_Copilot_Desktop.exe" "LiningOps_Copilot_Setup.exe" >nul
copy /Y "LiningOps_Copilot_Desktop.exe" "desktop_client\LiningOps_Copilot_Setup.exe" >nul
if exist "desktop_client\ui_bundle.zip" del /Q "desktop_client\ui_bundle.zip"

echo.
echo ======================================================================
echo   Build Complete!
echo   1. LiningOps_Copilot_Setup.exe   (1-Click Installer + Desktop Icon)
echo   2. LiningOps_Copilot_Desktop.exe (Direct App Executable)
echo ======================================================================
