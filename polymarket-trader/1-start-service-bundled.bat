@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "PY=%~dp0Python312\python.exe"

if not exist "%PY%" goto nopy

"%PY%" --version >nul 2>&1
if errorlevel 1 goto nopy

echo ============================================================
echo   Polymarket Trader is starting (bundled Python)...
echo   Press Ctrl+C to stop, or just close this window.
echo ============================================================
echo.

"%PY%" run.py --host 0.0.0.0 --port 8765

echo.
echo ============================================================
echo   Service stopped.
echo ============================================================
pause
exit /b 0

:nopy
echo [ERROR] Bundled Python not found:
echo   %PY%
echo.
echo This folder must contain the Python312 subfolder.
echo If you moved or copied only part of the folder, please
echo recopy the whole thing.
echo.
pause
exit /b 1
