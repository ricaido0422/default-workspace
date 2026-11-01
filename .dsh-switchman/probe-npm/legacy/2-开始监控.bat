@echo off
chcp 65001 >nul
title STEP 2 - Start Monitoring (SAFE, no real orders)
echo ============================================================
echo   STEP 2  -  START MONITORING
echo   DRY-RUN mode: watches and prints only. NEVER places an order.
echo   No npm install needed. No private key needed.
echo ============================================================
echo.
echo   Do NOT close this black window. It checks every 60 seconds.
echo   Press Ctrl+C to stop safely (state is saved automatically).
echo.
echo   NOTE: this old window entry has NO auto-guard (no backoff,
echo   no daily-loss circuit breaker). For automatic execution,
echo   use 3-Console.bat instead.
echo.
call npm run dryrun
echo.
echo ============================================================
echo   Monitoring stopped. State saved.
echo   Log files are in the logs\ folder.
echo ============================================================
pause
