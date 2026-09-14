"""Shared ATTOM Property API client.

ATTOM's `attomavm/detail` is the richest single call: it returns owner, AVM, assessment, building, lot, sale and
summary blocks in one response. Both the valuation provider (for the AVM) and the property-enrichment source (for the
characteristics) go through here so a property that is valued and enriched in the same run makes just one ATTOM call.

Lookups prefer the parcel (APN + county FIPS), which is exact, and fall back to the street address.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import httpx

from app.config import get_settings

BASE = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"
AVM_DETAIL = f"{BASE}/attomavm/detail"

# County → state+county FIPS code (used as ATTOM's `fips` parameter for parcel lookups).
FIPS = {"montco": "42091", "delco": "42045"}

SECRET_NAME = "provider.attom.api_key"
_CACHE_TTL = 300.0  # seconds


@dataclass
class AttomResponse:
    status_code: int
    url: str
    content: bytes
    content_type: str
    data: dict = field(default_factory=dict)

    @property
    def properties(self) -> list[dict]:
        return self.data.get("property") or []

    @property
    def status_msg(self) -> str:
        return (self.data.get("status") or {}).get("msg", "")


_cache: dict[str, tuple[float, AttomResponse]] = {}
_lock = threading.Lock()


def county_fips(county: str | None) -> str | None:
    return FIPS.get(county or "")


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _client() -> httpx.Client:
    return httpx.Client(timeout=40.0, headers={"User-Agent": get_settings().http_user_agent})


def _fetch(key: str, params: dict[str, str]) -> AttomResponse:
    cache_key = repr(sorted(params.items()))
    now = time.monotonic()
    with _lock:
        hit = _cache.get(cache_key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    with _client() as client:
        resp = client.get(AVM_DETAIL, params=params, headers={"apikey": key, "Accept": "application/json"})
    try:
        data = resp.json()
    except ValueError:
        data = {}
    out = AttomResponse(resp.status_code, str(resp.request.url), resp.content,
                        resp.headers.get("content-type", "application/json"), data)
    if resp.status_code == 200 and out.properties:
        with _lock:
            _cache[cache_key] = (now, out)
    return out


def lookup_avm_detail(*, key: str, county: str | None = None, parcel: str | None = None,
                      address1: str | None = None, address2: str | None = None) -> AttomResponse | None:
    """Fetch attomavm/detail, trying parcel (APN + FIPS) first and falling back to the address."""
    attempts: list[dict[str, str]] = []
    fips = county_fips(county)
    if parcel and fips:
        attempts.append({"apn": parcel, "fips": fips})
    if address1:
        attempts.append({"address1": address1, "address2": address2 or ""})
    last: AttomResponse | None = None
    for params in attempts:
        last = _fetch(key, params)
        if last.status_code == 200 and last.properties:
            return last
    return last


def resolve_key(session) -> str | None:
    """The ATTOM API key from the encrypted secret store (or its USI_SECRET_PROVIDER_ATTOM_API_KEY env fallback)."""
    from app.security.secrets import get_secret

    return get_secret(session, SECRET_NAME)
