"""Delaware County Treasurer tax payment lookup — payment portal flow; user-assisted capture only."""

from __future__ import annotations

from app.common.parsing import parse_int, parse_money
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
from app.connectors.payloads import TaxClaimPayload, TaxYearPayload
from app.enums import AccessMethod, LookupStatus

URL = "https://www.delcopa.gov/treasurer/paytaxes"

DESCRIPTOR = SourceDescriptor(
    key="delco.treasurer",
    county="delco",
    name="Delaware County Treasurer — Pay County Taxes (bill lookup)",
    category="tax",
    access_method=AccessMethod.USER_ASSISTED,
    base_urls=(URL,),
    searches=(SearchDef("Pay → Continue → folio", URL, "Folio without dashes"),),
    input_requirements="Folio without dashes; user navigates the payment portal to the bill summary only",
    parcel_rule="Folio-NBR without dashes",
    field_mappings={"Tax Year": "year", "Amount Due/Balance": "balance"},
    rate_limit_per_minute=1,
    min_interval_seconds=0,
    cache_ttl_hours=24,
    retry_policy=RetryPolicy(max_attempts=1),
    error_classification={"no bill rows": "parse_failure"},
    compliance_status="user_assisted",
    terms_notes=(
        "The county tax lookup is part of an online payment flow with interactive steps. The application never enters the "
        "payment flow; the user views the bill summary and captures it. Current-year county taxes are separate from the "
        "Tax Claim Bureau delinquent/upset-sale status."
    ),
    terms_url=URL,
)


class DelcoTreasurerAdapter(SourceAdapter):
    descriptor = DESCRIPTOR
    parser_version = "treasurer-bill/1"

    def capture_request(self, prop: PropertyRef) -> CaptureRequest:
        return CaptureRequest(
            url=URL,
            search_hint=f"Folio (no dashes): {prop.parcel.digits}",
            instructions="Click Pay → Continue, enter the folio above, stop at the bill summary (do not pay), copy the bill table and paste it here.",
        )

    def parse_capture(self, prop: PropertyRef, content: str) -> SourceOutcome:
        doc = parse_datalet(content)
        years = []
        for section in doc.sections:
            for row in section.rows:
                year = parse_int(row_value(row, "Tax Year", "Year", "Bill Year"))
                if year:
                    years.append(TaxYearPayload(year=year, kind="receivable",
                                                face=parse_money(row_value(row, "Face", "Face Amount", "Amount Billed")),
                                                balance=parse_money(row_value(row, "Balance", "Amount Due", "Due", "Total Due"))))
        if not years:
            return SourceOutcome(LookupStatus.PARSE_FAILURE, "No tax-year rows recognised in the captured bill")
        return SourceOutcome(LookupStatus.SUCCESS, f"{len(years)} tax year(s) captured", payload=TaxClaimPayload(years=years))
