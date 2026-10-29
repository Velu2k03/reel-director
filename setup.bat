@echo off
REM ReelDirector setup wrapper: runs the single setup entry point,
REM preferring a Python 3.12+ interpreter.
cd /d "%~dp0"
py -3.12 --version >nul 2>nul
if %errorlevel%==0 (
  py -3.12 setup.py
  exit /b %errorlevel%
)
py -3 --version >nul 2>nul
if %errorlevel%==0 (
  py -3 setup.py
  exit /b %errorlevel%
)
python --version >nul 2>nul
if %errorlevel%==0 (
  python setup.py
  exit /b %errorlevel%
)
echo Setup needs Python 3.12 or newer: install it from https://www.python.org/downloads/ ^(tick "Add Python to PATH"^), then run setup again.
exit /b 1
