@echo off
title AI Film Studio
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The program folder .venv is missing. Install it once as described in README.md
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m studio.daemon %*
if errorlevel 1 pause
