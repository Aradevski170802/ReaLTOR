"""Montgomery County Prothonotary civil case search (PSI) — human verification present; user-assisted only."""

from __future__ import annotations

from app.connectors.base import CaptureRequest, PropertyRef, RetryPolicy, SearchDef, SourceDescriptor
from app.connectors.civil_common import CivilCaptureMixin, montco_relevant
from app.enums import AccessMethod

URL = "http://webapp.montcopa.org/PSI/Viewer/Search.aspx?c=CaseSearch&panel=CaseNumber"

DESCRIPTOR = SourceDescriptor(
    key="montco.civil",
    county="montco",
    name="Montgomery County Prothonotary — Case Search (PSI)",
    category="civil",
    access_method=AccessMethod.USER_ASSISTED,
    base_urls=("http://webapp.montcopa.org/PSI/", "https://courtsapp.montcopa.org/psi/"),
    searches=(SearchDef("Case Search by Parcel #", URL, "Parcel number with dashes removed"),),
    input_requirements="Parcel number without dashes (12 digits)",
    parcel_rule="Remove all dashes",
    field_mappings={"Case Number": "case_number", "Status": "status", "Case Type": "case_type",
                    "Parties/Caption": "parties", "Filing Date": "filing_date", "Amount": "amount"},
    rate_limit_per_minute=1,
    min_interval_seconds=0,
    cache_ttl_hours=24 * 7,
    retry_policy=RetryPolicy(max_attempts=1),
    error_classification={"human verification page": "user_action_needed", "no rows": "no_match vs parse_failure distinguished"},
    compliance_status="user_assisted",
    terms_notes=(
        "The search redirects to an automated 'Validating' human-verification challenge. The application never attempts "
        "to solve or bypass it; the user completes it in their own browser and captures the results. Only cases with "
        "Status '1 - Open' and a case type including 'Lien' are retained."
    ),
    terms_url=URL,
    access_findings=("2026-09-13: request redirected to courtsapp.montcopa.org/psi/auth/init with a 'Validating' challenge page.",),
)


class MontcoCivilAdapter(CivilCaptureMixin):
    descriptor = DESCRIPTOR
    parser_version = "civil-table/1"
    relevance = staticmethod(montco_relevant)

    def capture_request(self, prop: PropertyRef) -> CaptureRequest:
        return CaptureRequest(
            url=URL,
            search_hint=f"Parcel #: {prop.parcel.digits}",
            instructions=(
                "Complete the 'Verify you are human' step yourself if shown, choose Prothonotary > Case Search by Parcel #, "
                "enter the number above, then copy the results table and paste it below (or upload the saved page). If the "
                "search shows no results, paste that page so 'no results' is recorded distinctly from 'not checked'."
            ),
        )
