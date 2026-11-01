@echo off
chcp 65001 >nul
title 跟单控制台 - 双击即用
cd /d "%~dp0"

echo ============================================================
echo   跟单控制台启动器
echo ============================================================
echo.
echo   接下来会自动打开浏览器。日常只做三件事：
echo.
echo     1. 点右上角「测试网络」    - 确认能连上、看 USDC 余额
echo     2. 点右上角「参数体检」    - 看当前参数能跟到几笔
echo     3. 点「启动」              - 第一轮只记录，不会跟单
echo.
echo   想用真钱下单，才去动页面上方那个「运行模式」大开关
echo   （切 LIVE 需要手输 CONFIRM，不会误触）。
echo.
echo   关闭这个黑窗口 = 停止机器人。持仓账已自动存盘。
echo ============================================================
echo.

if not exist node_modules (
  echo [首次运行] 正在安装依赖，需要一两分钟，只此一次...
  echo.
  call npm install
  if errorlevel 1 (
    echo.
    echo [出错了] 依赖安装失败。请把这个窗口完整截图发给对方。
    echo.
    pause
    exit /b 1
  )
  echo.
  echo [完成] 依赖装好了。以后启动不会再等。
  echo.
)

node src\console.mjs
if errorlevel 1 (
  echo.
  echo [出错了] 请把上面的错误信息截图或复制下来。
  echo.
)

echo.
echo ============================================================
echo   控制台已关闭。日志在 logs\ 目录。
echo   想彻底关掉自动守卫功能，删 logs\console-state.json 即可。
echo ============================================================
pause
