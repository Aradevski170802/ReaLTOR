"""FastAPI application. Run: uvicorn app.main:app --reload (from backend/). OpenAPI docs at /docs."""

from __future__ import annotations

import base64
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routers import imports, operations, projects, properties
from app.api.routers import settings as settings_router
from app.config import BACKEND_DIR, get_settings
from app.db import Base, get_engine, init_engine
from app.security.secrets import install_log_redaction, load_master_key

log = logging.getLogger("app")


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    install_log_redaction()
    settings.ensure_dirs()
    init_engine()
    load_master_key()
    if settings.auto_migrate:
        if (BACKEND_DIR / "alembic").exists():
            run_migrations()
        else:  # pragma: no cover - packaged without migrations
            Base.metadata.create_all(get_engine())
    worker = None
    if settings.run_worker_in_api:
        from app.jobs.worker import start_in_process_worker

        worker = start_in_process_worker()
    log.info("Upset Sale Intel %s ready (auth=%s, demo=%s)", __version__, settings.auth_mode, settings.demo_mode)
    yield
    if worker:
        worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Upset Sale Intel API",
        version=__version__,
        description=(
            "Property intelligence and upset-sale research for Montgomery County PA and Delaware County PA. "
            "Informational research only — not legal, title, appraisal, tax, investment or lien-clearance advice."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    if settings.site_password:
        # Shared-password gate for hosted demos: the browser shows its login prompt and resends the credentials
        # on every same-origin request. /api/health stays open for the hosting platform's health check.
        expected = f"{settings.site_username}:{settings.site_password}".encode()
        challenge = {"WWW-Authenticate": 'Basic realm="Upset Sale Intel", charset="UTF-8"'}

        @app.middleware("http")
        async def site_password_gate(request: Request, call_next):
            if request.url.path == "/api/health":
                return await call_next(request)
            header = request.headers.get("authorization", "")
            supplied = b""
            if header[:6].lower() == "basic ":
                try:
                    supplied = base64.b64decode(header[6:].strip(), validate=True)
                except ValueError:
                    supplied = b""
            if not supplied or not hmac.compare_digest(supplied, expected):
                return Response("Authentication required", status_code=401, headers=challenge)
            return await call_next(request)

    @app.exception_handler(PermissionError)
    async def permission_error(_: Request, exc: PermissionError):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    for module in (projects, imports, properties, operations, settings_router):
        app.include_router(module.router)

    dist: Path = settings.frontend_dist
    if dist.exists() and (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            candidate = dist / full_path
            if full_path and candidate.is_file() and dist in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
