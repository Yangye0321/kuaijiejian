@echo off
rem ASCII only - see start-kuaijiejian.bat for why.
rem Shows the virtual key code of every key you press.
rem Use it to find out what your F12 (or any key) really sends.
title Shortcut Manager - Key Codes
cd /d "%~dp0"

set "PY="
for /f "delims=" %%I in ('where python.exe 2^>nul') do (
    if not defined PY set "PY=%%I"
)
for %%I in ("%PY%") do if %%~zI==0 set "PY="

if not defined PY (
    echo Python was not found on PATH.
    echo Run:  python "%~dp0kuaijiejian.py" --keys
    pause
    exit /b 1
)

echo Make sure the Shortcut Manager app is CLOSED first (only one keyboard hook at a time).
echo.
"%PY%" -u "%~dp0kuaijiejian.py" --keys %*
echo.
echo Finished. Press any key to close this window.
pause >nul
