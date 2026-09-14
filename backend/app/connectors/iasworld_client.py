"""Automated session for Tyler iasWorld public-access portals (county property/tax records).

These portals gate access behind a one-click "Agree" disclaimer (a liability waiver, not a login or CAPTCHA).
When an administrator has acknowledged the source's terms, this client accepts that disclaimer once per host and
then fetches a parcel's data tabs ("datalets") directly. It honours robots.txt and the source's rate limit, and
never runs against a host whose robots.txt disallows it (e.g. Montgomery's propertyrecords portal — that county's
assessment data comes from the official GIS API instead).

The returned HTML is parsed by app.connectors.iasworld.parse_datalet, the same parser used for user-pasted pages.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

import httpx

from app.config import get_settings
from app.connectors.base import EvidenceDraft, SourceDescriptor, SourceError
from app.connectors.http import CAPTCHA_MARKERS, limiter_for, robots_cache
from app.enums import LookupStatus

_VS = re.compile(r'id="(__VIEWSTATE|__VIEWSTATEGENERATOR|__EVENTVALIDATION)"[^>]*value="([^"]*)"')
_DISCLAIMER_STILL = re.compile(r"(?is)id=\"?btAgree|name=\"btAgree|Disagree\s*</")


@dataclass
class IasWorldPortal:
    base: str  # e.g. http://delcorealestate.co.delaware.pa.us/pt
    jurisdiction: str  # iasWorld "jur" code (Delaware = 023)
    tax_year: int = 2026
    profile_modes: tuple[str, ...] = ("profileall",)
    residential_modes: tuple[str, ...] = ("residential",)
    commercial_modes: tuple[str, ...] = ("comsummary",)
    tax_modes: tuple[str, ...] = ("delinquent", "tax_delinquent")

    @property
    def disclaimer_url(self) -> str:
        return f"{self.base}/Search/Disclaimer.aspx?FromUrl=../search/commonsearch.aspx?mode=parid"

    def datalet_url(self, pin: str, mode: str) -> str:
        return f"{self.base}/datalets/datalet.aspx?mode={mode}&UseSearch=no&jur={self.jurisdiction}&taxyr={self.tax_year}&pin={pin}"


@dataclass
class _HostState:
    accepted: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


_hosts: dict[str, _HostState] = {}
_registry_lock = threading.Lock()


def _host_state(base: str) -> _HostState:
    with _registry_lock:
        return _hosts.setdefault(base, _HostState())


def reset_sessions() -> None:
    with _registry_lock:
        _hosts.clear()


class IasWorldSession:
    """One cookie-bearing httpx session per portal host; accepts the disclaimer lazily and caches it."""

    def __init__(self, descriptor: SourceDescriptor, portal: IasWorldPortal, client: httpx.Client | None = None, sleep=None):
        settings = get_settings()
        self.descriptor = descriptor
        self.portal = portal
        self._ua = settings.http_user_agent
        self._sleep = sleep or time.sleep
        self._client = client or httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=True,
                                              headers={"User-Agent": self._ua})
        self._owns = client is None

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _throttle_and_check(self, url: str) -> None:
        allowed, note = robots_cache().check(self._client, url)
        if not allowed:
            raise SourceError(LookupStatus.BLOCKED, note)
        limiter_for(self.descriptor).acquire(sleep=self._sleep)

    def _get(self, url: str) -> httpx.Response:
        self._throttle_and_check(url)
        try:
            return self._client.get(url)
        except httpx.TimeoutException as exc:
            raise SourceError(LookupStatus.UNEXPECTED, f"Timeout contacting {self.descriptor.name}") from exc
        except httpx.HTTPError as exc:
            raise SourceError(LookupStatus.UNEXPECTED, f"Connection error: {type(exc).__name__}") from exc

    def ensure_accepted(self, force: bool = False) -> None:
        state = _host_state(self.portal.base)
        with state.lock:
            if state.accepted and not force:
                return
            page = self._get(self.portal.disclaimer_url)
            if any(marker in page.text.lower() for marker in CAPTCHA_MARKERS):
                raise SourceError(LookupStatus.USER_ACTION,
                                  "The portal now presents a human-verification challenge; automated acceptance stopped. Use manual capture.")
            fields = {name: value for name, value in _VS.findall(page.text)}
            form = fields | {"hdURL": "../search/commonsearch.aspx?mode=parid", "action": "Agree", "btAgree": "Agree"}
            self._throttle_and_check(self.portal.disclaimer_url)
            resp = self._client.post(self.portal.disclaimer_url, data=form)
            if "DISCLAIMER" not in self._client.cookies and "commonsearch" not in str(resp.url):
                raise SourceError(LookupStatus.USER_ACTION, "Could not accept the portal disclaimer automatically; use manual capture.")
            state.accepted = True

    def fetch_property_html(self, pin: str, commercial: bool = False) -> tuple[str, EvidenceDraft]:
        """Accept if needed, then fetch profile + building + tax tabs and return combined HTML + evidence."""
        self.ensure_accepted()
        modes = list(self.portal.profile_modes)
        modes += list(self.portal.commercial_modes if commercial else self.portal.residential_modes)
        modes += list(self.portal.tax_modes)
        parts: list[str] = []
        fetched: list[str] = []
        for mode in modes:
            resp = self._get(self.portal.datalet_url(pin, mode))
            if resp.status_code == 500:
                continue  # a tab that does not apply to this parcel (e.g. residential tab on vacant land)
            if resp.status_code != 200:
                continue
            if _DISCLAIMER_STILL.search(resp.text):
                self.ensure_accepted(force=True)
                resp = self._get(self.portal.datalet_url(pin, mode))
            parts.append(f"<!-- datalet mode={mode} -->\n{resp.text}")
            fetched.append(mode)
        if not parts:
            raise SourceError(LookupStatus.NO_MATCH, f"No data tabs returned for parcel {pin}")
        combined = "\n".join(parts)
        evidence = EvidenceDraft(
            url=self.portal.datalet_url(pin, ",".join(fetched)),
            content=combined.encode("utf-8", "replace"),
            content_type="text/html",
            request_summary=f"Auto-accepted disclaimer; fetched datalet tabs {fetched} for pin {pin}",
            notes="Disclaimer terms acknowledged by an administrator; accepted automatically",
        )
        return combined, evidence
