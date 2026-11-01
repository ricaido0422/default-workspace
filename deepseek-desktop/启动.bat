@echo off
set "ELECTRON_RUN_AS_NODE="
cd /d "%~dp0"

if not exist "node_modules\electron\dist\electron.exe" (
  echo [ERROR] Electron runtime not found. Please run: npm install
  pause
  exit /b 1
)

start "" "node_modules\electron\dist\electron.exe" --disable-gpu --no-sandbox "."
exit /b 0