@echo off
chcp 65001 >nul
REM ============================================
REM Polymarket 跟单交易机器人 - TUI 交互模式
REM 前提：已开启梯子（代理 127.0.0.1:7897）
REM ============================================
set HTTPS_PROXY=http://127.0.0.1:7897
set HTTP_PROXY=http://127.0.0.1:7897
set ALL_PROXY=http://127.0.0.1:7897
echo.
echo === 启动 TUI 交互模式 ===
echo 代理: 127.0.0.1:7897
echo.
polymarket-toolkits.exe
pause
