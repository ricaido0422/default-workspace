@echo off
chcp 65001 >nul 2>&1
REM 查找 Python
set "PYTHON="
for %%P in (python python3) do (
    where %%P >nul 2>&1 && ( set "PYTHON=%%P" & goto found )
)
if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" (
    set "PYTHON=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
    goto found
)
echo [错误] 未找到 Python
pause
exit /b 1

:found
REM 默认跑 run（跟单+监测），可传子命令
if "%~1"=="" (
    "%PYTHON%" main.py run
) else (
    "%PYTHON%" main.py %*
)
