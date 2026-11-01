@echo off
REM ============================================================
REM  Polymarket Trader - dependency installer (run ONCE)
REM  Requires Python 3.10+ installed with "Add Python to PATH"
REM  ASCII ONLY - cmd.exe parses .bat as GBK, non-ASCII breaks it
REM ============================================================

chcp 65001 >nul 2>&1

cd /d "%~dp0"

echo ============================================================
echo   Polymarket Trader - installing dependencies
echo ============================================================
echo.

REM --- 1. Check Python ---
python --version >nul 2>&1
if errorlevel 1 goto nopython

REM Windows Store stub: python.exe exists but always fails / opens Store
python --version 2>nul | findstr /r "^Python 3\." >nul
if errorlevel 1 goto stubsuspect

goto pythonok

:nopython
echo [ERROR] Python not found on PATH.
echo.
echo Please install Python first:
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
echo   4. REBOOT your PC, then run this file again
echo.
pause
exit /b 1

:pythonok
echo [OK] Python found:
python --version
echo.

REM --- 2. Upgrade pip ---
echo [1/3] Upgrading pip...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo [WARN] pip upgrade failed, continuing anyway...
)
echo.

REM --- 3. Install dependencies ---
echo [2/3] Installing dependencies (this takes 1-3 minutes)...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Installation failed.
    echo.
    echo Common causes:
    echo   - No internet connection
    echo   - pip blocked by antivirus / firewall
    echo   - Corporate network requires a proxy
    echo.
    echo Try running this in a Command Prompt to see the full error:
    echo   python -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo.

REM --- 4. Verify ---
echo [3/3] Verifying installation...
python -c "import fastapi, httpx, pydantic, pydantic_settings, yaml, websockets, aiosqlite, uvicorn; print('[OK] all dependencies installed')"
if errorlevel 1 (
    echo [ERROR] Some dependencies are still missing.
    echo Please keep the messages above and report them.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Installation complete!
echo.
echo   Now double-click:  1-start-service.bat
echo   Then open in browser:  http://localhost:8765
echo ============================================================
pause
