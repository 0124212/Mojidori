#!/usr/bin/env python3
"""
Mojidori — Universal Launcher
Works on Windows, macOS, and Linux. No external dependencies beyond Python 3.9+.

Usage:
    python start.py              # Launch web UI
    python start.py --cli        # Launch CLI mode (legacy)
    python start.py --port 9000  # Custom port
"""

import os
import sys
import subprocess
import shutil
import webbrowser
import threading
import time
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
os.chdir(BASE_DIR)
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

MIN_PYTHON = (3, 9)
# import names that must be present (all come from requirements.txt)
NEEDED_MODULES = ['flask', 'dotenv', 'faster_whisper', 'whisper', 'torch', 'pydub', 'yt_dlp']
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"

BANNER = r"""
  ┌─────────────────────────────────────┐
  │             MOJIDORI                │
  │  Whisper-powered lecture transcription  │
  └─────────────────────────────────────┘
"""


def check_python():
    v = sys.version_info
    if v < MIN_PYTHON:
        print(f"  ✗ Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required (found {v.major}.{v.minor})")
        print(f"    Install: https://www.python.org/downloads/")
        sys.exit(1)


def find_pip():
    for cmd in [sys.executable, 'python3', 'python']:
        try:
            r = subprocess.run([cmd, '-m', 'pip', '--version'], capture_output=True, timeout=10)
            if r.returncode == 0:
                return [cmd, '-m', 'pip']
        except Exception:
            continue
    return None


def install_packages(pip, packages, label="dependencies"):
    print(f"  Installing {label}…")
    try:
        subprocess.run(pip + ['install', '--quiet', '--upgrade'] + packages, check=True)
        print(f"  ✓ {label} installed")
        return True
    except subprocess.CalledProcessError:
        print(f"  ✗ Failed to install {label}")
        print(f"    Try manually: {' '.join(pip)} install {' '.join(packages)}")
        return False


def has_nvidia_gpu() -> bool:
    if sys.platform == 'darwin' or not shutil.which('nvidia-smi'):
        return False
    try:
        return subprocess.run(['nvidia-smi', '-L'], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def torch_state() -> str:
    """'missing' | 'cuda' | 'cpu' — checked in a subprocess so a broken torch can't crash us."""
    code = "import torch,sys; sys.stdout.write('cuda' if torch.cuda.is_available() else 'cpu')"
    try:
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
        return r.stdout.strip() if r.returncode == 0 else 'missing'
    except Exception:
        return 'missing'


def ensure_deps():
    if getattr(sys, 'frozen', False):
        return  # bundled EXE: everything is baked in, nothing to install
    import importlib.util
    pip = find_pip()
    if pip is None:
        print("  ✗ pip not found. Install pip or use a full Python distribution.")
        sys.exit(1)

    # PyTorch first: plain `pip install torch` on Windows gives the CPU-only
    # build, which silently ignores an NVIDIA GPU. Get the CUDA build instead.
    if has_nvidia_gpu():
        state = torch_state()
        if state != 'cuda':
            why = "not installed" if state == 'missing' else "installed without GPU support"
            print(f"  NVIDIA GPU found, PyTorch {why} — installing the CUDA build (~2.5 GB, one-time)…")
            extra = ['--force-reinstall', '--no-deps'] if state == 'cpu' else []
            subprocess.run(pip + ['install', '--upgrade', *extra, 'torch', 'torchaudio',
                                  '--index-url', TORCH_CUDA_INDEX])

    missing = [m for m in NEEDED_MODULES if importlib.util.find_spec(m) is None]
    if missing:
        print(f"  Installing missing packages: {', '.join(missing)}")
        try:
            subprocess.run(pip + ['install', '-r', str(BASE_DIR / 'requirements.txt')], check=True)
            print("  ✓ packages installed")
        except subprocess.CalledProcessError:
            print(f"  ✗ install failed — try: {' '.join(pip)} install -r requirements.txt")
            sys.exit(1)


def check_ffmpeg():
    if shutil.which('ffmpeg'):
        return True
    print("  ⚠ FFmpeg not found — required for audio processing")
    print("    Install: https://ffmpeg.org/download.html")
    system = sys.platform
    if system == 'linux':
        print("    Debian/Ubuntu:  sudo apt install ffmpeg")
        print("    Fedora:         sudo dnf install ffmpeg")
    elif system == 'darwin':
        print("    macOS:          brew install ffmpeg")
    elif system == 'win32':
        print("    Windows:        winget install ffmpeg")
    print()
    return False


def create_dirs():
    (BASE_DIR / "queue").mkdir(exist_ok=True)
    (BASE_DIR / "processed").mkdir(exist_ok=True)


def launch_web(port, no_browser):
    from app import app, PROCESSED_DIR, QUEUE_DIR

    url = f"http://127.0.0.1:{port}"
    print(f"  Web UI:  {url}")
    print(f"  Queue:   {QUEUE_DIR}")
    print(f"  Output:  {PROCESSED_DIR}")
    print()

    if not no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)


def launch_cli():
    """Legacy CLI mode — runs transcribe_lecture.py"""
    cli_script = BASE_DIR / "transcribe_lecture.py"
    if cli_script.exists():
        os.execvp(sys.executable, [sys.executable, str(cli_script)])
    else:
        print("  ✗ transcribe_lecture.py not found")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Mojidori Launcher")
    parser.add_argument("--cli", action="store_true", help="Launch CLI mode instead of web UI")
    parser.add_argument("--port", type=int, default=8080, help="Web UI port (default: 8080)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    print(BANNER)

    # Step 1: Python version
    check_python()
    print(f"  ✓ Python {sys.version.split()[0]}")

    # Step 2: Directories
    create_dirs()
    print(f"  ✓ Directories ready")

    # Step 3: FFmpeg
    ffmpeg_ok = check_ffmpeg()
    if ffmpeg_ok:
        print(f"  ✓ FFmpeg found")

    # Step 4: Dependencies
    ensure_deps()
    print(f"  ✓ Dependencies ready")

    print()

    # Step 5: Launch
    if args.cli:
        launch_cli()
    else:
        launch_web(args.port, args.no_browser)


if __name__ == "__main__":
    try:
        from multiprocessing import freeze_support
        freeze_support()  # no-op unless frozen on Windows
    except ImportError:
        pass
    main()
