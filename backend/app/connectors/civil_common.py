"""Shared civil / lien case capture behaviour."""

from __future__ import annotations

import re

from app.connectors.base import SourceAdapter, SourceOutcome
from app.connectors.capture_tables import CIVIL_SYNONYMS, extract_tables, rows_from_tables, to_lien_cases
from app.connectors.payloads import LienCasePayload
from app.enums import LookupStatus


def montco_relevant(case: LienCasePayload) -> tuple[bool, str]:
    status = (case.status or "").upper()
    kind = f"{case.case_type or ''} {case.classification or ''}".upper()
    is_open = bool(re.search(r"\b1\s*-\s*OPEN\b", status)) or status.strip() == "OPEN"
    is_lien = "LIEN" in kind
    if is_open and is_lien:
        return True, "Status '1 - Open' and case type includes 'Lien'"
    missing = [x for x, ok in (("status is not '1 - Open'", is_open), ("case type lacks 'Lien'", is_lien)) if not ok]
    return False, "; ".join(missing)


def delco_relevant(case: LienCasePayload) -> tuple[bool, str]:
    text = f"{case.classification or ''} {case.case_type or ''}".upper()
    if "LIEN" in text:
        return True, "Case classification includes 'Lien'"
    return False, "Case classification does not include 'Lien'"


class CivilCaptureMixin(SourceAdapter):
    relevance = staticmethod(montco_relevant)
    required_columns: set[str] = {"case_number"}

    def parse_capture(self, prop, content: str) -> SourceOutcome:
        rows = rows_from_tables(extract_tables(content), CIVIL_SYNONYMS, self.required_columns)
        cases = to_lien_cases(rows)
        if not rows:
            if re.search(r"no\s+(records|results|cases)\s+(were\s+)?found", content, re.I):
                return SourceOutcome(LookupStatus.NO_MATCH, "Search returned no cases", payload=[])
            return SourceOutcome(LookupStatus.PARSE_FAILURE,
                                 "No case rows recognised; expected columns like Case Number | Status | Case Type/Classification | Parties | Filing Date")
        relevant = []
        for case in cases:
            keep, why = self.relevance(case)
            if keep:
                relevant.append((case, why))
        message = f"{len(cases)} case(s) captured; {len(relevant)} relevant lien case(s) kept"
        return SourceOutcome(LookupStatus.SUCCESS if relevant else LookupStatus.NO_MATCH, message, payload=relevant)
