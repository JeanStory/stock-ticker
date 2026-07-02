@echo off
REM 一键打包为单文件 exe，产物在 dist\stock-glance.exe
REM 需先: pip install -r requirements.txt pyinstaller

pyinstaller --onefile --windowed --name stock-glance ^
  --hidden-import pystray._win32 ^
  --hidden-import PIL._tkinter_finder ^
  --hidden-import win32gui ^
  --hidden-import win32con ^
  --hidden-import win32api ^
  --clean --noconfirm ^
  run.py

echo.
echo Build done. Output: dist\stock-glance.exe
