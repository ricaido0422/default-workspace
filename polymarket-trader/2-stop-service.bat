@echo off
REM ============================================================
REM  Polymarket Trader - stop service
REM  ASCII ONLY
REM ============================================================

chcp 65001 >nul 2>&1

cd /d "%~dp0"

echo Stopping Polymarket Trader service...

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
    echo Killing PID %%a
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo Stopped. Press any key to close this window.
pause >nul
