@echo off
setlocal
cd /d "%~dp0\..\.."
if not exist .venv (
  py -3.11 -m venv .venv
  if errorlevel 1 goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r option-intelligence-main\requirements.txt
pip install -r desktop\windows\requirements.txt
pyinstaller --noconfirm --clean --windowed --name "Option Intelligence" ^
  --collect-all PySide6 ^
  --collect-all uvicorn ^
  --collect-all psycopg ^
  --hidden-import=sqlite3 --hidden-import=sqlite3.dbapi2 --hidden-import=_sqlite3 ^
  --hidden-import=main --hidden-import=accounts --hidden-import=indicators ^
  --hidden-import=instruments --hidden-import=feed_parser --hidden-import=dashboard_data ^
  --hidden-import=diagnostics --hidden-import=signal_fusion --hidden-import=signal_exits ^
  --hidden-import=notifier --hidden-import=option_plan --hidden-import=signal_board --hidden-import=monitor ^
  --add-data "option-intelligence-main\static;option-intelligence-main\static" ^
  --add-data "option-intelligence-main;option-intelligence-main" ^
  desktop\windows\launcher.py
if errorlevel 1 goto :error
echo.
echo Build complete: dist\Option Intelligence\Option Intelligence.exe
echo Keep the entire generated folder together.
pause
exit /b 0
:error
echo BUILD FAILED. Review the error above.
pause
exit /b 1
