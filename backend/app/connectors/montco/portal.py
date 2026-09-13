"""Montgomery County property records portal (iasWorld) — user-assisted capture only.

robots.txt on propertyrecords.montcopa.org is 'Disallow: /' and the portal requires accepting a disclaimer, so
the app never fetches it. The official GIS table supplies the same assessment fields automatically; this adapter
lets a user paste a portal page they viewed themselves to fill gaps (e.g. unmapped land-use codes).
"""

from __future__ import annotations

from app.common.parsing import parse_date, parse_float, parse_int, parse_money
from app.connectors.base import (
    CaptureRequest,
    PropertyRef,
    RetryPolicy,
    SearchDef,
    SourceAdapter,
    SourceDescriptor,
    SourceOutcome,
)
from app.connectors.iasworld import parse_datalet, row_value
from app.connectors.payloads import AssessmentPayload, SalePayload
from app.enums import AccessMethod, LookupStatus
from app.rules.classification import classify_montco

URL = "https://propertyrecords.montcopa.org/pt/search/commonsearch.aspx?mode=parid"

DESCRIPTOR = SourceDescriptor(
    key="montco.assessment_portal",
    county="montco",
    name="Montgomery County Property Records portal (Profile / Residential / Commercial / Sales tabs)",
    category="assessment",
    access_method=AccessMethod.USER_ASSISTED,
    base_urls=("https://propertyrecords.montcopa.org/pt/",),
    searches=(SearchDef("Search by Parcel ID", URL, "Parcel ID with dashes"),),
    input_requirements="User opens the portal, accepts the disclaimer, searches the parcel and captures the tab(s)",
    parcel_rule="Parcel with dashes as printed on the sale list",
    field_mappings={"Land Use Description": "Land Use Description", "School District": "School District",
                    "Assessed Value": "Assessed Value", "Style": "Building Style", "Year Built": "Year Built",
                    "Exterior Wall": "Exterior Wall Material", "Living Area": "Sq Ft Living Area",
                    "Gross Building Area": "Gross Building Area", "Total Living Units": "Total Living Units",
                    "Sale Date/Sale Price": "Sale Date / Sale Price"},
    rate_limit_per_minute=1,
    min_interval_seconds=0,
    cache_ttl_hours=24 * 30,
    retry_policy=RetryPolicy(max_attempts=1),
    error_classification={"unrecognized page": "parse_failure"},
    compliance_status="user_assisted",
    terms_notes=(
        "robots.txt: 'User-agent: * Disallow: /'. Disclaimer: information is for the user's own use and cannot be sold; "
        "not a substitute for title search, survey or appraisal. Automated access is not performed."
    ),
    terms_url=URL,
    access_findings=("2026-09-13: search URL redirects to Disclaimer.aspx; robots.txt disallows all agents.",),
)


class MontcoAssessmentPortalAdapter(SourceAdapter):
    descriptor = DESCRIPTOR
    parser_version = "iasworld-montco/1"

    def capture_request(self, prop: PropertyRef) -> CaptureRequest:
        return CaptureRequest(
            url=URL,
            search_hint=f"Parcel ID: {prop.parcel.normalized}",
            instructions=(
                "Open the portal, read and accept the disclaimer yourself, search the parcel, then for each of the Profile, "
                "Residential or Commercial, and Sales tabs press Ctrl+A, Ctrl+C and paste below (or save the page and upload). "
                "Only needed when the official GIS record is missing or a code is unmapped."
            ),
        )

    def parse_capture(self, prop: PropertyRef, content: str) -> SourceOutcome:
        doc = parse_datalet(content)
        if not doc.sections:
            return SourceOutcome(LookupStatus.PARSE_FAILURE, "No labelled fields were recognised in the captured page")
        land_use = doc.find("Land Use Description", "Land Use Code", "Land Use")
        style = doc.find("Building Style", "Style")
        sales = []
        for row in doc.rows("sales", "sale history"):
            d = parse_date(row_value(row, "Sale Date", "Date"))
            if d:
                sales.append(SalePayload(sale_date=d, sale_price=parse_money(row_value(row, "Sale Price", "Price")),
                                         grantor=row_value(row, "Grantor", "Seller"), grantee=row_value(row, "Grantee", "Buyer")))
        sales.sort(key=lambda s: s.sale_date, reverse=True)
        latest = sales[0] if sales else None
        classification = classify_montco(land_use, style)
        payload = AssessmentPayload(
            land_use_description=land_use,
            category=classification.category if land_use else None,
            school_district=doc.find("School District"),
            site_address=doc.find("Property Location", "Location", "Parcel Location"),
            assessed_value=parse_money(doc.find("Assessed Value", "Total Assessment", "Assessment")),
            lot_size_text=doc.find("Lot Size", "Lot Area"),
            acres=parse_float(doc.find("Acres", "Total Acres")),
            building_style=style,
            year_built=parse_int(doc.find("Year Built")),
            exterior_wall=doc.find("Exterior Wall Material", "Exterior Wall"),
            living_area_sqft=parse_float(doc.find("Square Feet of Living Area", "Living Area", "Sq Ft Living Area")),
            total_rooms=parse_int(doc.find("Total Rooms")),
            bedrooms=parse_int(doc.find("Total Bedrooms", "Bedrooms", "Bedrms")),
            full_baths=parse_int(doc.find("Total Full Baths", "Full Baths", "Baths")),
            half_baths=parse_int(doc.find("Total Half Baths", "Half Baths")),
            last_sale_date=latest.sale_date if latest else parse_date(doc.find("Sale Date")),
            last_sale_price=latest.sale_price if latest else parse_money(doc.find("Sale Price")),
            gross_building_area=parse_float(doc.find("Gross Building Area")),
            total_living_units=parse_int(doc.find("Total Living Units", "Living Units")),
            structure_description=doc.find("Structure", "Structure Code"),
            commercial_year_built=parse_int(doc.find("Year Built", sections=("commercial",))),
            sales=sales,
        )
        found = sum(1 for v in vars(payload).values() if v not in (None, [], {}))
        return SourceOutcome(LookupStatus.SUCCESS if found >= 2 else LookupStatus.PARSE_FAILURE,
                             f"{found} field(s) recognised from captured portal page", payload=payload)
