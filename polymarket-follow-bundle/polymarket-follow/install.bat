@echo off
chcp 65001 >nul 2>&1
echo ============================================
echo   Polymarket 跟单监测工具 - 安装脚本
echo ============================================
echo.

REM 查找 Python
set "PYTHON="
for %%P in (python python3) do (
    where %%P >nul 2>&1 && (
        set "PYTHON=%%P"
        goto found
    )
)

REM 尝试 bundled Python
if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" (
    set "PYTHON=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
    goto found
)

echo [错误] 未找到 Python，请先安装 Python 3.8+
pause
exit /b 1

:found
echo [1/4] Python: %PYTHON%
"%PYTHON%" --version
echo.

echo [2/4] 安装可选依赖...
echo   - websocket-client (WebSocket 实时行情)
"%PYTHON%" -m pip install websocket-client --quiet 2>nul && echo     OK || echo     跳过（不影响核心功能）
echo   - py-clob-client (真实下单执行器)
"%PYTHON%" -m pip install py-clob-client --quiet 2>nul && echo     OK || echo     跳过（不影响核心功能，dry-run 模式可用）
echo.

echo [3/4] 运行离线测试...
"%PYTHON%" tests\run_tests.py
echo.

echo [4/4] 接口连通性自检...
"%PYTHON%" main.py selfcheck
echo.
echo ============================================
echo   安装完成！
echo   用法: python main.py run    (启动跟单+监测)
echo   帮助: python main.py --help
echo ============================================
pause
