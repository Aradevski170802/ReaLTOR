"""Single-process server for the Docker image / Render.

Runs migrations, optionally loads the anonymized demo projects (USI_SEED_DEMO=true), then serves the API, the built
web UI and an in-process background worker (with the daily refresh scheduler) on 0.0.0.0:$PORT.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("USI_RUN_WORKER_IN_API", "true")
os.environ.setdefault("USI_AUTO_MIGRATE", "false")
if os.environ.get("RENDER_EXTERNAL_URL"):
    os.environ.setdefault("USI_PUBLIC_BASE_URL", os.environ["RENDER_EXTERNAL_URL"])


def main() -> None:
    import uvicorn

    from app.config import get_settings
    from app.db import init_engine
    from app.main import run_migrations

    settings = get_settings()
    settings.ensure_dirs()
    init_engine()
    run_migrations()
    if settings.seed_demo:
        from app.seed import seed_demo

        print("Demo projects:", seed_demo(), flush=True)
    if settings.auth_mode == "local" and not settings.site_password:
        print("WARNING: USI_SITE_PASSWORD is not set — anyone with the link can use this site.", flush=True)
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), proxy_headers=True,
                forwarded_allow_ips="*", log_level="info")


if __name__ == "__main__":
    main()
