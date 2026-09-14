from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_prefix="USI_",
        extra="ignore",
    )

    app_env: str = "development"
    database_url: str = ""
    data_dir: Path = BACKEND_DIR / "data"
    master_key: str | None = None
    auth_mode: str = "local"  # local | token
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    http_user_agent: str = "UpsetSaleIntel/1.0 (property research)"
    http_timeout_seconds: float = 30.0

    daily_refresh_hour: int = 6
    daily_refresh_minute: int = 0
    timezone: str = "America/New_York"

    worker_poll_seconds: float = 2.0
    run_worker_in_api: bool = False
    demo_mode: bool = False
    tesseract_cmd: str | None = None
    frontend_dist: Path = REPO_DIR / "frontend" / "dist"

    max_upload_mb: int = 60
    public_base_url: str = "http://localhost:8000"
    auto_migrate: bool = True

    # Browser automation (real Chromium via Playwright) for public no-CAPTCHA portals — off by default; needs
    # `pip install playwright` + `playwright install chromium`. Enable on a machine/instance that can run a browser.
    browser_automation: bool = False
    browser_headless: bool = True
    browser_timeout_seconds: float = 45.0

    # Deployment
    site_username: str = "demo"
    site_password: str | None = None  # when set, HTTP Basic auth protects the whole site except /api/health
    seed_demo: bool = False  # load the anonymized demo projects at start-up (scripts/serve.py)
    # When true, the operator is acknowledging the terms of every "automated_requires_terms_ack" source at start-up
    # (Delco assessment/tax portal disclaimer, Montco Tax Claim Bureau). Recorded in the audit log.
    auto_accept_terms: bool = False

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            url = self.database_url
            if url.startswith("sqlite:///./"):
                url = "sqlite:///" + (BACKEND_DIR / url[len("sqlite:///./"):]).as_posix()
            return url
        return "sqlite:///" + (self.data_dir / "app.db").as_posix()

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.evidence_dir, self.exports_dir, self.images_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
