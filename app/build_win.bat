@echo off
REM TIPS ICE Planner - Windows配布ビルド（PyInstaller → .exe）。要 Python 3.13
cd /d "%~dp0"
if not exist .venv-win (
  py -3.13 -m venv .venv-win
)
call .venv-win\Scripts\activate
pip install -q -r requirements.txt pyinstaller
rmdir /s /q build 2>/dev/null
pyinstaller --noconfirm tips_ice.spec
echo Built: dist\TIPS ICE Planner\TIPS ICE Planner.exe
pause
