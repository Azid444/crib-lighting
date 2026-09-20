@echo off
REM Double-click to run. Keep this window open while you want the lights.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment missing. Run setup_windows.ps1 first.
    pause
    exit /b 1
)
echo Starting crib-lighting...
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=1" %%b in ("%%a") do echo   On your iPhone: http://%%b:8080
)
echo.
.venv\Scripts\python.exe -m crib.app
pause
