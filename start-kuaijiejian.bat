@echo off
rem ============================================================
rem  Shortcut Manager launcher (ASCII only on purpose!)
rem  cmd.exe reads .bat files in the local ANSI (GBK) codepage,
rem  so any UTF-8 Chinese text here would break the parser.
rem  Keep this file plain ASCII. Chinese messages live in the
rem  Python tool and in README.md instead.
rem
rem  Elevation: Windows UIPI only feeds WH_KEYBOARD_LL to a normal-
rem  integrity hook while the foreground app is at the same or lower
rem  integrity level. So the tool must run elevated to receive keys
rem  typed into elevated windows. This script re-launches itself
rem  elevated once (KJJ_ELEVATED guard prevents an infinite loop).
rem ============================================================
setlocal
cd /d "%~dp0"

if defined KJJ_ELEVATED goto :run
rem Already elevated? High integrity = S-1-16-12288, System = S-1-16-16384.
rem (whoami /groups is more reliable than fsutil, which can misfire.)
whoami /groups 2>nul | findstr /C:"S-1-16-12288" >nul 2>&1 && goto :run
whoami /groups 2>nul | findstr /C:"S-1-16-16384" >nul 2>&1 && goto :run

echo Requesting administrator privileges...
>>"%~dp0startup.log" echo [%DATE% %TIME%] ===== launcher: start-kuaijiejian.bat requesting elevation =====
rem NOTE: %* is empty when double-clicked. Passing -ArgumentList '' makes
rem PowerShell reject the call (null/empty argument), so only pass it when args exist.
if "%~1"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '%*'"
)
if errorlevel 1 (
    echo.
    echo   Elevation was cancelled or failed.
    echo   The tool can still run without admin rights, but it will NOT
    echo   receive keys typed into windows that run as administrator.
    echo   To run unelevated anyway use:
    echo       start-kuaijiejian-unelevated.bat
    echo.
    pause
    exit /b 1
)
exit /b 0

:run
set "KJJ_ELEVATED=1"
set "PYW="
set "PY="

rem Prefer the interpreter next to the running python, then PATH.
for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do (
    if not defined PYW set "PYW=%%I"
)
for /f "delims=" %%I in ('where python.exe 2^>nul') do (
    if not defined PY set "PY=%%I"
)

rem Skip the zero-byte Microsoft Store alias stubs.
for %%I in ("%PYW%") do if %%~zI==0 set "PYW="
for %%I in ("%PY%") do if %%~zI==0 set "PY="

if not defined PYW if defined PY (
    for %%I in ("%PY%") do if exist "%%~dpIpythonw.exe" set "PYW=%%~dpIpythonw.exe"
)

if not defined PYW if not defined PY goto :nopython

rem Marker so startup.log shows which launcher was used and when.
>>"%~dp0startup.log" echo [%DATE% %TIME%] ===== launcher: start-kuaijiejian.bat (pythonw mode, elevated) =====

if defined PYW (
    start "ShortcutManager" /d "%~dp0" "%PYW%" "%~dp0kuaijiejian.py" %*
) else (
    start "ShortcutManager" /d "%~dp0" "%PY%" "%~dp0kuaijiejian.py" %*
)
exit /b 0

:nopython
echo.
echo   [Shortcut Manager] Python was not found on PATH.
echo.
echo   Install Python 3.8+ and tick "Add Python to PATH",
echo   or run the tool manually with:
echo       python "%~dp0kuaijiejian.py"
echo.
pause
exit /b 1
