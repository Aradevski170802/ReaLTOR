"""Montgomery County official ArcGIS REST: Board of Assessment land/building table (GIS_BOA_LAND)."""

from __future__ import annotations

import json

from app.common.parsing import clean_ws, parse_date, parse_float, parse_int
from app.connectors.base import (
    PropertyRef,
    RetryPolicy,
    SearchDef,
    SourceAdapter,
    SourceDescriptor,
    SourceError,
    SourceOutcome,
)
from app.connectors.http import ComplianceHttpClient
from app.connectors.montco.codes import decode
from app.connectors.payloads import AssessmentPayload, SalePayload
from app.enums import AccessMethod, LookupStatus
from app.rules.classification import classify_montco

LAYER = "https://gis.montcopa.org/arcgis/rest/services/Parcels/GIS_BOA_LAND/FeatureServer/0"
QUERY = f"{LAYER}/query"
PORTAL_PROFILE = "https://propertyrecords.montcopa.org/pt/search/commonsearch.aspx?mode=parid"

FIELDS = [
    "PARCEL_NUMBER", "CLASS", "LAND_USE", "TOTAL_ASSESSMENT", "SCH_DIST", "MUNI_CODE", "LAND_ACRES", "LAND_SF", "FRONTFT",
    "STYLE", "EXTWALL", "SFLA", "RM_TOT", "BEDROOMS", "BATHS", "HALF_BATHS", "YEAR_BUILT", "SALE_DATE", "CONSIDERATION",
    "COMM_AREA", "COMM_NLA", "COMM_UNITS", "LIV_UNITS", "STRUCTURE", "USE_TYPE", "COMM_YR_BLT", "LOCATION1", "LOCATION2",
    "LOC_ZIP1_ZIP2", "OWN1", "OWN2", "DEED_BOOK", "DEED_PAGE", "ASMT_CHGDATE",
]

DESCRIPTOR = SourceDescriptor(
    key="montco.assessment_gis",
    county="montco",
    name="Montgomery County GIS — Board of Assessment land & building table (GIS_BOA_LAND)",
    category="assessment",
    access_method=AccessMethod.OFFICIAL_API,
    base_urls=(LAYER,),
    searches=(SearchDef("Query by parcel number", QUERY, "PARCEL_NUMBER LIKE '<12-digit parcel>%'"),),
    input_requirements="Montgomery parcel NN-NN-NNNNN-NN-N",
    parcel_rule="Strip dashes to 12 digits (check digit included); PARCEL_NUMBER is space-padded so a prefix LIKE with exact trimmed match is used",
    field_mappings={
        "LAND_USE": "Land Use Description (decoded)", "CLASS": "property class", "TOTAL_ASSESSMENT": "Assessed Value",
        "SCH_DIST": "School District (decoded)", "LAND_ACRES": "acres", "LAND_SF": "Lot Size (SF)", "STYLE": "Building Style",
        "YEAR_BUILT/COMM_YR_BLT": "Year Built", "EXTWALL": "Exterior Wall Material", "SFLA": "Sq Ft Living Area",
        "RM_TOT": "Total Rooms", "BEDROOMS": "Bedrooms", "BATHS": "Full Baths", "HALF_BATHS": "Half Baths",
        "SALE_DATE": "Sale Date", "CONSIDERATION": "Sale Price", "COMM_AREA": "Gross Building Area (C-)",
        "COMM_UNITS/LIV_UNITS": "Total Living Units (C-)", "STRUCTURE": "Structure (C-)",
    },
    rate_limit_per_minute=30,
    min_interval_seconds=1.0,
    cache_ttl_hours=24 * 7,
    retry_policy=RetryPolicy(max_attempts=4, base_delay_seconds=2.0),
    error_classification={
        "ArcGIS JSON error body": "unexpected_failure (retried)", "no feature for parcel": "no_match",
        "more than one exact feature": "ambiguous_match", "HTTP 429": "rate_limited", "HTTP 5xx/timeouts": "unexpected_failure",
    },
    compliance_status="automated_permitted",
    terms_notes=(
        "Public ArcGIS REST service published by Montgomery County Mapping & Data Services as part of the county's "
        "open-data program. No robots.txt restrictions on gis.montcopa.org. Values are the county's weekly assessment "
        "extract; informational only, not a substitute for a title search, appraisal or certified record. Codes are "
        "decoded with a crosswalk built from reviewed portal descriptions; unmapped codes are shown as codes."
    ),
    terms_url="https://data-montcopa.opendata.arcgis.com/",
    access_findings=(
        "2026-09-13: GIS_BOA_LAND queryable; parcel 02-00-01016-00-8 returned SFLA 1724 / 4 bed / 2 bath / 1880, matching the reference workbook.",
        "The interactive portal propertyrecords.montcopa.org has robots.txt 'Disallow: /' and a disclaimer gate; it is not automated.",
    ),
)


class MontcoAssessmentGisAdapter(SourceAdapter):
    descriptor = DESCRIPTOR
    parser_version = "gis_boa_land/1"

    def lookup(self, prop: PropertyRef, secrets: dict[str, str] | None = None) -> SourceOutcome:
        key = prop.parcel.digits
        if not prop.parcel.valid:
            return SourceOutcome(LookupStatus.NO_MATCH, f"Parcel '{prop.parcel.original}' is not a valid Montgomery parcel number")
        params = {"where": f"PARCEL_NUMBER LIKE '{key}%'", "outFields": ",".join(FIELDS), "returnGeometry": "false", "f": "json"}
        try:
            with ComplianceHttpClient(self.descriptor) as http:
                result = http.get(QUERY, params=params)
        except SourceError as exc:
            return SourceOutcome(exc.status, exc.message, retry_after=exc.retry_after,
                                 evidence=[exc.evidence] if exc.evidence else [])
        evidence = result.evidence()
        try:
            data = json.loads(result.text)
        except ValueError:
            return SourceOutcome(LookupStatus.PARSE_FAILURE, "GIS response was not JSON", evidence=[evidence])
        return self.parse_response(data, key, evidence)

    def parse_response(self, data: dict, key: str, evidence=None) -> SourceOutcome:
        evidence_list = [evidence] if evidence else []
        if "error" in data:
            msg = data["error"].get("message", "ArcGIS error")
            return SourceOutcome(LookupStatus.UNEXPECTED, f"ArcGIS error: {msg}", evidence=evidence_list)
        exact = [f["attributes"] for f in data.get("features", []) if (f["attributes"].get("PARCEL_NUMBER") or "").strip() == key]
        if not exact:
            return SourceOutcome(LookupStatus.NO_MATCH, f"No GIS_BOA_LAND record for parcel {key}", evidence=evidence_list)
        if len(exact) > 1:
            return SourceOutcome(LookupStatus.AMBIGUOUS, f"{len(exact)} GIS_BOA_LAND records for parcel {key}", evidence=evidence_list)
        return SourceOutcome(LookupStatus.SUCCESS, "Assessment record retrieved", payload=self.to_payload(exact[0]),
                             evidence=evidence_list)

    @staticmethod
    def to_payload(a: dict) -> AssessmentPayload:
        def s(name: str) -> str | None:
            return clean_ws(a.get(name))

        notes: dict[str, str] = {}
        land_use, lu_mapped = decode("land_use", s("LAND_USE"))
        cls = s("CLASS")
        class_desc, _ = decode("class", cls)
        if not lu_mapped and cls:
            prefix = {"R": "R", "C": "C", "A": "C", "I": "C"}.get(cls[:1])
            if prefix:
                land_use = f"{prefix} - {land_use}"
            notes["land_use_description"] = f"Land use code {s('LAND_USE')} not in crosswalk; class {cls} used for R-/C- prefix"
        school, _ = decode("school_district", s("SCH_DIST"))
        style, _ = decode("style", s("STYLE"))
        wall, _ = decode("exterior_wall", s("EXTWALL"))
        structure, _ = decode("structure", s("STRUCTURE"))
        classification = classify_montco(land_use, style, cls)

        comm_area = parse_float(a.get("COMM_AREA")) or None
        comm_units = parse_int(a.get("COMM_UNITS")) or None
        liv_units = parse_int(s("LIV_UNITS"))
        year_built = parse_int(s("YEAR_BUILT"))
        comm_year = parse_int(s("COMM_YR_BLT"))
        land_sf = parse_float(a.get("LAND_SF"))
        sfla = parse_float(a.get("SFLA")) or None
        location = " ".join(x for x in (s("LOCATION1"), s("LOCATION2")) if x)
        zip_code = s("LOC_ZIP1_ZIP2")
        sale_price = parse_float(a.get("CONSIDERATION"))
        sale_date = parse_date(s("SALE_DATE"))
        owner = " ".join(x for x in (s("OWN1"), s("OWN2")) if x) or None
        if sale_price is not None and sale_price <= 1:
            notes["last_sale_price"] = "Nominal consideration ($1 or less) recorded; not an arm's-length price"

        return AssessmentPayload(
            land_use_code=s("LAND_USE"),
            land_use_description=land_use,
            property_class=class_desc,
            category=classification.category,
            school_district=school,
            site_address=location or None,
            owner_name=owner,
            assessed_value=parse_float(a.get("TOTAL_ASSESSMENT")),
            lot_size_text=f"{int(land_sf)} SF" if land_sf else None,
            acres=parse_float(a.get("LAND_ACRES")),
            lot_sqft=land_sf,
            building_style=style if s("STYLE") else None,
            year_built=year_built or comm_year,
            exterior_wall=wall if s("EXTWALL") else None,
            living_area_sqft=sfla,
            total_rooms=parse_int(s("RM_TOT")),
            bedrooms=parse_int(s("BEDROOMS")),
            full_baths=parse_int(s("BATHS")),
            half_baths=parse_int(s("HALF_BATHS")),
            last_sale_date=sale_date,
            last_sale_price=sale_price,
            gross_building_area=comm_area,
            total_living_units=comm_units or (liv_units if comm_area else None),
            structure_description=structure if s("STRUCTURE") else None,
            commercial_year_built=comm_year,
            commercial_units=comm_units,
            detail_link=PORTAL_PROFILE,
            raw_codes={k: s(k) for k in ("CLASS", "LAND_USE", "SCH_DIST", "STYLE", "EXTWALL", "STRUCTURE", "USE_TYPE", "MUNI_CODE")}
            | {"LOC_ZIP": zip_code, "ASMT_CHGDATE": s("ASMT_CHGDATE"), "DEED_BOOK_PAGE": f"{s('DEED_BOOK') or ''}-{s('DEED_PAGE') or ''}",
               "classification_basis": classification.basis, "classification_confidence": classification.confidence},
            sales=[SalePayload(sale_date=sale_date, sale_price=sale_price)] if sale_date else [],
            field_notes=notes,
        )
