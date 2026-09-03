@echo off
echo Starting SAP Assessment Automation frontend...
cd /d "%~dp0frontend"

REM Install npm packages if node_modules doesn't exist
if not exist "node_modules" (
    echo Installing npm packages...
    npm install
)

echo.
echo Frontend starting at http://localhost:5173
echo.
npm run dev
pause
