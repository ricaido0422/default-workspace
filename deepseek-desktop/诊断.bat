@echo off
set "ELECTRON_RUN_AS_NODE="
cd /d "%~dp0"

if not exist "node_modules\electron\dist\electron.exe" (
  echo [ERROR] electron.exe not found
  pause
  exit /b 1
)

echo Starting Electron with --disable-gpu --no-sandbox ...
"node_modules\electron\dist\electron.exe" --disable-gpu --no-sandbox --enable-logging --log-file="%~dp0chrome.log" "." 2>"%~dp0electron-error.log"
echo Electron exited with code %errorlevel%.
echo ---- electron error log ----
type "%~dp0electron-error.log" 2>nul
echo ---- end ----
echo.
echo If still no window, please send me the file: chrome.log  (in this folder)
pause