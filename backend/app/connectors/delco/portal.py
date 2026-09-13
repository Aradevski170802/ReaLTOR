"""Delaware County Real Estate & Tax Records portal (iasWorld) — user-assisted capture.

The portal requires the user to accept a disclaimer (and carries reCAPTCHA hooks), so it is never fetched
automatically. The user opens the county's own deep link (from the GIS layer), accepts, and captures the
Site Information, Residential/Commercial, Owner History, Tax Sale Information and Delinquent Tax sections.
"""

from __future__ import annotations

import re

from app.common.parsing import clean_ws, parse_date, parse_float, parse_int, parse_money
from app.connectors.base import (
    CaptureRequest,
    PropertyRef,
    RetryPolicy,
    SearchDef,
    SourceAdapter,
    SourceDescriptor,
    SourceOutcome,
)
from app.connectors.iasworld import DataletDocument, parse_datalet, row_value
from app.connectors.payloads import AssessmentPayload, SalePayload, TaxClaimPayload, TaxYearPayload
from app.enums import AccessMethod, LookupStatus, TaxStatusCategory
from app.rules.classification import classify_delco

SEARCH_URL = "http://delcorealestate.co.delaware.pa.us/pt/search/commonsearch.aspx?mode=parid"
DATALET_URL = "http://delcorealestate.co.delaware.pa.us/pt/datalets/datalet.aspx?mode=&usesearch=no&jur=023&taxyr={year}&pin={pin}"
LISTED_RE = re.compile(r"^\s*listed\s+for\s+upset\s+sale\s*$", re.I)
RESOLVED_WORDS = ("PAID", "REDEEMED", "REMOVED", "SATISFIED", "CANCELLED", "CANCELED")
NOT_LISTED_WORDS = ("STAY", "STAYED", "AGREEMENT", "INSTALLMENT", "BANKRUPT", "EXCLUDED", "WITHDRAWN", "SOLD", "NOT LISTED",
                    "POSTPONED", "HOLD")

DESCRIPTOR = SourceDescriptor(
    key="delco.assessment_portal",
    county="delco",
    name="Delaware County Real Estate & Tax Records (Site Information, Residential/Commercial, Owner History, Tax Sale, Delinquent Tax)",
    category="assessment",
    access_method=AccessMethod.USER_ASSISTED,
    base_urls=("http://delcorealestate.co.delaware.pa.us/pt/",),
    searches=(SearchDef("Search by Parcel ID", SEARCH_URL, "Folio without dashes"),
              SearchDef("County deep link (from GIS)", DATALET_URL, "pin = folio digits")),
    input_requirements="User accepts the county disclaimer and captures the parcel's sections",
    parcel_rule="Folio-NBR without dashes (11 digits)",
    field_mappings={
        "Site Location": "site_address", "Legal Description": "legal_description", "School District": "school_district",
        "Property Type": "property_type", "Residential: Style/Base Area/Year Built/Bed Rooms/Full Baths/Acres": "residential fields",
        "Commercial: Improvement Name/Living Area/Year Built/Units": "commercial fields",
        "Owner History: Sale Date/Sale Price": "sales", "Assessment Value": "assessed_value",
        "Tax Sale Information: Status": "tax_sale_status", "Delinquent Tax: Year 2024/2025 taxes due": "delinquent years",
        "Delinquent Tax - All Years Combined: Total Due": "delinquent_total", "County Tax Receivable: Face Amount Due / Balance": "receivable years",
    },
    rate_limit_per_minute=1,
    min_interval_seconds=0,
    cache_ttl_hours=24,
    retry_policy=RetryPolicy(max_attempts=1),
    error_classification={"no recognised labels": "parse_failure", "not captured": "user_action_needed"},
    compliance_status="user_assisted",
    terms_notes=(
        "Access requires accepting the county disclaimer ('By using this information, the User is stating that the above "
        "Disclaimer has been read ... and is in agreement'); the disclaimer page includes reCAPTCHA elements. Name search "
        "and photos were removed by the county for privacy. The application does not accept the disclaimer on anyone's "
        "behalf and does not automate the portal. Daily tax-sale status checks create re-capture tasks."
    ),
    terms_url=SEARCH_URL,
    daily_refresh=True,
    access_findings=("2026-09-13: search URL redirects to Disclaimer.aspx containing captcha_div/recaptcha_area.",),
)


def status_category(status: str | None) -> str:
    if not status or not status.strip():
        return TaxStatusCategory.UNKNOWN
    if LISTED_RE.match(status):
        return TaxStatusCategory.LISTED
    upper = status.upper()
    if any(w in upper for w in RESOLVED_WORDS):
        return TaxStatusCategory.RESOLVED
    return TaxStatusCategory.NOT_LISTED


class DelcoAssessmentPortalAdapter(SourceAdapter):
    descriptor = DESCRIPTOR
    parser_version = "iasworld-delco/1"

    def capture_request(self, prop: PropertyRef) -> CaptureRequest:
        link = prop.identifiers.get("detail_link")
        return CaptureRequest(
            url=link or SEARCH_URL,
            search_hint=f"Parcel ID: {prop.parcel.digits}   (Folio-NBR {prop.parcel.normalized})",
            instructions=(
                "Open the link, read and click Agree on the county disclaimer yourself, search by Parcel ID if asked, then "
                "visit Site Information, Residential (or Commercial), Owner History, Tax Sale Information and Delinquent Tax. "
                "On each, press Ctrl+A then Ctrl+C and paste into the box (you can paste several tabs one after another), "
                "or save each page and upload. The preview shows every recognised field before anything is saved."
            ),
        )

    def parse_capture(self, prop: PropertyRef, content: str) -> SourceOutcome:
        doc = parse_datalet(content)
        if not doc.sections:
            return SourceOutcome(LookupStatus.PARSE_FAILURE, "No labelled fields were recognised in the captured page(s)")
        payload = self.extract(doc)
        recognised = [k for k, v in vars(payload).items() if v not in (None, [], {}) and k not in ("field_notes",)]
        if payload.tax:
            recognised += ["tax." + k for k, v in vars(payload.tax).items() if v not in (None, [], "unknown")]
        status = LookupStatus.SUCCESS if len(recognised) >= 2 else LookupStatus.PARSE_FAILURE
        return SourceOutcome(status, f"Recognised: {', '.join(recognised) or 'nothing'}", payload=payload)

    @staticmethod
    def extract(doc: DataletDocument) -> AssessmentPayload:
        ptype = doc.find("Property Type", "Class", "Property Class")
        style = doc.find("Style", "Residential Style", sections=("residential", "dwelling")) or doc.find("Style")
        classification = classify_delco(ptype, style)

        sales: list[SalePayload] = []
        for row in doc.rows("owner history", "sales", "sale history"):
            d = parse_date(row_value(row, "Sale Date", "Date"))
            if d:
                sales.append(SalePayload(sale_date=d, sale_price=parse_money(row_value(row, "Sale Price", "Price", "Consideration")),
                                         grantee=row_value(row, "Owner", "Name", "Grantee"), book_page=row_value(row, "Book/Page", "Deed Book")))
        sales.sort(key=lambda s: s.sale_date, reverse=True)

        tax = TaxClaimPayload()
        tax.tax_sale_status = clean_ws(doc.find("Status", sections=("tax sale information",)) or doc.find("Tax Sale Status"))
        tax.status_category = status_category(tax.tax_sale_status)
        tax.delinquent_total = parse_money(doc.find("Total Due", sections=("all years combined",)) or doc.find("Total Delinquent Due"))
        for row in doc.rows("outstanding delinquent taxes", "delinquent tax"):
            year = parse_int(row_value(row, "Year", "Tax Year"))
            if year:
                due = parse_money(row_value(row, "Taxes Due", "Total Due", "Amount Due", "Due", "Balance"))
                tax.years.append(TaxYearPayload(year=year, kind="delinquent", balance=due, total=due,
                                                face=parse_money(row_value(row, "Face", "Face Amount"))))
        for row in doc.rows("county tax receivable"):
            year = parse_int(row_value(row, "Year", "Tax Year"))
            if year:
                tax.years.append(TaxYearPayload(year=year, kind="receivable",
                                                face=parse_money(row_value(row, "Face Amount Due", "Face Amount", "Face")),
                                                balance=parse_money(row_value(row, "Balance", "Balance Due"))))
        if not (tax.tax_sale_status or tax.years or tax.delinquent_total is not None):
            tax = None  # type: ignore[assignment]

        commercial = bool(doc.section("commercial"))
        return AssessmentPayload(
            property_type=ptype,
            category=classification.category if ptype else None,
            site_address=doc.find("Site Location", "Property Location", "Location Address", "Location"),
            legal_description=doc.find("Legal Description", "Legal"),
            school_district=doc.find("School District"),
            municipality=doc.find("Municipality", "Taxing District"),
            assessed_value=parse_money(doc.find("Assessment Value", "Assessed Value", "Total Assessment", "Current Assessment", "Total")),
            building_style=style,
            base_area_sqft=parse_float(doc.find("Base Area", "Base Area (sq ft)", sections=("residential", "dwelling")) or doc.find("Base Area")),
            living_area_sqft=parse_float(doc.find("Living Area", "Finished Living Area", sections=("residential", "dwelling"))),
            year_built=parse_int(doc.find("Year Built", sections=("residential", "dwelling")) or (None if commercial else doc.find("Year Built"))),
            bedrooms=parse_int(doc.find("Bed Rooms", "Bedrooms", "Bedroom")),
            full_baths=parse_int(doc.find("Full Baths", "Full Bath")),
            half_baths=parse_int(doc.find("Half Baths", "Half Bath")),
            acres=parse_float(doc.find("Acres", "Acreage", "Total Acres")),
            last_sale_date=sales[0].sale_date if sales else parse_date(doc.find("Sale Date")),
            last_sale_price=sales[0].sale_price if sales else parse_money(doc.find("Sale Price")),
            improvement_name=doc.find("Improvement Name", "Improvement", sections=("commercial",)),
            commercial_living_area=parse_float(doc.find("Living Area", "Area", sections=("commercial",))),
            commercial_year_built=parse_int(doc.find("Year Built", sections=("commercial",))),
            commercial_units=parse_int(doc.find("Units", "Number of Units", sections=("commercial",))),
            sales=sales,
            tax=tax,
            raw_codes={"classification_basis": classification.basis, "labels_seen": doc.all_labels()[:80]},
        )
