"""Normalize county-specific property classifications into common categories.

Montgomery: "Land Use Description" (e.g. "R - SINGLE FAMILY", "C - COMMERCIAL CONDO") plus building style.
Delaware:   "Property Type" (e.g. "01 - Taxable Residential", "06 - Ground") plus residential style
            (e.g. "10 - TWIN", "06 - CONVENTIONAL").
Falls back to the sale-list description (e.g. Delco "GRD", "2 STY HSE") with a lower confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.enums import PropertyCategory as C


@dataclass(frozen=True)
class Classification:
    category: str
    basis: str
    confidence: float


def _has(text: str, *words: str) -> bool:
    return any(re.search(rf"\b{re.escape(w)}", text) for w in words)


def _residential_style(style: str) -> str | None:
    if _has(style, "CONDO"):
        return C.CONDO
    if _has(style, "TWIN", "SEMI", "SEMI-DETACHED", "DOUBLE"):
        return C.TWIN
    if _has(style, "ROW", "TOWNHOUSE", "TOWN HOUSE", "END ROW", "INTERIOR ROW"):
        return C.ROW
    if _has(style, "MOBILE", "MANUFACTURED"):
        return C.MOBILE_HOME
    if _has(style, "DUPLEX", "TRIPLEX", "APART", "MULTI", "CONVERTED"):
        return C.MULTI_FAMILY
    return None


def classify_montco(land_use_description: str | None, style: str | None = None, property_class: str | None = None) -> Classification:
    text = (land_use_description or "").upper()
    style_u = (style or "").upper()
    cls = (property_class or "").upper()
    if not text and not cls:
        return Classification(C.UNKNOWN, "No land use description", 0.0)
    basis = f"Land Use Description '{land_use_description}'" + (f", style '{style}'" if style else "")
    prefix = text.split("-", 1)[0].strip() if "-" in text else cls[:1]
    if prefix == "R" or cls.startswith("R"):
        if _has(text, "VACANT", "LAND", "LOT ONLY", "PREFERENTIAL"):
            return Classification(C.LAND, basis, 0.9)
        if _has(text, "CONDO"):
            return Classification(C.CONDO, basis, 0.95)
        if _has(text, "MOBILE", "MANUFACTURED"):
            return Classification(C.MOBILE_HOME, basis, 0.95)
        if _has(text, "DUPLEX", "TRIPLEX", "APART", "MULTI", "CONVERTED", "MORE THAN 1"):
            return Classification(C.MULTI_FAMILY, basis, 0.9)
        if _has(text, "TWIN", "SEMI"):
            return Classification(C.TWIN, basis, 0.95)
        if _has(text, "ROW", "TOWNHOUSE"):
            return Classification(C.ROW, basis, 0.95)
        by_style = _residential_style(style_u)
        if _has(text, "SINGLE FAMILY"):
            return Classification(by_style if by_style in (C.TWIN, C.ROW, C.CONDO) else C.SINGLE_FAMILY, basis, 0.95)
        if by_style:
            return Classification(by_style, basis, 0.8)
        return Classification(C.OTHER_RESIDENTIAL, basis, 0.7)
    if prefix == "C" or cls.startswith(("C", "A", "I")):
        if _has(text, "VACANT LAND", "LAND ONLY", "VACANT"):
            return Classification(C.LAND, basis, 0.85)
        return Classification(C.COMMERCIAL, basis, 0.95)
    if prefix == "F" or cls.startswith("F"):
        return Classification(C.LAND, basis + " (farm)", 0.8)
    if prefix == "E" or cls.startswith("E"):
        return Classification(C.EXEMPT, basis, 0.9)
    return Classification(C.OTHER, basis, 0.5)


def classify_delco(property_type: str | None, style: str | None = None, sale_list_description: str | None = None) -> Classification:
    ptype = (property_type or "").upper()
    style_u = (style or "").upper()
    if ptype:
        basis = f"Property Type '{property_type}'" + (f", style '{style}'" if style else "")
        if _has(ptype, "GROUND", "VACANT", "LAND") or re.match(r"^0?6\b", ptype):
            return Classification(C.LAND, basis, 0.95)
        if _has(ptype, "COMMERCIAL", "INDUSTRIAL", "APARTMENT", "UTILITY"):
            return Classification(C.COMMERCIAL, basis, 0.95)
        if _has(ptype, "EXEMPT"):
            return Classification(C.EXEMPT, basis, 0.9)
        if _has(ptype, "RESIDENTIAL") or re.match(r"^0?1\b", ptype):
            by_style = _residential_style(style_u)
            if by_style:
                return Classification(by_style, basis, 0.95)
            return Classification(C.SINGLE_FAMILY, basis, 0.85 if style else 0.7)
        if _has(ptype, "CONDO"):
            return Classification(C.CONDO, basis, 0.9)
        return Classification(C.OTHER, basis, 0.5)
    desc = (sale_list_description or "").upper()
    if desc:
        basis = f"Sale-list description '{sale_list_description}' (assessment not yet retrieved)"
        if re.match(r"^(GRD|GROUND|VAC|VACANT)\b", desc) or _has(desc, "LOT ONLY"):
            return Classification(C.LAND, basis, 0.6)
        if _has(desc, "CONDO"):
            return Classification(C.CONDO, basis, 0.55)
        if _has(desc, "STORE", "OFFICE", "WAREHOUSE", "BLDG", "COMM", "FACTORY", "CELLULAR", "GARAGE BLDG"):
            return Classification(C.COMMERCIAL, basis, 0.5)
        if _has(desc, "DUP", "APT", "APTS"):
            return Classification(C.MULTI_FAMILY, basis, 0.5)
        if _has(desc, "TWIN"):
            return Classification(C.TWIN, basis, 0.55)
        if _has(desc, "ROW"):
            return Classification(C.ROW, basis, 0.55)
        if _has(desc, "HSE", "HOUSE", "STY", "DWG", "BUNG", "RANCH", "CAPE"):
            return Classification(C.SINGLE_FAMILY, basis, 0.5)
    return Classification(C.UNKNOWN, "No property type available", 0.0)
