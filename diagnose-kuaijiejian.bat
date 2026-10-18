@echo off
rem ASCII only - see start-kuaijiejian.bat for why.
rem Runs the live diagnostic: it prints what happens for every key you press.
title Shortcut Manager - Diag
cd /d "%~dp0"

set "PY="
for /f "delims=" %%I in ('where python.exe 2^>nul') do (
    if not defined PY set "PY=%%I"
)
for %%I in ("%PY%") do if %%~zI==0 set "PY="

if not defined PY (
    echo Python was not found on PATH.
    echo Run:  python "%~dp0kuaijiejian.py" --diag
    pause
    exit /b 1
)

"%PY%" "%~dp0kuaijiejian.py" --diag %*
echo.
echo Diagnostic finished. Press any key to close this window.
pause >nul
