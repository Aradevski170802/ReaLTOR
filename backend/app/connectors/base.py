"""County connector / source adapter abstraction.

A CountyConnector bundles parcel normalization, the sale-list parser and a set of SourceAdapters.
Each SourceAdapter declares (via SourceDescriptor) exactly how it may access its source, its limits
and its compliance status, so the enrichment engine never has to guess what is permitted.

Adding a county = implement a CountyConnector subclass + adapters and register it in registry.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from app.common.parcels import ParcelParts, normalize_parcel
from app.enums import AccessMethod, LookupStatus

VERIFIED_ON = "2026-09-13"


@dataclass(frozen=True)
class SearchDef:
    name: str
    url: str
    input: str


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 120.0
    retry_on: tuple[str, ...] = ("timeout", "connection error", "HTTP 429", "HTTP 5xx")


@dataclass(frozen=True)
class SourceDescriptor:
    key: str
    county: str
    name: str
    category: str  # parcel_gis | assessment | tax | recorder | civil
    access_method: AccessMethod
    base_urls: tuple[str, ...]
    searches: tuple[SearchDef, ...]
    input_requirements: str
    parcel_rule: str
    field_mappings: dict[str, str]
    rate_limit_per_minute: int
    min_interval_seconds: float
    cache_ttl_hours: float
    retry_policy: RetryPolicy
    error_classification: dict[str, str]
    compliance_status: str  # automated_permitted | automated_requires_terms_ack | user_assisted | manual_import
    terms_notes: str
    terms_url: str | None = None
    requires_terms_ack: bool = False
    daily_refresh: bool = False
    verified_on: str = VERIFIED_ON
    access_findings: tuple[str, ...] = ()

    @property
    def automated(self) -> bool:
        return self.access_method in (AccessMethod.OFFICIAL_API, AccessMethod.PUBLIC_WEB)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["access_method"] = str(self.access_method)
        data["automated"] = self.automated
        return data


@dataclass
class EvidenceDraft:
    url: str | None
    content: bytes
    content_type: str
    http_status: int | None = None
    request_summary: str | None = None
    entry_method: str = "automated"
    notes: str | None = None


@dataclass
class CaptureRequest:
    url: str | None
    search_hint: str
    instructions: str


@dataclass
class SourceOutcome:
    status: LookupStatus
    message: str = ""
    payload: Any = None
    evidence: list[EvidenceDraft] = field(default_factory=list)
    capture: CaptureRequest | None = None
    retry_after: float | None = None


class SourceError(Exception):
    def __init__(self, status: LookupStatus, message: str, retry_after: float | None = None,
                 evidence: EvidenceDraft | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after
        self.evidence = evidence


@dataclass
class PropertyRef:
    """Minimal, immutable view of a property handed to adapters."""

    id: int
    county: str
    parcel: ParcelParts
    owner_name: str | None
    property_address: str | None
    municipality: str | None
    identifiers: dict[str, str] = field(default_factory=dict)


class SourceAdapter(ABC):
    descriptor: SourceDescriptor
    parser_version = "1"

    def lookup(self, prop: PropertyRef, secrets: dict[str, str] | None = None) -> SourceOutcome:
        """Automated retrieval. User-assisted adapters return USER_ACTION with a capture request."""
        return SourceOutcome(
            status=LookupStatus.USER_ACTION,
            message=f"{self.descriptor.name} requires a user-assisted step ({self.descriptor.compliance_status})",
            capture=self.capture_request(prop),
        )

    def capture_request(self, prop: PropertyRef) -> CaptureRequest | None:
        return None

    def parse_capture(self, prop: PropertyRef, content: str) -> SourceOutcome:
        """Parse content a user captured from the official site (saved HTML or copied page text)."""
        return SourceOutcome(LookupStatus.PARSE_FAILURE, "This source does not support page capture; use manual import")

    @property
    def supports_capture(self) -> bool:
        return type(self).parse_capture is not SourceAdapter.parse_capture


class CountyConnector(ABC):
    county: str
    label: str
    parcel_label: str
    sale_list_parser_key: str
    workbook_spec_key: str

    @abstractmethod
    def sources(self) -> list[SourceAdapter]: ...

    def normalize_parcel(self, raw: str | None) -> ParcelParts:
        return normalize_parcel(self.county, raw)

    @abstractmethod
    def identifiers(self, parts: ParcelParts) -> dict[str, str]: ...

    def source(self, key: str) -> SourceAdapter:
        for adapter in self.sources():
            if adapter.descriptor.key == key:
                return adapter
        raise KeyError(key)

    def describe(self) -> dict[str, Any]:
        return {
            "county": self.county,
            "label": self.label,
            "parcel_label": self.parcel_label,
            "sale_list_parser": self.sale_list_parser_key,
            "sources": [a.descriptor.as_dict() | {"supports_capture": a.supports_capture} for a in self.sources()],
        }
