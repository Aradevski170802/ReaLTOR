"""Permitted property imagery.

Sources, in order of preference:
  1. a user-uploaded photo,
  2. Google Street View Static API (licensed; key kept server-side, image proxied, never stored),
  3. otherwise "No image available".
Listing-site photos are never scraped.
"""

from __future__ import annotations

import httpx

from app.config import get_settings

STREET_VIEW_METADATA = "https://maps.googleapis.com/maps/api/streetview/metadata"
STREET_VIEW_IMAGE = "https://maps.googleapis.com/maps/api/streetview"
STREET_VIEW_SECRET = "provider.google_street_view.api_key"
STREET_VIEW_ATTRIBUTION = "Imagery © Google"
STREET_VIEW_LICENSE = (
    "Google Maps Platform Street View Static API. Displayed via server proxy with attribution; images are not "
    "downloaded or stored by this application (Google Maps Platform Terms of Service)."
)

STREET_VIEW_DESCRIPTOR = {
    "key": "google_street_view",
    "name": "Google Street View Static API",
    "secret_names": [STREET_VIEW_SECRET],
    "docs_url": "https://developers.google.com/maps/documentation/streetview/overview",
    "setup_steps": [
        "Create a Google Cloud project, enable 'Street View Static API' and billing.",
        "Create an API key restricted to the Street View Static API (IP-restrict it to your server).",
        "Settings → Images → Google Street View → paste the key → Save.",
    ],
    "terms_notes": STREET_VIEW_LICENSE,
}


def street_view_available(address: str, api_key: str, client: httpx.Client | None = None) -> tuple[bool, str]:
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"User-Agent": get_settings().http_user_agent})
    try:
        resp = client.get(STREET_VIEW_METADATA, params={"location": address, "key": api_key})
        data = resp.json() if resp.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as exc:
        return False, f"Street View metadata request failed: {type(exc).__name__}"
    finally:
        if own:
            client.close()
    status = data.get("status", f"HTTP {resp.status_code}")
    return status == "OK", f"Street View metadata status: {status}"


def fetch_street_view(address: str, api_key: str, size: str = "640x400") -> tuple[bytes, str]:
    with httpx.Client(timeout=30, headers={"User-Agent": get_settings().http_user_agent}) as client:
        resp = client.get(STREET_VIEW_IMAGE, params={"size": size, "location": address, "key": api_key, "return_error_code": "true"})
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type", "image/jpeg")
