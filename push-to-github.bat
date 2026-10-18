@echo off
rem ============================================================
rem  Push this repository to GitHub (run me in YOUR OWN terminal,
rem  not inside any sandbox: HTTPS must work there).
rem
rem  Usage:
rem    push-to-github.bat <github-user> <repo-name> [public|private]
rem  or just run it and answer the two prompts.
rem
rem  Notes:
rem  * The repo must already exist on GitHub as an EMPTY repo
rem    (no README / no .gitignore), or you need GitHub CLI (gh)
rem    installed and logged in, in which case this script can create it.
rem  * ASCII only on purpose.
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "GHUSER=%~1"
set "REPO=%~2"
set "VIS=%~3"
if "%VIS%"=="" set "VIS=public"

if "%GHUSER%"=="" set /p GHUSER=GitHub username: 
if "%REPO%"=="" set /p REPO=Repository name (e.g. kuaijiejian): 
if "%GHUSER%"=="" goto :badargs
if "%REPO%"=="" goto :badargs

where git >nul 2>&1
if errorlevel 1 (
    echo git was not found on PATH. Install Git for Windows first.
    pause
    exit /b 1
)

if not exist ".git" (
    echo This folder is not a git repository yet.
    echo Run this from the repository folder, or ask the agent to init it.
    pause
    exit /b 1
)

set "REMOTE=https://github.com/%GHUSER%/%REPO%.git"
echo.
echo Target: %REMOTE%
echo.

rem --- Option A: GitHub CLI available and logged in -> create repo + push
where gh >nul 2>&1
if not errorlevel 1 (
    gh auth status >nul 2>&1
    if not errorlevel 1 (
        echo Using GitHub CLI to create the repository...
        gh repo create "%REPO%" --%VIS% --source=. --remote=origin --push
        if errorlevel 1 (
            echo.
            echo gh failed. Falling back to plain git push...
            git remote remove origin >nul 2>&1
        ) else (
            goto :done
        )
    )
)

rem --- Option B: plain git push (repo must exist and be empty)
git remote remove origin >nul 2>&1
git remote add origin "%REMOTE%"
echo Pushing to %REMOTE% ...
echo (A browser window may open for GitHub login; approve it.)
git push -u origin main
if errorlevel 1 (
    echo.
    echo Push failed. Common causes:
    echo   * the repository does not exist yet  -^> create an EMPTY repo on github.com first
    echo   * the repository already has commits -^> use "git push -u origin main --force"
    echo   * login/cancelled in the credential window -^> run again
    pause
    exit /b 1
)

:done
echo.
echo Done: https://github.com/%GHUSER%/%REPO%
pause
exit /b 0

:badargs
echo Username and repository name are required.
pause
exit /b 1
