"""Delaware County PA civil public access — party-name search; user-assisted only."""

from __future__ import annotations

from app.connectors.base import CaptureRequest, PropertyRef, RetryPolicy, SearchDef, SourceDescriptor
from app.connectors.civil_common import CivilCaptureMixin, delco_relevant
from app.connectors.names import party_search_terms
from app.enums import AccessMethod

URL = "https://delcopublicaccess.co.delaware.pa.us/"

DESCRIPTOR = SourceDescriptor(
    key="delco.civil",
    county="delco",
    name="Delaware County Public Access — Party Search (civil cases)",
    category="civil",
    access_method=AccessMethod.USER_ASSISTED,
    base_urls=(URL,),
    searches=(SearchDef("Party Search by Party Name", URL, "Owner 'LAST FIRST M' or company name"),),
    input_requirements="Owner name from the sale list / assessment record",
    parcel_rule="Not searched by parcel; owner party names are derived (Last + First + middle initial, or company name)",
    field_mappings={"Case Number": "case_number", "Case Classification": "classification", "Case Type": "case_type",
                    "Status": "status", "Party": "parties", "Filing Date": "filing_date"},
    rate_limit_per_minute=1,
    min_interval_seconds=0,
    cache_ttl_hours=24 * 7,
    retry_policy=RetryPolicy(max_attempts=1),
    error_classification={"'no records found' page": "no_match", "unrecognised page": "parse_failure", "not captured": "user_action_needed"},
    compliance_status="user_assisted",
    terms_notes=(
        "Access starts with 'CONTINUE AS PUBLIC USER' (an interactive acceptance) in a single-page application. Not "
        "automated. Name searches can return other people with the same name: every retained case should be reviewed. "
        "Only cases whose Case Classification includes 'Lien' are kept."
    ),
    terms_url=URL,
)


class DelcoCivilAdapter(CivilCaptureMixin):
    descriptor = DESCRIPTOR
    parser_version = "civil-table/1"
    relevance = staticmethod(delco_relevant)

    def capture_request(self, prop: PropertyRef) -> CaptureRequest:
        terms = party_search_terms(prop.owner_name)
        hint = "; ".join(f"{t.party_name} ({t.kind}{', ' + t.note if t.note else ''})" for t in terms) or "Owner name unavailable"
        return CaptureRequest(
            url=URL,
            search_hint=f"Party Name searches: {hint}",
            instructions=(
                "Click 'CONTINUE AS PUBLIC USER', open 'Party Search', and run one search per name above (Party Name field). "
                "Paste each results table below, one after another; paste the 'no records' page too so it is recorded as "
                "a completed search. Name matches are not identity matches — review each retained lien case."
            ),
        )
