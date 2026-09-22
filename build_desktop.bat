# Сборка Windows .exe (на машине Windows с установленным .venv):
#   .venv\Scripts\pip install pyinstaller
#   build_desktop.bat
#
# Готовый файл: dist\CyberX_Desktop.exe

@echo off
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist %PY% set PY=python

%PY% -m PyInstaller --noconfirm --clean ^
  --name CyberX_Desktop ^
  --windowed ^
  --add-data "data;data" ^
  --add-data "platform.yaml;." ^
  --add-data "config.yaml;." ^
  --add-data "models;models" ^
  --hidden-import ultralytics ^
  --hidden-import rapidocr_onnxruntime ^
  --hidden-import src.desktop.app ^
  run_desktop.py

echo.
echo Готово: dist\CyberX_Desktop.exe
pause
