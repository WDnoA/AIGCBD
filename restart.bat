@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  微信AI自动回复系统 - 一键重启
REM  做三件事：停掉所有在跑的实例（含僵尸实例）→ 确认退干净 → 重新启动
REM  若换机器，只需改下面 PROJ 和 PYTHON 两个变量
REM ============================================================

set "PROJ=e:\PYnowjj\AIDH"
set "PYTHON=E:\ANACONDA3\envs\aids\python.exe"

cd /d "%PROJ%"
title 微信AI自动回复系统

echo ============================================================
echo   微信AI自动回复系统 - 一键重启
echo ============================================================
echo.
echo [1/2] 停止正在运行的实例...

REM 反复重试最多 12 次，避免上一次的进程还没退干净就启动下一个
powershell -NoProfile -Command "$sig='*main.py*'; for($i=0;$i -lt 12;$i++){ $p=Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like $sig }; if(-not $p){break}; $p | ForEach-Object { Write-Host ('      killed PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 800 }; $n=(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like $sig } | Measure-Object).Count; if($n -eq 0){ Write-Host '      [OK] all old instances stopped' } else { Write-Host ('      [WARN] ' + $n + ' instance(s) still alive') }"

echo.
echo [2/2] 启动程序...
echo.

if not exist "%PYTHON%" (
    echo [错误] 找不到 Python 解释器：%PYTHON%
    echo        请修改本脚本顶部的 PYTHON 变量。
    echo.
    pause
    exit /b 1
)

"%PYTHON%" "%PROJ%\src\main.py"

echo.
echo 程序已退出，按任意键关闭本窗口。
pause >nul
