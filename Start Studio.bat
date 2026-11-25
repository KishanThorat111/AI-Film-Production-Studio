@echo off
title AI Film Studio
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The program folder .venv is missing. See docs\P0-USER-GUIDE.md
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m studio.daemon %*
if errorlevel 1 pause
