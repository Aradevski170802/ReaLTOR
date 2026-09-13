"""Typed payloads returned by source adapters and persisted by the enrichment service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class SalePayload:
    sale_date: date | None = None
    sale_price: float | None = None
    grantor: str | None = None
    grantee: str | None = None
    book_page: str | None = None


@dataclass
class AssessmentPayload:
    land_use_code: str | None = None
    land_use_description: str | None = None
    property_class: str | None = None
    property_type: str | None = None
    category: str | None = None
    school_district: str | None = None
    site_address: str | None = None
    legal_description: str | None = None
    municipality: str | None = None
    owner_name: str | None = None
    assessed_value: float | None = None
    assessed_land: float | None = None
    assessed_building: float | None = None
    lot_size_text: str | None = None
    acres: float | None = None
    lot_sqft: float | None = None
    building_style: str | None = None
    year_built: int | None = None
    exterior_wall: str | None = None
    living_area_sqft: float | None = None
    base_area_sqft: float | None = None
    total_rooms: int | None = None
    bedrooms: int | None = None
    full_baths: int | None = None
    half_baths: int | None = None
    last_sale_date: date | None = None
    last_sale_price: float | None = None
    gross_building_area: float | None = None
    total_living_units: int | None = None
    structure_description: str | None = None
    improvement_name: str | None = None
    commercial_living_area: float | None = None
    commercial_units: int | None = None
    commercial_year_built: int | None = None
    detail_link: str | None = None
    raw_codes: dict[str, Any] | None = None
    sales: list[SalePayload] = field(default_factory=list)
    # Optional tax / status information that some portals show on the assessment page (Delco iasWorld)
    tax: TaxClaimPayload | None = None
    field_notes: dict[str, str] = field(default_factory=dict)


@dataclass
class TaxYearPayload:
    year: int
    kind: str = "claim"
    face: float | None = None
    penalty: float | None = None
    interest: float | None = None
    total: float | None = None
    paid: float | None = None
    balance: float | None = None


@dataclass
class TaxClaimPayload:
    tax_sale_status: str | None = None
    status_category: str = "unknown"
    delinquent_total: float | None = None
    district: str | None = None
    description: str | None = None
    assessed_value: float | None = None
    deed_book_page: str | None = None
    owner_name: str | None = None
    location: str | None = None
    as_of_text: str | None = None
    years: list[TaxYearPayload] = field(default_factory=list)
    notes: str | None = None

    def year(self, year: int, kind: str = "claim") -> TaxYearPayload | None:
        return next((y for y in self.years if y.year == year and y.kind == kind), None)


@dataclass
class ParcelGisPayload:
    """Basic parcel facts from an official GIS layer."""

    site_address: str | None = None
    acres: float | None = None
    legal_description: str | None = None
    lot_size_text: str | None = None
    map_id: str | None = None
    municipality_code: str | None = None
    detail_link: str | None = None
    tax_year: int | None = None


@dataclass
class RecorderDocumentPayload:
    instrument_number: str | None
    doc_type_raw: str | None
    recorded_date: date | None
    grantors: str | None = None
    grantees: str | None = None
    amount: float | None = None
    book: str | None = None
    page: str | None = None
    related_reference: str | None = None
    source_url: str | None = None


@dataclass
class LienCasePayload:
    case_number: str
    status: str | None = None
    classification: str | None = None
    case_type: str | None = None
    parties: str | None = None
    filing_date: date | None = None
    amount: float | None = None
    source_url: str | None = None
    search_term: str | None = None
