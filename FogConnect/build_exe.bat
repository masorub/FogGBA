@echo off
setlocal
cd /d "%~dp0"
python -m pip install -q -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name FogConnect ^
  --add-data "assets;assets" ^
  --hidden-import PIL._tkinter_finder ^
  main.py
if errorlevel 1 exit /b 1
echo Built: dist\FogConnect.exe
endlocal
