"""Persist recorder documents, run satisfaction matching, record reviewer decisions."""

from __future__ import annotations

import hashlib
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.connectors.payloads import RecorderDocumentPayload
from app.db import utcnow
from app.enums import DocCategory, MatchDecision, MortgageStatus
from app.models import MortgageRecord, Property, RecorderDocument, SatisfactionMatch
from app.recorder.matching import (
    DocView,
    MatchCandidate,
    MortgageEvaluation,
    apply_user_decisions,
    classify_document,
    evaluate_mortgages,
)


def doc_key(doc: RecorderDocumentPayload) -> str:
    if doc.instrument_number:
        return doc.instrument_number.strip().upper()[:160]
    if doc.book and doc.page:
        return f"BK{doc.book.lstrip('0')}-PG{doc.page.lstrip('0')}"
    basis = f"{doc.doc_type_raw}|{doc.recorded_date}|{doc.grantors}|{doc.grantees}|{doc.amount}"
    return "H" + hashlib.sha1(basis.encode()).hexdigest()[:20]


def save_documents(session: Session, prop: Property, source_key: str, docs: list[RecorderDocumentPayload],
                   evidence_id: int | None, entry_method: str) -> tuple[int, int]:
    existing = {d.doc_key: d for d in session.scalars(
        select(RecorderDocument).where(RecorderDocument.property_id == prop.id, RecorderDocument.source_key == source_key))}
    created = updated = 0
    for payload in docs:
        key = doc_key(payload)
        category = classify_document(payload.doc_type_raw, payload.instrument_number)
        row = existing.get(key)
        values = dict(instrument_number=payload.instrument_number, book=payload.book, page=payload.page,
                      doc_type_raw=payload.doc_type_raw, doc_category=category, recorded_date=payload.recorded_date,
                      grantors=payload.grantors, grantees=payload.grantees, amount=payload.amount,
                      related_reference=payload.related_reference, source_url=payload.source_url)
        if row is None:
            row = RecorderDocument(property_id=prop.id, source_key=source_key, doc_key=key, evidence_id=evidence_id,
                                   entry_method=entry_method, **values)
            session.add(row)
            existing[key] = row
            created += 1
        else:
            changed = any(getattr(row, k) != v for k, v in values.items() if v is not None)
            for k, v in values.items():
                if v is not None:
                    setattr(row, k, v)
            if changed:
                row.evidence_id = evidence_id
                updated += 1
    session.flush()
    return created, updated


def evaluate_property_mortgages(session: Session, prop: Property, since_year: int, searched: bool = True) -> list[MortgageRecord]:
    documents = session.scalars(select(RecorderDocument).where(RecorderDocument.property_id == prop.id)).all()
    views = [DocView(d.id, d.doc_category, d.doc_type_raw, d.instrument_number, d.book, d.page, d.recorded_date,
                     d.grantors, d.grantees, d.amount, d.related_reference) for d in documents]
    evaluations = evaluate_mortgages(views, since=date(since_year, 1, 1), searched=searched)
    by_doc = {d.id: d for d in documents}
    current = {m.document_id: m for m in session.scalars(select(MortgageRecord).where(MortgageRecord.property_id == prop.id))}
    keep_ids: set[int] = set()
    for ev in evaluations:
        doc = by_doc[ev.mortgage_id]
        mortgage = current.get(doc.id)
        if mortgage is None:
            mortgage = MortgageRecord(property_id=prop.id, document_id=doc.id, status=ev.status)
            session.add(mortgage)
            session.flush()
        user_matches = {m.satisfaction_document_id: m for m in mortgage.matches
                        if m.decision in (MatchDecision.USER_CONFIRMED, MatchDecision.USER_REJECTED)}
        for match in list(mortgage.matches):
            if match.satisfaction_document_id not in user_matches:
                session.delete(match)
        session.flush()
        confirmed = {sid for sid, m in user_matches.items() if m.decision == MatchDecision.USER_CONFIRMED}
        rejected = {sid for sid, m in user_matches.items() if m.decision == MatchDecision.USER_REJECTED}
        final = apply_user_decisions(ev, confirmed, rejected) if user_matches else ev
        for cand in final.candidates:
            if cand.satisfaction_id in user_matches:
                continue
            session.add(SatisfactionMatch(mortgage_id=mortgage.id, satisfaction_document_id=cand.satisfaction_id, score=cand.score,
                                          method=cand.method, reasons=cand.reasons, decision=cand.decision))
        mortgage.recorded_date = doc.recorded_date
        mortgage.original_amount = doc.amount
        mortgage.lender = doc.grantees
        mortgage.borrower = doc.grantors
        mortgage.status = final.status
        mortgage.status_reason = final.reason
        mortgage.evaluated_at = utcnow()
        keep_ids.add(mortgage.id)
    for mortgage in current.values():
        if mortgage.id not in keep_ids:
            session.delete(mortgage)
    session.flush()
    return session.scalars(select(MortgageRecord).where(MortgageRecord.property_id == prop.id)).all()


def review_match(session: Session, mortgage: MortgageRecord, satisfaction_document_id: int, confirm: bool, actor: str,
                 note: str | None, since_year: int) -> MortgageRecord:
    match = next((m for m in mortgage.matches if m.satisfaction_document_id == satisfaction_document_id), None)
    decision = MatchDecision.USER_CONFIRMED if confirm else MatchDecision.USER_REJECTED
    if match is None:
        doc = session.get(RecorderDocument, satisfaction_document_id)
        if doc is None or doc.property_id != mortgage.property_id or doc.doc_category not in (DocCategory.SATISFACTION, DocCategory.RELEASE):
            raise ValueError("Satisfaction document not found for this property")
        match = SatisfactionMatch(mortgage_id=mortgage.id, satisfaction_document_id=satisfaction_document_id, score=1.0,
                                  method="reviewer", reasons=[note or "Linked manually by reviewer"], decision=decision)
        session.add(match)
    match.decision = decision
    match.decided_by = actor
    match.decided_at = utcnow()
    if note:
        match.reasons = (match.reasons or []) + [f"Reviewer note: {note}"]
    session.flush()
    session.refresh(mortgage)
    ev = MortgageEvaluation(mortgage.id, mortgage.status, mortgage.status_reason or "",
                            [MatchCandidate(m.satisfaction_document_id, m.score, m.method, m.reasons or [], m.decision) for m in mortgage.matches])
    confirmed = {m.satisfaction_document_id for m in mortgage.matches if m.decision == MatchDecision.USER_CONFIRMED}
    rejected = {m.satisfaction_document_id for m in mortgage.matches if m.decision == MatchDecision.USER_REJECTED}
    final = apply_user_decisions(ev, confirmed, rejected)
    mortgage.status = final.status
    mortgage.status_reason = final.reason
    mortgage.reviewed_by = actor
    mortgage.reviewed_at = utcnow()
    mortgage.review_note = note
    record_audit(session, actor, "mortgage.match_review", "mortgage_record", mortgage.id, None,
                 {"satisfaction_document_id": satisfaction_document_id, "decision": decision, "note": note, "status": final.status})
    return mortgage


def set_mortgage_status(session: Session, mortgage: MortgageRecord, status: str | None, actor: str, note: str) -> MortgageRecord:
    if status is not None and status not in {s.value for s in MortgageStatus}:
        raise ValueError(f"Invalid status {status!r}")
    mortgage.user_status = status
    mortgage.reviewed_by = actor
    mortgage.reviewed_at = utcnow()
    mortgage.review_note = note
    record_audit(session, actor, "mortgage.status_override", "mortgage_record", mortgage.id, None, {"status": status, "note": note})
    return mortgage
