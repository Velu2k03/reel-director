#!/bin/sh
# ReelDirector start: runs the console with the project .venv.
cd "$(dirname "$0")" || exit 1
echo "Open http://127.0.0.1:5000"
.venv/bin/python app.py
