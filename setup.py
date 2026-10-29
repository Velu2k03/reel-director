"""ReelDirector one-step setup: Python check, .venv, packages, ffmpeg.

Run: python setup.py (or setup.sh / setup.bat on your platform).
Needs no API key and never asks for one.
"""

import os
import platform
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(HERE, ".venv")


def venv_python():
    if os.name == "nt":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def check_media(python):
    """Trigger the one-time ffmpeg/ffprobe fetch in the venv and
    report found/missing per binary. Returns True when both resolve
    (tools/ dir, PATH, or the static-ffmpeg download)."""
    code = (
        "import os, shutil, render\n"
        "for _n in ('ffmpeg', 'ffprobe'):\n"
        "    _p = render._tool(_n)\n"
        "    _ok = os.path.isfile(_p) or bool(shutil.which(_p))\n"
        "    print('%s=%s' % (_n, 'found' if _ok else 'missing'),\n"
        "          flush=True)\n"
    )
    print("Checking ffmpeg/ffprobe (platform binaries download "
          "once on first run) ...")
    r = subprocess.run([python, "-c", code], capture_output=True,
                       text=True, cwd=HERE)
    found = {}
    for line in (r.stdout or "").splitlines():
        print("  " + line.strip())
        if "=" in line:
            name, _, state = line.strip().partition("=")
            if name in ("ffmpeg", "ffprobe"):
                found[name] = (state == "found")
    if r.returncode != 0:
        print((r.stderr or "").strip())
        return False
    return found.get("ffmpeg") is True and found.get("ffprobe") is True


def ffmpeg_install_line():
    system = platform.system()
    if system == "Windows":
        return "winget install Gyan.FFmpeg"
    if system == "Darwin":
        return "brew install ffmpeg"
    return "sudo apt install -y ffmpeg"


def main():
    if sys.version_info < (3, 12):
        print("Setup needs Python 3.12 or newer (found %s)."
              % platform.python_version())
        print("Install it from https://www.python.org/downloads/ "
              "(Windows: tick 'Add Python to PATH'; macOS: use the "
              "python.org installer or 'brew install python@3.12'; "
              "Linux: your package manager), then run setup again.")
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

    if not check_media(python):
        print("ffmpeg/ffprobe not found and the automatic download "
              "failed. Install it with one command:")
        print("  %s" % ffmpeg_install_line())
        print("Then run setup again.")
        return 1
    print("Media tools ok (ffmpeg + ffprobe found).")

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
