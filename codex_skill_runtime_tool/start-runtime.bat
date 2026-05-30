@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start-runtime.ps1" %*
if /i not "%~1"=="--no-pause" pause
