@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   跟单机器人 - 单元测试（只读，不下单，不充钱）
echo ================================================
echo.
echo 这会检查：私钥打码 / 跨站请求拦截 / 风控停机 /
echo           退避重试 / 换跟单对象清账 / 跨日熔断重置
echo.
echo 跑完会自动关闭窗口。看到"全部通过"就说明护栏是好的。
echo.
pause
node scripts\run-all-tests.mjs
echo.
pause
