"""Pluggable valuation-provider interface.

Only documented, licensed APIs (or the user's own manual observations) produce market estimates.
Websites that prohibit automated access are never scraped. With no configured provider the application
reports "Not configured" / "No permitted source available" instead of inventing values.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from app.connectors.base import EvidenceDraft
from app.enums import CoverageStatus


@dataclass(frozen=True)
class ProviderDescriptor:
    key: str
    name: str
    product: str
    estimate_types: tuple[str, ...]
    docs_url: str
    secret_names: tuple[str, ...]
    setup_steps: tuple[str, ...]
    coverage_notes: str
    terms_notes: str
    verified_integration: bool = True
    is_sandbox: bool = False
    supports_images: bool = False
    default_settings: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "product": self.product, "estimate_types": list(self.estimate_types),
            "docs_url": self.docs_url, "secret_names": list(self.secret_names), "setup_steps": list(self.setup_steps),
            "coverage_notes": self.coverage_notes, "terms_notes": self.terms_notes,
            "verified_integration": self.verified_integration, "is_sandbox": self.is_sandbox,
            "supports_images": self.supports_images, "default_settings": self.default_settings,
        }


@dataclass
class ValuationSubject:
    property_id: int
    county: str
    parcel: str
    street: str | None
    city: str | None
    state: str = "PA"
    zip_code: str | None = None
    category: str | None = None
    bedrooms: int | None = None
    bathrooms: float | None = None
    square_feet: float | None = None

    @property
    def one_line(self) -> str:
        tail = " ".join(x for x in (self.state, self.zip_code) if x)
        return ", ".join(x for x in (self.street, self.city, tail) if x)


@dataclass
class ValuationResult:
    coverage_status: str
    estimate_type: str | None = None
    point: float | None = None
    low: float | None = None
    high: float | None = None
    currency: str = "USD"
    estimate_date: date | None = None
    provider_property_id: str | None = None
    source_url: str | None = None
    confidence: float | None = None
    confidence_label: str | None = None
    match_score: float | None = None
    matched_address: str | None = None
    notes: str | None = None
    retry_after: float | None = None
    evidence: EvidenceDraft | None = None
    image_urls: list[str] = field(default_factory=list)

    @classmethod
    def status_only(cls, status: CoverageStatus, notes: str) -> ValuationResult:
        return cls(coverage_status=status, notes=notes)


class ValuationProvider(ABC):
    descriptor: ProviderDescriptor

    @abstractmethod
    def estimate(self, subject: ValuationSubject, secrets: dict[str, str], settings: dict) -> ValuationResult: ...

    def missing_secrets(self, secrets: dict[str, str]) -> list[str]:
        return [name for name in self.descriptor.secret_names if not secrets.get(name)]


_STREET_SUFFIX = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "LANE": "LN", "COURT": "CT", "PLACE": "PL",
    "BOULEVARD": "BLVD", "CIRCLE": "CIR", "TERRACE": "TER", "TERR": "TER", "HIGHWAY": "HWY", "PARKWAY": "PKWY",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
}


def _addr_tokens(address: str | None) -> list[str]:
    if not address:
        return []
    street = address.split(",")[0].upper()
    tokens = re.findall(r"[A-Z0-9]+", street)
    return [_STREET_SUFFIX.get(t, t) for t in tokens]


def address_match_score(expected: str | None, returned: str | None) -> float:
    """1.0 when house number and street tokens agree; 0 when house numbers differ."""
    a, b = _addr_tokens(expected), _addr_tokens(returned)
    if not a or not b:
        return 0.0
    num_a = next((t for t in a if t.isdigit()), None)
    num_b = next((t for t in b if t.isdigit()), None)
    if num_a and num_b and num_a != num_b:
        return 0.0
    words_a = {t for t in a if not t.isdigit()}
    words_b = {t for t in b if not t.isdigit()}
    if not words_a or not words_b:
        return 0.5
    overlap = len(words_a & words_b) / len(words_a | words_b)
    return round(0.4 + 0.6 * overlap if (num_a and num_a == num_b) else 0.6 * overlap, 3)


PROPERTY_TYPE_LABELS = {
    "single_family": "Single Family", "twin": "Townhouse", "row": "Townhouse", "condo": "Condo",
    "multi_family": "Multi-Family", "land": "Land", "mobile_home": "Manufactured",
}
