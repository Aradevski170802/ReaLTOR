"""Montgomery County Tax Claim Bureau parcel search (public web, no login/CAPTCHA; terms acknowledgement required)."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.common.parsing import clean_ws, parse_money
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
from app.connectors.http import ComplianceHttpClient
from app.connectors.payloads import TaxClaimPayload, TaxYearPayload
from app.enums import AccessMethod, LookupStatus, TaxStatusCategory

BASE = "https://webapp02.montcopa.org/taxclaim/"
SEARCH_URL = BASE + "ParcelList.aspx"
DETAIL_URL = BASE + "ParcelInfo.aspx"
GUID_RE = re.compile(r"showDetails\(\s*(?:&#39;|')([0-9a-fA-F-]{36})(?:&#39;|')\s*\)")
CLAIM_YEAR_RE = re.compile(r"Claim\s+Year\s+(\d{4})", re.I)
YEAR_TOTAL_RE = re.compile(r"^(\d{4})\s+Total$", re.I)
AS_OF_RE = re.compile(r"Delinquent Taxes Due as of\s*([0-9/]+\s*[0-9:]*\s*[AP]M)?", re.I)

DESCRIPTOR = SourceDescriptor(
    key="montco.tax_claim",
    county="montco",
    name="Montgomery County Tax Claim Bureau — Parcel Search",
    category="tax",
    access_method=AccessMethod.PUBLIC_WEB,
    base_urls=(BASE,),
    searches=(
        SearchDef("Parcel search", SEARCH_URL, "POST parcelNum=<NN-NN-NNNNN-NN-N>"),
        SearchDef("Parcel details", DETAIL_URL, "POST entity=<result GUID>"),
    ),
    input_requirements="Montgomery parcel with dashes",
    parcel_rule="Dashed parcel NN-NN-NNNNN-NN-N; exact match required on the result list",
    field_mappings={
        "Claim Year 2023 Total": "2023 Total", "Claim Year 2024 Total/Balance": "2024 Total / 2024 Balance",
        "Claim Year 2025 Total/Balance": "2025 Total", "Delinquent Taxes Due Total": "Tax Claim Delinquent Total",
        "Assessed Value": "cross-check", "Description": "cross-check",
    },
    rate_limit_per_minute=12,
    min_interval_seconds=5.0,
    cache_ttl_hours=12,
    retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=5.0, max_delay_seconds=90),
    error_classification={
        "'Query returned no results'": "no_match", "several parcels without exact match": "ambiguous_match",
        "redirect to login/disclaimer": "user_action_needed", "HTTP 429": "rate_limited", "HTTP 5xx/timeouts": "unexpected_failure",
        "missing claim tables": "parse_failure",
    },
    compliance_status="automated_requires_terms_ack",
    terms_notes=(
        "No robots.txt, no login and no CAPTCHA on webapp02.montcopa.org (checked 2026-09-13). The montcopa.org Terms of Use "
        "limit downloads to personal, non-commercial use and require prior written approval of the Montgomery County CIO for "
        "commercial use of county technology resources. The page states it is NOT a certified search. An administrator must "
        "acknowledge these terms (and confirm the intended use is permitted) before automated lookups run; otherwise use "
        "the user-assisted capture."
    ),
    terms_url="https://www.montgomerycountypa.gov/",
    requires_terms_ack=True,
    daily_refresh=True,
    access_findings=(
        "2026-09-13: POST ParcelList.aspx returns showDetails(GUID) links; POST ParcelInfo.aspx returns tblParcel and tblLiens "
        "with per-claim-year Face/Penalty/Interest/Total/Paid/Balance.",
    ),
)


class MontcoTaxClaimAdapter(SourceAdapter):
    descriptor = DESCRIPTOR
    parser_version = "taxclaim/1"

    def lookup(self, prop: PropertyRef, secrets: dict[str, str] | None = None) -> SourceOutcome:
        parcel = prop.parcel.normalized
        try:
            with ComplianceHttpClient(self.descriptor) as http:
                listing = http.post(SEARCH_URL, data={"parcelNum": parcel, "address": ""})
                evidence = [listing.evidence("search results")]
                guid, status, message = self.pick_guid(listing.text, parcel)
                if guid is None:
                    return SourceOutcome(status, message, evidence=evidence)
                detail = http.post(DETAIL_URL, data={"entities": guid, "entity": guid})
                evidence.append(detail.evidence("parcel details"))
        except SourceError as exc:
            return SourceOutcome(exc.status, exc.message, retry_after=exc.retry_after,
                                 evidence=[exc.evidence] if exc.evidence else [])
        return self._outcome(detail.text, parcel, evidence)

    def capture_request(self, prop: PropertyRef) -> CaptureRequest:
        return CaptureRequest(
            url=BASE + "Search.aspx",
            search_hint=f"Parcel Number: {prop.parcel.normalized}",
            instructions="Search the parcel, open its details, then save the page (Ctrl+S) and upload it, or copy the page source and paste it.",
        )

    def parse_capture(self, prop: PropertyRef, content: str) -> SourceOutcome:
        return self._outcome(content, prop.parcel.normalized, [])

    @staticmethod
    def pick_guid(html: str, parcel: str) -> tuple[str | None, LookupStatus, str]:
        if "Query returned no results" in html:
            return None, LookupStatus.NO_MATCH, "Tax Claim Bureau search returned no results (no delinquent claim on file or parcel not found)"
        soup = BeautifulSoup(html, "lxml")
        matches = []
        for a in soup.find_all("a", href=True):
            m = GUID_RE.search(a["href"])
            if m:
                matches.append((clean_ws(a.get_text()), m.group(1)))
        exact = [guid for text, guid in matches if text == parcel]
        if len(exact) == 1:
            return exact[0], LookupStatus.SUCCESS, ""
        if not matches:
            return None, LookupStatus.PARSE_FAILURE, "Search results page did not contain parcel links"
        if not exact:
            return None, LookupStatus.NO_MATCH, f"{len(matches)} result(s) but none exactly matched {parcel}"
        return None, LookupStatus.AMBIGUOUS, f"{len(exact)} results exactly matched {parcel}"

    def _outcome(self, html: str, parcel: str, evidence) -> SourceOutcome:
        try:
            payload = self.parse_detail(html)
        except ValueError as exc:
            return SourceOutcome(LookupStatus.PARSE_FAILURE, str(exc), evidence=evidence)
        page_parcel = clean_ws(BeautifulSoup(html, "lxml").select_one("#lblParcelNum").get_text()) if "lblParcelNum" in html else None
        if page_parcel and page_parcel != parcel:
            return SourceOutcome(LookupStatus.AMBIGUOUS, f"Details page is for {page_parcel}, expected {parcel}", evidence=evidence)
        return SourceOutcome(LookupStatus.SUCCESS, payload.tax_sale_status or "Tax claim details retrieved", payload=payload, evidence=evidence)

    @staticmethod
    def parse_detail(html: str) -> TaxClaimPayload:
        soup = BeautifulSoup(html, "lxml")
        if soup.select_one("#tblParcel") is None and soup.select_one("#tblLiens") is None:
            raise ValueError("Page is not a Tax Claim Bureau parcel details page")

        def span(el_id: str) -> str | None:
            el = soup.select_one(f"#{el_id}")
            return clean_ws(el.get_text(" ")) if el else None

        payload = TaxClaimPayload(
            district=span("lblDistrict"),
            description=span("lblDescription"),
            assessed_value=parse_money(span("lblAssessedValue")),
            deed_book_page=span("lblDeedInfo"),
            owner_name=span("lblBillName"),
            location=span("lblLocation"),
        )
        text = soup.get_text(" ")
        if m := AS_OF_RE.search(text):
            payload.as_of_text = clean_ws(m.group(0))

        payment = soup.select_one("#tblPayment")
        if payment:
            ths = [clean_ws(th.get_text()) for th in payment.find_all("th")]
            for i, label in enumerate(ths[:-1]):
                if label and label.lower() == "total":
                    payload.delinquent_total = parse_money(ths[i + 1])

        liens = soup.select_one("#tblLiens")
        if liens:
            current: TaxYearPayload | None = None
            for tr in liens.find_all("tr"):
                th = tr.find("th")
                if th and (m := CLAIM_YEAR_RE.search(th.get_text())):
                    current = TaxYearPayload(year=int(m.group(1)), face=0.0, penalty=0.0, interest=0.0)
                    payload.years.append(current)
                    continue
                cells = [clean_ws(td.get_text()) for td in tr.find_all("td")]
                if not cells or current is None:
                    continue
                if YEAR_TOTAL_RE.match(cells[0] or ""):
                    nums = [parse_money(c) for c in cells[1:]]
                    if len(nums) >= 3:
                        current.total, current.paid, current.balance = nums[-3], nums[-2], nums[-1]
                elif len(cells) == 7 and cells[0]:
                    face, penalty, interest = (parse_money(c) for c in cells[1:4])
                    current.face = round((current.face or 0) + (face or 0), 2)
                    current.penalty = round((current.penalty or 0) + (penalty or 0), 2)
                    current.interest = round((current.interest or 0) + (interest or 0), 2)

        y2024 = payload.year(2024)
        if y2024 is not None and y2024.balance is not None and abs(y2024.balance) < 0.005:
            payload.status_category = TaxStatusCategory.RESOLVED
            payload.tax_sale_status = "Resolved / no 2024 balance"
        elif y2024 is not None and (y2024.balance or 0) > 0:
            payload.status_category = TaxStatusCategory.LISTED
            payload.tax_sale_status = f"2024 claim balance ${y2024.balance:,.2f}"
        elif payload.years:
            payload.status_category = TaxStatusCategory.UNKNOWN
            payload.tax_sale_status = "No 2024 claim year on file"
        else:
            payload.status_category = TaxStatusCategory.UNKNOWN
            payload.tax_sale_status = "No claim years on file"
        return payload
