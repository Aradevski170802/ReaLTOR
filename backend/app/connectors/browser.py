"""Real-browser automation for public county portals that are JavaScript apps but have NO bot-detection challenge.

This drives sites the way a person's browser does (Playwright + Chromium). It is used only for portals whose public
access is a plain "continue as public user" / login flow — never to bypass a CAPTCHA, Cloudflare Turnstile, or any
"verify you are human" control. Adapters that need it declare `browser_capable = True` and implement `browse()`.

Disabled unless USI_BROWSER_AUTOMATION=true AND Playwright + its Chromium are installed, so the default deployment
(and CI) never depends on a browser. When unavailable, browser-capable sources fall back to a user-assisted task.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

from app.config import get_settings

if TYPE_CHECKING:  # pragma: no cover
    from playwright.sync_api import Page

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36"


def playwright_installed() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def browser_available() -> bool:
    return get_settings().browser_automation and playwright_installed()


class BrowserRunner:
    """Context manager that owns one Chromium instance; hand out pages to adapters, then tear everything down."""

    def __init__(self, headless: bool | None = None):
        settings = get_settings()
        self.headless = settings.browser_headless if headless is None else headless
        self.nav_timeout_ms = int(settings.browser_timeout_seconds * 1000)
        self._pw = None
        self._browser = None

    def __enter__(self) -> BrowserRunner:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless, args=["--no-sandbox", "--disable-dev-shm-usage"])
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    @contextmanager
    def page(self):
        context = self._browser.new_context(user_agent=UA, ignore_https_errors=True, viewport={"width": 1366, "height": 900})
        context.set_default_timeout(self.nav_timeout_ms)
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()


def page_evidence(page: Page, note: str):
    """Capture the current page HTML as an immutable evidence snapshot draft."""
    from app.connectors.base import EvidenceDraft

    return EvidenceDraft(url=page.url, content=page.content().encode("utf-8", "replace"), content_type="text/html",
                         request_summary=note, entry_method="automated",
                         notes="Retrieved with an automated browser from a public no-login/no-CAPTCHA portal")
