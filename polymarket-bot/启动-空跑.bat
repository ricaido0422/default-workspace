@echo off
chcp 65001 >nul
REM ============================================
REM Polymarket 跟单交易机器人 - 空跑启动
REM 前提：已开启梯子（代理 127.0.0.1:7897）
REM 空跑模式：enable_trading=false，不会真正下单
REM ============================================
set HTTPS_PROXY=http://127.0.0.1:7897
set HTTP_PROXY=http://127.0.0.1:7897
set ALL_PROXY=http://127.0.0.1:7897
echo.
echo === 启动 copy-trading 空跑模式 ===
echo enable_trading=false（不会真正下单）
echo 代理: 127.0.0.1:7897
echo 按 Ctrl+C 停止
echo.
polymarket-toolkits.exe run copy-trading
pause
