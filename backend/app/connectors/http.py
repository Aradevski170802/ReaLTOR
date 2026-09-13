"""Compliance-aware HTTP client for automated source access.

Every automated request:
  * checks robots.txt for the origin (cached 24h) and refuses disallowed paths,
  * waits for the per-source rate limiter (minimum interval + per-minute budget),
  * retries timeouts / 429 / 5xx with exponential backoff + jitter, honouring Retry-After,
  * classifies failures into LookupStatus values, including CAPTCHA / human-verification pages,
  * never follows a response into an interactive gate (login, disclaimer, challenge).
"""

from __future__ import annotations

import logging
import random
import threading
import time
import urllib.robotparser
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx

from app.config import get_settings
from app.connectors.base import EvidenceDraft, SourceDescriptor, SourceError
from app.enums import LookupStatus
from app.security.secrets import redact_text

log = logging.getLogger(__name__)

CAPTCHA_MARKERS = (
    "g-recaptcha",
    "hcaptcha",
    "cf-challenge",
    "challenge-platform",
    "cf-turnstile",
    "verify you are human",
    "are you a robot",
    "<title>validating</title>",
)
LOGIN_MARKERS = ('type="password"', "type='password'")


@dataclass
class HttpResult:
    url: str
    status: int
    content: bytes
    content_type: str
    elapsed: float
    request_summary: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def evidence(self, notes: str | None = None) -> EvidenceDraft:
        return EvidenceDraft(
            url=self.url,
            content=self.content,
            content_type=self.content_type,
            http_status=self.status,
            request_summary=self.request_summary,
            notes=notes,
        )


class RobotsCache:
    def __init__(self, user_agent: str, ttl_seconds: float = 86400):
        self._ua = user_agent
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None, str]] = {}
        self._lock = threading.Lock()

    def check(self, client: httpx.Client, url: str) -> tuple[bool, str]:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(origin)
        if cached is None or now - cached[0] > self._ttl:
            parser, note = self._fetch(client, origin)
            with self._lock:
                self._cache[origin] = (now, parser, note)
        else:
            _, parser, note = cached
        if parser is None:
            return True, note
        allowed = parser.can_fetch(self._ua, url)
        return allowed, note if allowed else f"robots.txt at {origin} disallows automated access to {parts.path}"

    def _fetch(self, client: httpx.Client, origin: str) -> tuple[urllib.robotparser.RobotFileParser | None, str]:
        robots_url = f"{origin}/robots.txt"
        try:
            resp = client.get(robots_url, headers={"User-Agent": self._ua}, timeout=15, follow_redirects=True)
        except httpx.HTTPError as exc:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
            return parser, f"robots.txt unreachable at {origin} ({type(exc).__name__}); treating as disallowed"
        body = resp.text if resp.status_code == 200 else ""
        if resp.status_code in (401, 403):
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
            return parser, f"robots.txt access denied at {origin}; treating as disallowed"
        if resp.status_code >= 400 or "<html" in body[:400].lower():
            return None, f"no robots.txt at {origin} (HTTP {resp.status_code})"
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(body.splitlines())
        return parser, f"robots.txt checked at {origin}"


class RateLimiter:
    def __init__(self, per_minute: int, min_interval: float):
        self.per_minute = max(1, per_minute)
        self.min_interval = max(0.0, min_interval)
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, sleep=time.sleep, clock=time.monotonic) -> float:
        waited = 0.0
        while True:
            with self._lock:
                now = clock()
                while self._stamps and now - self._stamps[0] >= 60:
                    self._stamps.popleft()
                wait = 0.0
                if self._stamps:
                    wait = max(wait, self.min_interval - (now - self._stamps[-1]))
                if len(self._stamps) >= self.per_minute:
                    wait = max(wait, 60 - (now - self._stamps[0]))
                if wait <= 0:
                    self._stamps.append(now)
                    return waited
            sleep(wait)
            waited += wait


_limiters: dict[str, RateLimiter] = {}
_robots: RobotsCache | None = None
_registry_lock = threading.Lock()


def limiter_for(descriptor: SourceDescriptor) -> RateLimiter:
    with _registry_lock:
        if descriptor.key not in _limiters:
            _limiters[descriptor.key] = RateLimiter(descriptor.rate_limit_per_minute, descriptor.min_interval_seconds)
        return _limiters[descriptor.key]


def robots_cache() -> RobotsCache:
    global _robots
    with _registry_lock:
        if _robots is None:
            _robots = RobotsCache(get_settings().http_user_agent)
        return _robots


def reset_http_state() -> None:
    global _robots
    with _registry_lock:
        _limiters.clear()
        _robots = None


class ComplianceHttpClient:
    def __init__(self, descriptor: SourceDescriptor, client: httpx.Client | None = None, sleep=None):
        settings = get_settings()
        self.descriptor = descriptor
        self._client = client or httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=False)
        self._owns_client = client is None
        self._ua = settings.http_user_agent
        self._sleep = sleep or time.sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> HttpResult:
        return self._request("GET", url, params=params, headers=headers)

    def post(self, url: str, data: dict | None = None, headers: dict | None = None) -> HttpResult:
        return self._request("POST", url, data=data, headers=headers)

    def _request(self, method: str, url: str, params=None, data=None, headers=None) -> HttpResult:
        allowed, note = robots_cache().check(self._client, url)
        if not allowed:
            raise SourceError(LookupStatus.BLOCKED, note)

        policy = self.descriptor.retry_policy
        summary = redact_text(f"{method} {url}" + (f"?{urlencode(params)}" if params else "") +
                              (f" form={sorted(data)}" if data else ""))
        last_error: SourceError | None = None
        for attempt in range(1, policy.max_attempts + 1):
            limiter_for(self.descriptor).acquire(sleep=self._sleep)
            started = time.monotonic()
            try:
                resp = self._client.request(
                    method, url, params=params, data=data,
                    headers={"User-Agent": self._ua, **(headers or {})},
                )
            except httpx.TimeoutException:
                last_error = SourceError(LookupStatus.UNEXPECTED, f"Timeout contacting {self.descriptor.name}")
            except httpx.HTTPError as exc:
                last_error = SourceError(LookupStatus.UNEXPECTED, f"Connection error: {type(exc).__name__}")
            else:
                result = HttpResult(
                    url=str(resp.url), status=resp.status_code, content=resp.content,
                    content_type=resp.headers.get("content-type", ""), elapsed=time.monotonic() - started,
                    request_summary=summary,
                )
                error = self._classify(resp, result)
                if error is None:
                    return result
                if error.status not in (LookupStatus.RATE_LIMITED, LookupStatus.UNEXPECTED) or resp.status_code < 429:
                    raise error
                last_error = error
            if attempt < policy.max_attempts:
                delay = min(policy.max_delay_seconds, policy.base_delay_seconds * (2 ** (attempt - 1)))
                if last_error and last_error.retry_after:
                    delay = max(delay, min(policy.max_delay_seconds, last_error.retry_after))
                delay += random.uniform(0, delay * 0.25)
                log.info("Retrying %s (%s) in %.1fs: %s", self.descriptor.key, attempt, delay, last_error.message)
                self._sleep(delay)
        assert last_error is not None
        raise last_error

    def _classify(self, resp: httpx.Response, result: HttpResult) -> SourceError | None:
        status = resp.status_code
        if status in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            lowered = location.lower()
            if any(k in lowered for k in ("disclaimer", "login", "auth", "signin", "captcha", "validate")):
                return SourceError(LookupStatus.USER_ACTION,
                                   f"Source redirected to an interactive gate ({location}); a user-assisted step is required",
                                   evidence=result.evidence())
            return SourceError(LookupStatus.UNEXPECTED, f"Unexpected redirect to {location}", evidence=result.evidence())
        if status == 429:
            retry_after = _retry_after(resp)
            return SourceError(LookupStatus.RATE_LIMITED, "Source rate limit reached (HTTP 429)", retry_after=retry_after)
        if status in (401, 407):
            return SourceError(LookupStatus.AUTH_NEEDED, f"Authentication required (HTTP {status})")
        if status == 403:
            return SourceError(LookupStatus.BLOCKED, "Access forbidden (HTTP 403)", evidence=result.evidence())
        if status >= 500:
            return SourceError(LookupStatus.UNEXPECTED, f"Source server error (HTTP {status})", retry_after=_retry_after(resp))
        if status >= 400:
            return SourceError(LookupStatus.UNEXPECTED, f"Unexpected HTTP {status}")
        if "html" in result.content_type.lower() or result.content[:200].lstrip().startswith(b"<"):
            lowered = result.text[:200_000].lower()
            if any(marker in lowered for marker in CAPTCHA_MARKERS):
                return SourceError(LookupStatus.USER_ACTION,
                                   "Human verification (CAPTCHA/challenge) detected; not bypassed. Use the user-assisted capture.",
                                   evidence=result.evidence())
            if any(marker in lowered for marker in LOGIN_MARKERS) and self.descriptor.category not in ("tax",):
                return SourceError(LookupStatus.AUTH_NEEDED, "Login form detected; credentials are never automated",
                                   evidence=result.evidence())
        return None


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
