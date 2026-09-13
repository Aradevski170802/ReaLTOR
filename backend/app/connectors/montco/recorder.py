"""Montgomery County Recorder of Deeds (countyweb) — authenticated, user-assisted only."""

from __future__ import annotations

from app.connectors.base import CaptureRequest, PropertyRef, RetryPolicy, SearchDef, SourceDescriptor
from app.connectors.recorder_common import RECORDER_IMPORT_COLUMNS, RecorderCaptureMixin
from app.enums import AccessMethod

URL = "https://rodviewer.montcopa.org/countyweb/"

DESCRIPTOR = SourceDescriptor(
    key="montco.recorder",
    county="montco",
    name="Montgomery County Recorder of Deeds — Online Services (countyweb)",
    category="recorder",
    access_method=AccessMethod.AUTHENTICATED_BROWSER,
    base_urls=(URL,),
    searches=(SearchDef("Search Public Records by Parcel Id", URL, "Parcel Id with dashes"),),
    input_requirements="The user's own countyweb account; search by Parcel Id; documents recorded since 1990",
    parcel_rule="Parcel Id NN-NN-NNNNN-NN-N",
    field_mappings={"Instrument": "instrument_number", "Type": "doc_type_raw", "Recorded Date": "recorded_date",
                    "Grantor": "grantors", "Grantee": "grantees", "Amount/Consideration": "amount (original mortgage amount)",
                    "Book/Page": "book, page", "Assoc Instruments": "related_reference"},
    rate_limit_per_minute=1,
    min_interval_seconds=0,
    cache_ttl_hours=24 * 30,
    retry_policy=RetryPolicy(max_attempts=1),
    error_classification={"no rows recognised": "parse_failure", "not captured yet": "user_action_needed"},
    compliance_status="user_assisted",
    terms_notes=(
        "Requires login and a disclaimer acceptance; robots.txt is 'Disallow: /'. Credentials are never stored or "
        "automated by this application — the user signs in with their own account in their own browser and captures or "
        "exports the results. Recorder indexes are not title searches."
    ),
    terms_url=URL,
    access_findings=("2026-09-13: robots.txt 'User-agent: * Disallow: /'; application requires sign-in.",),
)


class MontcoRecorderAdapter(RecorderCaptureMixin):
    descriptor = DESCRIPTOR
    parser_version = "recorder-table/1"

    def capture_request(self, prop: PropertyRef) -> CaptureRequest:
        return CaptureRequest(
            url=URL,
            search_hint=f"Parcel Id: {prop.parcel.normalized}   (documents recorded 01/01/1990 to today)",
            instructions=(
                "Sign in with YOUR OWN Recorder of Deeds account (this app never asks for or stores it), accept the "
                "disclaimer, search Public Records by Parcel Id, show all results, then copy the results table "
                "(select it, Ctrl+C) and paste below, or upload the saved results page / an export. Columns: "
                + RECORDER_IMPORT_COLUMNS
            ),
        )
