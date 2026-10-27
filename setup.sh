#!/bin/sh
# ReelDirector setup wrapper: runs the single setup entry point.
cd "$(dirname "$0")" || exit 1
python3 setup.py
