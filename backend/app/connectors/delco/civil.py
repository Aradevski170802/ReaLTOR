"""Delaware County PA civil public access (C-Track) — party-name search.

Automated with a real browser when browser automation is enabled: the portal's public access is a plain
"CONTINUE AS PUBLIC USER" step with no login and no CAPTCHA, so a browser can open it, run a Party Search for each
owner name, and read the results grid (Case Number, Case Title, Case Classification, Case Filed Date). Only cases
whose classification contains "Lien" are kept. Without browser automation it falls back to user-assisted capture.
"""

from __future__ import annotations

import re

from app.common.parsing import parse_date
from app.connectors.base import CaptureRequest, PropertyRef, RetryPolicy, SearchDef, SourceDescriptor, SourceOutcome
from app.connectors.civil_common import CivilCaptureMixin, delco_relevant
from app.connectors.names import party_search_terms
from app.connectors.payloads import LienCasePayload
from app.enums import AccessMethod, LookupStatus

URL = "https://delcopublicaccess.co.delaware.pa.us/"

DESCRIPTOR = SourceDescriptor(
    key="delco.civil",
    county="delco",
    name="Delaware County Public Access (C-Track) — Party Search (civil cases)",
    category="civil",
    # Browser-driven, not raw HTTP: access_method stays USER_ASSISTED (so it is never treated as an HTTP API), but the
    # app drives the no-CAPTCHA public portal with a real browser when browser automation is enabled.
    access_method=AccessMethod.USER_ASSISTED,
    base_urls=(URL,),
    searches=(SearchDef("Party Search by Party Name", URL, "Owner 'LAST FIRST M' or company name"),),
    input_requirements="Owner name from the sale list / assessment record",
    parcel_rule="Not searched by parcel; owner party names are derived (Last + First + middle initial, or company name)",
    field_mappings={"Case Number": "case_number", "Case Title": "parties", "Case Classification": "classification",
                    "Case Filed Date": "filing_date"},
    rate_limit_per_minute=20,
    min_interval_seconds=2.0,
    cache_ttl_hours=24 * 7,
    retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=3.0),
    error_classification={"no records found": "no_match", "unexpected page": "parse_failure", "browser unavailable": "user_action_needed"},
    compliance_status="automated_browser_no_captcha",
    terms_notes=(
        "Public access begins with a 'CONTINUE AS PUBLIC USER' click (no account, no CAPTCHA). When browser automation "
        "is enabled the app performs that click and the Party Search itself. Name searches can return other people with "
        "the same name, so every retained case is flagged for review. Only cases whose Case Classification contains "
        "'Lien' are kept."
    ),
    terms_url=URL,
    access_findings=(
        "2026-09-14: 'CONTINUE AS PUBLIC USER' then Party Search returns a grid of Case Number / Case Title / "
        "Case Classification / Case Filed Date with no CAPTCHA; classifications include 'Municipal Lien' etc.",
    ),
)

CV_RE = re.compile(r"^[A-Z]{1,3}-?\d{2,4}-?\d{3,7}$")


def ctrack_party_name(term) -> str:
    """C-Track stores parties as 'LAST, FIRST MI'. Format a person that way; leave a company name as-is."""
    if term.kind == "entity":
        return term.party_name
    parts = term.party_name.split()
    return f"{parts[0]}, {' '.join(parts[1:])}" if len(parts) > 1 else term.party_name


def parse_ctrack_rows(rows: list[list[str]], search_term: str) -> list[LienCasePayload]:
    """Parse C-Track result rows into lien-case payloads, handling both result layouts:
      * Party search:  [Case Number, Case Classification, Case Filed Date, Party Name, Party Role]
      * Case search:   [Case Number, Case Title, Case Classification, Case Filed Date]
    """
    cases: list[LienCasePayload] = []
    for row in rows:
        cells = [c.strip() for c in row]
        if not cells or not cells[0] or "no records" in cells[0].lower() or not CV_RE.match(cells[0].replace(" ", "")):
            continue
        if len(cells) >= 5:  # party-search view
            case_number, classification, filed, party, role = cells[0], cells[1], cells[2], cells[3], cells[4]
            parties = f"{party} ({role})" if role else party
        elif len(cells) == 4:  # case-search view
            case_number, parties, classification, filed = cells
        else:
            continue
        cases.append(LienCasePayload(case_number=case_number, classification=classification, case_type=classification,
                                     parties=parties, filing_date=parse_date(filed), source_url=URL, search_term=search_term))
    return cases


class DelcoCivilAdapter(CivilCaptureMixin):
    descriptor = DESCRIPTOR
    parser_version = "ctrack-browser/1"
    relevance = staticmethod(delco_relevant)
    browser_capable = True

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

    def browse(self, runner, prop: PropertyRef, secrets: dict[str, str] | None = None) -> SourceOutcome:
        from app.connectors.browser import page_evidence

        terms = party_search_terms(prop.owner_name)
        if not terms:
            return SourceOutcome(LookupStatus.NO_MATCH, "No owner name to search", payload=[])
        all_cases: list[LienCasePayload] = []
        evidence = []
        find_party_field = ("()=>{for(const l of document.querySelectorAll('label')){const t=l.innerText.trim();"
                            "if(/^party name$/i.test(t)){const f=l.getAttribute('for'); if(f) return f;}}return null;}")
        with runner.page() as page:
            page.goto(URL, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            page.get_by_text(re.compile("CONTINUE AS PUBLIC USER", re.I)).first.click()
            page.wait_for_timeout(2000)
            for term in terms[:6]:
                page.goto(URL.rstrip("/") + "/search/party", wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                field = page.evaluate(find_party_field)
                if not field:
                    continue
                page.fill(f"#{field}", ctrack_party_name(term))
                page.get_by_role("button", name=re.compile("^search$", re.I)).last.click()
                page.wait_for_timeout(6000)
                try:
                    page.wait_for_selector("table tbody tr", timeout=15000)
                except Exception:  # noqa: BLE001
                    continue
                rows = page.eval_on_selector_all(
                    "table tbody tr",
                    "els=>els.map(r=>Array.from(r.querySelectorAll('td')).map(c=>c.innerText.trim()))")
                all_cases.extend(parse_ctrack_rows(rows, ctrack_party_name(term)))
                evidence.append(page_evidence(page, f"C-Track Party Search '{ctrack_party_name(term)}'"))
        # Deduplicate by case number, keep only lien classifications.
        seen: dict[str, LienCasePayload] = {}
        for case in all_cases:
            seen.setdefault(case.case_number, case)
        relevant = [(c, why) for c in seen.values() for keep, why in [self.relevance(c)] if keep]
        message = f"Searched {len(terms)} name(s); {len(seen)} case(s), {len(relevant)} lien case(s) kept"
        status = LookupStatus.SUCCESS if relevant else LookupStatus.NO_MATCH
        return SourceOutcome(status, message, payload=relevant, evidence=evidence)
