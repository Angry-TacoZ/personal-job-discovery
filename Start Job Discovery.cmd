@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" goto install
".venv\Scripts\python.exe" -c "import multipart" >nul 2>nul
if errorlevel 1 goto install
start "" ".venv\Scripts\pythonw.exe" -m job_discovery.gui --config "%CD%\config\companies.yml"
exit /b 0

:install
call "%~dp0scripts\install-and-launch-gui.cmd"
exit /b %ERRORLEVEL%
