@echo off
chcp 65001 >nul
title STEP 1 - Network Check (via your proxy 127.0.0.1:7897)
echo ============================================================
echo   STEP 1  -  NETWORK CHECK  (v2, uses your proxy)
echo   Read-only. No private key. Will NOT place any order.
echo ============================================================
echo.
echo   This version routes through 127.0.0.1:7897
echo   because Node.js does not use the Windows proxy by default.
echo.
call npm run probe
echo.
echo ============================================================
echo   HOW TO READ THIS:
echo   - All lines say "HTTP 200"  -^> network OK, go to STEP 2
echo   - Still timing out          -^> see README section 6
echo ============================================================
echo   Screenshot this window and send it to me.
echo ============================================================
pause
