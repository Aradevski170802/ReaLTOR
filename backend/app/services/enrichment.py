"""Enrichment orchestration and persistence of source outcomes (automated lookups and user captures)."""

from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.connectors.base import EvidenceDraft, SourceAdapter, SourceOutcome
from app.connectors.payloads import (
    AssessmentPayload,
    LienCasePayload,
    ParcelGisPayload,
    RecorderDocumentPayload,
    TaxClaimPayload,
)
from app.connectors.registry import get_connector, get_source
from app.db import utcnow
from app.enums import AccessMethod, EntryMethod, LookupStatus
from app.models import (
    AssessmentRecord,
    AssessmentSale,
    CaptureTask,
    CivilLienCase,
    FieldProvenance,
    Property,
    SourceLookup,
    TaxClaimRecord,
    TaxClaimYear,
    TaxStatusHistory,
)
from app.services.evidence import store_evidence
from app.services.policies import get_policy
from app.services.properties import property_ref, set_identifier, supersede
from app.services.recorder_service import evaluate_property_mortgages, save_documents
from app.services.rules_service import active_ruleset, evaluate_properties

log = logging.getLogger(__name__)

ASSESSMENT_COLUMNS = [
    "land_use_code", "land_use_description", "property_class", "property_type", "category", "school_district", "site_address",
    "legal_description", "municipality", "owner_name", "assessed_value", "assessed_land", "assessed_building", "lot_size_text",
    "acres", "lot_sqft", "building_style", "year_built", "exterior_wall", "living_area_sqft", "base_area_sqft", "total_rooms",
    "bedrooms", "full_baths", "half_baths", "last_sale_date", "last_sale_price", "gross_building_area", "total_living_units",
    "structure_description", "improvement_name", "commercial_living_area", "commercial_units", "commercial_year_built",
    "detail_link", "raw_codes",
]
RAW_CODE_FOR = {"land_use_description": "LAND_USE", "school_district": "SCH_DIST", "building_style": "STYLE",
                "exterior_wall": "EXTWALL", "structure_description": "STRUCTURE", "property_class": "CLASS"}
DEFAULT_CAPTURE = {
    "montco": ["montco.recorder", "montco.civil"],
    "delco": ["delco.recorder_countyweb", "delco.civil"],
}


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def _provenance(session: Session, prop_id: int, field: str, value: Any, raw: Any, source_key: str, source_name: str,
                source_url: str | None, evidence_id: int | None, quality: str, notes: str | None = None,
                confidence: float | None = None) -> None:
    session.query(FieldProvenance).filter(FieldProvenance.property_id == prop_id, FieldProvenance.field == field,
                                          FieldProvenance.source_key == source_key, FieldProvenance.is_current.is_(True)
                                          ).update({"is_current": False})
    session.add(FieldProvenance(property_id=prop_id, field=field, value=None if value is None else str(value),
                                raw_value=None if raw is None else str(raw), source_key=source_key, source_name=source_name,
                                source_url=source_url, evidence_id=evidence_id, retrieved_at=utcnow(), confidence=confidence,
                                quality=quality, notes=notes))


# --------------------------------------------------------------------------------------------- persistence


def save_assessment(session: Session, prop: Property, adapter: SourceAdapter, payload: AssessmentPayload, evidence_id: int | None,
                    evidence_url: str | None, entry_method: str, refresh_run_id: int | None = None) -> AssessmentRecord:
    key = adapter.descriptor.key
    supersede(session, AssessmentRecord, prop.id, key)
    record = AssessmentRecord(property_id=prop.id, source_key=key, evidence_id=evidence_id, entry_method=entry_method,
                              retrieved_at=utcnow(), **{c: getattr(payload, c) for c in ASSESSMENT_COLUMNS})
    for sale in payload.sales:
        record.sales.append(AssessmentSale(**asdict(sale)))
    session.add(record)
    raw_codes = payload.raw_codes or {}
    for column in ASSESSMENT_COLUMNS:
        value = getattr(payload, column)
        if value in (None, "", [], {}) or column in ("raw_codes", "detail_link", "land_use_code"):
            continue
        field = "lot_size" if column == "lot_size_text" else column
        _provenance(session, prop.id, field, value, raw_codes.get(RAW_CODE_FOR.get(column, ""), value), key, adapter.descriptor.name,
                    evidence_url, evidence_id, "manual" if entry_method == EntryMethod.MANUAL else "verified",
                    notes=payload.field_notes.get(column))
    if payload.detail_link and key == "delco.assessment_portal":
        set_identifier(session, prop, "detail_link", payload.detail_link, key)
    if payload.tax is not None:
        save_tax_claim(session, prop, adapter, payload.tax, evidence_id, evidence_url, entry_method, refresh_run_id)
    return record


def save_parcel_gis(session: Session, prop: Property, adapter: SourceAdapter, payload: ParcelGisPayload, evidence_id: int | None,
                    evidence_url: str | None) -> AssessmentRecord:
    key = adapter.descriptor.key
    supersede(session, AssessmentRecord, prop.id, key)
    record = AssessmentRecord(property_id=prop.id, source_key=key, evidence_id=evidence_id, entry_method=EntryMethod.AUTOMATED,
                              site_address=payload.site_address, acres=payload.acres, legal_description=payload.legal_description,
                              lot_size_text=payload.lot_size_text, detail_link=payload.detail_link,
                              raw_codes={"MAPID": payload.map_id, "MUNICIPALI": payload.municipality_code, "TAXYR": payload.tax_year})
    session.add(record)
    for field, value, quality, notes in (
        ("site_address", payload.site_address, "verified", None),
        ("acres", payload.acres, "estimated", "GIS-calculated acreage from parcel geometry (not assessor acreage)"),
        ("legal_description", payload.legal_description, "verified", None),
        ("lot_size", payload.lot_size_text, "verified", None),
    ):
        if value is not None:
            _provenance(session, prop.id, field, value, value, key, adapter.descriptor.name, evidence_url, evidence_id, quality, notes)
    set_identifier(session, prop, "detail_link", payload.detail_link, key)
    set_identifier(session, prop, "map_id", payload.map_id, key)
    return record


def save_tax_claim(session: Session, prop: Property, adapter: SourceAdapter, payload: TaxClaimPayload, evidence_id: int | None,
                   evidence_url: str | None, entry_method: str, refresh_run_id: int | None = None) -> TaxClaimRecord:
    key = adapter.descriptor.key
    previous = session.scalar(select(TaxClaimRecord).where(TaxClaimRecord.property_id == prop.id, TaxClaimRecord.source_key == key,
                                                           TaxClaimRecord.is_current.is_(True)))
    supersede(session, TaxClaimRecord, prop.id, key)
    record = TaxClaimRecord(property_id=prop.id, source_key=key, evidence_id=evidence_id, entry_method=entry_method,
                            retrieved_at=utcnow(), tax_sale_status=payload.tax_sale_status, status_category=payload.status_category,
                            delinquent_total=payload.delinquent_total, district=payload.district, description=payload.description,
                            assessed_value=payload.assessed_value, deed_book_page=payload.deed_book_page, as_of_text=payload.as_of_text,
                            notes=payload.notes)
    for y in payload.years:
        record.years.append(TaxClaimYear(year=y.year, kind=y.kind, face=y.face, penalty=y.penalty, interest=y.interest,
                                         total=y.total, paid=y.paid, balance=y.balance))
    session.add(record)
    y2024 = payload.year(2024)
    prev_2024 = next((y for y in previous.years if y.year == 2024 and y.kind == "claim"), None) if previous else None
    changed = previous is not None and (
        previous.status_category != payload.status_category
        or (prev_2024.balance if prev_2024 else None) != (y2024.balance if y2024 else None)
        or (previous.tax_sale_status or "") != (payload.tax_sale_status or "")
    )
    session.add(TaxStatusHistory(property_id=prop.id, source_key=key, lookup_status=LookupStatus.SUCCESS,
                                 status_text=payload.tax_sale_status, status_category=payload.status_category,
                                 balance_2024=y2024.balance if y2024 else None, delinquent_total=payload.delinquent_total,
                                 changed=changed, refresh_run_id=refresh_run_id))
    for field, value in (("tax_sale_status", payload.tax_sale_status), ("delinquent_total", payload.delinquent_total)):
        if value is not None:
            _provenance(session, prop.id, field, value, value, key, adapter.descriptor.name, evidence_url, evidence_id,
                        "verified", notes=payload.as_of_text)
    for y in payload.years:
        for attr in ("total", "balance"):
            value = getattr(y, attr)
            if value is None:
                continue
            field = f"tax_{y.year}_{attr}" if y.kind == "claim" else (f"tax_{y.year}_due" if attr == "balance" and y.kind == "delinquent" else None)
            if field:
                _provenance(session, prop.id, field, value, value, key, adapter.descriptor.name, evidence_url, evidence_id, "verified",
                            notes=f"Claim year {y.year} ({y.kind}): face {y.face}, penalty {y.penalty}, interest {y.interest}, paid {y.paid}")
    return record


def save_lien_cases(session: Session, prop: Property, adapter: SourceAdapter, cases: list[tuple[LienCasePayload, str]],
                    evidence_id: int | None, entry_method: str) -> int:
    key = adapter.descriptor.key
    existing = {c.case_number: c for c in session.scalars(select(CivilLienCase).where(CivilLienCase.property_id == prop.id,
                                                                                      CivilLienCase.source_key == key))}
    for case, why in cases:
        row = existing.get(case.case_number)
        values = dict(status=case.status, classification=case.classification, case_type=case.case_type, parties=case.parties,
                      filing_date=case.filing_date, amount=case.amount, source_url=case.source_url, search_term=case.search_term,
                      is_relevant=True, relevance_reason=why, evidence_id=evidence_id, entry_method=entry_method, retrieved_at=utcnow())
        if row is None:
            row = CivilLienCase(property_id=prop.id, source_key=key, case_number=case.case_number, **values)
            session.add(row)
            existing[case.case_number] = row
        else:
            for k, v in values.items():
                setattr(row, k, v)
    return len(cases)


def persist_outcome(session: Session, prop: Property, adapter: SourceAdapter, outcome: SourceOutcome, *, entry_method: str,
                    actor: str, job_id: int | None = None, refresh_run_id: int | None = None) -> SourceLookup:
    d = adapter.descriptor
    evidence_rows = [
        store_evidence(session, draft, source_key=d.key, source_name=d.name, access_method=str(d.access_method), property_id=prop.id,
                       project_id=prop.project_id, parser_version=adapter.parser_version, captured_by=None if entry_method == EntryMethod.AUTOMATED else actor)
        for draft in outcome.evidence
    ]
    primary = evidence_rows[-1] if evidence_rows else None
    evidence_id = primary.id if primary else None
    evidence_url = primary.url if primary else None

    supersede(session, SourceLookup, prop.id, d.key)
    lookup = SourceLookup(property_id=prop.id, source_key=d.key, status=outcome.status, message=outcome.message[:4000] if outcome.message else None,
                          evidence_id=evidence_id, job_id=job_id, started_at=utcnow(), finished_at=utcnow())
    session.add(lookup)

    payload = outcome.payload
    if outcome.status in (LookupStatus.SUCCESS, LookupStatus.NO_MATCH) and payload is not None:
        if isinstance(payload, AssessmentPayload):
            save_assessment(session, prop, adapter, payload, evidence_id, evidence_url, entry_method, refresh_run_id)
        elif isinstance(payload, ParcelGisPayload):
            save_parcel_gis(session, prop, adapter, payload, evidence_id, evidence_url)
        elif isinstance(payload, TaxClaimPayload):
            save_tax_claim(session, prop, adapter, payload, evidence_id, evidence_url, entry_method, refresh_run_id)
        elif isinstance(payload, list) and d.category == "recorder":
            docs = [p for p in payload if isinstance(p, RecorderDocumentPayload)]
            created, updated = save_documents(session, prop, d.key, docs, evidence_id, entry_method)
            lookup.message = f"{outcome.message}; {created} new, {updated} updated document(s)"
        elif isinstance(payload, list) and d.category == "civil":
            save_lien_cases(session, prop, adapter, payload, evidence_id, entry_method)
    elif d.category == "tax" and outcome.status not in (LookupStatus.SUCCESS,):
        session.add(TaxStatusHistory(property_id=prop.id, source_key=d.key, lookup_status=outcome.status, refresh_run_id=refresh_run_id))

    if d.category == "recorder" and outcome.status in (LookupStatus.SUCCESS, LookupStatus.NO_MATCH):
        session.flush()
        evaluate_property_mortgages(session, prop, active_ruleset(session).config["mortgage"]["since_year"], searched=True)

    if outcome.status in (LookupStatus.SUCCESS, LookupStatus.NO_MATCH):
        for task in session.scalars(select(CaptureTask).where(CaptureTask.property_id == prop.id, CaptureTask.source_key == d.key,
                                                              CaptureTask.status == "open")):
            task.status = "done"
            task.completed_at = utcnow()
            task.completed_by = actor
            task.evidence_id = evidence_id
    session.flush()
    record_audit(session, actor, "enrichment.lookup", "property", prop.id, prop.project_id,
                 {"source": d.key, "status": outcome.status, "message": outcome.message, "evidence_id": evidence_id,
                  "entry_method": entry_method, "job_id": job_id})
    return lookup


# --------------------------------------------------------------------------------------------- orchestration


def ensure_capture_task(session: Session, prop: Property, adapter: SourceAdapter, reason: str) -> CaptureTask | None:
    ref = property_ref(prop)
    request = adapter.capture_request(ref)
    if request is None:
        return None
    task = session.scalar(select(CaptureTask).where(CaptureTask.property_id == prop.id, CaptureTask.source_key == adapter.descriptor.key,
                                                    CaptureTask.status == "open"))
    if task is None:
        task = CaptureTask(project_id=prop.project_id, property_id=prop.id, source_key=adapter.descriptor.key, reason=reason)
        session.add(task)
    task.url = request.url
    task.search_hint = request.search_hint
    task.instructions = request.instructions
    if reason == "daily_refresh":
        task.reason = reason
    session.flush()
    return task


def _fresh_success(session: Session, prop_id: int, source_key: str, ttl_hours: float) -> bool:
    lookup = session.scalar(select(SourceLookup).where(SourceLookup.property_id == prop_id, SourceLookup.source_key == source_key,
                                                       SourceLookup.is_current.is_(True)))
    return bool(lookup and lookup.status == LookupStatus.SUCCESS and lookup.finished_at
                and utcnow() - lookup.finished_at < timedelta(hours=ttl_hours))


def _has_capture(session: Session, prop_id: int, source_key: str) -> bool:
    lookup = session.scalar(select(SourceLookup).where(SourceLookup.property_id == prop_id, SourceLookup.source_key == source_key,
                                                       SourceLookup.is_current.is_(True)))
    return bool(lookup and lookup.status in (LookupStatus.SUCCESS, LookupStatus.NO_MATCH))


def enrich_property(session: Session, prop: Property, *, actor: str = "system", source_keys: list[str] | None = None,
                    force: bool = False, job_id: int | None = None, run_valuation: bool = True) -> dict[str, str]:
    connector = get_connector(prop.county)
    summary: dict[str, str] = {}
    wanted = set(source_keys) if source_keys else None
    for adapter in connector.sources():
        d = adapter.descriptor
        if wanted is not None and d.key not in wanted:
            continue
        policy = get_policy(session, d.key)
        if not policy.enabled:
            summary[d.key] = "disabled"
            continue
        if d.automated:
            if d.requires_terms_ack and policy.terms_acknowledged_at is None:
                outcome = SourceOutcome(LookupStatus.TERMS_NOT_ACKNOWLEDGED,
                                        "Automated access awaits an administrator's acknowledgement of this source's terms of use; "
                                        "use the capture task meanwhile")
                persist_outcome(session, prop, adapter, outcome, entry_method=EntryMethod.AUTOMATED, actor=actor, job_id=job_id)
                ensure_capture_task(session, prop, adapter, "terms_not_acknowledged")
                summary[d.key] = outcome.status
                continue
            if not force and _fresh_success(session, prop.id, d.key, d.cache_ttl_hours):
                summary[d.key] = "cached"
                continue
            try:
                outcome = adapter.lookup(property_ref(prop))
            except Exception as exc:  # adapters should not raise, but never lose the error
                log.exception("Adapter %s failed for property %s", d.key, prop.id)
                outcome = SourceOutcome(LookupStatus.UNEXPECTED, f"{type(exc).__name__}: {exc}")
            persist_outcome(session, prop, adapter, outcome, entry_method=EntryMethod.AUTOMATED, actor=actor, job_id=job_id)
            summary[d.key] = outcome.status
            if outcome.status in (LookupStatus.NO_MATCH, LookupStatus.BLOCKED, LookupStatus.USER_ACTION) and prop.county == "montco" \
                    and d.key == "montco.assessment_gis":
                ensure_capture_task(session, prop, connector.source("montco.assessment_portal"), "gis_unavailable")
        else:
            default = d.key in DEFAULT_CAPTURE.get(prop.county, [])
            if wanted is None and not default:
                continue
            if _has_capture(session, prop.id, d.key) and not force:
                summary[d.key] = "captured"
                continue
            ensure_capture_task(session, prop, adapter, "enrichment")
            if not session.scalar(select(SourceLookup.id).where(SourceLookup.property_id == prop.id, SourceLookup.source_key == d.key,
                                                                SourceLookup.is_current.is_(True))):
                persist_outcome(session, prop, adapter, SourceOutcome(LookupStatus.USER_ACTION,
                                f"{d.name}: user-assisted step required ({d.compliance_status})"),
                                entry_method=EntryMethod.AUTOMATED, actor=actor, job_id=job_id)
            summary[d.key] = LookupStatus.USER_ACTION

    config = active_ruleset(session).config
    if run_valuation:
        from app.services.valuation_service import run_valuations

        run_valuations(session, prop, config, actor=actor, force=force)
    prop.last_enriched_at = utcnow()
    session.flush()
    evaluate_properties(session, [prop.id], actor)
    return summary


def submit_capture(session: Session, prop: Property, source_key: str, content: str, actor: str, *, no_results: bool = False,
                   preview: bool = False, url: str | None = None) -> tuple[SourceOutcome, dict]:
    adapter = get_source(source_key)
    if adapter.descriptor.county != prop.county:
        raise ValueError("Source does not belong to this property's county")
    if no_results:
        if adapter.descriptor.category not in ("recorder", "civil"):
            raise ValueError("'No results' confirmation is only available for recorder and civil searches")
        outcome = SourceOutcome(LookupStatus.NO_MATCH, "User confirmed the official search returned no results", payload=[])
    else:
        if not content or not content.strip():
            raise ValueError("Captured content is empty")
        outcome = adapter.parse_capture(property_ref(prop), content)
    preview_data = {"status": outcome.status, "message": outcome.message, "payload": jsonable(outcome.payload)}
    if preview:
        return outcome, preview_data
    task = session.scalar(select(CaptureTask).where(CaptureTask.property_id == prop.id, CaptureTask.source_key == source_key,
                                                    CaptureTask.status == "open"))
    if outcome.status in (LookupStatus.SUCCESS, LookupStatus.NO_MATCH):
        body = (content or "(user confirmed no results)").encode("utf-8")
        outcome.evidence = [EvidenceDraft(url=url or (task.url if task else None), content=body,
                                          content_type="text/html" if "<" in (content or "")[:1000] else "text/plain",
                                          entry_method=EntryMethod.USER_CAPTURE, request_summary=f"Captured from official site by {actor}")]
        persist_outcome(session, prop, adapter, outcome, entry_method=EntryMethod.USER_CAPTURE, actor=actor)
        if adapter.descriptor.key == "delco.assessment_portal":
            from app.services.valuation_service import run_valuations

            run_valuations(session, prop, active_ruleset(session).config, actor=actor)
        evaluate_properties(session, [prop.id], actor)
    elif task is not None:
        task.last_error = outcome.message
        record_audit(session, actor, "capture.rejected", "property", prop.id, prop.project_id, {"source": source_key, "message": outcome.message})
    session.flush()
    return outcome, preview_data


__all__ = ["enrich_property", "submit_capture", "persist_outcome", "ensure_capture_task", "AccessMethod"]
