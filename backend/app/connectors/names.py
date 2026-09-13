"""Owner-name handling for party-name searches (Delco civil public access)."""

from __future__ import annotations

import re
from dataclasses import dataclass

ENTITY_WORDS = {
    "LLC", "L.L.C.", "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LP", "LLP", "LTD", "TRUST", "BANK",
    "ASSOCIATES", "ASSOCIATION", "ASSOC", "PARTNERS", "PARTNERSHIP", "HOLDINGS", "PROPERTIES", "PROPERTY", "INVESTMENTS",
    "INVESTMENT", "INVESTORS", "GROUP", "FUND", "FOUNDATION", "CHURCH", "REALTY", "MANAGEMENT", "SERVICES", "ENTERPRISES",
    "DEVELOPMENT", "CAPITAL", "VENTURES", "AUTHORITY", "CITY", "COUNTY", "BOROUGH", "TOWNSHIP", "SCHOOL", "HOUSING",
    "MINISTRIES", "CLUB", "SOCIETY", "LEAGUE", "UNION", "CONDOMINIUM", "HOMES", "BUILDERS", "LENDING", "MORTGAGE",
    "SOLUTIONS", "CONSTRUCTION", "RESTORATION", "TEMPLE", "MOSQUE", "CONGREGATION", "FEDERAL", "NATIONAL", "COMMONWEALTH",
}
ROLE_WORDS = {"TRUSTEE", "TRUSTEES", "EXECUTOR", "EXECUTRIX", "ADMINISTRATOR", "ADMINISTRATRIX", "EST", "ESTATE",
              "HEIRS", "ETAL", "ET", "AL", "AKA", "FKA", "DBA", "C/O"}
SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}


@dataclass(frozen=True)
class PartySearchTerm:
    kind: str  # person | entity
    party_name: str
    note: str | None = None


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", text.strip().upper()) if t]


def is_entity(text: str) -> bool:
    tokens = {t.strip(".,") for t in _tokens(text)}
    return bool(tokens & ENTITY_WORDS) or bool(re.search(r"\d", text))


def party_search_terms(owner_name: str | None) -> list[PartySearchTerm]:
    if not owner_name:
        return []
    text = re.sub(r"\s+", " ", owner_name.upper()).strip().rstrip("&").strip()
    terms: list[PartySearchTerm] = []

    # "X TRUSTEE OF THE Y TRUST" -> person X and entity Y TRUST
    trust = re.match(r"^(?P<person>.+?)\s+TRUSTEES?\s+OF\s+(?:THE\s+)?(?P<entity>.+)$", text)
    if trust:
        terms.extend(party_search_terms(trust.group("person")))
        terms.append(PartySearchTerm("entity", trust.group("entity").strip(), "Trust named on the deed"))
        return _dedupe(terms)

    if is_entity(text) and "&" not in text:
        name = " ".join(t for t in _tokens(text) if t not in {"C/O"})
        return [PartySearchTerm("entity", name, "Company name search")]

    parts = [p.strip() for p in re.split(r"\s*&\s*|\s+AND\s+", text) if p.strip()]
    surname: str | None = None
    for part in parts:
        if is_entity(part):
            terms.append(PartySearchTerm("entity", part, "Company name search"))
            continue
        tokens = [t.strip(".,") for t in _tokens(part)]
        roles = [t for t in tokens if t in ROLE_WORDS]
        suffix = [t for t in tokens if t in SUFFIXES]
        core = [t for t in tokens if t not in ROLE_WORDS and t not in SUFFIXES]
        if not core:
            continue
        if len(core) == 1 and surname:
            core = [surname] + core
        elif len(core) <= 2 and surname and len(core[0]) > 1 and all(len(t) == 1 for t in core[1:]) and len(parts) > 1:
            # "SALLY M" after "RUTKO WALTER" -> "RUTKO SALLY M"
            core = [surname] + core
        surname = core[0]
        last, rest = core[0], core[1:]
        first = rest[0] if rest else ""
        middle_initial = rest[1][0] if len(rest) > 1 else ""
        name = " ".join(x for x in (last, first, middle_initial) if x)
        notes = []
        if suffix:
            notes.append(f"suffix {' '.join(suffix)} omitted")
        if roles:
            notes.append(f"role {' '.join(roles)} omitted")
        terms.append(PartySearchTerm("person", name, "; ".join(notes) or None))
    return _dedupe(terms)


def _dedupe(terms: list[PartySearchTerm]) -> list[PartySearchTerm]:
    seen, out = set(), []
    for term in terms:
        if term.party_name not in seen:
            seen.add(term.party_name)
            out.append(term)
    return out
