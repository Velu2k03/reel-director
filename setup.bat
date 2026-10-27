@echo off
REM ReelDirector setup wrapper: runs the single setup entry point.
cd /d "%~dp0"
python setup.py
