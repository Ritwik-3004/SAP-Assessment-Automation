@echo off
echo Starting SAP Assessment Automation frontend...
cd /d "%~dp0frontend"

REM Install / update dependencies (same as the backend script does with pip).
REM Runs on every start, so a package added by a git pull is picked up automatically;
REM when nothing changed, npm finishes in a couple of seconds.
echo Installing dependencies...
call npm install
if errorlevel 1 (
    echo ERROR: npm install failed. See errors above.
    pause & exit /b 1
)

echo.
echo Frontend starting at http://localhost:5173
echo.
call npm run dev
pause
