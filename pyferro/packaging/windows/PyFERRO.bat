@echo off
rem Launch FERRO without a console window. Extra arguments are passed through (e.g. --simulate).
setlocal
cd /d "%~dp0"
if not exist "%~dp0env\python.exe" (
    echo FERRO is not installed yet - run INSTALL.bat first.
    pause
    exit /b 1
)
call "%~dp0activate.bat"
set "PYTHONPATH=%~dp0app"
start "" pythonw -m ferro %*
