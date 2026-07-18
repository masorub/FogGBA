@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Python 3.10+ required / Нужен Python 3.10+
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)
python -c "import PIL" 2>nul
if errorlevel 1 (
  echo Installing Pillow...
  python -m pip install -r requirements.txt
)
echo Starting FogConnect...
python main.py
if errorlevel 1 pause
