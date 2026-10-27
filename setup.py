"""ReelDirector one-step setup: Python check, .venv, packages, ffmpeg.

Run: python setup.py (or setup.sh / setup.bat on your platform).
Needs no API key and never asks for one.
"""

import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(HERE, ".venv")
TOOLS_DIR = os.path.join(HERE, "tools")


def venv_python():
    if os.name == "nt":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def has_ffmpeg():
    if shutil.which("ffmpeg"):
        return True
    suffix = ".exe" if os.name == "nt" else ""
    for cand in (os.path.join(TOOLS_DIR, "ffmpeg" + suffix),
                 os.path.join(TOOLS_DIR, "bin", "ffmpeg" + suffix)):
        if os.path.isfile(cand):
            return True
    return False


def ffmpeg_install_line():
    system = platform.system()
    if system == "Windows":
        return "winget install Gyan.FFmpeg"
    if system == "Darwin":
        return "brew install ffmpeg"
    return "sudo apt install -y ffmpeg"


def main():
    if sys.version_info < (3, 10):
        print("Setup needs Python 3.10 or newer.")
        return 1
    print("Python ok: %s" % platform.python_version())

    if not os.path.isdir(VENV_DIR):
        print("Creating .venv ...")
        r = subprocess.run([sys.executable, "-m", "venv", VENV_DIR])
        if r.returncode != 0:
            print("Could not create .venv.")
            return 1
    python = venv_python()
    print("Installing packages from requirements.txt ...")
    r = subprocess.run(
        [python, "-m", "pip", "install", "-r",
         os.path.join(HERE, "requirements.txt")])
    if r.returncode != 0:
        print("Package install failed.")
        return 1
    print("Packages ok.")

    if not has_ffmpeg():
        print("ffmpeg not found. Install it with one command:")
        print("  %s" % ffmpeg_install_line())
        print("Then run setup again.")
        return 1
    print("FFmpeg ok.")

    key = os.environ.get("META_API_KEY", "").strip()
    if key:
        print("Key present: META_API_KEY is set.")
    else:
        print("Key still needed: paste it in the console after starting.")

    if os.name == "nt":
        start = ".venv\\Scripts\\python app.py"
    else:
        start = ".venv/bin/python app.py"
    print("Start: %s, then open http://127.0.0.1:5000" % start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
