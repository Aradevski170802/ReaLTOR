"""Montgomery County judgments via the PA Unified Judicial System (UJS) web portal — Participant Name search.

The statewide UJS portal (ujsportal.pacourts.us) has no CAPTCHA, no robots.txt restriction and a Terms page that is a
liability disclaimer only, so a real browser can run its public Case Search. For Montgomery County it returns
**Magisterial District civil** dockets (small-claims money judgments, landlord/tenant) — NOT Court of Common Pleas
municipal liens (those live in the Prothonotary/PSI portal, which is human-verification gated and never bypassed).

These are surfaced as a distinct source: a civil money judgment against a property owner is a genuine distress signal
and can become a judgment lien, but it is not a recorded municipal lien. Only cases where the owner appears on the
defendant side of the caption are kept, and every one is flagged for review because a name match is not an identity
match. Without browser automation it falls back to a user-assisted capture task.
"""

from __future__ import annotations

import re

from app.common.parsing import parse_date
from app.connectors.base import CaptureRequest, PropertyRef, RetryPolicy, SearchDef, SourceDescriptor, SourceOutcome
from app.connectors.civil_common import CivilCaptureMixin
from app.connectors.names import party_search_terms
from app.connectors.payloads import LienCasePayload
from app.enums import AccessMethod, LookupStatus

URL = "https://ujsportal.pacourts.us/CaseSearch"
BASE = "https://ujsportal.pacourts.us"

DESCRIPTOR = SourceDescriptor(
    key="montco.ujs_judgments",
    county="montco",
    name="PA UJS Portal — Montgomery Magisterial District civil judgments (Participant Name)",
    category="civil",
    # Browser-driven public portal (no login, no CAPTCHA); access_method stays USER_ASSISTED so it is never treated as
    # an HTTP API, but the app drives it with a real browser when browser automation is enabled.
    access_method=AccessMethod.USER_ASSISTED,
    base_urls=(URL,),
    searches=(SearchDef("Participant Name search (County=Montgomery, Docket Type=Civil)", URL, "Owner 'LAST' + 'FIRST'"),),
    input_requirements="Owner name from the sale list / assessment record",
    parcel_rule="Not searched by parcel; owner party names are derived (Last + First)",
    field_mappings={"Docket Number": "case_number", "Court Type": "case_type", "Case Caption": "parties",
                    "Case Status": "status", "Filing Date": "filing_date"},
    rate_limit_per_minute=20,
    min_interval_seconds=2.0,
    cache_ttl_hours=24 * 7,
    retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=3.0),
    error_classification={"no cases": "no_match", "unexpected page": "parse_failure", "browser unavailable": "user_action_needed"},
    compliance_status="automated_browser_no_captcha",
    terms_notes=(
        "Public Case Search on the PA UJS portal: no login, no CAPTCHA, no robots.txt restriction; the Terms page is a "
        "liability disclaimer. Returns Montgomery MAGISTERIAL DISTRICT civil dockets (money judgments, landlord/tenant) "
        "— not Common Pleas municipal liens. Only cases with the owner on the defendant side are kept, each flagged for "
        "review (a name match is not an identity match)."
    ),
    terms_url="https://ujsportal.pacourts.us/Home/Terms",
    access_findings=(
        "2026-09-15: Case Search → Participant Name with County=Montgomery, Docket Type=Civil returns a grid "
        "(#caseSearchResultGrid) of Docket Number / Court Type / Case Caption / Case Status / Filing Date / Primary "
        "Participant(s); no CAPTCHA. Montgomery 'Civil' results are Magisterial District (MJ-...) dockets.",
    ),
)

DOCKET_RE = re.compile(r"^(?:MJ|CP|MC)-\S+")
VS_RE = re.compile(r"\s+v\.?s?\.?\s+", re.I)


def ujs_participant_names(owner_name: str | None) -> list[tuple[str, str]]:
    """(last, first) pairs for the UJS Participant Name search, from the derived person party terms."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for term in party_search_terms(owner_name):
        if term.kind != "person":
            continue
        parts = term.party_name.split()
        last, first = parts[0], (parts[1] if len(parts) > 1 else "")
        if (last, first) not in seen:
            seen.add((last, first))
            pairs.append((last, first))
    return pairs


def caption_defendant(caption: str) -> str:
    """The defendant side of a 'Plaintiff v. Defendant' caption (the whole caption if there is no separator)."""
    parts = VS_RE.split(caption or "", maxsplit=1)
    return parts[1] if len(parts) == 2 else (caption or "")


def ujs_relevant(case: LienCasePayload) -> tuple[bool, str]:
    """Keep a case only when the searched owner appears on the defendant side (a possible money judgment against them)."""
    last = (case.search_term or "").upper().strip()
    defendant = caption_defendant(case.parties or "").upper()
    if last and last in defendant:
        return True, "Owner appears as defendant in a Montgomery MDJ civil case (possible money judgment; verify on the docket)"
    return False, "Owner is not the defendant (informational only)"


def parse_ujs_rows(rows: list[dict], search_last: str) -> list[LienCasePayload]:
    """Parse UJS result rows into judgment payloads. Each row is {'cells': [...], 'url': docket-sheet href or None}.

    Columns are read as offsets from the docket-number cell (leading index/expander columns vary): docket, court type,
    caption, status, filing date, primary participants.
    """
    cases: list[LienCasePayload] = []
    for row in rows:
        cells = [c.strip() for c in row.get("cells", [])]
        d = next((i for i, c in enumerate(cells) if DOCKET_RE.match(c)), None)
        if d is None:
            continue

        def at(offset: int, _d=d, _cells=cells) -> str:
            j = _d + offset
            return _cells[j] if 0 <= j < len(_cells) else ""

        docket = cells[d]
        caption = at(2)
        href = row.get("url")
        source_url = (BASE + href) if href and href.startswith("/") else (href or URL)
        cases.append(LienCasePayload(
            case_number=docket, status=at(3) or None, classification="Magisterial District Civil",
            case_type=at(1) or "Magisterial District", parties=caption or None,
            filing_date=parse_date(at(4)), source_url=source_url, search_term=search_last))
    return cases


# JS run in the page to fill the Select2-backed search form and to read the results grid.
_FILL_JS = """
(args) => {
  const [last, first] = args;
  const vis = el => el && el.offsetParent !== null;
  const selByOpt = v => [...document.querySelectorAll('select')].find(s => [...s.options].some(o => o.value === v));
  const fire = el => el.dispatchEvent(new Event('change', {bubbles: true}));
  const sb = selByOpt('ParticipantName');
  if (sb) { sb.value = 'ParticipantName'; fire(sb); }
  return true;
}
"""

_FILL2_JS = """
(args) => {
  const [last, first] = args;
  const vis = el => el && el.offsetParent !== null;
  const fire = el => el.dispatchEvent(new Event('change', {bubbles: true}));
  const county = [...document.querySelectorAll('select')].find(s => vis(s) && [...s.options].some(o => o.text === 'Montgomery'));
  if (county) { county.value = 'Montgomery'; fire(county); }
  const docket = [...document.querySelectorAll('select')].find(s => vis(s) && [...s.options].some(o => o.value === 'Civil'));
  if (docket) { docket.value = 'Civil'; fire(docket); }
  const ln = document.querySelector('input[name="ParticipantLastName"]');
  const fn = document.querySelector('input[name="ParticipantFirstName"]');
  if (ln) ln.value = last;
  if (fn) fn.value = first || '';
  return !!(ln && county && docket);
}
"""

_ROWS_JS = """
els => els.map(r => ({
  cells: [...r.querySelectorAll('td')].map(c => c.innerText.trim()),
  url: (r.querySelector('a[href*="DocketSheet"]') || {}).getAttribute
       ? r.querySelector('a[href*="DocketSheet"]').getAttribute('href') : null
}))
"""


class MontcoUjsJudgmentsAdapter(CivilCaptureMixin):
    descriptor = DESCRIPTOR
    parser_version = "ujs-browser/1"
    relevance = staticmethod(ujs_relevant)
    browser_capable = True

    def capture_request(self, prop: PropertyRef) -> CaptureRequest:
        pairs = ujs_participant_names(prop.owner_name)
        hint = "; ".join(f"{last}, {first}".strip(", ") for last, first in pairs) or "Owner name unavailable"
        return CaptureRequest(
            url=URL,
            search_hint=f"Participant Name searches (County=Montgomery, Docket Type=Civil): {hint}",
            instructions=(
                "On the UJS Case Search, set Search By = 'Participant Name', County = 'Montgomery', Docket Type = "
                "'Civil', then run one search per name above. Paste each results grid below (Docket Number, Court Type, "
                "Case Caption, Case Status, Filing Date). These are Magisterial District civil judgments, not municipal "
                "liens — review each retained case; a name match is not an identity match."
            ),
        )

    def browse(self, runner, prop: PropertyRef, secrets: dict[str, str] | None = None) -> SourceOutcome:
        from app.connectors.browser import page_evidence

        pairs = ujs_participant_names(prop.owner_name)
        if not pairs:
            return SourceOutcome(LookupStatus.NO_MATCH, "No person owner name to search", payload=[])
        all_cases: list[LienCasePayload] = []
        evidence = []
        with runner.page() as page:
            for last, first in pairs[:6]:
                page.goto(URL, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                page.evaluate(_FILL_JS, [last, first])
                page.wait_for_timeout(1200)
                if not page.evaluate(_FILL2_JS, [last, first]):
                    continue
                page.click("#btnSearch")
                page.wait_for_timeout(7000)
                try:
                    page.wait_for_selector("#caseSearchResultGrid tbody tr", timeout=15000)
                except Exception:  # noqa: BLE001
                    evidence.append(page_evidence(page, f"UJS Participant Search '{last}, {first}' (no grid)"))
                    continue
                rows = page.eval_on_selector_all("#caseSearchResultGrid tbody tr", _ROWS_JS)
                all_cases.extend(parse_ujs_rows(rows, last))
                evidence.append(page_evidence(page, f"UJS Participant Search '{last}, {first}'"))
        seen: dict[str, LienCasePayload] = {}
        for case in all_cases:
            seen.setdefault(case.case_number, case)
        relevant = [(c, why) for c in seen.values() for keep, why in [self.relevance(c)] if keep]
        message = f"Searched {len(pairs)} name(s); {len(seen)} MDJ civil case(s), {len(relevant)} with owner as defendant"
        status = LookupStatus.SUCCESS if relevant else LookupStatus.NO_MATCH
        return SourceOutcome(status, message, payload=relevant, evidence=evidence)
