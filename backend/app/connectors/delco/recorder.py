"""Delaware County PA Recorder of Deeds — public search (publicsearch.us) and guest countyweb; user-assisted only."""

from __future__ import annotations

from app.connectors.base import CaptureRequest, PropertyRef, RetryPolicy, SearchDef, SourceDescriptor
from app.connectors.recorder_common import RECORDER_IMPORT_COLUMNS, RecorderCaptureMixin
from app.enums import AccessMethod

PUBLICSEARCH_URL = "https://delaware.pa.publicsearch.us/"
COUNTYWEB_URL = "https://delcorodonlineservices.co.delaware.pa.us/countyweb/search/searchMain.do?defaultType=Public"

_FIELDS = {"Instrument/Doc #": "instrument_number", "Doc Type": "doc_type_raw", "Recorded Date": "recorded_date",
           "Grantor": "grantors", "Grantee": "grantees", "Consideration/Amount": "amount (original mortgage amount)",
           "Book/Page": "book, page", "Related/Marginal references": "related_reference"}

PUBLICSEARCH = SourceDescriptor(
    key="delco.recorder_publicsearch",
    county="delco",
    name="Delaware County Recorder of Deeds — Official Record Search (publicsearch.us)",
    category="recorder",
    access_method=AccessMethod.USER_ASSISTED,
    base_urls=(PUBLICSEARCH_URL,),
    searches=(SearchDef("Search by parcel / folio", PUBLICSEARCH_URL, "Folio number (with or without dashes)"),),
    input_requirements="Folio number; documents recorded since 1990",
    parcel_rule="Try the dashed folio first, then digits only",
    field_mappings=_FIELDS,
    rate_limit_per_minute=1,
    min_interval_seconds=0,
    cache_ttl_hours=24 * 30,
    retry_policy=RetryPolicy(max_attempts=1),
    error_classification={"no rows recognised": "parse_failure", "not captured": "user_action_needed"},
    compliance_status="user_assisted",
    terms_notes="robots.txt allows only the home page ('Allow: /$', 'Disallow: /'); the site presents its own disclaimer. Not automated; user captures or exports results.",
    terms_url=PUBLICSEARCH_URL,
    access_findings=("2026-09-13: robots.txt 'user-agent: * Allow: /$ Disallow: /'.",),
)

COUNTYWEB = SourceDescriptor(
    key="delco.recorder_countyweb",
    county="delco",
    name="Delaware County Recorder of Deeds — Online Services (countyweb, Login as Guest)",
    category="recorder",
    access_method=AccessMethod.USER_ASSISTED,
    base_urls=("https://delcorodonlineservices.co.delaware.pa.us/countyweb/",),
    searches=(SearchDef("Search Public Records by Parcel Id / Folio Number", COUNTYWEB_URL, "Folio number"),),
    input_requirements="User clicks 'Login as Guest' and accepts the Recorder of Deeds disclaimer; search by Parcel Id / Folio Number",
    parcel_rule="Folio number with dashes",
    field_mappings=_FIELDS,
    rate_limit_per_minute=1,
    min_interval_seconds=0,
    cache_ttl_hours=24 * 30,
    retry_policy=RetryPolicy(max_attempts=1),
    error_classification={"no rows recognised": "parse_failure", "not captured": "user_action_needed"},
    compliance_status="user_assisted",
    terms_notes="Guest login plus disclaimer acceptance are interactive agreements performed by the user; the application does not automate them.",
    terms_url=COUNTYWEB_URL,
)


class DelcoPublicSearchRecorderAdapter(RecorderCaptureMixin):
    descriptor = PUBLICSEARCH
    parser_version = "recorder-table/1"

    def capture_request(self, prop: PropertyRef) -> CaptureRequest:
        return CaptureRequest(
            url=PUBLICSEARCH_URL,
            search_hint=f"Folio: {prop.parcel.normalized}  (or {prop.parcel.digits}); recorded 01/01/1990 – today",
            instructions="Search by parcel/folio, show all document types, copy the results table and paste it below (or upload an export). Columns: "
            + RECORDER_IMPORT_COLUMNS,
        )


class DelcoCountywebRecorderAdapter(RecorderCaptureMixin):
    descriptor = COUNTYWEB
    parser_version = "recorder-table/1"

    def capture_request(self, prop: PropertyRef) -> CaptureRequest:
        return CaptureRequest(
            url=COUNTYWEB_URL,
            search_hint=f"Parcel Id / Folio Number: {prop.parcel.normalized}",
            instructions="Click 'Login as Guest', read and accept the disclaimer yourself, search Public Records by Parcel Id, then copy the results table (Deeds, Mortgages, Satisfactions, Assignments, …) and paste it below. Columns: "
            + RECORDER_IMPORT_COLUMNS,
        )
