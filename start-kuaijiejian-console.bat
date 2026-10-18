@echo off
rem ASCII only - see start-kuaijiejian.bat for why.
rem Same tool as start-kuaijiejian.bat, but keeps a console window open
rem so you can see live output: hook install results, key decisions, errors.
title Shortcut Manager - Console
cd /d "%~dp0"

set "PY="
for /f "delims=" %%I in ('where python.exe 2^>nul') do (
    if not defined PY set "PY=%%I"
)
for %%I in ("%PY%") do if %%~zI==0 set "PY="

if not defined PY (
    echo Python was not found on PATH.
    echo Run:  python "%~dp0kuaijiejian.py"
    pause
    exit /b 1
)

echo Starting Shortcut Manager (console mode)...
echo Close this window or the app window to stop.
echo.
"%PY%" -u "%~dp0kuaijiejian.py" %*
echo.
echo Exited with code %ERRORLEVEL%.
pause >nul
