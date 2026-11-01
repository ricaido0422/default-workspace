@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   Polymarket Copy-Trading Console - Launcher
echo ============================================================
echo.

REM ---- 1. Find a usable Python ----
set "PY="

REM Prefer DSH bundled Python (known to work)
if exist "C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" (
    set "PY=C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
    echo [1/3] Found DSH bundled Python
)

REM Fall back to system python on PATH
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        set "PY=python"
        echo [1/3] Found system Python
    )
)

if not defined PY (
    echo [ERROR] Python not found.
    echo Make sure DSH is installed, or install Python 3.10+
    echo.
    pause
    exit /b 1
)

REM ---- 2. Check whether port 8791 is already in use ----
netstat -ano | findstr ":8791 " | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo.
    echo [INFO] Port 8791 is already in use. The console may already be running.
    echo Open in browser:  http://127.0.0.1:8791
    echo.
    start "" http://127.0.0.1:8791
    echo If the browser cannot connect, close other black console windows and retry.
    echo.
    pause
    exit /b 0
)

echo [2/3] Port 8791 is free, starting...
echo.

REM ---- 3. Launch ----
echo [3/3] Started! Your browser should open automatically.
echo.
echo  ============================================================
echo   Console URL:  http://127.0.0.1:8791
echo   Closing this black window STOPS the copy-trading
echo   Do NOT close it, or you will lose the signal feed
echo  ============================================================
echo.

start "" http://127.0.0.1:8791
"%PY%" -u ui.py --port 8791

echo.
echo Console stopped.
pause
