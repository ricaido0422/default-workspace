@echo off
set "EDGE="
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%LocalAppData%\Microsoft\Edge\Application\msedge.exe" set "EDGE=%LocalAppData%\Microsoft\Edge\Application\msedge.exe"

if "%EDGE%"=="" (
  echo [ERROR] Microsoft Edge not found on this PC.
  pause
  exit /b 1
)

start "" "%EDGE%" --app="https://chat.deepseek.com/" --user-data-dir="%LocalAppData%\DeepSeekAssistant"
exit /b 0
