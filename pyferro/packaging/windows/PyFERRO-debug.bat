@echo off
rem Run FERRO with a console window so start-up errors are visible (for troubleshooting).
setlocal
cd /d "%~dp0"
call "%~dp0activate.bat"
set "PYTHONPATH=%~dp0app"
python -c "import sys, pyvisa; print('Python', sys.version); rm = pyvisa.ResourceManager(); print('VISA library:', rm.visalib); print('Resources:', rm.list_resources())"
python -c "from serial.tools import list_ports; [print('Serial:', p.device, '-', p.description) for p in list_ports.comports()]"
python -m ferro %*
pause
