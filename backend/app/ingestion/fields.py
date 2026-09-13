"""Canonical sale-list fields, county-specific labels and header synonyms."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    county_labels: dict[str, str]
    essential: bool = False
    kind: str = "text"  # text | money | date | int

    def label_for(self, county: str) -> str:
        return self.county_labels.get(county, self.label)


FIELDS: list[FieldDef] = [
    FieldDef("sale_number", "Sale Number", {"montco": "Sale Number", "delco": "Record #"}),
    FieldDef("parcel", "Parcel", {"montco": "Parcel", "delco": "Folio-NBR"}, essential=True),
    FieldDef("owner_name", "Owner Name", {}, essential=True),
    FieldDef("property_address", "Location", {"delco": "Site Location"}, essential=True),
    FieldDef("municipality", "Municipality", {}),
    FieldDef("sale_date", "Sale Date", {}, kind="date"),
    FieldDef("amount_due", "Amount Due", {"montco": "Approx. Sale Price", "delco": "Total Due"}, kind="money"),
    FieldDef("assessment", "Current Assessment", {}, kind="money"),
    FieldDef("description", "Description", {}),
    FieldDef("lot_size", "Lot Size", {}),
    FieldDef("mailing_address", "Mailing Address", {}),
]

FIELD_KEYS = [f.key for f in FIELDS]
FIELD_BY_KEY = {f.key: f for f in FIELDS}

SYNONYMS: dict[str, list[str]] = {
    "parcel": ["parcel", "parcel id", "parcel number", "parcel #", "parcel no", "folio", "folio-nbr", "folio nbr",
               "folio number", "pin", "parid", "map number", "tax parcel"],
    "sale_number": ["sale number", "sale #", "sale no", "record #", "record", "record number", "record no", "item"],
    "owner_name": ["owner", "owner name", "boa owner name", "name", "owners", "reputed owner"],
    "property_address": ["location", "boa: location", "boa location", "property address", "site location", "address",
                         "situs", "property location"],
    "municipality": ["municipality", "muni", "district", "township", "borough"],
    "sale_date": ["sale date", "date of sale"],
    "amount_due": ["approx. sale price", "approx sale price", "total due", "total due (per pdf)", "amount due",
                   "upset price", "minimum bid", "upset amount"],
    "assessment": ["assessment", "current assessment", "assessed value", "assessment value"],
    "description": ["description", "legal description", "property description"],
    "lot_size": ["lot size", "size", "dimensions"],
    "mailing_address": ["mailing address", "owner address", "mail address"],
}

_NORM = re.compile(r"[^a-z0-9#:]+")


def _norm(text: str) -> str:
    return _NORM.sub(" ", text.lower()).strip()


_LOOKUP = {_norm(s): key for key, syns in SYNONYMS.items() for s in syns}


def map_header(header: str | None) -> str | None:
    if not header:
        return None
    return _LOOKUP.get(_norm(header))


def map_headers(headers: list[str | None]) -> dict[int, str]:
    """Map column index -> canonical field; first occurrence wins, unmapped columns become extra:<header>."""
    mapping: dict[int, str] = {}
    used: set[str] = set()
    for idx, header in enumerate(headers):
        key = map_header(header)
        if key and key not in used:
            mapping[idx] = key
            used.add(key)
        elif header and str(header).strip():
            mapping[idx] = f"extra:{str(header).strip()}"
    return mapping


def header_score(cells: list[str | None]) -> int:
    return sum(1 for c in cells if map_header(c))
