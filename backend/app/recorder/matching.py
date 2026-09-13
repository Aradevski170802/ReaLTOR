"""Transparent mortgage-to-satisfaction matching.

Principles
* A mortgage is "Satisfied" only on strong evidence: the satisfaction references the mortgage
  (instrument number or book/page), or lender + borrower + amount all agree with a unique candidate.
* Weaker agreement produces "Potential match — review required"; it is never treated as unsatisfied.
* "No satisfaction found" is reported only when the recorder index was searched and no satisfaction or
  release has any plausible link to the mortgage.
* "Insufficient data" when the mortgage itself lacks the date or parties needed to evaluate it.
Every decision carries human-readable reasons.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from app.common.parsing import normalize_name
from app.enums import DocCategory, MatchDecision, MortgageStatus

STOP_WORDS = {
    "THE", "OF", "AND", "&", "INC", "LLC", "CORP", "CORPORATION", "CO", "COMPANY", "NA", "N", "A", "FSB", "BANK",
    "NATIONAL", "ASSOCIATION", "ASSN", "MORTGAGE", "MTG", "FEDERAL", "SAVINGS", "LOAN", "TRUST", "FINANCIAL", "SERVICES",
    "SERVICING", "HOME", "LOANS", "FUNDING", "CREDIT", "UNION", "CU", "AS", "NOMINEE", "FOR", "ITS", "SUCCESSORS",
    "ASSIGNS", "SUCCESSOR", "BY", "MERGER", "TO", "FKA", "AKA", "DBA", "ET", "AL", "ETAL", "SER", "TR", "TRUSTEE",
}
MERS_TOKENS = {"MERS", "ELECTRONIC", "REGISTRATION", "SYSTEMS"}

AUTO_CONFIRM_SCORE = 0.85
CANDIDATE_SCORE = 0.45


@dataclass
class DocView:
    id: int
    category: str
    doc_type_raw: str | None
    instrument_number: str | None
    book: str | None
    page: str | None
    recorded_date: date | None
    grantors: str | None
    grantees: str | None
    amount: float | None
    related_reference: str | None


@dataclass
class MatchCandidate:
    satisfaction_id: int
    score: float
    method: str
    reasons: list[str]
    decision: str


@dataclass
class MortgageEvaluation:
    mortgage_id: int
    status: str
    reason: str
    candidates: list[MatchCandidate] = field(default_factory=list)


def classify_document(doc_type_raw: str | None, instrument_label: str | None = None) -> str:
    text = f"{doc_type_raw or ''} {instrument_label or ''}".upper()
    if re.search(r"\bSAT(ISFACTION)?\b|SATISFACTION|\bSATIS\b|RECONVEYANCE", text):
        return DocCategory.SATISFACTION
    if "ASSIGN" in text or re.match(r"^\s*(ASGN|ASN)\b", text):
        return DocCategory.ASSIGNMENT
    if re.search(r"RELEASE|DISCHARGE", text):
        return DocCategory.RELEASE
    if "MODIF" in text:
        return DocCategory.MODIFICATION
    if "SUBORDINAT" in text:
        return DocCategory.SUBORDINATION
    if re.search(r"MORTGAGE|\bMTG\b|\bMORT\b", text):
        return DocCategory.MORTGAGE
    if re.search(r"\bDEED\b", text):
        return DocCategory.DEED
    return DocCategory.OTHER


def _significant(name: str | None) -> set[str]:
    tokens = set(normalize_name(name).split())
    return {t for t in tokens if t not in STOP_WORDS and len(t) > 1}


def _parties(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in re.split(r";|\|", value) if p.strip()]


def _fuzzy_overlap(ta: set[str], tb: set[str]) -> int:
    """Count tokens that agree exactly or, for longer tokens, within a small typo distance (index typos are common)."""
    matched, used = 0, set()
    for token in ta:
        if token in tb and token not in used:
            matched += 1
            used.add(token)
            continue
        for other in tb - used:
            if len(token) >= 5 and len(other) >= 5 and SequenceMatcher(None, token, other).ratio() >= 0.85:
                matched += 1
                used.add(other)
                break
    return matched


def name_similarity(a: str | None, b: str | None) -> float:
    best = 0.0
    for pa in _parties(a) or [a or ""]:
        ta = _significant(pa)
        for pb in _parties(b) or [b or ""]:
            tb = _significant(pb)
            if not ta or not tb:
                continue
            if ta <= MERS_TOKENS or tb <= MERS_TOKENS:
                continue
            overlap = _fuzzy_overlap(ta, tb)
            score = overlap / (len(ta) + len(tb) - overlap)
            containment = overlap / min(len(ta), len(tb))
            best = max(best, score, 0.9 * containment if containment == 1.0 else score)
    return round(best, 3)


def _refs(text: str | None) -> tuple[set[str], set[tuple[int, int]]]:
    if not text:
        return set(), set()
    upper = text.upper()
    instruments = set(re.findall(r"\b\d{7,12}\b", upper))
    book_pages = {(int(b), int(p)) for b, p in re.findall(r"(?:BK|BOOK)\s*(\d+)\s*(?:PG|PAGE)\s*(\d+)", upper)}
    book_pages |= {(int(b), int(p)) for b, p in re.findall(r"\b(?:MTG|MORT|MORTGAGE)\s+(\d{1,6})\s+(\d{1,6})\b", upper)}
    return instruments, book_pages


def _mortgage_keys(m: DocView) -> tuple[set[str], set[tuple[int, int]]]:
    instruments = set()
    if m.instrument_number and re.fullmatch(r"\d{7,12}", m.instrument_number.strip()):
        instruments.add(m.instrument_number.strip())
    book_pages = set()
    if m.book and m.page and m.book.isdigit() and m.page.isdigit():
        book_pages.add((int(m.book), int(m.page)))
    return instruments, book_pages


def evaluate_mortgages(documents: list[DocView], since: date = date(1990, 1, 1),
                       searched: bool = True) -> list[MortgageEvaluation]:
    mortgages = [d for d in documents if d.category == DocCategory.MORTGAGE and (d.recorded_date is None or d.recorded_date >= since)]
    satisfactions = [d for d in documents if d.category in (DocCategory.SATISFACTION, DocCategory.RELEASE)]
    assignments = [d for d in documents if d.category == DocCategory.ASSIGNMENT]

    scored: dict[int, list[MatchCandidate]] = {}
    for m in mortgages:
        m_instruments, m_book_pages = _mortgage_keys(m)
        lenders = {m.grantees or ""}
        for a in assignments:
            a_inst, a_bp = _refs(a.related_reference)
            refs_mortgage = bool(a_inst & m_instruments or a_bp & m_book_pages)
            if refs_mortgage or (name_similarity(a.grantors, m.grantees) >= 0.6 and name_similarity(a.grantees, m.grantors) < 0.5):
                if a.recorded_date is None or m.recorded_date is None or a.recorded_date >= m.recorded_date:
                    lenders.add(a.grantees or "")
        candidates: list[MatchCandidate] = []
        for s in satisfactions:
            if m.recorded_date and s.recorded_date and s.recorded_date < m.recorded_date:
                continue
            reasons: list[str] = []
            s_inst, s_bp = _refs(s.related_reference)
            if s_inst & m_instruments or s_bp & m_book_pages:
                ref = sorted(s_inst & m_instruments) or [f"BK {b} PG {p}" for b, p in sorted(s_bp & m_book_pages)]
                reasons.append(f"Satisfaction references the mortgage ({', '.join(map(str, ref))})")
                is_release = s.category == DocCategory.RELEASE
                decision = MatchDecision.CANDIDATE if is_release else MatchDecision.AUTO_CONFIRMED
                if is_release:
                    reasons.append("Document is a release (may be partial) — confirm it discharges the full mortgage")
                candidates.append(MatchCandidate(s.id, 1.0, "reference", reasons, decision))
                continue
            if s_inst or s_bp:
                continue  # the satisfaction explicitly references a different instrument
            lender_sim = max((name_similarity(s.grantors, lender) for lender in lenders), default=0.0)
            borrower_sim = name_similarity(s.grantees, m.grantors)
            amount_match = m.amount is not None and s.amount is not None and abs(m.amount - s.amount) < 1.0
            score = 0.5 * lender_sim + 0.3 * borrower_sim + (0.2 if amount_match else 0.0)
            borrower_only = borrower_sim >= 0.8 and lender_sim < 0.5
            if score < CANDIDATE_SCORE and not borrower_only:
                continue
            if borrower_only:
                reasons.append("Borrower matches but the releasing party differs from the recorded lender/assignees "
                               "(possible servicer or unindexed assignment)")
            reasons.append(f"Lender/assignee name similarity {lender_sim:.2f}")
            reasons.append(f"Borrower name similarity {borrower_sim:.2f}")
            if amount_match:
                reasons.append(f"Amount matches (${m.amount:,.2f})")
            elif m.amount is not None and s.amount is not None:
                reasons.append(f"Amounts differ (${m.amount:,.2f} vs ${s.amount:,.2f})")
            strong = lender_sim >= 0.7 and borrower_sim >= 0.5 and amount_match and s.category == DocCategory.SATISFACTION
            decision = MatchDecision.AUTO_CONFIRMED if strong and score >= AUTO_CONFIRM_SCORE else MatchDecision.CANDIDATE
            candidates.append(MatchCandidate(s.id, round(score, 3), "parties", reasons, decision))
        candidates.sort(key=lambda c: c.score, reverse=True)
        scored[m.id] = candidates

    # A satisfaction may confirm only one mortgage. When it is the strongest candidate for several,
    # keep auto-confirmation only for a unique top score; demote the rest to review.
    claims: dict[int, list[tuple[float, int]]] = {}
    for mid, cands in scored.items():
        for c in cands:
            if c.decision == MatchDecision.AUTO_CONFIRMED:
                claims.setdefault(c.satisfaction_id, []).append((c.score, mid))
    for sid, owners in claims.items():
        if len(owners) <= 1:
            continue
        owners.sort(reverse=True)
        top_score = owners[0][0]
        tied = [mid for score, mid in owners if score == top_score]
        winners = set(tied[:1]) if len(tied) == 1 else set()
        for _, mid in owners:
            if mid in winners:
                continue
            for c in scored[mid]:
                if c.satisfaction_id == sid and c.decision == MatchDecision.AUTO_CONFIRMED:
                    c.decision = MatchDecision.CANDIDATE
                    c.reasons.append("Same satisfaction also matches another mortgage — review required")

    results: list[MortgageEvaluation] = []
    for m in mortgages:
        cands = scored[m.id]
        if m.recorded_date is None or not (m.grantors or m.grantees or m.instrument_number or (m.book and m.page)):
            status, reason = MortgageStatus.INSUFFICIENT, "Mortgage is missing its recording date or party/instrument details"
            if cands:
                reason += f"; {len(cands)} possible satisfaction(s) listed for review"
        elif any(c.decision == MatchDecision.AUTO_CONFIRMED for c in cands):
            best = next(c for c in cands if c.decision == MatchDecision.AUTO_CONFIRMED)
            status, reason = MortgageStatus.SATISFIED, "; ".join(best.reasons)
        elif cands:
            status = MortgageStatus.REVIEW
            reason = f"{len(cands)} possible satisfaction(s); best score {cands[0].score:.2f}: " + "; ".join(cands[0].reasons)
        elif not searched:
            status, reason = MortgageStatus.INSUFFICIENT, "Recorder index was not fully searched for satisfactions"
        else:
            status = MortgageStatus.NO_SATISFACTION
            reason = f"No satisfaction or release in the searched index ({len(satisfactions)} checked) links to this mortgage"
        results.append(MortgageEvaluation(m.id, status, reason, cands))
    return results


def apply_user_decisions(evaluation: MortgageEvaluation, confirmed: set[int], rejected: set[int]) -> MortgageEvaluation:
    """Re-derive status after a reviewer confirms or rejects specific satisfaction candidates (inputs are not mutated)."""
    remaining = [
        MatchCandidate(c.satisfaction_id, c.score, c.method, list(c.reasons),
                       MatchDecision.USER_CONFIRMED if c.satisfaction_id in confirmed else c.decision)
        for c in evaluation.candidates if c.satisfaction_id not in rejected
    ]
    if any(c.decision == MatchDecision.USER_CONFIRMED for c in remaining):
        return MortgageEvaluation(evaluation.mortgage_id, MortgageStatus.SATISFIED, "Satisfaction confirmed by reviewer", remaining)
    if any(c.decision == MatchDecision.AUTO_CONFIRMED for c in remaining):
        return MortgageEvaluation(evaluation.mortgage_id, MortgageStatus.SATISFIED, evaluation.reason, remaining)
    if remaining:
        return MortgageEvaluation(evaluation.mortgage_id, MortgageStatus.REVIEW, evaluation.reason, remaining)
    if evaluation.status == MortgageStatus.INSUFFICIENT:
        return evaluation
    return MortgageEvaluation(evaluation.mortgage_id, MortgageStatus.NO_SATISFACTION,
                              "All candidate satisfactions were rejected by a reviewer", remaining)
