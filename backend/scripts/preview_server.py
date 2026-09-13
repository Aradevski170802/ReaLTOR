"""Single-process preview: migrate, load the offline demo projects, serve API + built UI + in-process worker.

    python scripts/preview_server.py --port 8765

Uses a separate data directory (backend/data-preview) so it never touches your working database.
Build the UI first with `npm run build` in frontend/ so the API can serve it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("USI_DATA_DIR", str(BACKEND / "data-preview"))
os.environ.setdefault("USI_RUN_WORKER_IN_API", "true")
os.environ.setdefault("USI_AUTO_MIGRATE", "false")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    os.environ.setdefault("USI_PUBLIC_BASE_URL", f"http://127.0.0.1:{args.port}")

    import uvicorn

    from app.config import get_settings
    from app.db import init_engine
    from app.main import run_migrations
    from app.seed import seed_demo

    get_settings().ensure_dirs()
    init_engine()
    run_migrations()
    print("Demo projects:", seed_demo())
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
