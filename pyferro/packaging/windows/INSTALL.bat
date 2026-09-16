@echo off
rem FERRO offline installer - no internet needed.
rem Unpacks the bundled Python environment next to this file and adds a desktop shortcut.
setlocal
cd /d "%~dp0"

if exist "%~dp0env\python.exe" (
    echo FERRO is already installed in %~dp0env
    goto shortcut
)

echo Unpacking the Python environment (about 1 GB on disk, takes a few minutes)...
"%~dp0tools\pixi-unpack.exe" "%~dp0environment-win-64.tar" --output-directory "%~dp0." --env-name env --shell cmd
if errorlevel 1 (
    echo.
    echo *** Installation failed. Make sure this folder is on a local disk with enough free space. ***
    pause
    exit /b 1
)

:shortcut
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\PyFERRO.lnk');" ^
  "$s.TargetPath='%~dp0PyFERRO.bat'; $s.WorkingDirectory='%~dp0.'; $s.WindowStyle=7;" ^
  "$s.IconLocation='%~dp0env\pythonw.exe,0'; $s.Save()"

echo.
echo Done. Start FERRO from the desktop shortcut or PyFERRO.bat.
echo Before measuring, install NI-VISA + NI-488.2 (GPIB) and the FTDI driver if they are not already present - see README.md.
pause
