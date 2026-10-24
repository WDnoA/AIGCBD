@echo off
REM ============================================================
REM  WeChat AI Bot - Restart
REM
REM  IMPORTANT: keep this file pure ASCII.
REM  cmd.exe reads a .bat byte by byte using the OEM code page (936
REM  on this machine) while the file is written in UTF-8. Any Chinese
REM  character gets decoded into stray bytes, and a byte that lands on
REM  a metacharacter (& | ^ >) is executed as a new command -- that is
REM  exactly the "'??)' is not recognized" garbage you saw before.
REM  "chcp 65001" cannot fix it: the code page changes mid-file and
REM  every following byte offset is then misaligned.
REM  So: English output only here. Chinese output lives in the .py
REM  files, where Python encodes to the console code page itself.
REM
REM  Steps: stop every running main.py -> start a fresh one.
REM ============================================================
setlocal

set "PROJ=%~dp0"
if "%PROJ:~-1%"=="\" set "PROJ=%PROJ:~0,-1%"
set "PYTHON=I:\Python313\python.exe"

if not exist "%PYTHON%" (
    echo [ERROR] Python interpreter not found: %PYTHON%
    echo         Edit the PYTHON variable at the top of this script.
    echo.
    pause
    exit /b 1
)

cd /d "%PROJ%"
title WeChat AI Bot

echo ============================================================
echo   WeChat AI Bot - Restart
echo ============================================================
echo.
echo [1/2] Stopping running instances...
"%PYTHON%" "%PROJ%\tools\stop_instances.py"
if errorlevel 1 (
    echo.
    echo [ERROR] Old instances are still alive, aborted.
    echo         Two bots on one WeChat window will fight over messages.
    echo.
    pause
    exit /b 1
)

echo.
echo [2/2] Starting...
echo.
"%PYTHON%" "%PROJ%\src\main.py"

echo.
echo Bot exited. Press any key to close this window.
pause >nul
