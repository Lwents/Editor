@echo off
if not exist "%~dp0Editor.exe" (
    powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0scripts\build-launcher.ps1"
    if errorlevel 1 exit /b 1
)
start "" "%~dp0Editor.exe"
