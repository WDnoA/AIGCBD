@echo off
REM ============================================================
REM  AIGCBD dependency installer (ASCII only on purpose)
REM
REM  Do NOT put non-ASCII text in this file. cmd.exe parses .bat
REM  using the OEM codepage (936 on zh-CN), so a UTF-8 saved file
REM  with Chinese comments gets executed as garbage commands.
REM  All real work + Chinese output lives in install_deps.py.
REM
REM  Usage: double click, or:
REM      set PY=C:\path\to\python.exe     (default I:\Python313)
REM      install_deps.bat --check         (check only)
REM ============================================================
setlocal

if not defined PY set "PY=I:\Python313\python.exe"

if not exist "%PY%" (
  echo [ERROR] Python not found: %PY%
  echo         Run:  set PY=C:\path\to\python.exe
  pause
  exit /b 1
)

"%PY%" "%~dp0install_deps.py" %*

echo.
pause
