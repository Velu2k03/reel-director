#!/bin/sh
# ReelDirector setup wrapper: runs the single setup entry point,
# preferring a Python 3.12+ interpreter.
cd "$(dirname "$0")" || exit 1
for py in python3.12 python3.13 python3; do
  if command -v "$py" >/dev/null 2>&1; then
    exec "$py" setup.py
  fi
done
echo "Setup needs Python 3.12 or newer: install it from https://www.python.org/downloads/, then run setup again." >&2
exit 1
