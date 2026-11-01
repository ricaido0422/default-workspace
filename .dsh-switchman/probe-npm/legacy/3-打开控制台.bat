@echo off
chcp 65001 >nul
cd /d "%~dp0"
node src\console.mjs
if errorlevel 1 (
  echo.
  echo [出错了] 请把上面的错误信息截图或复制下来。
)
echo.
pause
