@echo off
REM ReelDirector start: runs the console with the project .venv.
cd /d "%~dp0"
echo Open http://127.0.0.1:5000
.venv\Scripts\python app.py
