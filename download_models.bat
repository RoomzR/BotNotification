@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe scripts\download_models.py
) else (
  python scripts\download_models.py
)
pause
