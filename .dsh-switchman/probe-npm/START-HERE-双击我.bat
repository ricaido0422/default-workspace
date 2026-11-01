@echo off
chcp 65001 >nul
title 跟单控制台 - 双击即用
cd /d "%~dp0"

echo ============================================================
echo   Polymarket Copy-Trading Console
echo ============================================================
echo.
echo   Your browser will open automatically. Three things to do:
echo.
echo     1. "Test Network" button  - confirm connection + USDC balance
echo     2. "Parameter Check"     - see how many trades current settings catch
echo     3. "Start"               - first round only records, never copies
echo.
echo   Only touch the big "Run Mode" switch at the top when you
echo   actually want to trade real money. Switching to LIVE
echo   requires typing CONFIRM, so it cannot happen by accident.
echo.
echo   Closing this black window STOPS the bot.
echo   Position ledger is saved automatically.
echo ============================================================
echo.

if not exist node_modules (
  echo [First run] Installing dependencies. Takes 1-2 min, once only...
  echo.
  call npm install
  if errorlevel 1 (
    echo.
    echo [Error] Dependency install failed. Screenshot this whole window.
    echo.
    pause
    exit /b 1
  )
  echo.
  echo [Done] Dependencies installed. Future starts are instant.
  echo.
)

node src\console.mjs
if errorlevel 1 (
  echo.
  echo [Error] Screenshot or copy the message above.
  echo.
)

echo.
echo ============================================================
echo   Console closed. Logs are in the logs\ folder.
echo ============================================================
pause
