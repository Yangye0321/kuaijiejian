@echo off
rem ============================================================
rem  Shortcut Manager - launcher WITHOUT elevation (ASCII only)
rem
rem  Use this one if you do not want a UAC prompt on every start.
rem  Trade-off: Windows UIPI does not deliver keyboard-hook events
rem  for windows that run elevated, so while an elevated app is in
rem  the foreground this tool will not see your keys.
rem  The normal launcher (start-kuaijiejian.bat) requests admin.
rem ============================================================
setlocal
cd /d "%~dp0"

set "PYW="
set "PY="

for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do (
    if not defined PYW set "PYW=%%I"
)
for /f "delims=" %%I in ('where python.exe 2^>nul') do (
    if not defined PY set "PY=%%I"
)
for %%I in ("%PYW%") do if %%~zI==0 set "PYW="
for %%I in ("%PY%") do if %%~zI==0 set "PY="
if not defined PYW if defined PY (
    for %%I in ("%PY%") do if exist "%%~dpIpythonw.exe" set "PYW=%%~dpIpythonw.exe"
)

if not defined PYW if not defined PY goto :nopython

>>"%~dp0startup.log" echo [%DATE% %TIME%] ===== launcher: start-kuaijiejian-unelevated.bat =====

if defined PYW (
    start "ShortcutManager" /d "%~dp0" "%PYW%" "%~dp0kuaijiejian.py" %*
) else (
    start "ShortcutManager" /d "%~dp0" "%PY%" "%~dp0kuaijiejian.py" %*
)
exit /b 0

:nopython
echo.
echo   [Shortcut Manager] Python was not found on PATH.
echo   Install Python 3.8+ (with tkinter) and tick "Add Python to PATH".
echo.
pause
exit /b 1
