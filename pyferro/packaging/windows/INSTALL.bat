@echo off
rem PyFERRO offline installer - no internet needed.
rem Unpacks the bundled Python environment next to this file and adds a desktop shortcut.
setlocal
cd /d "%~dp0" 2>nul || goto badfolder

rem Everything below has to sit next to this file. Running INSTALL.bat straight out
rem of Explorer's zip viewer copies only the .bat to a temp folder, and a part-finished
rem extraction leaves the rest behind too, so say which piece is missing rather than
rem letting cmd report "The system cannot find the path specified".
if not exist "%~dp0tools\pixi-unpack.exe"  goto notextracted
if not exist "%~dp0tools\vcruntime140.dll" goto notextracted
if not exist "%~dp0environment-win-64.tar" goto notextracted
if not exist "%~dp0app\ferro\__init__.py"  goto notextracted

if exist "%~dp0env\python.exe" (
    echo PyFERRO is already installed in %~dp0env
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
echo Done. Start PyFERRO from the desktop shortcut or PyFERRO.bat.
echo Before measuring, install NI-VISA + NI-488.2 (GPIB) and the FTDI driver if they are not already present - see README.md.
pause
exit /b 0

:notextracted
echo.
echo *** This folder does not hold a complete copy of the bundle. ***
echo.
echo     Looking in: %~dp0
echo.
dir /b "%~dp0"
echo.
echo It should contain app, tools, manuals and environment-win-64.tar, and
echo tools should hold pixi-unpack.exe next to vcruntime140.dll.
echo Extract the whole PyFERRO-...-win64-offline.zip to a folder on a local disk
echo - C:\PyFERRO is a good choice - and run INSTALL.bat from there. Opening the zip
echo in Explorer and double-clicking INSTALL.bat inside it does not work: Windows
echo copies only that one file to a temporary folder.
echo.
pause
exit /b 1

:badfolder
echo.
echo *** Could not switch to %~dp0 ***
echo.
echo If that path starts with \\ the bundle is on a network share, which cmd cannot
echo use as a working folder. Copy the folder to a local disk and run it from there.
echo.
pause
exit /b 1
