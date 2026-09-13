"""One-command local start for Upset Sale Intel.

    python dev.py            # install if needed, migrate, start API + worker + web UI
    python dev.py --seed     # also load the anonymized offline demo projects
    python dev.py --no-web   # API + worker only

API: http://127.0.0.1:8000 (docs at /docs) · Web UI: http://127.0.0.1:5173
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def venv_python() -> Path:
    windows = BACKEND / ".venv" / "Scripts" / "python.exe"
    return windows if windows.exists() or os.name == "nt" else BACKEND / ".venv" / "bin" / "python"


def ensure_backend() -> None:
    if not venv_python().exists():
        print("Creating backend virtual environment…")
        subprocess.check_call([sys.executable, "-m", "venv", str(BACKEND / ".venv")])
        subprocess.check_call([str(venv_python()), "-m", "pip", "install", "-r", "requirements-dev.txt"], cwd=BACKEND)


def npm() -> str:
    found = shutil.which("npm")
    if not found:
        sys.exit("npm was not found on PATH. Install Node.js 20+ or run with --no-web.")
    return found


def ensure_frontend() -> None:
    if not (FRONTEND / "node_modules").exists():
        print("Installing frontend dependencies…")
        subprocess.check_call([npm(), "install"], cwd=FRONTEND)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", action="store_true", help="load anonymized demo projects (offline fixtures)")
    parser.add_argument("--no-web", action="store_true", help="do not start the Vite web UI")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    ensure_backend()
    python = str(venv_python())
    env = {**os.environ, "USI_AUTO_MIGRATE": "false"}
    subprocess.check_call([python, "-m", "app.cli", "migrate"], cwd=BACKEND, env=env)
    if args.seed:
        subprocess.check_call([python, "-m", "app.cli", "seed-demo"], cwd=BACKEND, env={**env, "USI_DEMO_MODE": os.environ.get("USI_DEMO_MODE", "false")})

    procs: list[subprocess.Popen] = [
        subprocess.Popen([python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port)], cwd=BACKEND, env=env),
        subprocess.Popen([python, "-m", "app.jobs.worker"], cwd=BACKEND, env=env),
    ]
    if not args.no_web:
        ensure_frontend()
        procs.append(subprocess.Popen([npm(), "run", "dev", "--", "--host", "127.0.0.1"], cwd=FRONTEND))

    print(f"\nAPI  http://127.0.0.1:{args.port}/docs\nWeb  http://127.0.0.1:5173\nPress Ctrl+C to stop.\n")
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                if os.name == "nt":
                    p.terminate()
                else:
                    p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
