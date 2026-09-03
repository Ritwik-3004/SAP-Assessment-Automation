@echo off
echo Starting SAP Assessment Automation backend...
cd /d "%~dp0backend"

REM Create virtual environment if it doesn't exist
REM Must be 32-bit Python: SAP GUI Scripting only registers its COM
REM moniker in the 32-bit registry view, so a 64-bit interpreter fails
REM with "GetObject('SAPGUI') -> Invalid syntax".
if not exist ".venv" (
    echo Creating Python virtual environment ^(32-bit^)...
    py -3.12-32 -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment. Is 32-bit Python 3.12 installed?
        echo Install it with: py install 3.12-32
        pause & exit /b 1
    )
)

REM Activate venv
call .venv\Scripts\activate.bat

REM Install / update dependencies
echo Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed. See errors above.
    pause & exit /b 1
)

echo.
echo Backend starting at http://127.0.0.1:8000
echo API docs at http://127.0.0.1:8000/docs
echo.
python main.py
pause
