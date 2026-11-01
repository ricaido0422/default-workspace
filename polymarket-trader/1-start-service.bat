@echo off
REM ============================================================
REM  Polymarket Trader - start service (double-click to run)
REM  Auto-picks a Python: bundled Python312 first, then system PATH
REM  ASCII ONLY - cmd.exe parses .bat as GBK, non-ASCII breaks it
REM ============================================================

chcp 65001 >nul 2>&1

cd /d "%~dp0"

set "PY="

REM --- 1) Prefer the bundled Python shipped in this folder ---
if exist "%~dp0Python312\python.exe" (
    "%~dp0Python312\python.exe" --version >nul 2>&1
    if not errorlevel 1 (
        set "PY=%~dp0Python312\python.exe"
        echo [INFO] Using bundled Python: Python312\python.exe
    )
)

REM --- 2) Fall back to system Python on PATH ---
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 (
        python --version 2>nul | findstr /r "^Python 3\." >nul
        if not errorlevel 1 (
            set "PY=python"
            echo [INFO] Using system Python from PATH.
        ) else (
            goto stubsuspect
        )
    )
)

if not defined PY goto nopython

REM --- 3) Check key dependencies ---
"%PY%" -c "import fastapi, uvicorn, aiosqlite, websockets" >nul 2>&1
if errorlevel 1 goto nodeps

goto startok

:nopython
echo [ERROR] No usable Python found.
echo.
echo Neither the bundled Python312 folder nor PATH python works.
echo.
echo Fix option A (easiest): make sure the folder "Python312"
echo   sits next to this .bat file. It ships inside the project zip.
echo   If it is missing, re-extract the zip (do not copy files one by one).
echo.
echo Fix option B (own Python):
echo   1. Go to https://www.python.org/downloads/
echo   2. Click "Download Python 3.12"
echo   3. Run the installer
echo   4. *** TICK "Add Python to PATH" on the first screen ***
echo   5. After install, REBOOT your PC, then run this file again
echo.
pause
exit /b 1

:stubsuspect
echo [ERROR] The "python" command on your PC is a Windows Store
echo placeholder, not a real Python. It cannot run anything.
echo.
echo Fix: uninstall the fake entries, then install real Python.
echo   1. Press Win, type "Python", if you see "Python 3.x (Microsoft Store)"
echo      right-click it and choose Uninstall
echo   2. Also check: Settings ^> Apps ^> Installed apps
echo      remove anything named "Python 3.x" that says Microsoft Store
echo   3. Then install from https://www.python.org/downloads/
echo      TICK "Add Python to PATH" on the first screen
echo   4. REBOOT your PC, then run 0-install-deps.bat again
echo.
echo TIP: this project also ships its own Python. Just make sure the
echo   "Python312" folder is next to this .bat and run it again.
echo.
pause
exit /b 1

:nodeps
echo [ERROR] Dependencies missing for: %PY%
echo.
echo If using your own Python, run 0-install-deps.bat first.
echo If using the bundled Python, the zip may be incomplete -
echo   please re-extract the full project zip.
echo.
pause
exit /b 1

:startok
echo ============================================================
echo   Polymarket Trader is starting...
echo   Press Ctrl+C to stop, or just close this window.
echo ============================================================
echo.

"%PY%" run.py --host 0.0.0.0 --port 8765

echo.
echo ============================================================
echo   Service stopped.
echo   If you saw errors above, please keep a screenshot.
echo ============================================================
pause
