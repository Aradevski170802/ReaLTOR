"""Delaware County Real Estate & Tax Records portal (iasWorld) — automated once terms are acknowledged.

The portal gates access behind a one-click "Agree" disclaimer (a liability waiver — no login, and no active CAPTCHA
as of 2026-09-14). When an administrator acknowledges this source's terms, the app accepts the disclaimer
automatically (recorded in the audit log) and fetches each parcel's Site Information, Residential/Commercial and
Delinquent Tax tabs directly. If the portal ever changes to require human verification, it falls back to the
user-assisted capture, which is always available. delcorealestate has no robots.txt restriction.
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
    SourceError,
    SourceOutcome,
)
from app.connectors.iasworld import DataletDocument, parse_datalets, row_value
from app.connectors.iasworld_client import IasWorldPortal, IasWorldSession
from app.connectors.payloads import AssessmentPayload, SalePayload, TaxClaimPayload, TaxYearPayload
from app.enums import AccessMethod, LookupStatus, TaxStatusCategory
from app.rules.classification import classify_delco

SEARCH_URL = "http://delcorealestate.co.delaware.pa.us/pt/search/commonsearch.aspx?mode=parid"
DATALET_URL = "http://delcorealestate.co.delaware.pa.us/pt/datalets/datalet.aspx?mode=&usesearch=no&jur=023&taxyr={year}&pin={pin}"
PORTAL = IasWorldPortal(base="http://delcorealestate.co.delaware.pa.us/pt", jurisdiction="023", tax_year=2026)
COMMERCIAL_TYPES = ("COMMERCIAL", "INDUSTRIAL", "APARTMENT", "UTILITY")
LISTED_RE = re.compile(r"^\s*listed\s+for\s+upset\s+sale\s*$", re.I)
RESOLVED_WORDS = ("PAID", "REDEEMED", "REMOVED", "SATISFIED", "CANCELLED", "CANCELED")
NOT_LISTED_WORDS = ("STAY", "STAYED", "AGREEMENT", "INSTALLMENT", "BANKRUPT", "EXCLUDED", "WITHDRAWN", "SOLD", "NOT LISTED",
                    "POSTPONED", "HOLD")

DESCRIPTOR = SourceDescriptor(
    key="delco.assessment_portal",
    county="delco",
    name="Delaware County Real Estate & Tax Records (Site Information, Residential/Commercial, Owner History, Tax Sale, Delinquent Tax)",
    category="assessment",
    access_method=AccessMethod.PUBLIC_WEB,
    base_urls=("http://delcorealestate.co.delaware.pa.us/pt/",),
    searches=(SearchDef("Search by Parcel ID", SEARCH_URL, "Folio without dashes"),
              SearchDef("County deep link (from GIS)", DATALET_URL, "pin = folio digits")),
    input_requirements="Folio digits; the app accepts the disclaimer and fetches the parcel's data tabs",
    parcel_rule="Folio-NBR without dashes (11 digits)",
    field_mappings={
        "Site Location": "site_address", "Legal Description": "legal_description", "School District": "school_district",
        "Property Type": "property_type", "Residential: Style/Base Area/Year Built/Bed Rooms/Full Baths/Acres": "residential fields",
        "Commercial: Improvement Name/Living Area/Year Built/Units": "commercial fields",
        "Owner History: Sale Date/Sale Price": "sales", "Assessment Value": "assessed_value",
        "Tax Sale Information: Status": "tax_sale_status", "Delinquent Tax: Year 2024/2025 taxes due": "delinquent years",
        "Delinquent Tax - All Years Combined: Total Due": "delinquent_total", "County Tax Receivable: Face Amount Due / Balance": "receivable years",
    },
    rate_limit_per_minute=10,
    min_interval_seconds=3.0,
    cache_ttl_hours=24,
    retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=3.0),
    error_classification={"no data tabs for parcel": "no_match", "human verification appears": "user_action_needed",
                          "no recognised labels": "parse_failure", "HTTP 429/5xx/timeouts": "unexpected_failure"},
    compliance_status="automated_requires_terms_ack",
    terms_notes=(
        "Access requires accepting the county disclaimer ('By using this information, the User is stating that the above "
        "Disclaimer has been read ... and is in agreement'). It is a liability waiver, not a login or CAPTCHA, and "
        "delcorealestate has no robots.txt restriction. An administrator must acknowledge these terms — which records "
        "that the operator agrees to the disclaimer — before the app accepts it automatically. Name search and photos "
        "were removed by the county for privacy; this app searches only by parcel/folio. If the portal later adds a "
        "human-verification step, the app stops automating and falls back to user-assisted capture."
    ),
    terms_url=SEARCH_URL,
    requires_terms_ack=True,
    daily_refresh=True,
    access_findings=(
        "2026-09-14: GET Disclaimer.aspx then POST action=Agree sets a DISCLAIMER=1 cookie and lands on commonsearch.aspx; "
        "datalet.aspx tabs profileall / residential / comsummary / delinquent then return the parcel data. No CAPTCHA present.",
    ),
)


def _first_year(text: str | None) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    return int(m.group(0)) if m else None


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
    parser_version = "iasworld-delco/2"

    def lookup(self, prop: PropertyRef, secrets: dict[str, str] | None = None) -> SourceOutcome:
        if not prop.parcel.valid:
            return SourceOutcome(LookupStatus.NO_MATCH, f"Folio '{prop.parcel.original}' is not a valid Delaware County folio")
        # A GIS "Property Type" hint (if already stored) tells us whether to fetch the residential or commercial tab.
        commercial = any(w in (prop.identifiers.get("property_type") or "").upper() for w in COMMERCIAL_TYPES)
        try:
            with IasWorldSession(self.descriptor, PORTAL) as session:
                html, evidence = session.fetch_property_html(prop.parcel.digits, commercial=commercial)
        except SourceError as exc:
            return SourceOutcome(exc.status, exc.message, retry_after=exc.retry_after,
                                 evidence=[exc.evidence] if exc.evidence else [])
        doc = parse_datalets(html)
        if not doc.sections:
            return SourceOutcome(LookupStatus.PARSE_FAILURE, "Portal returned pages with no recognisable fields", evidence=[evidence])
        payload = self.extract(doc)
        if not commercial and payload.property_type and any(w in payload.property_type.upper() for w in COMMERCIAL_TYPES):
            # First fetch used the residential tab but the parcel is commercial: fetch the commercial tab and merge.
            try:
                with IasWorldSession(self.descriptor, PORTAL) as session:
                    extra, _ = session.fetch_property_html(prop.parcel.digits, commercial=True)
                payload = self.extract(parse_datalets(html + "\n" + extra))
            except SourceError:
                pass
        return SourceOutcome(LookupStatus.SUCCESS, payload.tax.tax_sale_status if payload.tax and payload.tax.tax_sale_status
                             else "Assessment and tax detail retrieved", payload=payload, evidence=[evidence])

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
        doc = parse_datalets(content)
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
            year_built=_first_year(doc.find("Year Built / Effective Year", "Year Built", "Year Built/Effective Year",
                                            sections=("residential", "dwelling")) or doc.find("Year Built / Effective Year", "Year Built")),
            bedrooms=parse_int(doc.find("Bed Rooms", "Bedrooms", "Bedroom")),
            full_baths=parse_int(doc.find("Full Baths", "Full Bath")),
            half_baths=parse_int(doc.find("Half Baths", "Half Bath")),
            acres=parse_float(doc.find("Acres", "Acreage", "Total Acres")),
            last_sale_date=sales[0].sale_date if sales else parse_date(doc.find("Sale Date")),
            last_sale_price=sales[0].sale_price if sales else parse_money(doc.find("Sale Price")),
            improvement_name=doc.find("Improvement Name", "Improvement", sections=("commercial",)),
            commercial_living_area=parse_float(doc.find("Living Area", "Area", sections=("commercial",))),
            commercial_year_built=_first_year(doc.find("Year Built / Effective Year", "Year Built", sections=("commercial",))),
            commercial_units=parse_int(doc.find("Units", "Number of Units", sections=("commercial",))),
            sales=sales,
            tax=tax,
            raw_codes={"classification_basis": classification.basis, "labels_seen": doc.all_labels()[:80]},
        )
