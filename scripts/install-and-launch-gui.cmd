@echo off
setlocal
title Personal Job Discovery - First-time setup
cd /d "%~dp0\.."

echo Preparing Personal Job Discovery for first use...
where py >nul 2>nul
if errorlevel 1 goto no_python

py -3.12 -m venv .venv >nul 2>nul
if errorlevel 1 py -m venv .venv
if errorlevel 1 goto setup_failed

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto setup_failed
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto setup_failed

start "" ".venv\Scripts\pythonw.exe" -m job_discovery.gui --config "%CD%\config\companies.yml"
exit /b 0

:no_python
echo.
echo Python is not installed. Install Python 3.12 or newer from python.org,
echo then double-click Start Job Discovery again.
pause
exit /b 1

:setup_failed
echo.
echo Setup could not finish. The messages above show what needs attention.
pause
exit /b 1
